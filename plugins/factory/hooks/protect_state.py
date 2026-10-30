#!/usr/bin/env python3
"""PreToolUse guard: the deterministic half of the factory's human gates.

Blocks (exit 2, reason on stderr):
  1. any edit of .factory/state.json, and any Bash use of `gate.py approve`.
     Approval runs only through /factory:approve, whose `!` injection executes
     before the model is involved and never passes through this hook.
  2. writes into a phase's artifact folder (docs/factory/0N-*) while that
     phase's upstream phases are not approved.
  3. `gh pr create` while the build phase is blocked.
Applies to the main session and to subagents alike.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def gate_blockers(phase: str) -> list[str]:
    import gate  # noqa: imported lazily so a broken repo state never breaks unrelated tools
    state = gate.load()
    if not state:
        return []  # factory not initialised in this repo: stay out of the way
    return gate.blockers(phase, state)


try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = event.get("tool_name", "")
inp = event.get("tool_input", {}) or {}

if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    path = str(inp.get("file_path") or inp.get("notebook_path") or "")
    if path.replace("\\", "/").endswith(".factory/state.json"):
        block("Blocked: .factory/state.json is managed by gate.py. "
              "Only the human can approve phases via /factory:approve.")
    try:
        import gate
        phase = gate.phase_for_path(path)
    except Exception:
        phase = None
    if phase:
        bl = gate_blockers(phase)
        if bl:
            block(f"Blocked by factory gate: cannot write '{phase}' artifacts yet: "
                  + "; ".join(bl) + ". Ask the user to approve upstream phases.")

if tool == "Bash":
    cmd = str(inp.get("command", ""))
    if re.search(r"gate\.py[\"']?\s+approve\b", cmd):
        block("Blocked: phase approval is a human gate. "
              "Ask the user to run /factory:approve <phase>.")
    if "state.json" in cmd and ".factory" in cmd:
        block("Blocked: do not read or modify .factory/state.json directly; "
              "use `gate.py status` or `gate.py check <phase>`.")
    # Shell writes into a gated phase folder (best effort: write-ish commands
    # that mention the folder's name, e.g. `echo x > docs/factory/02-architecture/A.md`).
    writes = re.search(r">|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i|\btouch\b|\brm\b|\bpython3?\b|\bnode\b|\bperl\b|\bdd\b|\binstall\b", cmd)
    if writes:
        try:
            import gate
            for phase, spec in gate.PHASES.items():
                for a in spec["artifacts"]:
                    if a.split("/")[-1] in cmd and phase != "build":
                        bl = gate_blockers(phase)
                        if bl:
                            block(f"Blocked by factory gate: shell write into '{phase}' artifacts: "
                                  + "; ".join(bl) + ". Ask the user to approve upstream phases.")
        except SystemExit:
            raise
        except Exception:
            pass
    if re.search(r"\bgh\s+pr\s+create\b", cmd):
        try:
            bl = gate_blockers("build")
        except Exception:
            bl = []
        if bl:
            block("Blocked by factory gate: build phase is not open: " + "; ".join(bl))

sys.exit(0)
