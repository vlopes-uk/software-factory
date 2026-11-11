#!/usr/bin/env python3
"""PreToolUse guard: the deterministic half of the factory's human gates.

Blocks (exit 2, reason on stderr):
  1. any edit of .factory/state.json, and any Bash use of `gate.py approve`
     (which also covers `approve merge`). Approval runs only through
     /factory:approve, whose `!` injection executes before the model is involved
     and never passes through this hook.
  2. writes into a phase's artifact folder - main (docs/factory/0N-*) or a change
     request (docs/factory/changes/CR-NNN-*/0N-*) - while that phase's upstream
     phases are not approved, or into an unregistered CR folder.
  3. `gh pr create` while neither main nor any open change request has build open.
  4. agents changing build autonomy (.factory/config.json "autonomy", `gate autonomy set|preset`)
  5. destructive Azure commands (delete/purge, Complete-mode deployments) and direct
     production deploys (they must go through a GitHub release + prod environment approval).
  6. explorations (docs/factory/explorations/EXP-NNN-*): writes into unregistered or closed
     (promoted/rejected) explorations, PoC docs before the approach is approved, DECISION.md,
     and the model running `gate explore reject|pre` (rejecting is the human's call).
  7. spike branches (spike/EXP-NNN-*): no PR, no merge/cherry-pick/pull of them, no push to main.
Applies to the main session and to subagents alike.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:  # Windows consoles default to a legacy code page; never crash on non-ASCII paths/messages
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def check_target(gate, state, phase, scope, how: str) -> None:
    if isinstance(scope, str) and scope.startswith("unregistered:"):
        block(f"Blocked: {scope.split(':', 1)[1]} is not a registered change request. "
              "Create CRs with /factory:change new <title>.")
    bl = gate.blockers(phase, scope, state)
    if bl and scope is gate.MAIN and gate.mergeable_crs(state):
        # A fully approved CR is being folded into main (/factory:change merge): main docs
        # are 'changed' by the merge itself. The human still accepts it via /factory:approve merge.
        return
    if bl:
        block(f"Blocked by factory gate: cannot {how} {gate.label(scope)} '{phase}' artifacts yet: "
              + "; ".join(bl) + ". Ask the user to approve upstream phases.")


def branch_of(cmd: str, cwd: str) -> str:
    """Best-effort current branch of the directory the command runs in (last `cd` wins)."""
    dirs = re.findall(r"\bcd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", cmd)
    d = Path(cwd or ".")
    if dirs:
        d = d / Path(dirs[-1].strip("\"'")).expanduser()
    try:
        return subprocess.run(["git", "-C", str(d), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


try:
    event = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
except Exception:
    sys.exit(0)

tool = event.get("tool_name", "")
inp = event.get("tool_input", {}) or {}

try:
    import gate
    state = gate.load()
except Exception:
    gate, state = None, {}

if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    path = str(inp.get("file_path") or inp.get("notebook_path") or "")
    if path.replace("\\", "/").endswith(".factory/state.json"):
        block("Blocked: .factory/state.json is managed by gate.py. "
              "Only the human can approve phases via /factory:approve.")
    if "/.factory/snapshots/" in path.replace("\\", "/") or path.replace("\\", "/").startswith(".factory/snapshots/"):
        block("Blocked: .factory/snapshots/ holds the approved versions used for reviews; "
              "only gate.py writes there.")
    norm = path.replace("\\", "/")
    if norm.endswith(".factory/config.json") and gate:
        keys = ["autonomy", "preset", *gate.AUTONOMY_KEYS, *gate.AUTONOMY_PRESETS]
        if tool == "Write":
            try:
                new_auto = json.loads(inp.get("content") or "{}").get("autonomy")
                cur = json.loads(gate.CONFIG.read_text(encoding="utf-8")).get("autonomy") if gate.CONFIG.exists() else None
            except Exception:
                new_auto, cur = None, None
            if new_auto != cur and not (new_auto is None and cur is None):
                block("Blocked: build autonomy is the human's decision. Ask the user to run "
                      "/factory:autonomy (preset or set) themselves.")
        else:
            edits = inp.get("edits") or [inp]
            text = " ".join(str(e.get("old_string", "")) + " " + str(e.get("new_string", "")) for e in edits)
            if any(k in text for k in keys):
                block("Blocked: build autonomy is the human's decision. Ask the user to run "
                      "/factory:autonomy themselves.")
    if gate and state:
        hit = gate.path_scope_phase(path, state)
        if hit:
            check_target(gate, state, hit[0], hit[1], "write")
        why = gate.exploration_write_blocker(path, state)
        if why:
            block("Blocked by factory gate: " + why)

if tool == "Bash":
    cmd = str(inp.get("command", ""))
    # Azure guardrails: agents never delete/purge resources or deploy Bicep in Complete mode.
    if re.search(r"\baz\b[^|;&]*\b(delete|purge)\b", cmd) or re.search(r"--mode\s+[Cc]omplete\b", cmd):
        block("Blocked: destructive Azure command (delete/purge or Complete-mode deployment). "
              "Propose it to the user; they run deletions themselves.")
    # Production deploys happen only via a published GitHub release + the prod environment approval.
    if re.search(r"\bgh\s+workflow\s+run\b.*\bprod\b", cmd) or (re.search(r"\baz\s+deployment\b.*\benv=prod\b", cmd) and "what-if" not in cmd):
        block("Blocked: production changes go through `gh release create` and the GitHub `prod` "
              "environment approval, never directly from this session.")
    if re.search(r"\bgate(?:\.py)?[\"']?\s+approve\b", cmd):
        block("Blocked: phase approval is a human gate. "
              "Ask the user to run /factory:approve ... themselves.")
    if re.search(r"\bgate(?:\.py)?[\"']?\s+explore\s+(reject|pre)\b", cmd):
        block("Blocked: rejecting an exploration is the human's decision. Ask the user to run "
              "/factory:explore reject EXP-NNN <why> themselves.")
    # Spike branches hold PoC code for an exploration: reference only, never reviewed or merged.
    spike_msg = ("Blocked: spike branches (spike/EXP-NNN-*) are PoC reference code. They never get a PR "
                 "and are never merged, cherry-picked or pushed to main; push the spike branch itself and report back.")
    if re.search(r"\bgit\s+(merge|cherry-pick|pull|rebase)\b[^|;&]*\bspike/", cmd):
        block(spike_msg)
    if re.search(r"\bgit\s+push\b[^|;&]*spike/[^\s:]*:(refs/heads/)?(main|master)\b", cmd):
        block(spike_msg)
    if re.search(r"\bgh\s+pr\s+(create|merge)\b", cmd) and (
            re.search(r"\bspike/", cmd) or branch_of(cmd, event.get("cwd", "")).startswith("spike/")):
        block(spike_msg)
    if re.search(r"\bgate(?:\.py)?[\"']?\s+autonomy\s+(set|preset)\b", cmd):
        block("Blocked: build autonomy is the human's decision. Ask the user to run "
              "/factory:autonomy themselves.")
    if ".factory/config.json" in cmd.replace("\\", "/") and re.search(
            r">|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i|\bpython3?\b|\bperl\b|\bjq\b", cmd):
        block("Blocked: edit .factory/config.json with the Write/Edit tools (autonomy settings are "
              "human-only via /factory:autonomy).")
    if ".factory/snapshots" in cmd and re.search(r">|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i|\btouch\b|\brm\b|\bpython3?\b|\bperl\b", cmd) \
            and not re.search(r"scripts[/\\\\]gate", cmd):
        block("Blocked: .factory/snapshots/ is managed by gate.py (read it, never write it).")
    if "state.json" in cmd and ".factory" in cmd:
        block("Blocked: do not read or modify .factory/state.json directly; "
              "use `gate.py status` or `gate.py check <phase>`.")
    if gate and state:
        writes = re.search(r">|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i|\btouch\b|\brm\b|\bpython3?\b|"
                           r"\bnode\b|\bperl\b|\bdd\b|\binstall\b|\bmkdir\b", cmd)
        if writes and not re.search(r"scripts[/\\\\]gate", cmd):
            cr_path = re.compile(r"changes/(CR-\d{3,}[^/\s'\"]*)/(0\d-[a-z-]+)")
            # 1) change-request folders mentioned in the command
            for folder, sub in cr_path.findall(cmd):
                hit = gate.path_scope_phase(f"docs/factory/changes/{folder}/{sub}", state)
                if hit:
                    check_target(gate, state, hit[0], hit[1], "shell-write")
            # 2) exploration folders mentioned in the command
            for folder, rest in re.findall(r"explorations/(EXP-\d{3,}[^/\s'\"]*)/?([^\s'\";&|>]*)", cmd):
                why = gate.exploration_write_blocker(f"docs/factory/explorations/{folder}/{rest}".rstrip("/"), state)
                if why:
                    block("Blocked by factory gate: " + why)
            # 3) main phase folders mentioned anywhere else in the command
            rest = cr_path.sub(" ", cmd)
            for phase in gate.DOC_PHASES:
                if re.search(rf"\b{re.escape(gate.PHASES[phase]['dir'])}\b", rest):
                    check_target(gate, state, phase, gate.MAIN, "shell-write")
        if re.search(r"\bgh\s+pr\s+create\b", cmd):
            scopes = [gate.MAIN] + [k for k, v in state.get("changes", {}).items()
                                    if v.get("status") == "open"]
            memo: dict = {}
            if not any(gate.status_of("build", s, state, memo) == "ready" for s in scopes):
                block("Blocked by factory gate: no build phase is open (main or any change request).")

sys.exit(0)
