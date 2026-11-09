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
    """Brief status as {'phase': st} for main plus {'main: phase'/'CR-001: phase': st}."""
    _, out = run(repo, "status", "--brief")
    result = {}
    for part in out.strip().removeprefix("Factory status: ").split(" | "):
        scope, cells = part.split(": ", 1)
        for kv in cells.split(", "):
            k, v = kv.split("=")
            result[f"{scope}: {k}"] = v
            if scope == "main":
                result[k] = v
    return result


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


def approve_all_main(repo):
    write(repo, "docs/factory/01-requirements/PRD.md", "prd")
    write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "arch")
    write(repo, "docs/factory/02-architecture/openapi.yaml", "paths: {a: 1}")
    write(repo, "docs/factory/03-design/UI-SPEC.md", "ui")
    write(repo, "docs/factory/04-plan/PLAN.md", "plan")
    write(repo, "docs/factory/05-qa/TEST-PLAN.md", "tp")
    for ph in ("requirements", "architecture", "design", "planning", "test-plan"):
        assert run(repo, "approve", ph)[0] == 0, ph
        time.sleep(1.05)


def cr_doc(repo, folder, sub, name, text):
    write(repo, f"docs/factory/changes/{folder}/{sub}/{name}", text)


def test_change_request_flow():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        approve_all_main(repo)
        assert run(repo, "check", "build")[0] == 0

        code, out = run(repo, "change", "new", "Online payments!")
        assert code == 0 and "CR-001" in out
        f1 = "CR-001-online-payments"
        assert (Path(repo) / "docs/factory/changes" / f1 / "02-architecture/adr").is_dir()
        run(repo, "change", "new", "Waiting list")
        f2 = "CR-002-waiting-list"

        # CR gates are independent of main and of each other
        assert run(repo, "check", "architecture", "--scope", "CR-001")[0] == 3
        assert run(repo, "check", "requirements", "--scope", "cr-001")[0] == 0
        assert run(repo, "check", "requirements", "--scope", "CR-999")[0] == 3
        assert run(repo, "check", "architecture", "--scope", "bash")[0] == 0   # not a CR -> main
        cr_doc(repo, f1, "01-requirements", "REQUIREMENTS.md", "pay")
        assert run(repo, "approve", "CR-001", "requirements")[0] == 0
        assert run(repo, "check", "architecture", "--scope", "CR-001")[0] == 0
        assert run(repo, "check", "architecture", "--scope", "CR-002")[0] == 3
        assert run(repo, "check", "build")[0] == 0                              # main keeps building

        # finish CR-001 with a contract change, CR-002 also touches the contract
        cr_doc(repo, f1, "02-architecture", "ARCHITECTURE.md", "delta")
        cr_doc(repo, f1, "02-architecture", "openapi.yaml", "paths: {a: 1, pay: 1}")
        cr_doc(repo, f1, "03-design", "UI-SPEC.md", "pay screen")
        time.sleep(1.05)
        for ph in ("architecture", "design"):
            assert run(repo, "approve", "CR-001", ph)[0] == 0
        assert run(repo, "approve", "merge", "CR-001")[0] == 3                  # plan/test missing
        cr_doc(repo, f1, "04-plan", "PLAN.md", "tasks")
        time.sleep(1.05)
        assert run(repo, "approve", "CR-001", "planning")[0] == 0
        cr_doc(repo, f1, "05-qa", "TEST-PLAN.md", "tests")
        time.sleep(1.05)
        assert run(repo, "approve", "CR-001", "test-plan")[0] == 0
        assert run(repo, "check", "build", "--scope", "CR-001")[0] == 0

        cr_doc(repo, f2, "01-requirements", "REQUIREMENTS.md", "wait")
        assert run(repo, "approve", "CR-002", "requirements")[0] == 0
        cr_doc(repo, f2, "02-architecture", "ARCHITECTURE.md", "delta2")
        cr_doc(repo, f2, "02-architecture", "openapi.yaml", "paths: {a: 1, wait: 1}")
        time.sleep(1.05)
        assert run(repo, "approve", "CR-002", "architecture")[0] == 0

        # merge refused until main contract equals the CR contract
        assert run(repo, "approve", "merge", "CR-001")[0] == 3
        write(repo, "docs/factory/02-architecture/openapi.yaml", "paths: {a: 1, pay: 1}")
        write(repo, "docs/factory/01-requirements/PRD.md", "prd + pay")
        assert status(repo)["main: requirements"] == "changed"
        code, out = run(repo, "approve", "merge", "CR-001")
        assert code == 0 and "MERGED" in out, out
        s = status(repo)
        assert s["main: requirements"] == "approved" and s["main: build"] == "ready"
        assert "CR-001: requirements" not in s                                   # merged CRs hidden from brief
        assert run(repo, "check", "build", "--scope", "CR-001")[0] == 0          # merged -> main build

        # CR-002 was designed on the old contract -> merge must demand a rebase
        cr_doc(repo, f2, "03-design", "UI-SPEC.md", "x")
        cr_doc(repo, f2, "04-plan", "PLAN.md", "x")
        cr_doc(repo, f2, "05-qa", "TEST-PLAN.md", "x")
        time.sleep(1.05)
        for ph in ("design", "planning", "test-plan"):
            assert run(repo, "approve", "CR-002", ph)[0] == 0
            time.sleep(1.05)
        write(repo, "docs/factory/02-architecture/openapi.yaml", "paths: {a: 1, wait: 1}")
        code, out = run(repo, "approve", "merge", "CR-002")
        assert code == 3 and "Rebase" in out, out


def test_hook_change_request_paths():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        approve_all_main(repo)
        run(repo, "change", "new", "Payments")
        f = "CR-001-payments"

        def h(event):
            return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                                  capture_output=True, text=True, cwd=repo).returncode

        base = f"{repo}/docs/factory/changes/{f}"
        assert h({"tool_name": "Write", "tool_input": {"file_path": f"{base}/01-requirements/REQUIREMENTS.md"}}) == 0
        assert h({"tool_name": "Write", "tool_input": {"file_path": f"{base}/02-architecture/ARCHITECTURE.md"}}) == 2
        assert h({"tool_name": "Write", "tool_input": {"file_path": f"{repo}/docs/factory/changes/CR-777-x/01-requirements/R.md"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": f"echo x > docs/factory/changes/{f}/02-architecture/A.md"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": f"echo x > docs/factory/changes/{f}/01-requirements/R.md"}}) == 0
        assert h({"tool_name": "Bash", "tool_input": {"command": "python3 /p/scripts/gate.py approve merge CR-001"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": "python3 /p/scripts/gate.py change new Payments"}}) == 0
        assert h({"tool_name": "Bash", "tool_input": {"command": "gh pr create --fill"}}) == 0   # main build open


def test_review_snapshots_and_diff():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        write(repo, "docs/factory/01-requirements/PRD.md", "S-01 book a slot\nS-02 cancel\n")
        assert "snapshot v1" in run(repo, "approve", "requirements")[1]
        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "arch")
        time.sleep(1.05)
        run(repo, "approve", "architecture")
        snap = Path(repo) / ".factory/snapshots/main/requirements/v1/PRD.md"
        assert snap.read_text().startswith("S-01")
        out = run(repo, "review", "architecture")[1]
        assert "0 upstream file change" in out

        # requirements change + re-approval -> architecture stale, review shows the exact diff
        write(repo, "docs/factory/01-requirements/PRD.md", "S-01 book a slot\nS-02 cancel with refund\nS-03 waiting list\n")
        time.sleep(1.05)
        out = run(repo, "approve", "requirements")[1]
        assert "snapshot v2" in out and "review mode" in out
        assert status(repo)["architecture"] == "stale"
        _, st = run(repo, "status")
        assert "/factory:architect review" in st
        out = run(repo, "review", "architecture")[1]
        assert "snapshot v1" in out and "+S-03 waiting list" in out and "-S-02 cancel" in out, out
        assert "1 upstream file change" in out

        # re-approved without content change -> review says it is safe
        time.sleep(1.05)
        run(repo, "approve", "architecture")
        time.sleep(1.05)
        run(repo, "approve", "requirements")              # same content, new approval
        out = run(repo, "review", "architecture")[1]
        assert "only re-approved" in out, out

        # own edits after approval are shown too
        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "arch v2")
        out = run(repo, "review", "architecture")[1]
        assert "YOUR DOCUMENT was edited" in out and "+arch v2" in out

        # hook protects snapshots
        def h(event):
            return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                                  capture_output=True, text=True, cwd=repo).returncode
        assert h({"tool_name": "Write", "tool_input": {"file_path": f"{snap}"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": "echo x > .factory/snapshots/main/requirements/v1/PRD.md"}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": "ls .factory/snapshots/main"}}) == 0


def test_review_git_fallback_for_legacy_approvals():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
        run(repo, "init")
        write(repo, "docs/factory/01-requirements/PRD.md", "old line\n")
        run(repo, "approve", "requirements")
        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "arch")
        time.sleep(1.05)
        run(repo, "approve", "architecture")
        g("add", "-A"); g("commit", "-qm", "baseline")
        # simulate an approval made before snapshots existed
        st = json.loads((Path(repo) / ".factory/state.json").read_text())
        st["phases"]["architecture"].pop("upstream_versions")
        (Path(repo) / ".factory/state.json").write_text(json.dumps(st))
        time.sleep(1.05)
        write(repo, "docs/factory/01-requirements/PRD.md", "new line\n")
        out = run(repo, "review", "architecture")[1]
        assert "git history" in out and "+new line" in out, out


def test_hook_azure_guardrails():
    ok = lambda c: hook({"tool_name": "Bash", "tool_input": {"command": c}})
    assert ok("az group delete -n rg-booking-dev --yes") == 2
    assert ok("az keyvault purge --name kv-booking-prod") == 2
    assert ok("az deployment group create -g rg -f infra/main.bicep --mode Complete") == 2
    assert ok("az deployment group create -g rg-booking-prod -f infra/main.bicep -p env=prod") == 2
    assert ok("gh workflow run deploy.yml -f environment=prod") == 2
    assert ok("az deployment group what-if -g rg-booking-dev -f infra/main.bicep -p env=dev") == 0
    assert ok("az deployment group what-if -g rg-booking-prod -f infra/main.bicep -p env=prod") == 0  # read-only preview
    assert ok("az group create -n rg-booking-dev -l westeurope") == 0
    assert ok("gh release create v1.0.0 --repo me/booking-api --notes-file notes.md") == 0


def test_windows_python_launcher_and_encoding():
    import os, shutil, stat
    py_launcher = PLUGIN / "scripts" / "py"
    bash = shutil.which("bash")
    with tempfile.TemporaryDirectory() as tmp:
        fake_bin = Path(tmp) / "bin"
        fake_bin.mkdir()
        # Microsoft Store alias stub: exists, prints a message, exits 9009
        stub = fake_bin / "python3"
        stub.write_text("#!/bin/sh\necho STUB-NOT-PYTHON\nexit 9009\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        # only "python" is real (python.org installer on Windows)
        (fake_bin / "python").symlink_to(sys.executable)
        env = {"PATH": f"{fake_bin}:/usr/bin:/bin"}
        r = subprocess.run([bash, str(py_launcher), "-c", "import sys; print('OK', sys.version_info[0])"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0 and r.stdout.strip() == "OK 3" and "STUB" not in r.stdout, r
        # nothing usable -> clear message, exit 127
        only_stub = Path(tmp) / "only"
        only_stub.mkdir()
        shutil.copy(stub, only_stub / "python3")
        r = subprocess.run([bash, str(py_launcher), "-c", "print(1)"], capture_output=True, text=True,
                           env={"PATH": str(only_stub)})
        assert r.returncode == 127 and "Python 3.9+ not found" in r.stderr, r

    # gate wrapper end-to-end + non-ASCII docs on a legacy-code-page console
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        gate_sh = PLUGIN / "scripts" / "gate"
        def g(*a, env=None):
            return subprocess.run([bash, str(gate_sh), *a], cwd=repo, capture_output=True,
                                  text=True, encoding="utf-8", env=env)
        assert g("init").returncode == 0
        write(repo, "docs/factory/01-requirements/PRD.md", "Paciente: marcação ✓\n")
        assert g("approve", "requirements").returncode == 0
        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md", "arq → API ∥ web")
        time.sleep(1.05)
        g("approve", "architecture")
        Path(repo, "docs/factory/01-requirements/PRD.md").write_text("Paciente: marcação ✓ e pagamento €\n", encoding="utf-8")
        time.sleep(1.05)
        g("approve", "requirements")
        cp1252 = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        r = subprocess.run([sys.executable, str(GATE), "review", "architecture"], cwd=repo,
                           capture_output=True, env=cp1252)
        assert r.returncode == 0 and b"Traceback" not in r.stderr, r.stderr
        assert "pagamento €".encode("utf-8") in r.stdout

    # hook catches approvals through the wrapper too
    assert hook({"tool_name": "Bash", "tool_input": {"command": 'bash "/p/scripts/gate" approve planning'}}) == 2
    assert hook({"tool_name": "Bash", "tool_input": {"command": 'bash "/p/scripts/gate" check build'}}) == 0


def test_task_selection_next_and_story():
    import os
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        cfg = json.loads((Path(repo) / ".factory/config.json").read_text())
        cfg["repos"]["product"]["github"] = "me/bk-product"
        cfg["repos"]["api"]["github"] = "me/bk-api"
        cfg["repos"]["web"]["github"] = "me/bk-web"
        (Path(repo) / ".factory/config.json").write_text(json.dumps(cfg))
        lab = lambda *n: [{"name": x} for x in n]
        story = lambda num, sid, prio: {"number": num, "title": f"[{sid}] story", "state": "OPEN",
                                         "labels": lab("factory:story", f"priority:{prio}"), "body": ""}
        task = lambda num, tid, st, story_no, deps="", extra_labels=(), state="OPEN", cr="": {
            "number": num, "title": f"[{tid}] task {tid}", "state": state,
            "labels": lab("factory:task", *extra_labels),
            "body": f"Implements me/bk-product#{story_no} (ACs: AC1)\n" + (f"Change request: {cr}\n" if cr else "")
                    + (f"Depends on {deps}\n" if deps else "") + "### What\nstuff"}
        fixture = {
            "issues": {
                "me/bk-product": [story(1, "S-01", "should"), story(2, "S-02", "must"), story(3, "S-03", "could")],
                "me/bk-api": [
                    task(10, "T-00-api", "", 1, state="CLOSED", extra_labels=("be",)),
                    task(11, "T-01", "", 1, deps="me/bk-api#10", extra_labels=("be",)),          # ready, should
                    task(12, "T-02", "", 2, deps="me/bk-api#10", extra_labels=("be",)),          # ready, must -> next
                    task(13, "T-03", "", 2, deps="me/bk-api#12", extra_labels=("be",)),          # waiting
                    task(14, "T-04", "", 3, extra_labels=("be", "factory:blocked")),             # blocked
                    task(15, "T-CR001-01", "", 3, cr="CR-001", extra_labels=("be",)),            # gated (unknown CR)
                ],
                "me/bk-web": [
                    task(20, "T-05", "", 2, deps="me/bk-api#10", extra_labels=("fe",)),          # ready, must
                    task(21, "T-06", "", 2, extra_labels=("fe",)),                              # in review
                ],
            },
            "prs": {"me/bk-api": [], "me/bk-web": [{"number": 31, "body": "Closes #21", "headRefName": "factory/21-x"}]},
        }
        fx = Path(repo) / "fixture.json"
        fx.write_text(json.dumps(fixture))
        env = dict(os.environ, FACTORY_GH_FIXTURE=str(fx))
        T = lambda *a: subprocess.run([sys.executable, str(GATE), "tasks", *a], cwd=repo,
                                      capture_output=True, text=True, env=env).stdout

        # build gate closed -> everything open is gated
        assert "NEXT: none ready" in T("next")
        approve_all_main(repo)
        out = T("list")
        assert "READY     api#11" in out and "WAITING   api#13" in out and "depends on api#12" in out
        assert "BLOCKED   api#14" in out and "IN-REVIEW web#21" in out and "PR #31" in out
        assert "GATED     api#15" in out and "api#10" not in out          # done tasks hidden
        out = T("next")
        assert out.startswith("NEXT: api#12 T-02 (story S-02, must)"), out   # must before should, T order
        assert "web#20" in out and "api#11" in out                          # runners-up
        out = T("story", "S-02")
        assert "STORY S-02: 2 ready of 4 tasks" in out, out
        assert "DISPATCH: api#12 web#20" in out
        assert "STORY S-09: no factory:task" in T("story", "S-09")
        assert "TASK api#13" in T("plan", "api#13", "") and "WAITING" in T("plan", "api#13", "")
        assert T("plan", "next", "").startswith("NEXT: api#12")
        assert "DISPATCH" in T("plan", "story", "S-02")
        # gh unavailable -> graceful message, no traceback
        bad = dict(os.environ, PATH="/nonexistent")
        r = subprocess.run([sys.executable, str(GATE), "tasks", "next"], cwd=repo, capture_output=True, text=True, env=bad)
        assert "TASKS UNAVAILABLE" in r.stdout and "Traceback" not in r.stderr, r


def test_story_and_epic_sync():
    import os
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        cfg = json.loads((Path(repo) / ".factory/config.json").read_text())
        cfg["repos"]["product"]["github"] = "Me/BK-product"          # case differs from issue links
        cfg["repos"]["api"]["github"] = "me/bk-api"
        cfg["repos"]["web"]["github"] = "me/bk-web"
        (Path(repo) / ".factory/config.json").write_text(json.dumps(cfg))
        lab = lambda *n: [{"name": x} for x in n]
        epic = lambda num, eid, state="OPEN": {"number": num, "title": f"[{eid}] epic", "state": state,
                                               "labels": lab("factory:epic"), "body": ""}
        story = lambda num, sid, ep, state="OPEN": {"number": num, "title": f"[{sid}] story", "state": state,
                                                    "labels": lab("factory:story", "priority:must"),
                                                    "body": f"Part of #{ep}\n\nAs a user..."}
        task = lambda num, tid, s, state: {"number": num, "title": f"[{tid}] t", "state": state,
                                           "labels": lab("factory:task"),
                                           "body": f"Implements me/bk-product#{s} (ACs: AC1)\n"}
        fx = {"issues": {
            "me/bk-product": [epic(1, "E-01"), epic(2, "E-02"), epic(3, "E-03", "CLOSED"),
                              story(10, "S-01", 1), story(11, "S-02", 2), story(12, "S-03", 3, "CLOSED"),
                              story(13, "S-04", 2)],
            "me/bk-api": [task(20, "T-01", 10, "CLOSED"), task(21, "T-02", 11, "CLOSED"),
                          task(22, "T-03", 12, "OPEN")],
            "me/bk-web": [task(30, "T-04", 10, "CLOSED"), task(31, "T-05", 11, "OPEN")]},
            "prs": {}}
        f = Path(repo) / "fx.json"
        f.write_text(json.dumps(fx))
        env = dict(os.environ, FACTORY_GH_FIXTURE=str(f))
        T = lambda *a: subprocess.run([sys.executable, str(GATE), "tasks", *a], cwd=repo,
                                      capture_output=True, text=True, env=env).stdout
        out = T("sync")
        assert "SYNC WOULD: close story S-01" in out, out          # all tasks closed (api + web)
        assert "close epic E-01" in out                            # its only story now closed
        assert "story S-02" not in out and "epic E-02" not in out  # S-02 has an open task
        assert "reopen story S-03" in out and "reopen epic E-03" in out   # new open work (e.g. a CR)
        assert "S-04" not in out                                   # no tasks yet -> untouched
        assert "Dry run" in out
        out = T("sync", "--apply")
        assert "SYNC APPLIED: close story S-01" in out and "Dry run" not in out, out
        # everything consistent -> nothing to do
        fx["issues"]["me/bk-api"] = [task(20, "T-01", 10, "CLOSED")]
        fx["issues"]["me/bk-web"] = []
        fx["issues"]["me/bk-product"] = [epic(1, "E-01", "CLOSED"), story(10, "S-01", 1, "CLOSED")]
        f.write_text(json.dumps(fx))
        assert "up to date" in T("sync")


def test_autonomy_settings_and_protection():
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        cfgp = Path(repo) / ".factory/config.json"
        assert json.loads(cfgp.read_text())["autonomy"] == {"preset": "assisted"}
        out = run(repo, "autonomy")[1]
        assert out.startswith("AUTONOMY assisted: auto QA on, 1 auto-fix round(s), asks before starting")
        assert "Merges and planning approvals always need you" in out
        assert "Autonomy (build phase): assisted" in run(repo, "status")[1]

        out = run(repo, "autonomy", "preset", "autopilot")[1]
        assert "AUTONOMY SET: preset 'autopilot'" in out and "starts without asking" in out
        assert "continues to next ready task (max 5 per run)" in out
        out = run(repo, "autonomy", "set", "max_fix_rounds", "3")[1]
        assert "max_fix_rounds = 3" in out and "3 auto-fix round(s)" in out
        assert "between 0 and 3; nothing changed" in run(repo, "autonomy", "set", "max_fix_rounds", "9")[1]
        assert "expects true/false" in run(repo, "autonomy", "set", "auto_qa", "maybe")[1]
        assert "can't be automated" in run(repo, "autonomy", "set", "auto_merge", "true")[1]
        assert "unknown preset" in run(repo, "autonomy", "preset", "yolo")[1]
        assert "overrides cleared" in run(repo, "autonomy", "preset", "manual")[1]
        assert "auto QA off" in run(repo, "autonomy")[1]

        # hand-edited config: bad values are clamped/ignored with warnings, never crash
        cfg = json.loads(cfgp.read_text())
        cfg["autonomy"] = {"preset": "assisted", "max_parallel": 50, "auto_merge": True, "auto_qa": "yes", "foo": 1}
        cfgp.write_text(json.dumps(cfg))
        out = run(repo, "autonomy")[1]
        assert "max_parallel' =50 clamped to 5" in out.replace("'max_parallel'=50", "max_parallel' =50") or "clamped to 5" in out
        assert "'auto_merge' ignored" in out and "'auto_qa' must be true/false" in out and "unknown autonomy key 'foo'" in out

        # hook: agents can't change autonomy, but can edit other config
        def h(event):
            return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                                  capture_output=True, text=True, cwd=repo).returncode
        cur = json.loads(cfgp.read_text())
        same = dict(cur, repos={"product": {"github": "me/p", "path": "."}})
        changed = dict(cur, autonomy={"preset": "autopilot"})
        assert h({"tool_name": "Write", "tool_input": {"file_path": str(cfgp), "content": json.dumps(same)}}) == 0
        assert h({"tool_name": "Write", "tool_input": {"file_path": str(cfgp), "content": json.dumps(changed)}}) == 2
        dropped = {k: v for k, v in cur.items() if k != "autonomy"}
        assert h({"tool_name": "Write", "tool_input": {"file_path": str(cfgp), "content": json.dumps(dropped)}}) == 2
        assert h({"tool_name": "Edit", "tool_input": {"file_path": str(cfgp), "old_string": '"preset": "assisted"',
                                                       "new_string": '"preset": "autopilot"'}}) == 2
        assert h({"tool_name": "Edit", "tool_input": {"file_path": str(cfgp), "old_string": "OWNER/PRODUCT-api",
                                                       "new_string": "me/booking-api"}}) == 0
        assert h({"tool_name": "MultiEdit", "tool_input": {"file_path": str(cfgp), "edits": [
            {"old_string": "x", "new_string": "y"}, {"old_string": "\"max_fix_rounds\": 1", "new_string": "\"max_fix_rounds\": 3"}]}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": 'bash "/p/scripts/gate" autonomy preset autopilot'}}) == 2
        assert h({"tool_name": "Bash", "tool_input": {"command": 'bash "/p/scripts/gate" autonomy show'}}) == 0
        assert h({"tool_name": "Bash", "tool_input": {"command": "sed -i s/assisted/autopilot/ .factory/config.json"}}) == 2


def test_bug_priority_ids_and_story_reopen():
    import os
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        cfgp = Path(repo) / ".factory/config.json"
        cfg = json.loads(cfgp.read_text())
        cfg["repos"]["product"]["github"] = "me/bk-product"
        cfg["repos"]["api"]["github"] = "me/bk-api"
        cfg["repos"]["web"]["github"] = "me/bk-web"
        cfgp.write_text(json.dumps(cfg))
        approve_all_main(repo)
        lab = lambda *n: [{"name": x} for x in n]
        story = lambda num, sid, prio, state="OPEN": {"number": num, "title": f"[{sid}] s", "state": state,
                                                     "labels": lab("factory:story", f"priority:{prio}"), "body": ""}
        task = lambda num, title, s, labels, state="OPEN": {"number": num, "title": title, "state": state,
                                                            "labels": lab("factory:task", *labels),
                                                            "body": f"Implements me/bk-product#{s}\n"}
        fx = {"issues": {
            "me/bk-product": [story(1, "S-01", "must"), story(2, "S-02", "should", "CLOSED"), story(3, "S-03", "could")],
            "me/bk-api": [task(10, "[T-01] feature must", 1, ("be",)),
                          task(11, "[BUG-001] low bug", 2, ("be", "bug", "severity:low"), "CLOSED"),
                          task(12, "[BUG-002] medium bug", 2, ("be", "bug", "severity:medium"))],
            "me/bk-web": [task(20, "[BUG-007] high bug", 2, ("fe", "bug", "severity:high")),
                          task(21, "[T-09] feature could", 3, ("fe",))]},
            "prs": {}}
        f = Path(repo) / "fx.json"
        f.write_text(json.dumps(fx))
        env = dict(os.environ, FACTORY_GH_FIXTURE=str(f))
        T = lambda *a: subprocess.run([sys.executable, str(GATE), "tasks", *a], cwd=repo,
                                      capture_output=True, text=True, env=env).stdout
        out = T("next")
        assert out.startswith("NEXT: web#20 BUG-007 (story S-02, bug, high)"), out   # high bug beats a must feature
        assert "api#10" in out                                                     # must feature is runner-up
        listing = T("list")
        order = [l.split()[1] for l in listing.splitlines() if l.startswith("  READY")]
        assert order == ["web#20", "api#10", "api#12", "web#21"], order            # high > must > medium(=should) > could
        assert "prio=BUG-high" in listing
        assert T("bug-id").strip() == "BUG-ID: BUG-008"
        # an open bug fix against a closed story reopens it; after the fix is merged it closes again
        assert "reopen story S-02" in T("sync")
        for t_ in fx["issues"]["me/bk-api"] + fx["issues"]["me/bk-web"]:
            if t_["number"] in (12, 20):
                t_["state"] = "CLOSED"
        fx["issues"]["me/bk-product"][1]["state"] = "OPEN"
        f.write_text(json.dumps(fx))
        assert "close story S-02" in T("sync")


def test_spec_pack_extracts_only_referenced_specs():
    import os, shutil
    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        run(repo, "init")
        cfgp = Path(repo) / ".factory/config.json"
        cfg = json.loads(cfgp.read_text())
        cfg["repos"]["product"]["github"] = "me/bk-product"
        cfg["repos"]["api"]["github"] = "me/bk-api"
        cfg["repos"]["web"]["github"] = "me/bk-web"
        cfgp.write_text(json.dumps(cfg))
        write(repo, "docs/factory/01-requirements/PRD.md",
              "# PRD\n## 7. Stories\n### S-01 Create order\n- AC1: Given items, then created.\n"
              "### S-02 List orders\n- AC1: lists them.\n## 8. NFRs\nfast\n")
        shutil.copy(PLUGIN / "templates/openapi.yaml", Path(repo) / "docs/factory/02-architecture/openapi.yaml")
        write(repo, "docs/factory/02-architecture/ARCHITECTURE.md",
              "# Arch\n## 5. Decisions\nx\n## 6. Backend design\nOrders slice: handlers.\n### Orders\ndetail\n## 7. Frontend\ny\n")
        write(repo, "docs/factory/02-architecture/adr/0001-database.md", "# ADR 0001: Azure SQL\nchosen")
        write(repo, "docs/factory/02-architecture/adr/0002-hosting.md", "# ADR 0002: App Service")
        write(repo, "docs/factory/03-design/UI-SPEC.md",
              "# UI\n## 6. Screens\n### SCR-01 Orders list\nlist\n### SCR-02 Order form\nform states: empty, error\n## 7. Components\n")
        write(repo, "docs/factory/05-qa/TEST-PLAN.md",
              "# TP\n| Story · AC | Test case | Level |\n| --- | --- | --- |\n| S-01 · AC1 | TC-001 create | Integration |\n"
              "| S-02 · AC1 | TC-002 list | Integration |\n")
        body = ("Implements me/bk-product#1 (ACs: AC1)\n\n### Spec references (binding)\n"
                "- `docs/factory/02-architecture/openapi.yaml`: operationIds `createOrder`, `deleteOrder`\n"
                "- `ARCHITECTURE.md` §6 feature `Orders`; ADR 0001\n- `UI-SPEC.md` SCR-02\n- `TEST-PLAN.md` TC-001\n")
        fx = {"issues": {"me/bk-product": [{"number": 1, "title": "[S-01] Create order", "state": "OPEN",
                                             "labels": [{"name": "factory:story"}], "body": ""}],
                         "me/bk-api": [{"number": 12, "title": "[T-03] createOrder", "state": "OPEN",
                                        "labels": [{"name": "factory:task"}], "body": body}]}}
        f = Path(repo) / "fx.json"
        f.write_text(json.dumps(fx))
        env = dict(os.environ, FACTORY_GH_FIXTURE=str(f))
        out = subprocess.run([sys.executable, str(GATE), "spec", "api#12"], cwd=repo,
                             capture_output=True, text=True, env=env).stdout
        assert out.startswith("# Spec pack for me/bk-api#12"), out
        assert "### S-01 Create order" in out and "S-02 List orders" not in out          # only its story
        assert "operationId: createOrder" in out and "listOrders" not in out           # only its operation
        assert "CreateOrderRequest:" in out and "ProblemDetails:" in out               # schemas it references (recursive)
        assert "OrderPage" not in out                                                  # not unrelated schemas
        assert "## Architecture §6" in out and "Orders slice" in out and "## 7. Frontend" not in out
        assert "ADR 0001: Azure SQL" in out and "App Service" not in out
        assert "### SCR-02 Order form" in out and "SCR-01 Orders list" not in out
        assert "TC-001 create" in out and "TC-002" not in out
        assert "- operationId deleteOrder" in out                                      # reported as missing
        assert len(out) < len("".join(p.read_text() for p in (Path(repo) / "docs").rglob("*.md")) +
                              (Path(repo) / "docs/factory/02-architecture/openapi.yaml").read_text())
        bad = subprocess.run([sys.executable, str(GATE), "spec", "nonsense"], cwd=repo,
                             capture_output=True, text=True, env=env).stdout
        assert "SPEC PACK UNAVAILABLE" in bad


if __name__ == "__main__":
    test_spec_pack_extracts_only_referenced_specs()
    test_bug_priority_ids_and_story_reopen()
    test_autonomy_settings_and_protection()
    test_story_and_epic_sync()
    test_task_selection_next_and_story()
    test_windows_python_launcher_and_encoding()
    test_hook_azure_guardrails()
    test_review_snapshots_and_diff()
    test_review_git_fallback_for_legacy_approvals()
    test_change_request_flow()
    test_hook_change_request_paths()
    test_config_created_and_printed()
    test_hook_blocks_writes_into_gated_phase()
    test_full_flow()
    test_hook_blocks_self_approval_and_state_edits()
    print("all tests passed")
