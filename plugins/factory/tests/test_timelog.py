"""Run: python3 -m pytest plugins/factory/tests -q  (or python3 test_timelog.py)"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
GATE = PLUGIN / "scripts" / "gate.py"
HOOK = PLUGIN / "hooks" / "protect_state.py"
sys.path.insert(0, str(PLUGIN / "scripts"))


def issue(n, title, state, labels, body, created, closed=None):
    return {"number": n, "title": title, "state": state, "labels": [{"name": l} for l in labels],
            "body": body, "createdAt": created, "closedAt": closed}


FIXTURE = {
    "issues": {
        "o/p-product": [issue(1, "[S-01] story", "OPEN", ["factory:story", "priority:must"], "", "2026-11-01T00:00:00Z")],
        "o/p-api": [
            issue(1, "[T-01] api one", "CLOSED", ["factory:task", "be"],
                  "Implements o/p-product#1\n**Size:** S · **Parallel with:** -\n**Estimate:** agent 2h · human 6h",
                  "2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z"),
            issue(2, "[T-03] api two", "CLOSED", ["factory:task", "be"],
                  "Implements o/p-product#1\n**Size:** S\n**Estimate:** agent 1h · human 3h",
                  "2026-11-01T00:00:00Z", "2026-11-04T00:00:00Z"),
            issue(3, "[T-04] api three", "CLOSED", ["factory:task", "be"],
                  "Implements o/p-product#1\n**Size:** S\n**Estimate:** agent 1h · human 3h",
                  "2026-11-01T00:00:00Z", "2026-11-05T00:00:00Z"),
            issue(4, "[T-05] api untracked", "CLOSED", ["factory:task", "be"],
                  "**Size:** S\n**Estimate:** agent 1h · human 3h", "2026-11-01T00:00:00Z", "2026-11-05T00:00:00Z"),
        ],
        "o/p-web": [issue(1, "[T-02] web one", "OPEN", ["factory:task", "fe"],
                          "Implements o/p-product#1\n**Size:** M\n**Estimate:** agent 3h · human 8h\n"
                          "**Revised estimate:** agent 4h · human 10h (2026-11-02: grew)", "2026-11-01T00:00:00Z")],
    },
    "prs": {},
}


def setup_repo(tmp):
    subprocess.run(["git", "init", "-q", tmp], check=True)
    subprocess.run([sys.executable, str(GATE), "init"], cwd=tmp, capture_output=True, check=True)
    (Path(tmp) / ".factory" / "config.json").write_text(json.dumps({"repos": {
        "product": {"github": "o/p-product"}, "api": {"github": "o/p-api", "label": "be"},
        "web": {"github": "o/p-web", "label": "fe"}}, "stack": "dotnet-react-azure"}))
    plan = Path(tmp) / "docs/factory/04-plan/PLAN.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("| Milestone | Goal | Stories | Tasks |\n| --- | --- | --- | --- |\n| M1 x | g | S-01 | T-03, T-04 |\n\n"
                    "| ID | Repo | Issue | Title | Size | Estimate (agent · human) | Milestone |\n"
                    "| --- | --- | --- | --- | --- | --- | --- |\n| T-01 | api | | x | S | 2h · 6h | M0 |\n")
    fx = Path(tmp) / "fx.json"
    fx.write_text(json.dumps(FIXTURE))
    env = dict(os.environ, FACTORY_GH_FIXTURE=str(fx), FACTORY_METRICS_DIR=str(Path(tmp) / "metrics"),
               CLAUDE_PROJECT_DIR=tmp)
    return env


def t(tmp, env, when, *args):
    p = subprocess.run([sys.executable, str(GATE), "time", *args], cwd=tmp, capture_output=True, text=True,
                       env=dict(env, FACTORY_NOW=when))
    return p.returncode, p.stdout


def test_parsers():
    import timelog
    assert timelog.parse_hours("2.5h") == 2.5
    assert timelog.parse_hours("90m") == 1.5
    assert timelog.parse_hours("2h30m") == 2.5
    assert timelog.parse_hours("3") == 3.0
    assert timelog.parse_hours("x") is None
    e = timelog.parse_estimates("**Size:** S\n**Estimate:** agent 2.5h · human 6h\n"
                                "**Revised estimate:** agent 3h · human 8h (2026-11-20: scope)\n"
                                "**Revised estimate:** agent 4h (later)")
    assert (e["est_agent"], e["est_human"], e["rev_agent"], e["rev_human"]) == (2.5, 6.0, 4.0, 8.0)
    assert timelog.parse_estimates("no estimate here")["est_agent"] is None
    assert timelog.parse_size("**Size:** M · **Parallel with:** T-02") == "M"


def test_segments_pairing():
    import timelog
    ev = lambda ts, e, stage="dev", task="o/r#1": {"ts": ts, "event": e, "task": task, "stage": stage}
    log = [ev("2026-01-01T10:00:00Z", "start"), ev("2026-01-01T10:00:10Z", "start"),   # duplicate start
           ev("2026-01-01T11:00:00Z", "stop"), ev("2026-01-01T11:01:00Z", "stop"),      # duplicate stop
           ev("2026-01-01T12:00:00Z", "start", "fix"),                                  # abandoned (old)
           ev("2026-01-02T09:00:00Z", "start", "qa")]                                   # still running
    now = timelog.parse_ts("2026-01-02T09:30:00Z")
    segs = timelog.segments(log, now)["o/r#1"]
    states = {(s["stage"], s["state"]): s["hours"] for s in segs}
    assert states[("dev", "closed")] == 1.0
    assert states[("fix", "abandoned")] == 0.0
    assert states[("qa", "running")] == 0.5


def test_flow_report_calibration_export():
    with tempfile.TemporaryDirectory() as tmp:
        env = setup_repo(tmp)
        steps = [("2026-11-02T10:00:00Z", "start", "api#1", "dev"), ("2026-11-02T12:30:00Z", "stop", "api#1", "dev"),
                 ("2026-11-02T13:00:00Z", "start", "api#1", "qa"), ("2026-11-02T13:30:00Z", "stop", "api#1", "qa"),
                 ("2026-11-03T10:00:00Z", "start", "o/p-api#2", "dev"), ("2026-11-03T11:30:00Z", "stop", "api#2", "dev"),
                 ("2026-11-04T10:00:00Z", "start", "api#3", "dev"), ("2026-11-04T11:15:00Z", "stop", "api#3", "dev"),
                 ("2026-11-05T10:00:00Z", "start", "web#1", "dev"), ("2026-11-05T12:00:00Z", "stop", "web#1", "dev")]
        for when, verb, task, stage in steps:
            code, out = t(tmp, env, when, verb, task, stage)
            assert code == 0 and "TIME:" in out, out
        assert t(tmp, env, "2026-11-05T12:01:00Z", "stop", "web#1", "dev")[1].startswith("TIME: no running")
        assert ": 7h" in t(tmp, env, "2026-11-02T13:31:00Z", "human", "api#1", "7h", "bigger")[1]

        code, out = t(tmp, env, "2026-11-05T14:00:00Z", "report")
        assert code == 0
        assert "Tasks: 4/5 done" in out
        assert "+50%" in out                       # api#1: 2h -> 3h
        assert "1 closed task(s) have no recorded time" in out
        assert "Re-estimates moved the agent plan from 8h to 9h" in out
        # web#1: revised 4h, factor x1.5 (3 samples, 'all') -> expected 6h, spent 2h -> forecast 4h
        assert "forecast 4h" in out, out

        code, out = t(tmp, env, "2026-11-05T14:00:00Z", "calibration")
        assert "be/feature/S" in out and "x1.50" in out and "T-01" in out

        code, out = t(tmp, env, "2026-11-05T14:00:00Z", "export")
        assert "EXPORT: 5 item(s) of 'p'" in out
        db = sqlite3.connect(str(Path(tmp) / "metrics" / "factory.db"))
        rows = dict(db.execute("SELECT tid, milestone FROM tasks").fetchall())
        assert rows["T-01"] == "M0" and rows["T-03"] == "M1"           # column wins; list as fallback
        assert db.execute("SELECT deviation_pct FROM tasks WHERE tid='T-01'").fetchone()[0] == 50.0
        assert db.execute("SELECT count(*) FROM burndown WHERE scope='all'").fetchone()[0] >= 4
        assert db.execute("SELECT factor FROM calibration WHERE category='human'").fetchone()[0] > 1
        db.close()
        # re-export replaces this product's rows, keeps others
        db = sqlite3.connect(str(Path(tmp) / "metrics" / "factory.db"))
        db.execute("INSERT INTO tasks (product, ref) VALUES ('other', 'api#9')")
        db.commit()
        db.close()
        t(tmp, env, "2026-11-05T15:00:00Z", "report", "--export")
        db = sqlite3.connect(str(Path(tmp) / "metrics" / "factory.db"))
        assert db.execute("SELECT count(*) FROM tasks WHERE product='p'").fetchone()[0] == 5
        assert db.execute("SELECT count(*) FROM tasks WHERE product='other'").fetchone()[0] == 1


def test_manual_checks():
    with tempfile.TemporaryDirectory() as tmp:
        env = setup_repo(tmp)
        tp = Path(tmp) / "docs/factory/05-qa/TEST-PLAN.md"
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("## 5. Manual checks\n| ID | Check | Pass when | Your hours |\n| --- | --- | --- | --- |\n"
                      "| MAN-01 | Screen reader pass | ok | 1 |\n| MAN-02 | Real Safari | ok | 0.5 |\n\n"
                      "### CR-002 manual checks\n| ID | Check | When |\n| --- | --- | --- |\n| MAN-01 | Phone keyboard | now |\n")
        cr = Path(tmp) / "docs/factory/changes/CR-002-version-1-4/01-requirements/REQUIREMENTS.md"
        cr.parent.mkdir(parents=True, exist_ok=True)
        cr.write_text("| Field | Value |\n| --- | --- |\n| Version | 1.4 |\n")
        state = json.loads((Path(tmp) / ".factory/state.json").read_text())
        state["changes"] = {"CR-002": {"title": "Something", "status": "open", "slug": "version-1-4"}}
        (Path(tmp) / ".factory/state.json").write_text(json.dumps(state))
        # main checks are tracked (no CR merged yet), CR-002 too (open)
        code, out = t(tmp, env, "2026-11-05T09:00:00Z", "manual")
        assert "MAN-01" in out and "CR-002/MAN-01" in out and "0/3 passed" in out, out
        assert "TIME UNAVAILABLE" in t(tmp, env, "2026-11-05T09:00:00Z", "start", "MAN-01")[1]   # ambiguous
        assert "started" in t(tmp, env, "2026-11-05T09:00:00Z", "start", "main/MAN-01")[1]
        assert "stopped (1.5h), result pass" in t(tmp, env, "2026-11-05T10:30:00Z", "stop", "main/MAN-01",
                                                   "--result", "pass")[1]
        t(tmp, env, "2026-11-05T11:00:00Z", "log", "MAN-02", "45m", "--result", "fail")
        t(tmp, env, "2026-11-05T11:00:00Z", "estimate", "CR-002/MAN-01", "2h")
        code, out = t(tmp, env, "2026-11-05T12:00:00Z", "manual", "all")
        assert "1/3 passed" in out and "Failed" in out and "Passed" in out, out
        assert "estimated 3.5h" in out, out        # 1 + 0.5 + 2
        assert "remaining 2.5h" in out, out        # failed MAN-02 runs again in full (0.5) + 2
        t(tmp, env, "2026-11-05T12:00:00Z", "report", "--export")
        db = sqlite3.connect(str(Path(tmp) / "metrics" / "factory.db"))
        rows = {r[0]: r[1:] for r in db.execute(
            "SELECT ref, who, type, version, status, actual_h FROM tasks WHERE who='you'")}
        assert rows["MAN-01"] == ("you", "Manual QA", "baseline", "Passed", 1.5)
        assert rows["CR-002/MAN-01"][2] == "1.4.0"
        your = db.execute("SELECT SUM(your_est_h), SUM(your_spent_h) FROM summary WHERE scope != 'all'").fetchone()
        assert your == (3.5, 2.25), your
        assert db.execute("SELECT agent_est_h FROM summary WHERE scope='all'").fetchone()[0] == 9.0


def test_errors_never_crash():
    with tempfile.TemporaryDirectory() as tmp:
        env = setup_repo(tmp)
        assert t(tmp, env, "2026-11-05T14:00:00Z", "start", "api#1", "nope")[0] == 2
        assert "TIME UNAVAILABLE" in t(tmp, env, "2026-11-05T14:00:00Z", "start", "foo", "dev")[1]
        env2 = dict(env, FACTORY_GH_FIXTURE=str(Path(tmp) / "missing.json"))
        assert "TIME UNAVAILABLE" in t(tmp, env2, "2026-11-05T14:00:00Z", "report")[1]


def test_hook_protects_timelog():
    def hook(event):
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                              capture_output=True, text=True).returncode
    assert hook({"tool_name": "Write", "tool_input": {"file_path": "/r/.factory/timelog.jsonl", "content": ""}}) == 2
    assert hook({"tool_name": "Edit", "tool_input": {"file_path": "C:\\r\\.factory\\timelog.jsonl"}}) == 2
    assert hook({"tool_name": "Bash", "tool_input": {"command": "echo x >> .factory/timelog.jsonl"}}) == 2
    assert hook({"tool_name": "Bash", "tool_input": {"command": "git checkout -- .factory/timelog.jsonl"}}) == 2
    assert hook({"tool_name": "Bash", "tool_input": {"command": "tail .factory/timelog.jsonl"}}) == 0
    assert hook({"tool_name": "Bash", "tool_input": {"command": 'bash "/p/scripts/gate" time stop api#1 dev'}}) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
