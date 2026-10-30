"""Run: python3 -m pytest plugins/factory/tests -q  (or python3 test_gate.py)"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
GATE = PLUGIN / "scripts" / "gate.py"
HOOK = PLUGIN / "hooks" / "protect_state.py"


def run(repo, *args):
    p = subprocess.run([sys.executable, str(GATE), *args], cwd=repo, capture_output=True, text=True)
    return p.returncode, p.stdout


def hook(event):
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True)
    return p.returncode


def write(repo, rel, text):
    f = Path(repo) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def status(repo):
    _, out = run(repo, "status", "--brief")
    return dict(kv.split("=") for kv in out.split(": ", 1)[1].strip().split(", "))


def test_full_flow():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        assert run(repo, "check", "architecture")[0] == 3          # not initialised
        assert run(repo, "init")[0] == 0
        assert run(repo, "check", "requirements")[0] == 0          # no deps
        assert run(repo, "check", "architecture")[0] == 3          # PRD not approved
        assert run(repo, "approve", "requirements")[0] == 3        # PRD missing

        write(repo, "docs/factory/01-requirements/PRD.md", "v1")
        assert status(repo)["requirements"] == "draft"
        assert run(repo, "approve", "requirements")[0] == 0
        assert run(repo, "check", "architecture")[0] == 0
        assert run(repo, "check", "design")[0] == 0
        assert run(repo, "check", "planning")[0] == 3              # needs arch + design
        assert run(repo, "approve", "planning")[0] == 3            # can't approve past a blocker

        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "a")
        write(repo, "docs/factory/02-architecture/adr/0001-stack.md", "adr")
        write(repo, "docs/factory/03-design/UI-SPEC.md", "d")
        time.sleep(1.1)
        assert run(repo, "approve", "architecture")[0] == 0
        assert run(repo, "approve", "design")[0] == 0
        assert run(repo, "check", "planning")[0] == 0

        # drift: edit an ADR after approval -> architecture changed, planning blocked
        write(repo, "docs/factory/02-architecture/adr/0001-stack.md", "adr v2")
        assert status(repo)["architecture"] == "changed"
        assert run(repo, "check", "planning")[0] == 3
        time.sleep(1.1)
        assert run(repo, "approve", "architecture")[0] == 0
        assert run(repo, "check", "planning")[0] == 0

        write(repo, "docs/factory/04-plan/PLAN.md", "p")
        write(repo, "docs/factory/05-qa/TEST-PLAN.md", "t")
        time.sleep(1.1)
        assert run(repo, "approve", "planning")[0] == 0
        assert run(repo, "check", "build")[0] == 3                 # test plan not approved
        time.sleep(1.1)
        assert run(repo, "approve", "test-plan")[0] == 0
        assert run(repo, "check", "build")[0] == 0
        assert run(repo, "approve", "build")[0] == 3               # build is gated per PR

        # staleness: revise + re-approve PRD -> everything downstream stale, build blocked
        write(repo, "docs/factory/01-requirements/PRD.md", "v2")
        assert status(repo)["requirements"] == "changed"
        assert run(repo, "check", "build")[0] == 3
        time.sleep(1.1)
        assert run(repo, "approve", "requirements")[0] == 0
        s = status(repo)
        assert s["architecture"] == "stale" and s["design"] == "stale"
        assert s["planning"] == "stale" and s["build"] == "blocked"
        time.sleep(1.1)
        for ph in ("architecture", "design", "planning", "test-plan"):
            assert run(repo, "approve", ph)[0] == 0, ph
            time.sleep(1.1)
        assert run(repo, "check", "build")[0] == 0


def test_hook_blocks_self_approval_and_state_edits():
    assert hook({"tool_name": "Bash", "tool_input": {"command": "python3 /x/scripts/gate.py approve planning"}}) == 2
    assert hook({"tool_name": "Bash", "tool_input": {"command": "echo {} > .factory/state.json"}}) == 2
    assert hook({"tool_name": "Write", "tool_input": {"file_path": "/repo/.factory/state.json"}}) == 2
    assert hook({"tool_name": "Edit", "tool_input": {"file_path": "/repo/docs/factory/04-plan/PLAN.md"}}) == 0
    assert hook({"tool_name": "Bash", "tool_input": {"command": "python3 /x/scripts/gate.py check build"}}) == 0
    assert hook({"tool_name": "Bash", "tool_input": {"command": "git add docs/factory .factory"}}) == 0


def test_hook_blocks_writes_into_gated_phase():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")

        def h(event):
            p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                               capture_output=True, text=True, cwd=repo)
            return p.returncode

        arch = f"{repo}/docs/factory/02-architecture/ARCHITECTURE.md"
        prd = f"{repo}/docs/factory/01-requirements/PRD.md"
        assert h({"tool_name": "Write", "tool_input": {"file_path": prd}}) == 0
        assert h({"tool_name": "Write", "tool_input": {"file_path": arch}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": "gh pr create --fill"}}) == 2
        assert h({"tool_name": "Write", "tool_input": {"file_path": f"{repo}/src/app.py"}}) == 0
        assert h({"tool_name": "Bash", "tool_input": {"command": "mkdir -p docs/factory/02-architecture && printf '# t' > docs/factory/02-architecture/ARCHITECTURE.md"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": "ls docs/factory/02-architecture"}}) == 0
        assert h({"tool_name": "Bash", "tool_input": {"command": "echo hi > notes.txt"}}) == 0
        write(repo, "docs/factory/01-requirements/PRD.md", "v1")
        run(repo, "approve", "requirements")
        assert h({"tool_name": "Write", "tool_input": {"file_path": arch}}) == 0
        assert run(repo, "check", "planning", "--soft") == (0, run(repo, "check", "planning", "--soft")[1])
        assert "GATE BLOCKED" in run(repo, "check", "planning", "--soft")[1]


def test_config_created_and_printed():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        assert "CONFIG MISSING" in run(repo, "config")[1]
        run(repo, "init")
        cfg = json.loads((Path(repo) / ".factory/config.json").read_text())
        assert set(cfg["repos"]) == {"product", "api", "web"}
        out = run(repo, "config")[1]
        assert "WARNING" in out and "dotnet-react-azure.md" in out
        assert (PLUGIN / "stack" / "dotnet-react-azure.md").exists()


if __name__ == "__main__":
    test_config_created_and_printed()
    test_hook_blocks_writes_into_gated_phase()
    test_full_flow()
    test_hook_blocks_self_approval_and_state_edits()
    print("all tests passed")
