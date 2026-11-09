#!/usr/bin/env python3
"""Deterministic phase gates for the factory plugin.

State lives in <repo>/.factory/state.json. Approval records a SHA-256 of each
phase's artifacts; any later edit to an approved artifact ("changed") or a
re-approval upstream ("stale") blocks every downstream phase until a human
re-approves.

Two kinds of scope:
  main     the product baseline, docs/factory/0N-*
  CR-NNN   a change request (new feature), docs/factory/changes/CR-NNN-<slug>/0N-*
           Each CR phase depends on its own upstream phases AND on the same
           main phase being approved. Gating a CR never blocks main or other CRs.
           A merged CR's docs are folded into main and main is re-baselined.

Usage:
  gate.py init
  gate.py config                          print .factory/config.json + stack profile path
  gate.py status [--brief]
  gate.py check <phase> [--scope X] [--soft]
                                          X = CR-NNN or anything else (= main)
                                          exit 0 = may start, 3 = blocked (--soft: always 0)
  gate.py change new <title words...>     create a change request
  gate.py change list
  gate.py approve <phase>                 human-only (the hook blocks the model)
  gate.py approve CR-NNN <phase>          human-only
  gate.py approve merge CR-NNN            human-only: accept CR docs merged into main
  gate.py autonomy [show | preset <manual|assisted|autopilot> | set <key> <value>]
                                          build-phase autonomy (preset/set are human-only)
  gate.py spec <api#N | web#N>            spec pack: only the spec excerpts that task references
  gate.py tasks list|next|story S-NN|plan <target> [x]
                                          task readiness for /factory:build (see tasks.py)
  gate.py review <phase> [--scope CR-NNN] diff of upstream docs since this phase was approved
                                          (from approval snapshots; git history as fallback)
"""
from __future__ import annotations

import datetime as _dt
import difflib
import shutil
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # Windows consoles default to a legacy code page; diffs may contain any character
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PHASES: dict[str, dict] = {
    "requirements": {"role": "Product Owner (/factory:po)", "deps": [],
                     "dir": "01-requirements", "primary": "PRD.md", "cr_primary": "REQUIREMENTS.md"},
    "architecture": {"role": "Architect (/factory:architect)", "deps": ["requirements"],
                     "dir": "02-architecture", "primary": "ARCHITECTURE.md", "cr_primary": "ARCHITECTURE.md"},
    "design": {"role": "UI Designer (/factory:design)", "deps": ["requirements"],
               "dir": "03-design", "primary": "UI-SPEC.md", "cr_primary": "UI-SPEC.md"},
    "planning": {"role": "Architect, planning mode (/factory:plan)", "deps": ["architecture", "design"],
                 "dir": "04-plan", "primary": "PLAN.md", "cr_primary": "PLAN.md"},
    "test-plan": {"role": "QA (/factory:qa plan)", "deps": ["planning"],
                  "dir": "05-qa", "primary": "TEST-PLAN.md", "cr_primary": "TEST-PLAN.md"},
    # build is never approved as a whole: each PR is gated by a human merge.
    "build": {"role": "BE/FE Developers (/factory:build), QA review (/factory:qa review)",
              "deps": ["planning", "test-plan"], "dir": None, "primary": None, "cr_primary": None},
}
ORDER = list(PHASES)
DOC_PHASES = [p for p in ORDER if PHASES[p]["dir"]]
EXIT_BLOCKED = 3
CR_RE = re.compile(r"^CR-\d{3,}$")
CONTRACT = "openapi.yaml"
MAIN = None  # scope value for the baseline
REVIEW_LINES = 400  # max diff lines printed per upstream phase


def repo_root() -> Path:
    # Hooks and skills run with CLAUDE_PROJECT_DIR set to the product repo, even when
    # an agent has cd'ed into a sibling code repo.
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / ".factory").exists():
        return Path(env)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
        if out:
            return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Path(env or os.getcwd())


ROOT = repo_root()
STATE = ROOT / ".factory" / "state.json"
CONFIG = ROOT / ".factory" / "config.json"
DOCS = ROOT / "docs" / "factory"
CHANGES = DOCS / "changes"
SNAP = ROOT / ".factory" / "snapshots"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# ---------------------------------------------------------------- autonomy (build phase only)
# Planning phases always need a human approval and merges always need a human: those are
# not settings. Autonomy only shapes what happens *inside* the build phase.
AUTONOMY_KEYS = {
    # key: (type, min, max, description)
    "auto_qa": (bool, None, None, "run the QA reviewer automatically on every PR a developer opens"),
    "max_fix_rounds": (int, 0, 3, "automatic developer-fix -> QA re-check rounds when QA requests changes"),
    "confirm_dispatch": (bool, None, None, "ask for 'go' before `next` / `story` start developers"),
    "auto_continue": (bool, None, None, "after a task's chain ends, start the next ready task automatically"),
    "max_tasks_per_run": (int, 1, 10, "cap on tasks one /factory:build run may start (auto_continue)"),
    "max_parallel": (int, 1, 5, "developers running at the same time"),
}
AUTONOMY_PRESETS = {
    "manual":    {"auto_qa": False, "max_fix_rounds": 0, "confirm_dispatch": True,
                  "auto_continue": False, "max_tasks_per_run": 1, "max_parallel": 1},
    "assisted":  {"auto_qa": True, "max_fix_rounds": 1, "confirm_dispatch": True,
                  "auto_continue": False, "max_tasks_per_run": 3, "max_parallel": 3},
    "autopilot": {"auto_qa": True, "max_fix_rounds": 2, "confirm_dispatch": False,
                  "auto_continue": True, "max_tasks_per_run": 5, "max_parallel": 3},
}
NEVER_AUTONOMOUS = {"auto_merge", "auto_approve", "planning", "requirements", "architecture",
                    "design", "test_plan", "test-plan", "approve", "merge", "deploy_prod", "prod"}
DEFAULT_AUTONOMY = {"preset": "assisted"}


def effective_autonomy(cfg: dict) -> tuple[dict, list[str]]:
    """Return (settings, warnings) from cfg['autonomy']: preset defaults + validated overrides."""
    raw = cfg.get("autonomy") or dict(DEFAULT_AUTONOMY)
    warnings: list[str] = []
    preset = raw.get("preset", DEFAULT_AUTONOMY["preset"])
    if preset not in AUTONOMY_PRESETS:
        warnings.append(f"unknown preset '{preset}', using 'assisted'")
        preset = "assisted"
    eff = dict(AUTONOMY_PRESETS[preset], preset=preset)
    for k, v in raw.items():
        if k == "preset":
            continue
        if k in NEVER_AUTONOMOUS or k.replace("-", "_") in NEVER_AUTONOMOUS:
            warnings.append(f"'{k}' ignored: planning approvals, merges and prod deploys always need you")
            continue
        if k not in AUTONOMY_KEYS:
            warnings.append(f"unknown autonomy key '{k}' ignored")
            continue
        typ, lo, hi, _ = AUTONOMY_KEYS[k]
        if typ is bool:
            if not isinstance(v, bool):
                warnings.append(f"'{k}' must be true/false; kept {eff[k]}")
                continue
        else:
            if isinstance(v, bool) or not isinstance(v, int):
                warnings.append(f"'{k}' must be a number; kept {eff[k]}")
                continue
            if v < lo or v > hi:
                warnings.append(f"'{k}'={v} clamped to {min(max(v, lo), hi)} (allowed {lo}-{hi})")
                v = min(max(v, lo), hi)
        eff[k] = v
    return eff, warnings


def autonomy_summary(eff: dict) -> str:
    bits = [f"auto QA {'on' if eff['auto_qa'] else 'off'}"]
    if eff["auto_qa"]:
        bits.append(f"{eff['max_fix_rounds']} auto-fix round(s)")
    bits.append("asks before starting" if eff["confirm_dispatch"] else "starts without asking")
    if eff["auto_continue"]:
        bits.append(f"continues to next ready task (max {eff['max_tasks_per_run']} per run)")
    bits.append(f"max {eff['max_parallel']} in parallel")
    return f"{eff['preset']}: " + ", ".join(bits) + ". Merges and planning approvals always need you."


def parse_value(key: str, text: str):
    typ = AUTONOMY_KEYS[key][0]
    if typ is bool:
        low = text.strip().lower()
        if low in ("true", "on", "yes", "1"):
            return True
        if low in ("false", "off", "no", "0"):
            return False
        raise ValueError(f"{key} expects true/false")
    return int(text)


def cmd_autonomy(args: list[str]) -> None:
    if not CONFIG.exists():
        print("AUTONOMY: no .factory/config.json; run /factory:init first.")
        return
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    sub = args[0].lower() if args else "show"
    if sub == "preset":
        name = (args[1] if len(args) > 1 else "").lower()
        if name not in AUTONOMY_PRESETS:
            print(f"AUTONOMY: unknown preset '{name}'. Choose: {', '.join(AUTONOMY_PRESETS)}")
            return
        cfg["autonomy"] = {"preset": name}  # a preset resets individual overrides
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"AUTONOMY SET: preset '{name}' (individual overrides cleared).")
    elif sub == "set":
        if len(args) < 3 or args[1] not in AUTONOMY_KEYS:
            if len(args) > 1 and (args[1] in NEVER_AUTONOMOUS or args[1].replace("-", "_") in NEVER_AUTONOMOUS):
                print(f"AUTONOMY: '{args[1]}' can't be automated: planning approvals, merges and prod "
                      "deploys always need you.")
                return
            print("AUTONOMY: usage: set <key> <value>. Keys: " + ", ".join(AUTONOMY_KEYS))
            return
        try:
            value = parse_value(args[1], args[2])
        except ValueError as e:
            print(f"AUTONOMY: {e}")
            return
        typ, lo, hi, _ = AUTONOMY_KEYS[args[1]]
        if typ is int and not lo <= value <= hi:
            print(f"AUTONOMY: {args[1]} must be between {lo} and {hi}; nothing changed.")
            return
        cfg.setdefault("autonomy", dict(DEFAULT_AUTONOMY))[args[1]] = value
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"AUTONOMY SET: {args[1]} = {json.dumps(value)}")
    elif sub != "show":
        print("AUTONOMY: usage: show | preset <manual|assisted|autopilot> | set <key> <value>")
        return
    eff, warnings = effective_autonomy(json.loads(CONFIG.read_text(encoding="utf-8")))
    print("AUTONOMY " + autonomy_summary(eff))
    for k, (typ, lo, hi, desc) in AUTONOMY_KEYS.items():
        rng = "true/false" if typ is bool else f"{lo}-{hi}"
        print(f"  {k:18} = {json.dumps(eff[k]):6} ({rng})  {desc}")
    for w in warnings:
        print(f"  WARNING: {w}")


DEFAULT_CONFIG = {
    "stack": "dotnet-react-azure",
    "autonomy": dict(DEFAULT_AUTONOMY),
    "repos": {
        "product": {"github": "OWNER/PRODUCT-product", "path": "."},
        "api": {"github": "OWNER/PRODUCT-api", "path": "../PRODUCT-api", "label": "be"},
        "web": {"github": "OWNER/PRODUCT-web", "path": "../PRODUCT-web", "label": "fe"},
    },
}


# ---------------------------------------------------------------- basics

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def label(scope) -> str:
    return scope or "main"


def norm_scope(raw: str | None):
    if raw and CR_RE.match(raw.strip().upper()):
        return raw.strip().upper()
    return MAIN


def require_init(state: dict) -> None:
    if not state:
        print("GATE: factory not initialised in this repo. Run /factory:init first.")
        sys.exit(EXIT_BLOCKED)


def cr(state: dict, scope: str) -> dict:
    c = state.get("changes", {}).get(scope)
    if c is None:
        print(f"GATE: unknown change request {scope}. Create one with /factory:change new <title>.")
        sys.exit(EXIT_BLOCKED)
    return c


# ---------------------------------------------------------------- scope-aware paths

def phase_dir(phase: str, scope, state: dict) -> Path:
    d = PHASES[phase]["dir"]
    if scope is MAIN:
        return DOCS / d
    return CHANGES / cr(state, scope)["folder"] / d


def primary(phase: str, scope, state: dict) -> Path:
    key = "primary" if scope is MAIN else "cr_primary"
    return phase_dir(phase, scope, state) / PHASES[phase][key]


def records(state: dict, scope) -> dict:
    if scope is MAIN:
        return state.setdefault("phases", {})
    return cr(state, scope).setdefault("phases", {})


def artifact_files(phase: str, scope, state: dict) -> list[Path]:
    d = phase_dir(phase, scope, state)
    if not d.is_dir():
        return []
    return sorted(f for f in d.rglob("*") if f.is_file() and f.name != ".gitkeep")


def digest(phase: str, scope, state: dict) -> str:
    h = hashlib.sha256()
    for f in artifact_files(phase, scope, state):
        h.update(rel(f).encode() + b"\0")
        h.update(f.read_bytes() + b"\0")
    return h.hexdigest()


def deps_of(phase: str, scope) -> list[tuple]:
    """(phase, scope) pairs this phase depends on."""
    deps = [(d, scope) for d in PHASES[phase]["deps"]]
    if scope is not MAIN and phase != "build":
        deps.append((phase, MAIN))  # a CR builds on the approved baseline
    return deps


# ---------------------------------------------------------------- status engine

def status_of(phase: str, scope, state: dict, memo: dict | None = None) -> str:
    """not-started | draft | approved | changed | stale | ready | blocked | merged"""
    memo = {} if memo is None else memo
    key = (phase, scope)
    if key in memo:
        return memo[key]
    if scope is not MAIN and cr(state, scope).get("status") == "merged":
        result = status_of(phase, MAIN, state, memo) if phase == "build" else "merged"
        memo[key] = result
        return result
    deps = deps_of(phase, scope)
    deps_ok = all(status_of(p, s, state, memo) == "approved" for p, s in deps)
    if PHASES[phase]["dir"] is None:  # build
        result = "ready" if deps_ok else "blocked"
    else:
        rec = records(state, scope).get(phase)
        if rec is None:
            result = "draft" if primary(phase, scope, state).exists() else "not-started"
        elif rec.get("sha256") != digest(phase, scope, state):
            result = "changed"
        elif not deps_ok:
            result = "stale"
        else:
            mine = rec["approved_at"]
            newer = any(records(state, s)[p]["approved_at"] > mine for p, s in deps)
            result = "stale" if newer else "approved"
    memo[key] = result
    return result


def blockers(phase: str, scope, state: dict) -> list[str]:
    memo: dict = {}
    out = []
    for p, s in deps_of(phase, scope):
        st = status_of(p, s, state, memo)
        if st != "approved":
            out.append(f"{label(s)} {p} is '{st}' (owner: {PHASES[p]['role']})")
    return out


def path_scope_phase(path: str, state: dict | None = None):
    """Map a file path to (phase, scope) of the artifact folder containing it, or None.
    scope may be an unknown 'CR-NNN?' string when the folder isn't registered."""
    try:
        r = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        r = Path(path).as_posix()
    state = load() if state is None else state
    for phase in DOC_PHASES:
        d = f"docs/factory/{PHASES[phase]['dir']}"
        if r == d or r.startswith(d + "/"):
            return phase, MAIN
    m = re.match(r"docs/factory/changes/([^/]+)/([^/]+)", r)
    if m:
        folder, sub = m.groups()
        cid = next((k for k, v in state.get("changes", {}).items() if v["folder"] == folder), None)
        for phase in DOC_PHASES:
            if PHASES[phase]["dir"] == sub:
                return phase, (cid or f"unregistered:{folder}")
    return None


# kept for backwards compatibility with the hook
def phase_for_path(path: str):
    hit = path_scope_phase(path)
    return hit[0] if hit else None


# ---------------------------------------------------------------- commands

def cmd_config() -> None:
    if not CONFIG.exists():
        print("CONFIG MISSING: run /factory:init to create .factory/config.json")
        return
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    print(json.dumps(cfg, indent=2))
    if "OWNER/" in json.dumps(cfg):
        print("WARNING: config.json still has placeholder repo names; fix them via /factory:init.")
    print(f"Stack profile: {PLUGIN_ROOT / 'stack' / (cfg.get('stack', 'dotnet-react-azure') + '.md')}")


def cmd_init() -> None:
    state = load()
    if state:
        print(f"Factory already initialised at {rel(STATE)}.")
    else:
        save({"version": 2, "created_at": now(), "phases": {}, "changes": {}})
        print(f"Created {rel(STATE)}.")
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        print(f"Created {rel(CONFIG)} with placeholders (edit repo names).")
    make_dirs(DOCS)
    print("Artifact folders ready under docs/factory/.")


def make_dirs(base: Path) -> None:
    dirs = [base / PHASES[p]["dir"] for p in DOC_PHASES]
    dirs += [base / "02-architecture" / "adr", base / "03-design" / "wireframes"]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        if not any(d.iterdir()):
            (d / ".gitkeep").touch()


def cmd_change(args: list[str]) -> None:
    state = load()
    require_init(state)
    changes = state.setdefault("changes", {})
    if not args or args[0] == "list":
        if not changes:
            print("No change requests yet. Create one: /factory:change new <title>")
        for cid, c in sorted(changes.items()):
            print(f"{cid}  [{c['status']}]  {c['title']}  ->  docs/factory/changes/{c['folder']}")
        return
    if args[0] != "new" or len(args) < 2:
        print("Usage: gate.py change new <title words...> | gate.py change list")
        sys.exit(2)
    title = " ".join(args[1:]).strip()
    n = 1 + max([int(k.split("-")[1]) for k in changes] or [0])
    cid = f"CR-{n:03d}"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "change"
    folder = f"{cid}-{slug}"
    changes[cid] = {"title": title, "folder": folder, "status": "open",
                    "created_at": now(), "phases": {}}
    save(state)
    make_dirs(CHANGES / folder)
    print(f"CREATED {cid}: '{title}' at docs/factory/changes/{folder}/")
    bl = blockers("requirements", cid, state)
    if bl:
        print("Note: this CR can start once the baseline is approved: " + "; ".join(bl))
    else:
        print(f"Next: /factory:po {cid}")


def status_rows(scope, state: dict, memo: dict) -> list[tuple]:
    rows = []
    for phase in ORDER:
        s = status_of(phase, scope, state, memo)
        rec = records(state, scope).get(phase, {}) if PHASES[phase]["dir"] else {}
        rows.append((phase, s, rec.get("approved_at", "-")))
    return rows


def cmd_status(brief: bool) -> None:
    state = load()
    if not state:
        if brief:
            return
        require_init(state)
    memo: dict = {}
    main_rows = status_rows(MAIN, state, memo)
    open_crs = [k for k, v in sorted(state.get("changes", {}).items()) if v["status"] == "open"]
    if brief:
        line = "Factory status: main: " + ", ".join(f"{p}={s}" for p, s, _ in main_rows)
        for cid in open_crs:
            line += f" | {cid}: " + ", ".join(f"{p}={s}" for p, s, _ in status_rows(cid, state, memo))
        print(line)
        return
    w = max(len(p) for p in ORDER)
    print("MAIN (baseline)")
    print(f"  {'PHASE'.ljust(w)}  {'STATUS'.ljust(11)}  {'APPROVED AT'.ljust(25)}  OWNER")
    for p, s, a in main_rows:
        print(f"  {p.ljust(w)}  {s.ljust(11)}  {a.ljust(25)}  {PHASES[p]['role']}")
    changes = state.get("changes", {})
    if changes:
        print("\nCHANGE REQUESTS")
        short = {"requirements": "req", "architecture": "arch", "design": "design",
                 "planning": "plan", "test-plan": "test", "build": "build"}
        for cid, c in sorted(changes.items()):
            if c["status"] == "merged":
                print(f"  {cid}  merged {c.get('merged_at', '')}  {c['title']}")
                continue
            cells = ", ".join(f"{short[p]}={s}" for p, s, _ in status_rows(cid, state, memo))
            print(f"  {cid}  {c['title']}\n      {cells}")
    if CONFIG.exists():
        try:
            eff, _ = effective_autonomy(json.loads(CONFIG.read_text(encoding="utf-8")))
            print("\nAutonomy (build phase): " + autonomy_summary(eff))
        except (ValueError, OSError):
            pass
    steps = next_steps(state, memo)
    if steps:
        print("\nNext:")
        for s in steps:
            print(f"  - {s}")


def review_cmd(phase: str, scope) -> str:
    arg = "" if scope is MAIN else f" {scope}"
    return {"requirements": f"/factory:po{arg} review",
            "architecture": f"/factory:architect{arg} review",
            "design": f"/factory:design{arg} review",
            "planning": f"/factory:plan{arg} review",
            "test-plan": f"/factory:qa review-plan{arg}"}[phase]


def next_step_for(scope, state: dict, memo: dict) -> str:
    tag = "" if scope is MAIN else f"{scope} "
    arg = "" if scope is MAIN else f" {scope}"
    for phase in ORDER:
        s = status_of(phase, scope, state, memo)
        if phase == "build":
            if s == "ready":
                if scope is MAIN:
                    return "build is open: /factory:build <api|web>#N, then /factory:qa review <api|web>#PR"
                return (f"{tag}build is open: build its tasks; merge docs into main any time with "
                        f"/factory:change merge {scope}")
            return ""
        if s in ("not-started", "draft") and not blockers(phase, scope, state):
            cmd = PHASES[phase]["role"].split("(")[1].rstrip(")")
            return f"{tag}{phase}: run {cmd}{arg}, then /factory:approve{arg} {phase}"
        if s == "stale":
            return (f"{tag}{phase} is stale (upstream changed): run {review_cmd(phase, scope)} "
                    f"for an impact report, then /factory:approve{arg} {phase}")
        if s == "changed":
            return f"{tag}{phase} was edited after approval: review the edits, then /factory:approve{arg} {phase}"
        if s != "approved":
            return ""
    return ""


def next_steps(state: dict, memo: dict) -> list[str]:
    out = [next_step_for(MAIN, state, memo)]
    for cid, c in sorted(state.get("changes", {}).items()):
        if c["status"] == "open":
            out.append(next_step_for(cid, state, memo))
    return [s for s in out if s]


def cmd_check(phase: str, scope, soft: bool) -> None:
    state = load()
    if not state:
        print("GATE BLOCKED: factory not initialised in this repo. Run /factory:init first. STOP.")
        if soft:
            return
        sys.exit(EXIT_BLOCKED)
    if scope is not MAIN and scope not in state.get("changes", {}):
        print(f"GATE BLOCKED: unknown change request {scope}. "
              "Create it with /factory:change new <title>. STOP.")
        if soft:
            return
        sys.exit(EXIT_BLOCKED)
    if scope is not MAIN and state["changes"][scope]["status"] == "merged":
        if phase != "build":
            print(f"GATE BLOCKED: {scope} is merged into main; its documents are frozen. "
                  "Change main, or open a new CR with /factory:change new <title>. STOP.")
            if soft:
                return
            sys.exit(EXIT_BLOCKED)
        print(f"Note: {scope} is merged; its tasks are now gated by the main build phase.")
        scope = MAIN
    bl = blockers(phase, scope, state)
    if bl:
        print(f"GATE BLOCKED: {label(scope)} '{phase}' cannot start yet.")
        for b in bl:
            print(f"  - {b}")
        print("Approve upstream phases with /factory:approve (human only). STOP.")
        if soft:
            return
        sys.exit(EXIT_BLOCKED)
    s = status_of(phase, scope, state)
    print(f"GATE OPEN: {label(scope)} '{phase}' may proceed (current status: {s}).")
    if scope is not MAIN:
        c = state["changes"][scope]
        print(f"SCOPE: change request {scope} '{c['title']}'")
        if PHASES[phase]["dir"]:
            print(f"Write ONLY in: {rel(phase_dir(phase, scope, state))}/  "
                  f"(primary file: {PHASES[phase]['cr_primary']})")
        print(f"Baseline (read-only for this CR): docs/factory/0N-*/  "
              f"Other CR docs: docs/factory/changes/{c['folder']}/")
    elif PHASES[phase]["dir"]:
        print(f"SCOPE: main baseline. Write ONLY in: {rel(phase_dir(phase, MAIN, state))}/")
    if s == "approved":
        print("Note: already approved; any edit marks it 'changed' and blocks what depends on it.")


def approval_record(phase: str, scope, state: dict) -> dict:
    files = artifact_files(phase, scope, state)
    return {
        "approved_at": now(),
        "approved_by": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "sha256": digest(phase, scope, state),
        "files": [rel(f) for f in files],
        "file_hashes": {rel(f): sha(f) for f in files},
    }


def snap_root(phase: str, scope) -> Path:
    return SNAP / label(scope) / phase


def take_snapshot(phase: str, scope, state: dict) -> int:
    """Copy the phase's current artifacts to .factory/snapshots/<scope>/<phase>/vN; return N."""
    root = snap_root(phase, scope)
    root.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(d.name[1:]) for d in root.iterdir() if d.name.startswith("v") and d.name[1:].isdigit()]
                or [0])
    base = phase_dir(phase, scope, state)
    for f in artifact_files(phase, scope, state):
        dst = root / f"v{n}" / f.relative_to(base)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    (root / f"v{n}").mkdir(parents=True, exist_ok=True)
    return n


def stamp_versions(phase: str, scope, state: dict, rec: dict) -> None:
    rec["snapshot"] = take_snapshot(phase, scope, state)
    rec["upstream_versions"] = {
        f"{label(s)}:{p}": records(state, s).get(p, {}).get("snapshot")
        for p, s in deps_of(phase, scope) if PHASES[p]["dir"]}


def cmd_approve(phase: str, scope) -> None:
    state = load()
    require_init(state)
    if scope is not MAIN and cr(state, scope)["status"] != "open":
        print(f"GATE: {scope} is already merged; change main or open a new CR.")
        sys.exit(EXIT_BLOCKED)
    if PHASES[phase]["dir"] is None:
        print("GATE: 'build' is approved per PR by merging it on GitHub, not here.")
        sys.exit(EXIT_BLOCKED)
    bl = blockers(phase, scope, state)
    if bl:
        print(f"GATE: cannot approve {label(scope)} '{phase}' while upstream is not approved:")
        for b in bl:
            print(f"  - {b}")
        sys.exit(EXIT_BLOCKED)
    p = primary(phase, scope, state)
    if not p.exists():
        print(f"GATE: cannot approve {label(scope)} '{phase}': {rel(p)} does not exist.")
        sys.exit(EXIT_BLOCKED)
    rec = approval_record(phase, scope, state)
    if scope is not MAIN and phase == "architecture":
        main_contract = DOCS / "02-architecture" / CONTRACT
        # main architecture is approved (it is a dependency), so its current file is the approved one
        rec["base_contract_sha256"] = sha(main_contract) if main_contract.exists() else None
    stamp_versions(phase, scope, state, rec)
    records(state, scope)[phase] = rec
    save(state)
    print(f"APPROVED: {label(scope)} '{phase}' ({len(rec['files'])} files hashed, snapshot v{rec['snapshot']}).")
    memo: dict = {}
    stale = [f"{label(s)} {p}" for s in [MAIN, *state.get("changes", {})]
             for p in DOC_PHASES
             if not (s is MAIN and p == phase and scope is MAIN)
             and (s is MAIN or state["changes"][s]["status"] == "open")
             and status_of(p, s, state, memo) == "stale"]
    if stale:
        print("Now stale (run each role's review mode for an impact report, then re-approve): "
              + ", ".join(stale))
    for s in next_steps(state, memo):
        print(f"Next: {s}")


def cmd_merge(scope) -> None:
    """Human-only: accept that a CR's docs have been folded into the main docs."""
    state = load()
    require_init(state)
    c = cr(state, scope)
    if c["status"] != "open":
        print(f"GATE: {scope} is already merged.")
        sys.exit(EXIT_BLOCKED)
    # main docs may be 'changed' (that IS the merge being applied) but nothing may be stale or missing
    # i.e. every main phase has an approval and none was approved before its upstream's re-approval
    main_recs = records(state, MAIN)
    main_bad = []
    for p in DOC_PHASES:
        rec = main_recs.get(p)
        if rec is None:
            main_bad.append(f"main {p} has never been approved")
        elif any(main_recs.get(d, {}).get("approved_at", "") > rec["approved_at"]
                 for d in PHASES[p]["deps"]):
            main_bad.append(f"main {p} is stale (an upstream phase was re-approved after it)")
    if main_bad:
        print("GATE: cannot merge; the main baseline must be approved (edited by the merge is fine):")
        for pr in main_bad:
            print(f"  - {pr}")
        sys.exit(EXIT_BLOCKED)
    # judge the CR against the baseline as it was approved, ignoring the in-progress merge edits
    memo: dict = {(p, MAIN): "approved" for p in DOC_PHASES}
    problems = [f"{scope} {p} is '{status_of(p, scope, state, memo)}'"
                for p in DOC_PHASES if status_of(p, scope, state, memo) != "approved"]
    if problems:
        print(f"GATE: cannot merge {scope}; every CR phase must be approved first:")
        for pr in problems:
            print(f"  - {pr}")
        sys.exit(EXIT_BLOCKED)
    # contract safety: CR contract must have been drafted on the contract main still had approved
    cr_contract = phase_dir("architecture", scope, state) / CONTRACT
    main_contract = DOCS / "02-architecture" / CONTRACT
    if cr_contract.exists():
        base = records(state, scope)["architecture"].get("base_contract_sha256")
        approved_main = records(state, MAIN)["architecture"].get("file_hashes", {}).get(rel(main_contract))
        if approved_main and base and approved_main != base:
            print(f"GATE: cannot merge {scope}: the main contract changed (another merge?) after "
                  f"{scope}'s architecture was approved. Rebase: /factory:architect {scope} rebase, "
                  f"then /factory:approve {scope} architecture (and downstream), then merge again.")
            sys.exit(EXIT_BLOCKED)
        if not main_contract.exists() or sha(main_contract) != sha(cr_contract):
            print(f"GATE: cannot merge {scope}: main {CONTRACT} is not identical to the CR's "
                  f"{CONTRACT} yet. Run /factory:change merge {scope} first.")
            sys.exit(EXIT_BLOCKED)
    stamp = now()
    for p in DOC_PHASES:  # re-baseline without touching approved_at (avoids cascading staleness)
        rec = records(state, MAIN)[p]
        files = artifact_files(p, MAIN, state)
        rec.update(sha256=digest(p, MAIN, state), files=[rel(f) for f in files],
                   file_hashes={rel(f): sha(f) for f in files}, rebaselined_at=stamp)
        rec.setdefault("merged_changes", []).append(scope)
        rec["snapshot"] = take_snapshot(p, MAIN, state)
    for p in DOC_PHASES:  # second pass: deps now point at the re-baselined snapshots
        records(state, MAIN)[p]["upstream_versions"] = {
            f"main:{d}": records(state, MAIN)[d]["snapshot"] for d in PHASES[p]["deps"]}
    c.update(status="merged", merged_at=stamp)
    save(state)
    print(f"MERGED: {scope} '{c['title']}' is now part of the main baseline (main re-baselined).")
    others = [k for k, v in state["changes"].items() if v["status"] == "open"]
    if others:
        print("Open CRs to sanity-check against the new baseline: " + ", ".join(sorted(others)))


def read_tree(base: Path) -> dict:
    if not base.is_dir():
        return {}
    return {f.relative_to(base).as_posix(): f.read_text(encoding="utf-8", errors="replace")
            for f in sorted(base.rglob("*")) if f.is_file() and f.name != ".gitkeep"}


def git_tree_at(when: str, folder: Path) -> dict | None:
    """Contents of folder in the last commit at or before `when`, or None if unavailable."""
    try:
        r = rel(folder)
        rev = subprocess.run(["git", "rev-list", "-1", f"--before={when}", "HEAD", "--", r],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
        if not rev:
            return None
        names = subprocess.run(["git", "ls-tree", "-r", "--name-only", rev, "--", r], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.split()
        out = {}
        for n in names:
            if n.endswith(".gitkeep"):
                continue
            out[Path(n).relative_to(r).as_posix()] = subprocess.run(
                ["git", "show", f"{rev}:{n}"], cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=True).stdout
        return out
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


def print_diff(old: dict, new: dict, old_tag: str, new_tag: str) -> int:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    print(f"  files: {len(changed)} modified, {len(added)} added, {len(removed)} removed")
    for k in added:
        print(f"    + {k}")
    for k in removed:
        print(f"    - {k}")
    lines: list[str] = []
    for k in changed + added + removed:
        lines += difflib.unified_diff(old.get(k, "").splitlines(), new.get(k, "").splitlines(),
                                      f"{old_tag}/{k}", f"{new_tag}/{k}", lineterm="", n=2)
    for line in lines[:REVIEW_LINES]:
        print("    " + line)
    if len(lines) > REVIEW_LINES:
        print(f"    ... {len(lines) - REVIEW_LINES} more diff lines; read the files directly.")
    return len(changed) + len(added) + len(removed)


def cmd_review(phase: str, scope) -> None:
    state = load()
    require_init(state)
    if PHASES[phase]["dir"] is None:
        print("REVIEW: build has no document; review PRs with /factory:qa review <api|web>#PR.")
        return
    if scope is not MAIN:
        cr(state, scope)
    rec = records(state, scope).get(phase)
    memo: dict = {}
    st = status_of(phase, scope, state, memo)
    print(f"REVIEW {label(scope)} '{phase}' (status: {st})")
    if rec is None:
        print("  Not approved yet, so there is no baseline to compare against. "
              "Run the role normally to create or finish the document.")
        return
    print(f"  Your document was approved at {rec['approved_at']}.")
    deps = [(p, s) for p, s in deps_of(phase, scope) if PHASES[p]["dir"]]
    if not deps:
        print("  This phase has no upstream documents.")
    total = 0
    for p, s in deps:
        key = f"{label(s)}:{p}"
        dep_state = status_of(p, s, state, memo)
        print(f"\nUPSTREAM {key} (now: {dep_state})")
        if dep_state != "approved":
            print(f"  WARNING: {key} is '{dep_state}'. Ask the human to approve it before you edit; "
                  "your writes are blocked until then.")
        ver = (rec.get("upstream_versions") or {}).get(key)
        old_dir = snap_root(p, s) / f"v{ver}" if ver else None
        if old_dir and old_dir.is_dir():
            old, source = read_tree(old_dir), f"snapshot v{ver}"
        else:
            old, source = git_tree_at(rec["approved_at"], phase_dir(p, s, state)), "git history"
        if old is None:
            print("  No baseline available (no snapshot and no git commit at that time). "
                  "Compare against the current upstream documents in full.")
            continue
        print(f"  Compared: {source} (as it was when '{phase}' was approved) -> current files")
        total += print_diff(old, read_tree(phase_dir(p, s, state)), f"{p}@then", f"{p}@now")
    own_snap = snap_root(phase, scope) / f"v{rec.get('snapshot')}" if rec.get("snapshot") else None
    if st == "changed" and own_snap and own_snap.is_dir():
        print(f"\nYOUR DOCUMENT was edited after approval:")
        print_diff(read_tree(own_snap), read_tree(phase_dir(phase, scope, state)), "approved", "now")
    print(f"\nSUMMARY: {total} upstream file change(s) since '{phase}' was approved.")
    if total == 0 and st == "stale":
        print("  Upstream content is unchanged (it was only re-approved). Re-approving as-is is safe.")


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        sys.exit(2)
    cmd, args = argv[0], [a for a in argv[1:] if a.strip()]  # skills may pass empty named args
    flags = [a for a in args if a.startswith("--")]
    pos = [a for a in args if not a.startswith("--")]
    if cmd == "init":
        cmd_init()
    elif cmd == "spec":  # spec pack for one task (reads GitHub via gh)
        import specs
        specs.cmd([a for a in argv[1:] if a.strip()])
    elif cmd == "autonomy":
        cmd_autonomy([a for a in argv[1:] if a.strip()])
    elif cmd == "config":
        cmd_config()
    elif cmd == "status":
        cmd_status("--brief" in flags)
    elif cmd == "change":
        cmd_change(pos)
    elif cmd == "tasks":  # task readiness for /factory:build (reads GitHub via gh)
        import tasks
        tasks.cmd(argv[1:])
    elif cmd in ("check", "review"):
        scope = MAIN
        if "--scope" in args:
            i = args.index("--scope")
            scope = norm_scope(args[i + 1] if i + 1 < len(args) else None)
            pos = [a for a in pos if a != (args[i + 1] if i + 1 < len(args) else None)]
        if len(pos) != 1 or pos[0] not in PHASES:
            print(f"Usage: gate.py {cmd} <{'|'.join(ORDER)}> [--scope CR-NNN] [--soft]")
            sys.exit(2)
        if cmd == "review":
            cmd_review(pos[0], scope)
        else:
            cmd_check(pos[0], scope, "--soft" in flags)
    elif cmd == "approve":
        if len(pos) == 2 and pos[0].lower() == "merge":
            cmd_merge(norm_scope(pos[1]) or sys.exit(f"Usage: gate.py approve merge CR-NNN"))
        elif len(pos) == 2 and norm_scope(pos[0]) and pos[1] in PHASES:
            cmd_approve(pos[1], norm_scope(pos[0]))
        elif len(pos) == 1 and pos[0] in PHASES:
            cmd_approve(pos[0], MAIN)
        else:
            print("Usage: gate.py approve <phase> | approve CR-NNN <phase> | approve merge CR-NNN")
            sys.exit(2)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:  # output piped into head etc.
        sys.exit(0)
