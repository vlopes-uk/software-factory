#!/usr/bin/env python3
"""Task readiness for /factory:build: which task can a developer agent pick up?

Reads GitHub (via `gh`) and the local gate state. Run through the gate wrapper:
  bash scripts/gate tasks list                 every task with its status
  bash scripts/gate tasks next                 the single next ready task (+ runners-up)
  bash scripts/gate tasks story S-03           the tasks of one story
  bash scripts/gate tasks plan <target> [x]    what /factory:build injects: dispatches on target
                                               (next | story S-NN | api#N | web#N | owner/repo#N)
  bash scripts/gate tasks bug-id               next free BUG-NNN id across the api/web repos
  bash scripts/gate tasks sync [--apply]       close stories whose tasks are all closed, and epics
                                               whose stories are all closed (reopen if new open work
                                               appears). Dry run unless --apply.

Status of a task (first match wins):
  done        issue closed
  blocked     labelled factory:blocked (a developer stopped and asked a question)
  in-review   an open PR references it (Closes #N or branch factory/N-*)
  gated       the build gate is closed for its scope (main, or its change request)
  waiting     a `Depends on` issue is still open
  ready       none of the above

Ordering for `next`: story priority (must > should > could > none), then the
plan order (T-number in the title), then the issue number.

Testing: set FACTORY_GH_FIXTURE=<json> to replace gh with
  {"issues": {"owner/repo": [ {number,title,state,labels:[{name}],body} ]},
   "prs":    {"owner/repo": [ {number,body,headRefName} ]}}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import gate

PRIORITY = {"priority:must": 0, "priority:should": 1, "priority:could": 2}
# Bug fixes (label `bug` + `severity:*`): critical/high jump ahead of all feature work,
# medium ranks with "should" stories, low with "could".
SEVERITY = {"severity:critical": -2, "severity:high": -1, "severity:medium": 1, "severity:low": 2}
REF = re.compile(r"([\w.-]+/[\w.-]+)#(\d+)")
_fixture: dict | None = None


# ---------------------------------------------------------------- GitHub access

def _gh_json(args: list[str]):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(out.stdout or "[]")


def issues(repo: str, label: str) -> list[dict]:
    global _fixture
    if os.environ.get("FACTORY_GH_FIXTURE"):
        _fixture = _fixture or json.loads(open(os.environ["FACTORY_GH_FIXTURE"], encoding="utf-8").read())
        pool = {k.lower(): v for k, v in _fixture.get("issues", {}).items()}
        return [i for i in pool.get(repo.lower(), [])
                if label in {l["name"] for l in i.get("labels", [])}]
    return _gh_json(["issue", "list", "--repo", repo, "--label", label, "--state", "all",
                     "--limit", "500", "--json", "number,title,state,labels,body"])


def open_prs(repo: str) -> list[dict]:
    global _fixture
    if os.environ.get("FACTORY_GH_FIXTURE"):
        _fixture = _fixture or json.loads(open(os.environ["FACTORY_GH_FIXTURE"], encoding="utf-8").read())
        return {k.lower(): v for k, v in _fixture.get("prs", {}).items()}.get(repo.lower(), [])
    return _gh_json(["pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
                     "--json", "number,body,headRefName"])


# ---------------------------------------------------------------- model

def load_config() -> dict:
    if not gate.CONFIG.exists():
        raise RuntimeError("no .factory/config.json; run /factory:init")
    cfg = json.loads(gate.CONFIG.read_text(encoding="utf-8"))
    if "OWNER/" in json.dumps(cfg):
        raise RuntimeError(".factory/config.json still has placeholder repo names; run /factory:init")
    return cfg


def line_refs(body: str, prefix: str) -> list[tuple[str, int]]:
    refs = []
    for line in (body or "").splitlines():
        if line.strip().lower().startswith(prefix):
            refs += [(r.lower(), int(n)) for r, n in REF.findall(line)]
    return refs


def collect() -> dict:
    cfg = load_config()
    repos = cfg["repos"]
    product = repos["product"]["github"].lower()
    code = {k: repos[k]["github"].lower() for k in ("api", "web") if k in repos}

    stories = {}
    for s in issues(product, "factory:story"):
        m = re.search(r"\[(S-\d+)\]", s["title"])
        prio = next((l["name"] for l in s.get("labels", []) if l["name"] in PRIORITY), None)
        epic_refs = line_refs(s.get("body"), "part of")
        epic_refs += [(product, int(n)) for line in (s.get("body") or "").splitlines()
                      if line.strip().lower().startswith("part of")
                      for n in re.findall(r"(?<![\w/.-])#(\d+)", line)]
        stories[(product, s["number"])] = {"id": m.group(1) if m else f"#{s['number']}",
                                           "priority": prio, "title": s["title"],
                                           "number": s["number"], "state": s["state"].lower(),
                                           "epics": epic_refs}
    epics = {}
    for e in issues(product, "factory:epic"):
        m = re.search(r"\[(E-\d+)\]", e["title"])
        epics[(product, e["number"])] = {"id": m.group(1) if m else f"#{e['number']}",
                                         "number": e["number"], "title": e["title"],
                                         "state": e["state"].lower()}

    tasks, by_ref = [], {}
    for key, repo in code.items():
        prs = open_prs(repo)
        for t in issues(repo, "factory:task"):
            body = t.get("body") or ""
            m = re.search(r"\[((?:T|BUG)-[\w-]+)\]", t["title"])
            cr = re.search(r"^\s*Change request:\s*(CR-\d{3,})", body, re.M | re.I)
            impl = line_refs(body, "implements")
            story = stories.get(impl[0]) if impl else None
            in_review = [p["number"] for p in prs
                         if re.search(rf"(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#{t['number']}\b",
                                      p.get("body") or "", re.I)
                         or (p.get("headRefName") or "").startswith(f"factory/{t['number']}-")]
            task = {
                "ref": f"{key}#{t['number']}", "repo": repo, "key": key, "number": t["number"],
                "tid": m.group(1) if m else "", "title": t["title"],
                "state": t["state"].lower(),
                "labels": {l["name"] for l in t.get("labels", [])},
                "cr": cr.group(1).upper() if cr else None,
                "story": story["id"] if story else None,
                "priority": story["priority"] if story else None,
                "severity": next((l["name"] for l in t.get("labels", []) if l["name"] in SEVERITY), None)
                            if any(l["name"] == "bug" for l in t.get("labels", [])) else None,
                "deps": line_refs(body, "depends on"),
                "implements": impl,
                "prs": in_review,
            }
            tasks.append(task)
            by_ref[(repo, t["number"])] = task
    return {"tasks": tasks, "by_ref": by_ref, "stories": stories, "epics": epics, "product": product}


def classify(data: dict) -> None:
    state = gate.load()
    memo: dict = {}

    def build_open(cr):
        if not state:
            return False, "factory not initialised"
        if cr and cr not in state.get("changes", {}):
            return False, f"{cr} is not a registered change request"
        scope = cr if cr else gate.MAIN
        if scope is not gate.MAIN and state["changes"][scope]["status"] == "merged":
            scope = gate.MAIN
        st = gate.status_of("build", scope, state, memo)
        return st == "ready", f"{gate.label(scope)} build is '{st}'"

    for t in data["tasks"]:
        open_deps = []
        for repo, n in t["deps"]:
            dep = data["by_ref"].get((repo, n))
            if dep is None:
                open_deps.append(f"{repo}#{n} (unknown, check it)")
            elif dep["state"] != "closed":
                open_deps.append(dep["ref"])
        ok, why = build_open(t["cr"])
        if t["state"] == "closed":
            t["status"], t["why"] = "done", ""
        elif "factory:blocked" in t["labels"]:
            t["status"], t["why"] = "blocked", "labelled factory:blocked: answer the question on the issue"
        elif t["prs"]:
            t["status"], t["why"] = "in-review", "PR " + ", ".join(f"#{p}" for p in t["prs"])
        elif not ok:
            t["status"], t["why"] = "gated", why
        elif open_deps:
            t["status"], t["why"] = "waiting", "depends on " + ", ".join(open_deps)
        else:
            t["status"], t["why"] = "ready", ""


def sort_key(t: dict):
    tnum = re.findall(r"\d+", t["tid"])
    rank = SEVERITY[t["severity"]] if t.get("severity") else PRIORITY.get(t["priority"], 3)
    return (rank, 0 if t.get("severity") else 1, int(tnum[0]) if tnum else 9999, t["tid"], t["number"])


# ---------------------------------------------------------------- story / epic sync

def plan_sync(data: dict) -> list[dict]:
    """Close/reopen actions for stories (by their tasks) and epics (by their stories)."""
    actions = []
    product = data["product"]
    story_state = {k: s["state"] for k, s in data["stories"].items()}
    for key, s in sorted(data["stories"].items(), key=lambda kv: kv[0][1]):
        mine = [t for t in data["tasks"] if key in t["implements"]]
        if not mine:
            continue
        open_tasks = [t["ref"] for t in mine if t["state"] != "closed"]
        done = [t["ref"] for t in mine if t["state"] == "closed"]
        if s["state"] == "open" and not open_tasks:
            actions.append({"op": "close", "kind": "story", "repo": product, "number": s["number"],
                            "label": s["id"], "comment":
                            f"All {len(done)} task(s) done: {', '.join(done)}. "
                            "Closed automatically by the software factory."})
            story_state[key] = "closed"
        elif s["state"] == "closed" and open_tasks:
            actions.append({"op": "reopen", "kind": "story", "repo": product, "number": s["number"],
                            "label": s["id"], "comment":
                            f"Reopened: new open work implements this story: {', '.join(open_tasks)}. "
                            "Reopened automatically by the software factory."})
            story_state[key] = "open"
    for key, e in sorted(data["epics"].items(), key=lambda kv: kv[0][1]):
        mine = [k for k, s in data["stories"].items() if key in s["epics"]]
        if not mine:
            continue
        open_stories = [data["stories"][k]["id"] for k in mine if story_state[k] != "closed"]
        if e["state"] == "open" and not open_stories:
            ids = ", ".join(data["stories"][k]["id"] for k in mine)
            actions.append({"op": "close", "kind": "epic", "repo": product, "number": e["number"],
                            "label": e["id"], "comment":
                            f"All {len(mine)} stories done: {ids}. Closed automatically by the software factory."})
        elif e["state"] == "closed" and open_stories:
            actions.append({"op": "reopen", "kind": "epic", "repo": product, "number": e["number"],
                            "label": e["id"], "comment":
                            f"Reopened: open stories {', '.join(open_stories)}. "
                            "Reopened automatically by the software factory."})
    return actions


def apply_action(a: dict) -> str:
    args = ["issue", a["op"], str(a["number"]), "--repo", a["repo"], "--comment", a["comment"]]
    if os.environ.get("FACTORY_GH_FIXTURE"):
        return "fixture: gh " + " ".join(args[:4])
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh failed")
    return "ok"


def sync(data: dict, apply: bool) -> None:
    actions = plan_sync(data)
    if not actions:
        print("SYNC: stories and epics are up to date.")
        return
    verb = "APPLIED" if apply else "WOULD"
    for a in actions:
        line = f"{a['op']} {a['kind']} {a['label']} ({a['repo']}#{a['number']})"
        if apply:
            try:
                apply_action(a)
                print(f"SYNC {verb}: {line}")
            except Exception as e:
                print(f"SYNC FAILED: {line}: {e}")
        else:
            print(f"SYNC {verb}: {line}")
    if not apply:
        print("Dry run. Add --apply to make these changes.")


# ---------------------------------------------------------------- output

def row(t: dict) -> str:
    prio = ("BUG-" + t["severity"].replace("severity:", "")) if t.get("severity") else \
        (t["priority"] or "-").replace("priority:", "")
    extra = f"  ({t['why']})" if t["why"] else ""
    cr = f" {t['cr']}" if t["cr"] else ""
    return (f"  {t['status'].upper():9} {t['ref']:10} {t['tid'] or '-':10} story={t['story'] or '-':6} "
            f"prio={prio:6}{cr}  {t['title']}{extra}")


def show(tasks: list[dict], header: str) -> None:
    print(header)
    if not tasks:
        print("  (none)")
    for t in sorted(tasks, key=sort_key):
        print(row(t))


def cmd(args: list[str]) -> None:
    sub = args[0] if args else "list"
    if sub == "plan":
        target = (args[1] if len(args) > 1 else "").strip()
        extra = (args[2] if len(args) > 2 else "").strip()
        if target.lower() == "next":
            sub, args = "next", ["next"]
        elif target.lower() == "story":
            sub, args = "story", ["story", extra]
        elif target:
            sub, args = "one", ["one", target]
        else:
            print("TASKS: no target given. Use: next | story S-NN | api#N | web#N")
            return
    try:
        data = collect()
    except Exception as e:  # network, auth, config: report, never crash the skill
        print(f"TASKS UNAVAILABLE: {e}")
        print("Fall back to naming the task yourself: /factory:build api#N or web#N.")
        return
    classify(data)
    tasks = data["tasks"]
    if sub == "bug-id":
        nums = [int(n) for t in tasks for n in re.findall(r"\[BUG-(\d+)\]", t["title"])]
        print(f"BUG-ID: BUG-{(max(nums) + 1 if nums else 1):03d}")
        return
    if sub == "sync":
        sync(data, apply="--apply" in args)
        return
    if sub == "list":
        open_tasks = [t for t in tasks if t["status"] != "done"]
        show(open_tasks, f"TASKS: {len(open_tasks)} open of {len(tasks)}")
    elif sub == "next":
        ready = sorted([t for t in tasks if t["status"] == "ready"], key=sort_key)
        if not ready:
            print("NEXT: none ready.")
            show([t for t in tasks if t["status"] not in ("done",)], "Why (open tasks):")
            return
        t = ready[0]
        level = (f"bug, {t['severity'].replace('severity:', '')}" if t.get("severity")
                 else (t['priority'] or 'no priority').replace('priority:', ''))
        print(f"NEXT: {t['ref']} {t['tid']} (story {t['story'] or '-'}, {level})"
              f"{' ' + t['cr'] if t['cr'] else ''}")
        print(f"  {t['title']}")
        show(ready[1:4], "Runners-up (also ready):")
    elif sub == "story":
        sid = (args[1] if len(args) > 1 else "").upper()
        if not re.fullmatch(r"S-\d+", sid):
            print("STORY: give a story id, e.g. /factory:build story S-03")
            return
        mine = [t for t in tasks if (t["story"] or "").upper() == sid]
        if not mine:
            print(f"STORY {sid}: no factory:task issues found that say 'Implements <product>#<{sid} issue>'.")
            return
        ready = [t for t in mine if t["status"] == "ready"]
        show(mine, f"STORY {sid}: {len(ready)} ready of {len(mine)} tasks")
        if ready:
            print("DISPATCH: " + " ".join(t["ref"] for t in sorted(ready, key=sort_key)))
    elif sub == "one":
        target = args[1]
        t = next((t for t in tasks if t["ref"].lower() == target.lower()
                  or f"{t['repo']}#{t['number']}".lower() == target.lower()), None)
        if t is None:
            print(f"TASK {target}: not found among open factory:task issues in the api/web repos.")
            return
        show([t], f"TASK {t['ref']}:")
    else:
        print(__doc__)


if __name__ == "__main__":
    cmd(sys.argv[1:])
