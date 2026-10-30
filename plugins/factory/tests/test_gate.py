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


if __name__ == "__main__":
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
