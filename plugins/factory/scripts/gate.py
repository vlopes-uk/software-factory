#!/usr/bin/env python3
"""Deterministic phase gates for the factory plugin.

State lives in <repo>/.factory/state.json. Approval records a SHA-256 of each
phase's artifacts; any later edit to an approved artifact ("changed") or a
re-approval upstream ("stale") blocks every downstream phase until a human
re-approves.

Usage:
  gate.py init
  gate.py status [--brief]
  gate.py config               print .factory/config.json + stack profile path
  gate.py check <phase>        exit 0 = may start, exit 3 = blocked
  gate.py check <phase> --soft always exit 0; prints GATE BLOCKED (used by skills;
                               hooks/protect_state.py enforces the block on writes)
  gate.py approve <phase>      human-only (hook blocks the model from running it)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PHASES: dict[str, dict] = {
    "requirements": {
        "role": "Product Owner (/factory:po)",
        "deps": [],
        "artifacts": ["docs/factory/01-requirements"],
        "primary": "docs/factory/01-requirements/PRD.md",
    },
    "architecture": {
        "role": "Architect (/factory:architect)",
        "deps": ["requirements"],
        "artifacts": ["docs/factory/02-architecture"],
        "primary": "docs/factory/02-architecture/ARCHITECTURE.md",
    },
    "design": {
        "role": "UI Designer (/factory:design)",
        "deps": ["requirements"],
        "artifacts": ["docs/factory/03-design"],
        "primary": "docs/factory/03-design/UI-SPEC.md",
    },
    "planning": {
        "role": "Architect, planning mode (/factory:plan)",
        "deps": ["architecture", "design"],
        "artifacts": ["docs/factory/04-plan"],
        "primary": "docs/factory/04-plan/PLAN.md",
    },
    "test-plan": {
        "role": "QA (/factory:qa plan)",
        "deps": ["planning"],
        "artifacts": ["docs/factory/05-qa"],
        "primary": "docs/factory/05-qa/TEST-PLAN.md",
    },
    # build is never approved as a whole: each PR is gated by a human merge.
    "build": {
        "role": "BE/FE Developers (/factory:build), QA review (/factory:qa review)",
        "deps": ["planning", "test-plan"],
        "artifacts": [],
        "primary": None,
    },
}
ORDER = list(PHASES)
EXIT_BLOCKED = 3


def repo_root() -> Path:
    # Hooks and skills run with CLAUDE_PROJECT_DIR set to the product repo, even when
    # an agent has cd'ed into a sibling code repo.
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / ".factory").exists():
        return Path(env)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if out:
            return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


ROOT = repo_root()
STATE = ROOT / ".factory" / "state.json"
CONFIG = ROOT / ".factory" / "config.json"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = {
    "stack": "dotnet-react-azure",
    "repos": {
        "product": {"github": "OWNER/PRODUCT-product", "path": "."},
        "api": {"github": "OWNER/PRODUCT-api", "path": "../PRODUCT-api", "label": "be"},
        "web": {"github": "OWNER/PRODUCT-web", "path": "../PRODUCT-web", "label": "fe"},
    },
}


def cmd_config() -> None:
    """Print the repo/stack config plus the stack profile path, for skill injection."""
    if not CONFIG.exists():
        print("CONFIG MISSING: run /factory:init to create .factory/config.json")
        return
    cfg = json.loads(CONFIG.read_text())
    print(json.dumps(cfg, indent=2))
    if "OWNER/" in json.dumps(cfg):
        print("WARNING: config.json still has placeholder repo names; fix them via /factory:init.")
    profile = PLUGIN_ROOT / "stack" / f"{cfg.get('stack', 'dotnet-react-azure')}.md"
    print(f"Stack profile: {profile}")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def artifact_files(phase: str) -> list[Path]:
    files: list[Path] = []
    for rel in PHASES[phase]["artifacts"]:
        p = ROOT / rel
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(f for f in p.rglob("*") if f.is_file() and f.name != ".gitkeep")
    return sorted(files)


def digest(phase: str) -> str:
    h = hashlib.sha256()
    for f in artifact_files(phase):
        h.update(f.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(f.read_bytes() + b"\0")
    return h.hexdigest()


def status_of(phase: str, state: dict, _memo: dict | None = None) -> str:
    """not-started | draft | approved | changed | stale | ready | blocked"""
    memo = _memo if _memo is not None else {}
    if phase in memo:
        return memo[phase]
    spec = PHASES[phase]
    deps_ok = all(status_of(d, state, memo) == "approved" for d in spec["deps"])

    if spec["primary"] is None:  # build
        result = "ready" if deps_ok else "blocked"
    else:
        rec = state.get("phases", {}).get(phase)
        if rec is None:
            result = "draft" if (ROOT / spec["primary"]).exists() else "not-started"
        elif rec.get("sha256") != digest(phase):
            result = "changed"
        elif not deps_ok:
            result = "stale"
        else:
            mine = rec["approved_at"]
            newer_upstream = any(
                state["phases"][d]["approved_at"] > mine for d in spec["deps"]
            )
            result = "stale" if newer_upstream else "approved"
    memo[phase] = result
    return result


def blockers(phase: str, state: dict) -> list[str]:
    memo: dict = {}
    out = []
    for d in PHASES[phase]["deps"]:
        s = status_of(d, state, memo)
        if s != "approved":
            out.append(f"{d} is '{s}' (owner: {PHASES[d]['role']})")
    return out


def require_init(state: dict) -> None:
    if not state:
        print("GATE: factory not initialised in this repo. Run /factory:init first.")
        sys.exit(EXIT_BLOCKED)


def cmd_init() -> None:
    state = load()
    if state:
        print(f"Factory already initialised at {STATE.relative_to(ROOT)}.")
    else:
        save({"version": 1, "created_at": now(), "phases": {}})
        print(f"Created {STATE.relative_to(ROOT)}.")
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        print(f"Created {CONFIG.relative_to(ROOT)} with placeholders (edit repo names).")
    dirs = [rel for p in ORDER for rel in PHASES[p]["artifacts"]]
    dirs += ["docs/factory/02-architecture/adr", "docs/factory/03-design/wireframes"]
    for rel in dirs:
        d = ROOT / rel
        d.mkdir(parents=True, exist_ok=True)
        if not any(d.iterdir()):
            (d / ".gitkeep").touch()
    print("Artifact folders ready under docs/factory/.")


def cmd_status(brief: bool) -> None:
    state = load()
    if not state:
        if brief:
            return
        require_init(state)
    memo: dict = {}
    rows = []
    for phase in ORDER:
        s = status_of(phase, state, memo)
        rec = state.get("phases", {}).get(phase, {})
        rows.append((phase, s, rec.get("approved_at", "-"), PHASES[phase]["role"]))
    if brief:
        print("Factory status: " + ", ".join(f"{p}={s}" for p, s, *_ in rows))
        return
    w = max(len(r[0]) for r in rows)
    print(f"{'PHASE'.ljust(w)}  {'STATUS'.ljust(11)}  {'APPROVED AT'.ljust(25)}  OWNER")
    for p, s, a, role in rows:
        print(f"{p.ljust(w)}  {s.ljust(11)}  {a.ljust(25)}  {role}")
    nxt = next_step(state, memo)
    if nxt:
        print(f"\nNext: {nxt}")


def next_step(state: dict, memo: dict) -> str:
    for phase in ORDER:
        s = status_of(phase, state, memo)
        if phase == "build":
            return "build is open: /factory:build <issue>, then /factory:qa review <pr>" if s == "ready" else ""
        if s in ("not-started", "draft") and not blockers(phase, state):
            return f"{phase}: work with {PHASES[phase]['role']}, then /factory:approve {phase}"
        if s in ("changed", "stale"):
            return f"{phase} is {s}: review it, then /factory:approve {phase}"
    return ""


def phase_for_path(path: str) -> str | None:
    """Map a file path to the phase whose artifact folder contains it."""
    try:
        rel = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        rel = Path(path).as_posix()
    for phase, spec in PHASES.items():
        for a in spec["artifacts"]:
            if rel == a or rel.startswith(a + "/"):
                return phase
    return None


def cmd_check(phase: str, soft: bool = False) -> None:
    state = load()
    if not state and soft:
        print("GATE BLOCKED: factory not initialised in this repo. Run /factory:init first. STOP.")
        return
    require_init(state)
    bl = blockers(phase, state)
    if bl:
        print(f"GATE BLOCKED: '{phase}' cannot start yet.")
        for b in bl:
            print(f"  - {b}")
        print("Approve upstream phases with /factory:approve <phase> (human only). STOP.")
        if soft:
            return
        sys.exit(EXIT_BLOCKED)
    s = status_of(phase, state)
    print(f"GATE OPEN: '{phase}' may proceed (current status: {s}).")
    if s == "approved":
        print("Note: this phase is already approved; any edit will mark it 'changed' "
              "and block downstream phases until re-approved.")


def cmd_approve(phase: str) -> None:
    spec = PHASES[phase]
    if spec["primary"] is None:
        print("GATE: 'build' is approved per PR by merging it, not here.")
        sys.exit(EXIT_BLOCKED)
    state = load()
    require_init(state)
    bl = blockers(phase, state)
    if bl:
        print(f"GATE: cannot approve '{phase}' while upstream is not approved:")
        for b in bl:
            print(f"  - {b}")
        sys.exit(EXIT_BLOCKED)
    if not (ROOT / spec["primary"]).exists():
        print(f"GATE: cannot approve '{phase}': {spec['primary']} does not exist.")
        sys.exit(EXIT_BLOCKED)
    state.setdefault("phases", {})[phase] = {
        "approved_at": now(),
        "approved_by": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "sha256": digest(phase),
        "files": [f.relative_to(ROOT).as_posix() for f in artifact_files(phase)],
    }
    save(state)
    print(f"APPROVED: '{phase}' ({len(state['phases'][phase]['files'])} files hashed).")
    memo: dict = {}
    stale = [p for p in ORDER if p != phase and status_of(p, state, memo) == "stale"]
    if stale:
        print("Now stale (re-review and re-approve): " + ", ".join(stale))
    nxt = next_step(state, memo)
    if nxt:
        print(f"Next: {nxt}")


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd in ("check", "approve"):
        args = [a for a in args if not a.startswith("--")]
        if len(args) != 1 or args[0] not in PHASES:
            print(f"Usage: gate.py {cmd} <{'|'.join(ORDER)}>")
            sys.exit(2)
    if cmd == "init":
        cmd_init()
    elif cmd == "config":
        cmd_config()
    elif cmd == "status":
        cmd_status("--brief" in args)
    elif cmd == "check":
        cmd_check(args[0], soft="--soft" in argv)
    elif cmd == "approve":
        cmd_approve(args[0])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
