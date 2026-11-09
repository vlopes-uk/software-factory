#!/usr/bin/env python3
"""Spec pack: only the spec excerpts one task needs, so agents don't read whole documents.

  bash scripts/gate spec <api#N | web#N | owner/repo#N>

Reads the task issue and pulls, from the product repo's docs (the open change request's
docs first, when the issue says `Change request: CR-NNN`):
  - the story section(s) it implements (### S-NN ...) with their acceptance criteria
  - OpenAPI operations by operationId + every component schema they reference (recursively)
  - UI-SPEC screens (### SCR-NN ...) and named components
  - ARCHITECTURE.md sections referenced as §N
  - ADRs referenced as "ADR NNNN" (whole file)
  - TEST-PLAN rows mentioning the story/AC or TC-NNN ids
Anything it can't find is listed under MISSING, so the agent opens the full document for just that.
YAML is extracted by indentation (block style, as the Architect writes it); no PyYAML needed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import gate
import tasks

MAX_PACK_CHARS = 60_000


# ---------------------------------------------------------------- issue lookup

def resolve(target: str, cfg: dict) -> tuple[str, int]:
    m = re.fullmatch(r"\s*(?:(api|web)|([\w.-]+/[\w.-]+))#(\d+)\s*", target, re.I)
    if not m:
        raise ValueError("usage: gate spec <api#N | web#N | owner/repo#N>")
    repo = cfg["repos"][m.group(1).lower()]["github"] if m.group(1) else m.group(2)
    return repo.lower(), int(m.group(3))


def fetch_issue(repo: str, number: int) -> dict:
    if os.environ.get("FACTORY_GH_FIXTURE"):
        fx = json.loads(Path(os.environ["FACTORY_GH_FIXTURE"]).read_text(encoding="utf-8"))
        pool = {k.lower(): v for k, v in fx.get("issues", {}).items()}
        for i in pool.get(repo, []):
            if i["number"] == number:
                return i
        raise RuntimeError(f"{repo}#{number} not found")
    out = subprocess.run(["gh", "issue", "view", str(number), "--repo", repo, "--json",
                          "number,title,body,labels"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh issue view failed")
    return json.loads(out.stdout)


# ---------------------------------------------------------------- markdown helpers

def md_section(text: str, heading_re: str) -> str | None:
    """Return the section whose heading matches heading_re, up to the next heading of same or higher level."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and re.search(heading_re, m.group(2)):
            level = len(m.group(1))
            out = [line]
            for nxt in lines[i + 1:]:
                h = re.match(r"^(#{1,6})\s", nxt)
                if h and len(h.group(1)) <= level:
                    break
                out.append(nxt)
            return "\n".join(out).rstrip()
    return None


def table_rows(text: str, needles: list[str]) -> list[str]:
    rows, header = [], None
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            if header is None:
                header = line
            if any(n in line for n in needles) and line != header:
                rows.append(line)
        else:
            header = None
    return rows


# ---------------------------------------------------------------- yaml (indentation) helpers

def yaml_block(lines: list[str], start: int) -> list[str]:
    """Lines of the YAML node starting at `start` (its key line plus deeper-indented lines)."""
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def openapi_extract(text: str, op_ids: list[str]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    found, missing, chunks = set(), [], []
    path_re = re.compile(r"^  (/\S*):\s*(\{.*)?$")
    # operations: walk each path, then each method under it
    i = 0
    while i < len(lines):
        m = path_re.match(lines[i])
        if not m:
            i += 1
            continue
        block = yaml_block(lines, i)
        flat = "\n".join(block)
        hit = [op for op in op_ids if re.search(rf"operationId:\s*['\"]?{re.escape(op)}['\"]?\b", flat)]
        if hit:
            # keep only methods whose operationId matched (methods are at indent 4)
            keep = [block[0]]
            j = 1
            methods = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
            while j < len(block):
                km = re.match(r"^    (\w+):", block[j])
                if not km:
                    j += 1
                    continue
                sub = yaml_block(block, j)
                if km.group(1) not in methods or any(
                        re.search(rf"operationId:\s*['\"]?{re.escape(op)}['\"]?\b", "\n".join(sub)) for op in hit):
                    keep += sub  # matching operation, or path-level keys such as parameters
                j += len(sub)
            if len(keep) == 1:  # flow-style path line: keep it whole
                keep = block
            chunks.append("\n".join(keep))
            found.update(hit)
        i += len(block)
    missing += [f"operationId {op}" for op in op_ids if op not in found]
    # referenced components, recursively
    comp_start = next((k for k, l in enumerate(lines) if l.startswith("components:")), None)
    wanted, seen = set(), set()
    ref_re = re.compile(r"#/components/(\w+)/([\w.-]+)")

    def collect(txt):
        for kind, name in ref_re.findall(txt):
            wanted.add((kind, name))

    for c in chunks:
        collect(c)
    comp_chunks = []
    while comp_start is not None and wanted - seen:
        kind, name = sorted(wanted - seen)[0]
        seen.add((kind, name))
        kind_idx = next((k for k in range(comp_start, len(lines)) if lines[k] == f"  {kind}:"), None)
        if kind_idx is None:
            missing.append(f"components/{kind}/{name}")
            continue
        kind_block = yaml_block(lines, kind_idx)
        rel = next((k for k, l in enumerate(kind_block) if re.match(rf"^    {re.escape(name)}:", l)), None)
        if rel is None:
            missing.append(f"components/{kind}/{name}")
            continue
        node = "\n".join(yaml_block(kind_block, rel))
        comp_chunks.append(f"# components/{kind}\n{node}")
        collect(node)
    out = ""
    if chunks:
        out += "paths:\n" + "\n".join(chunks)
    if comp_chunks:
        out += "\n\n" + "\n\n".join(comp_chunks)
    return out.strip(), missing


# ---------------------------------------------------------------- pack

def doc_dirs(cr: str | None, state: dict) -> list[Path]:
    """Folders to search, most specific first: open CR folder, then main docs."""
    dirs = []
    if cr and cr in state.get("changes", {}) and state["changes"][cr]["status"] == "open":
        dirs.append(gate.CHANGES / state["changes"][cr]["folder"])
    dirs.append(gate.DOCS)
    return dirs


def find_file(dirs: list[Path], rel: str) -> Path | None:
    for d in dirs:
        p = d / rel
        if p.exists():
            return p
    return None


def build_pack(issue: dict, repo: str, cfg: dict) -> str:
    state = gate.load()
    body = issue.get("body") or ""
    cr = (re.search(r"^\s*Change request:\s*(CR-\d{3,})", body, re.M | re.I) or [None, None])[1]
    cr = cr.upper() if cr else None
    dirs = doc_dirs(cr, state)
    sections, missing = [], []

    # stories + ACs
    story_ids = set(re.findall(r"\bS-\d+\b", body))
    product = cfg["repos"]["product"]["github"].lower()
    for r, n in tasks.line_refs(body, "implements"):
        if r == product:
            try:
                s = fetch_issue(product, n)
                story_ids.update(re.findall(r"\[(S-\d+)\]", s.get("title", "")))
            except Exception:
                missing.append(f"story issue {r}#{n}")
    req_files = [p for p in (find_file([d], "01-requirements/REQUIREMENTS.md") for d in dirs) if p] + \
                [p for p in [gate.DOCS / "01-requirements/PRD.md"] if p.exists()]
    for sid in sorted(story_ids, key=lambda x: int(x[2:])):
        sec = next((s for f in req_files if (s := md_section(f.read_text(encoding="utf-8"),
                                                            rf"^{re.escape(sid)}\b"))), None)
        sections.append(("Story " + sid, sec)) if sec else missing.append(f"story {sid} section")

    # contract
    op_ids = sorted({x for line in body.splitlines() if re.search(r"operationIds?\b", line, re.I)
                     for x in re.findall(r"`(\w+)`", line)})
    if op_ids:
        spec = find_file(dirs, "02-architecture/openapi.yaml")
        if spec:
            text, miss = openapi_extract(spec.read_text(encoding="utf-8"), op_ids)
            if text:
                sections.append((f"Contract ({spec.relative_to(gate.ROOT).as_posix()}): {', '.join(op_ids)}",
                                 "```yaml\n" + text + "\n```"))
            missing += miss
        else:
            missing.append("openapi.yaml")

    # architecture sections (§N) and ADRs
    for num in sorted(set(re.findall(r"§\s*(\d+)", body)), key=int):
        sec = None
        for f in [p for p in (find_file([d], "02-architecture/ARCHITECTURE.md") for d in dirs) if p]:
            sec = md_section(f.read_text(encoding="utf-8"), rf"^(Changes to )?§?{num}\.?\s")
            if sec:
                break
        sections.append((f"Architecture §{num}", sec)) if sec else missing.append(f"ARCHITECTURE §{num}")
    for adr in sorted(set(re.findall(r"ADR[\s-]*(\d{4})", body))):
        f = next((p for d in dirs for p in (d / "02-architecture/adr").glob(f"{adr}-*.md")), None)
        sections.append((f"ADR {adr}", f.read_text(encoding="utf-8").strip())) if f else missing.append(f"ADR {adr}")

    # UI screens
    screens = sorted(set(re.findall(r"\bSCR-\d+\b", body)), key=lambda x: int(x[4:]))
    for scr in screens:
        sec = None
        for f in [p for p in (find_file([d], "03-design/UI-SPEC.md") for d in dirs) if p]:
            sec = md_section(f.read_text(encoding="utf-8"), rf"^{re.escape(scr)}\b")
            if sec:
                break
        sections.append((f"UI {scr}", sec)) if sec else missing.append(f"UI-SPEC {scr}")

    # test plan rows
    needles = sorted(set(re.findall(r"\bTC-\d+\b", body))) + sorted(story_ids)
    if needles:
        rows = []
        for f in [p for p in (find_file([d], "05-qa/TEST-PLAN.md") for d in dirs) if p]:
            rows += table_rows(f.read_text(encoding="utf-8"), needles)
        if rows:
            sections.append(("Test plan rows", "\n".join(dict.fromkeys(rows))))
        else:
            missing.append("TEST-PLAN rows for " + ", ".join(needles))

    head = [f"# Spec pack for {repo}#{issue['number']}: {issue.get('title', '')}",
            f"Scope: {cr + ' (open change request docs first, then main)' if cr and len(dirs) > 1 else 'main docs'}",
            "Binding: these excerpts are copied verbatim from the approved docs. If anything you need is "
            "missing below, open that document directly (only that part)."]
    out = "\n".join(head) + "\n"
    for title, content in sections:
        out += f"\n## {title}\n\n{content}\n"
    out += "\n## MISSING\n\n" + ("\n".join(f"- {m}" for m in missing) if missing else "- nothing") + "\n"
    if len(out) > MAX_PACK_CHARS:
        out = out[:MAX_PACK_CHARS] + "\n\n[TRUNCATED: pack too large; open the referenced docs directly]\n"
    return out


def cmd(args: list[str]) -> None:
    if not args:
        print(__doc__)
        return
    try:
        cfg = tasks.load_config()
        repo, number = resolve(args[0], cfg)
        issue = fetch_issue(repo, number)
        print(build_pack(issue, repo, cfg))
    except Exception as e:
        print(f"SPEC PACK UNAVAILABLE: {e}. Read the task's spec references in the full documents instead.")
