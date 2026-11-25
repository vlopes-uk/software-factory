#!/usr/bin/env python3
"""Estimates, time spent, deviations and calibration for factory tasks.

Who spends the time:
  agent   factory tasks (be/fe/bug issues): the agents' hours.
  you     manual QA checks (MAN-NN rows in a TEST-PLAN.md): your hours. They are tracked when their
          scope is an open change request (or the baseline before any CR was merged), or once they
          have an estimate or time recorded.

Two quantities per factory task, both in hours:
  agent   the factory's agents working on it (developer, QA reviewer, fix rounds).
          Estimated at planning, measured automatically by /factory:build.
  human   what a human developer would need for the same task. Estimated at planning;
          after the work, the QA reviewer re-estimates it from the finished PR (a proxy,
          since no human builds it).

Where the data lives:
  estimates   in each task issue body (the source of truth, written by /factory:plan):
                **Estimate:** agent 2.5h · human 6h
                **Revised estimate:** agent 3h · human 8h (2026-11-20: scope grew)
              The original line is never edited; the last revised line wins for forecasts.
  actuals     .factory/timelog.jsonl in the product repo, append-only, written only by this
              script (the hook blocks direct edits). One JSON object per line:
                {"ts": "...", "event": "start"|"stop", "task": "owner/repo#N", "stage": "dev|qa|fix"}
                {"ts": "...", "event": "human", "task": "owner/repo#N", "hours": 7.5, "note": "..."}
  manual QA   estimates: an "Your hours" (or "Estimate") column in the manual-checks table of a
              TEST-PLAN.md, or `time estimate MAN-NN <hours>` (also used to re-estimate);
              time: `time start|stop MAN-NN` (a timer) or `time log MAN-NN <hours>`, with
              `--result pass|fail`. MAN ids repeat across CRs: write CR-009/MAN-21 when ambiguous.
  dashboard   `time export` writes a SQLite database for Grafana (see dashboard/ in the
              factory repo): $FACTORY_METRICS_DIR/factory.db (default ~/.factory/metrics).
              One shared file, one row set per product. Learning stays per product.

Usage (through the gate wrapper):
  bash scripts/gate time start <task> <dev|qa|fix>     record a stage start (task: api#N, web#N, owner/repo#N)
  bash scripts/gate time stop  <task> <dev|qa|fix> [--result pr|blocked|passed|changes|failed]
  bash scripts/gate time human <task> <hours> [note]   QA's after-the-fact human-dev estimate
  bash scripts/gate time manual [open|all|CR-NNN]       your manual QA checks: status, estimate, time
  bash scripts/gate time start|stop MAN-NN [--result pass|fail]   timer for a manual check
  bash scripts/gate time log <task> <hours> [--result pass|fail] [--stage dev|qa|fix|manual]
  bash scripts/gate time estimate MAN-NN <hours> [note]           (re-)estimate a manual check
  bash scripts/gate time report [all|main|CR-NNN] [--export]
                                                       progress: spent, remaining, forecast, deviations
                                                       (--export also refreshes the Grafana database)
  bash scripts/gate time calibration                   accuracy by category + similar past tasks (for /factory:plan)
  bash scripts/gate time export                        refresh the Grafana database
  bash scripts/gate time unlogged                      closed tasks that have no recorded agent time

Pairing rule for start/stop (both the orchestrator and the agent itself may record them):
a segment opens at the first `start` with no open segment for that task+stage, and closes
at the first `stop` after it. Extra starts while open and extra stops while closed are
ignored, so double recording is harmless. A segment left open for more than
STALE_HOURS is treated as abandoned and not counted.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path

import gate

STAGES = ("dev", "qa", "fix", "manual")
AGENT_STAGES = ("dev", "qa", "fix")
MAN_RE = re.compile(r"^(?:(CR-\d{3,}|main)[/:@ ])?(MAN-\d+)$", re.I)
STALE_HOURS = 6.0
MIN_SAMPLES = 3
TIMELOG = gate.ROOT / ".factory" / "timelog.jsonl"
HOURS_RE = re.compile(r"^\s*(?:(\d+(?:[.,]\d+)?)\s*h)?\s*(?:(\d+(?:[.,]\d+)?)\s*m(?:in)?)?\s*$", re.I)
EST_LINE = re.compile(r"^\W*(revised\s+estimate|estimate)\W*:?\**\s*(.*)$", re.I)


# ---------------------------------------------------------------- small helpers

def now_dt() -> _dt.datetime:
    fake = os.environ.get("FACTORY_NOW")  # tests
    if fake:
        return parse_ts(fake)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def parse_ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def iso(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def epoch(d: _dt.datetime | None):
    return int(d.timestamp()) if d else None


def parse_hours(text: str | None) -> float | None:
    """'2.5h', '90m', '2h30m', '3' -> hours."""
    if text is None:
        return None
    t = str(text).strip().lower().rstrip(".,;)")
    if not t:
        return None
    try:
        return round(float(t.replace(",", ".")), 2)
    except ValueError:
        pass
    m = HOURS_RE.match(t)
    if not m or not (m.group(1) or m.group(2)):
        return None
    h = float((m.group(1) or "0").replace(",", "."))
    mins = float((m.group(2) or "0").replace(",", "."))
    return round(h + mins / 60, 2)


def fmt(h) -> str:
    if h is None:
        return "-"
    return (f"{h:.2f}".rstrip("0").rstrip(".") or "0") + "h"


def pct(a, b) -> str:
    if a is None or not b:
        return "-"
    return f"{(a - b) / b * 100:+.0f}%"


def parse_estimates(body: str) -> dict:
    """Original and (last) revised agent/human estimates from an issue body."""
    out = {"est_agent": None, "est_human": None, "rev_agent": None, "rev_human": None, "rev_note": None}
    for line in (body or "").splitlines():
        m = EST_LINE.match(line.strip())
        if not m:
            continue
        rest = m.group(2)
        a = re.search(r"agent\W*([\d.,]+\s*(?:h|m|min)?(?:\s*\d+\s*m)?)", rest, re.I)
        h = re.search(r"human\W*([\d.,]+\s*(?:h|m|min)?(?:\s*\d+\s*m)?)", rest, re.I)
        agent, human = (parse_hours(a.group(1)) if a else None), (parse_hours(h.group(1)) if h else None)
        if m.group(1).lower().startswith("revised"):
            out["rev_agent"] = agent if agent is not None else out["rev_agent"]
            out["rev_human"] = human if human is not None else out["rev_human"]
            note = re.search(r"\((.*)\)\s*$", rest)
            out["rev_note"] = note.group(1) if note else out["rev_note"]
        elif out["est_agent"] is None and out["est_human"] is None:
            out["est_agent"], out["est_human"] = agent, human
    return out


def parse_size(body: str) -> str | None:
    m = re.search(r"\*\*Size:?\*\*:?\s*([SML])\b", body or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- task refs

def code_repos() -> dict:
    cfg = json.loads(gate.CONFIG.read_text(encoding="utf-8")) if gate.CONFIG.exists() else {}
    return {k: v["github"].lower() for k, v in cfg.get("repos", {}).items()
            if k in ("api", "web") and isinstance(v, dict) and v.get("github")}


def norm_task(raw: str) -> str:
    m = MAN_RE.match(raw.strip())
    if m:
        return manual_key(m.group(1), m.group(2))
    t = raw.strip().lower().lstrip("#")
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", t)
    if m:
        return f"{m.group(1)}#{m.group(2)}"
    m = re.fullmatch(r"(api|web)#(\d+)", t)
    if m:
        repo = code_repos().get(m.group(1))
        if not repo:
            raise ValueError(f"no repos.{m.group(1)}.github in .factory/config.json")
        return f"{repo}#{m.group(2)}"
    raise ValueError(f"task '{raw}': use api#N, web#N or owner/repo#N")


# ---------------------------------------------------------------- the log

def append(event: dict) -> None:
    TIMELOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(TIMELOG, "a", encoding="utf-8", newline="\n") as f:  # one write per line: safe for parallel agents
        f.write(line)


def read_log() -> list[dict]:
    if not TIMELOG.exists():
        return []
    events = []
    for line in TIMELOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and parse_ts(e.get("ts")):
            events.append(e)
    events.sort(key=lambda e: parse_ts(e["ts"]))
    return events


def segments(events: list[dict], now: _dt.datetime | None = None) -> dict:
    """{task: [ {stage, start, end, hours, result, state: closed|running|abandoned} ]}"""
    now = now or now_dt()
    open_: dict = {}
    out: dict = {}
    for e in events:
        if e.get("event") == "log" and e.get("stage") in STAGES and isinstance(e.get("hours"), (int, float)):
            end = parse_ts(e["ts"])
            out.setdefault(e.get("task", "").lower(), []).append({
                "stage": e["stage"], "start": end - _dt.timedelta(hours=float(e["hours"])), "end": end,
                "hours": round(float(e["hours"]), 3), "result": e.get("result"), "state": "closed"})
            continue
        if e.get("event") not in ("start", "stop") or e.get("stage") not in STAGES:
            continue
        key = (e.get("task", "").lower(), e["stage"])
        ts = parse_ts(e["ts"])
        if e["event"] == "start":
            if key not in open_:
                open_[key] = ts
        elif key in open_:
            start = open_.pop(key)
            out.setdefault(key[0], []).append({
                "stage": key[1], "start": start, "end": ts,
                "hours": round(max((ts - start).total_seconds(), 0) / 3600, 3),
                "result": e.get("result"), "state": "closed"})
    for (task, stage), start in open_.items():
        age = (now - start).total_seconds() / 3600
        running = age <= STALE_HOURS
        out.setdefault(task, []).append({
            "stage": stage, "start": start, "end": now if running else None,
            "hours": round(age, 3) if running else 0.0, "result": None,
            "state": "running" if running else "abandoned"})
    for segs in out.values():
        segs.sort(key=lambda s: s["start"])
    return out


def human_posthoc(events: list[dict]) -> dict:
    """Last QA after-the-fact human-dev estimate per task."""
    out = {}
    for e in events:
        if e.get("event") == "human" and isinstance(e.get("hours"), (int, float)):
            out[e.get("task", "").lower()] = {"hours": float(e["hours"]), "note": e.get("note", "")}
    return out


# ---------------------------------------------------------------- milestones from PLAN.md

def milestones() -> dict:
    """{task id (T-..): milestone} from the Tasks table (Milestone column) or the Milestones table."""
    files = [gate.DOCS / "04-plan" / "PLAN.md"]
    if gate.CHANGES.exists():
        files += sorted(gate.CHANGES.glob("CR-*/04-plan/PLAN.md"))
    by_col, by_list = {}, {}
    for f in files:
        if not f.exists():
            continue
        header = None
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                header = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            row = dict(zip(header, cells))
            if "id" in row and "milestone" in row and row["milestone"]:
                tid = row["id"].strip("` ")
                if re.match(r"(T|BUG)-", tid):
                    by_col[tid.upper()] = row["milestone"]
            elif "milestone" in row and "tasks" in row:
                ms = row["milestone"].split(" ")[0] if row["milestone"] else ""
                for tid in re.findall(r"\b(?:T|BUG)-[\w-]+", row["tasks"]):
                    by_list.setdefault(tid.upper(), ms)
    by_list.update(by_col)
    return by_list


# ---------------------------------------------------------------- manual QA checks (your time)

def manual_key(scope: str | None, mid: str) -> str:
    mid = mid.upper()
    if scope:
        return f"{'main' if scope.lower() == 'main' else scope.upper()}/{mid}".lower()
    hits = [c for c in manual_checks() if c["id"] == mid]
    if not hits:
        raise ValueError(f"{mid} is not in any TEST-PLAN.md manual-checks table")
    if len(hits) > 1:
        tracked = [c for c in hits if c["auto"]]
        if len(tracked) == 1:
            return tracked[0]["key"]
        raise ValueError(f"{mid} exists in several scopes ({', '.join(c['scope'] for c in hits)}): "
                         f"write it as e.g. {hits[-1]['scope']}/{mid}")
    return hits[0]["key"]


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean(text: str) -> str:
    return re.sub(r"[*`]", "", text).strip()


def manual_checks() -> list[dict]:
    """MAN-NN rows from the manual-checks tables of the baseline and CR test plans."""
    files = [(gate.DOCS / "05-qa" / "TEST-PLAN.md", None)]
    if gate.CHANGES.exists():
        for f in sorted(gate.CHANGES.glob("CR-*/05-qa/TEST-PLAN.md")):
            m = re.match(r"(CR-\d{3,})", f.parent.parent.name, re.I)
            if m:
                files.append((f, m.group(1).upper()))
    found: dict = {}
    for f, fixed in files:
        if not f.exists():
            continue
        scope, header = fixed or "main", None
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                header = None
                if not fixed:
                    cr = re.search(r"\b(CR-\d{3,})\b", line)
                    if re.match(r"\s*#", line) and "manual" in line.lower() and not cr:
                        scope = "main"
                    elif cr and ("manual" in line.lower() or re.match(r"\s*(#+\s*|\*\*)CR-\d{3,}", line)):
                        scope = cr.group(1).upper()
                continue
            cells = _cells(line)
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            row = dict(zip(header, cells))
            mid = _clean(row.get("id", ""))
            if not re.fullmatch(r"MAN-\d+", mid):
                continue
            est_col = next((h for h in header if "hour" in h or "estimate" in h), None)
            check = next((row[h] for h in header if h.startswith("check")), "") or ""
            key = f"{scope}/{mid}".lower()
            prev = found.get(key)
            item = {"id": mid, "scope": scope, "key": key, "title": _clean(check)[:160],
                    "est_plan": parse_hours(_clean(row.get(est_col, ""))) if est_col else None,
                    "waived": "waived" in line.lower()}
            if prev is None or (fixed and prev["est_plan"] is None) or item["est_plan"] is not None:
                found[key] = item
    state = gate.load() or {}
    changes = state.get("changes", {})
    open_crs = {k for k, v in changes.items() if v.get("status") == "open"}
    any_merged = any(v.get("status") == "merged" for v in changes.values())
    out = []
    for c in found.values():
        c["auto"] = (c["scope"] in open_crs or (c["scope"] == "main" and not any_merged)) and not c["waived"]
        out.append(c)
    return sorted(out, key=lambda c: (c["scope"] != "main", c["scope"], int(c["id"].split("-")[1])))


def manual_rows(events: list[dict], segs: dict) -> list[dict]:
    est_events: dict = {}
    results: dict = {}
    touched = set()
    for e in events:
        k = e.get("task", "").lower()
        if e.get("event") == "estimate" and isinstance(e.get("hours"), (int, float)):
            est_events.setdefault(k, []).append(float(e["hours"]))
        if e.get("result") in ("pass", "fail") and e.get("stage") == "manual":
            results[k] = (e["result"], parse_ts(e["ts"]))
        if "/man-" in k:
            touched.add(k)
    rows = []
    for c in manual_checks():
        k = c["key"]
        if not (c["auto"] or k in touched):
            continue
        evs = est_events.get(k, [])
        est = c["est_plan"] if c["est_plan"] is not None else (evs[0] if evs else None)
        rev = (evs[-1] if c["est_plan"] is not None else (evs[-1] if len(evs) > 1 else None)) if evs else None
        mine = [x for x in segs.get(k, []) if x["stage"] == "manual"]
        spent = round(sum(x["hours"] for x in mine), 3)
        running = any(x["state"] == "running" for x in mine)
        res = results.get(k)
        if running:
            status = "in progress"
        elif res and res[0] == "pass":
            status = "passed"
        elif res and res[0] == "fail":
            status = "failed"
        else:
            status = "to do"
        rows.append({
            "ref": c["id"] if c["scope"] == "main" else f"{c['scope']}/{c['id']}", "full": k, "tid": c["id"],
            "title": c["title"], "who": "you", "repo_kind": "you", "kind": "manual", "size": None,
            "story": None, "scope": c["scope"], "milestone": None,
            "state": "closed" if status == "passed" else "open", "status": status,
            "est_agent": est, "est_human": None, "rev_agent": rev, "rev_human": None, "rev_note": None,
            "cur_agent": rev if rev is not None else est, "cur_human": None,
            "actual": spent, "dev": 0.0, "qa": 0.0, "fix": 0.0, "manual": spent, "fix_rounds": 0,
            "running": running, "abandoned": sum(1 for x in mine if x["state"] == "abandoned"),
            "segments": mine, "tracked": spent > 0, "posthoc_human": None,
            "created": None, "closed": res[1] if status == "passed" else None,
            "first_start": mine[0]["start"] if mine else None,
        })
    return rows


# ---------------------------------------------------------------- the model

def kind_of(t: dict) -> str:
    title = t["title"].lower()
    if "bug" in t["labels"]:
        return "bug"
    if "[ops]" in title:
        return "ops"
    if "sync contract" in title:
        return "sync"
    if re.match(r"t-00\b|t-00-", (t["tid"] or "").lower()):
        return "skeleton"
    return "feature"


STATUS_LABEL = {"ready": "Ready", "waiting": "Waiting", "in-review": "In review", "blocked": "Blocked",
                "gated": "Gated", "done": "Done", "to do": "To do", "in progress": "In progress",
                "failed": "Failed", "passed": "Passed"}


def build_model(data: dict | None = None, events: list[dict] | None = None, agent: bool = True) -> dict:
    import tasks as tasks_mod
    if data is None and agent:
        data = tasks_mod.collect()
        tasks_mod.classify(data)
    if data is None:
        try:
            prod = json.loads(gate.CONFIG.read_text(encoding="utf-8"))["repos"]["product"]["github"]
        except (OSError, ValueError, KeyError, TypeError):
            prod = gate.ROOT.name
        data = {"tasks": [], "product": prod.lower()}
    events = read_log() if events is None else events
    now = now_dt()
    segs = segments(events, now)
    posthoc = human_posthoc(events)
    ms = milestones()
    rows = []
    for t in data["tasks"]:
        key = f"{t['repo']}#{t['number']}".lower()
        body = t.get("body") or ""
        est = parse_estimates(body)
        mine = segs.get(key, [])
        by_stage = {s: round(sum(x["hours"] for x in mine if x["stage"] == s), 3) for s in AGENT_STAGES}
        actual = round(sum(by_stage.values()), 3)
        cur = est["rev_agent"] if est["rev_agent"] is not None else est["est_agent"]
        cur_h = est["rev_human"] if est["rev_human"] is not None else est["est_human"]
        rk = "be" if "be" in t["labels"] else "fe" if "fe" in t["labels"] else t["key"]
        rows.append({
            "ref": t["ref"], "full": key, "tid": t["tid"], "title": t["title"], "who": "agent", "repo_kind": rk,
            "kind": kind_of(t), "size": parse_size(body), "story": t.get("story"),
            "scope": t.get("cr") or "main", "milestone": ms.get((t["tid"] or "").upper()),
            "state": t["state"], "status": t.get("status", "done" if t["state"] == "closed" else "open"),
            **est, "cur_agent": cur, "cur_human": cur_h,
            "actual": actual, "dev": by_stage["dev"], "qa": by_stage["qa"], "fix": by_stage["fix"], "manual": 0.0,
            "fix_rounds": sum(1 for x in mine if x["stage"] == "fix" and x["state"] != "abandoned"),
            "running": any(x["state"] == "running" for x in mine),
            "abandoned": sum(1 for x in mine if x["state"] == "abandoned"),
            "segments": mine, "tracked": actual > 0,
            "posthoc_human": posthoc.get(key, {}).get("hours"),
            "created": parse_ts(t.get("createdAt")), "closed": parse_ts(t.get("closedAt")),
            "first_start": mine[0]["start"] if mine else None,
        })
    rows += manual_rows(events, segs)
    for r in rows:
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"].capitalize())
        r["type"] = {"manual": "Manual QA", "bug": "Bug"}.get(r["kind"]) or \
            {"be": "Backend", "fe": "Frontend"}.get(r["repo_kind"], r["repo_kind"])
    cal = calibrate(rows)
    for r in rows:
        f, src, _n = factor_for(cal, r)
        r["factor"], r["factor_src"] = f, src
        if r["state"] == "closed":
            r["remaining"] = r["forecast"] = 0.0
        elif r["cur_agent"] is None:
            r["remaining"] = r["forecast"] = None
        elif r["status"] == "failed":  # a failed manual check is run again in full
            r["remaining"] = r["cur_agent"]
            r["forecast"] = round(r["cur_agent"] * f, 3)
        else:
            r["remaining"] = round(max(r["cur_agent"] - r["actual"], 0.0), 3)
            expected = r["cur_agent"] * f
            r["forecast"] = round(max(expected - r["actual"], 0.1 * expected) if r["actual"] else expected, 3)
    return {"rows": rows, "cal": cal, "data": data, "now": now}


def cats(r: dict) -> list[str]:
    if r["who"] == "you":
        return ["manual"]
    return [f"{r['repo_kind']}/{r['kind']}/{r['size'] or '?'}", f"{r['repo_kind']}/{r['kind']}", r["kind"], "all"]


def samples(rows: list[dict]) -> list[dict]:
    """Done tasks with an estimate and recorded agent time: the learning set."""
    return [r for r in rows if r["state"] == "closed" and r["tracked"] and r["cur_agent"]]


def calibrate(rows: list[dict]) -> dict:
    groups: dict = {}
    for r in samples(rows):
        ratio = r["actual"] / r["cur_agent"]
        for c in cats(r):
            groups.setdefault(c, []).append(ratio)
    out = {}
    for c, ratios in groups.items():
        rs = sorted(ratios)
        p80 = rs[min(len(rs) - 1, int(round(0.8 * (len(rs) - 1))))]
        out[c] = {"n": len(rs), "factor": round(statistics.median(rs), 3), "p80": round(p80, 3)}
    hum = [r["posthoc_human"] / r["cur_human"] for r in rows
           if r["state"] == "closed" and r["posthoc_human"] and r["cur_human"]]
    if hum:
        out["human"] = {"n": len(hum), "factor": round(statistics.median(hum), 3),
                        "p80": round(sorted(hum)[min(len(hum) - 1, int(round(0.8 * (len(hum) - 1))))], 3)}
    return out


def factor_for(cal: dict, r: dict) -> tuple[float, str, int]:
    for c in cats(r):
        g = cal.get(c)
        if g and g["n"] >= MIN_SAMPLES:
            return g["factor"], c, g["n"]
    return 1.0, "default (too little history)", 0


# ---------------------------------------------------------------- report

def summarise(rows: list[dict]) -> dict:
    """Totals; hours are kept apart by who spends them (agent / you): they are different resources."""
    out = {"tasks": len(rows), "done": sum(1 for r in rows if r["state"] == "closed"),
           "est_human": round(sum(r["cur_human"] or 0 for r in rows), 2)}
    for who in ("agent", "you"):
        rs = [r for r in rows if r["who"] == who]
        est = [r for r in rs if r["cur_agent"] is not None]
        out[who] = {
            "tasks": len(rs), "done": sum(1 for r in rs if r["state"] == "closed"),
            "est": round(sum(r["cur_agent"] for r in est), 2),
            "est_orig": round(sum(r["est_agent"] or 0 for r in rs), 2),
            "spent": round(sum(r["actual"] for r in rs), 2),
            "remaining": round(sum(r["remaining"] or 0 for r in rs if r["state"] != "closed"), 2),
            "forecast": round(sum(r["forecast"] or 0 for r in rs if r["state"] != "closed"), 2),
            "unestimated": sum(1 for r in rs if r["cur_agent"] is None and r["state"] != "closed"),
        }
    return out


def table(headers: list[str], body: list[list[str]]) -> None:
    if not body:
        return
    w = [max(len(str(x)) for x in col) for col in zip(headers, *body)]
    print("  " + "  ".join(h.ljust(w[i]) for i, h in enumerate(headers)))
    for b in body:
        print("  " + "  ".join(str(x).ljust(w[i]) for i, x in enumerate(b)))


def row_label(r: dict) -> str:
    return r["ref"] if r["who"] == "you" else f"{r['ref']} {r['tid']}"


def group_rows(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key] or "-", []).append(r)
    return out


def model_or_manual() -> dict:
    """The full model; if GitHub can't be read, the manual QA checks alone (they need no GitHub)."""
    try:
        return build_model()
    except RuntimeError as e:
        print(f"TASKS UNAVAILABLE ({e}): showing manual QA checks only.")
        return build_model(agent=False)


def cmd_report(scope: str, export: bool = False) -> None:
    m = model_or_manual()
    if export:
        try:
            cmd_export(quiet=True, model=m)
        except (OSError, sqlite3.Error) as e:
            print(f"EXPORT FAILED: {e}")
    rows = m["rows"]
    if scope and scope.lower() not in ("all", ""):
        want = "main" if scope.lower() == "main" else scope.upper()
        rows = [r for r in rows if r["scope"] == want]
    if not rows:
        print("TIME: no factory tasks yet (or none in that scope).")
        return
    s = summarise(rows)
    a, y = s["agent"], s["you"]
    print("TIME (hours)")
    print(f"  Tasks: {s['done']}/{s['tasks']} done")
    print(f"  Agents (factory tasks {a['done']}/{a['tasks']}): estimated {fmt(a['est'])} · spent {fmt(a['spent'])} · "
          f"remaining {fmt(a['remaining'])} -> forecast {fmt(a['forecast'])}"
          + (f"  [human-dev equivalent {fmt(s['est_human'])}]" if s["est_human"] else ""))
    if y["tasks"]:
        print(f"  You (manual QA checks {y['done']}/{y['tasks']}): estimated {fmt(y['est'])} · spent {fmt(y['spent'])} · "
              f"remaining {fmt(y['remaining'])} -> forecast {fmt(y['forecast'])}")
    print("  Forecasts are corrected with this product's history (see CALIBRATION).")
    for who, t in (("agent", a), ("manual QA", y)):
        if t["est_orig"] and abs(t["est_orig"] - t["est"]) > 0.05:
            print(f"  Re-estimates moved the {who} plan from {fmt(t['est_orig'])} to {fmt(t['est'])}.")
        if t["unestimated"]:
            print(f"  WARNING: {t['unestimated']} open {who} item(s) have no estimate and are not in the totals.")
    running = [r for r in rows if r["running"]]
    if running:
        print("  Running now: " + ", ".join(f"{r['ref']} ({r['segments'][-1]['stage']})" for r in running))

    print("\nBY SCOPE / MILESTONE / STORY")
    body = []
    for key, label in (("scope", "scope"), ("milestone", "milestone"), ("story", "story")):
        groups = group_rows(rows, key)
        if key != "scope" and set(groups) == {"-"}:
            continue
        for g, rs in sorted(groups.items()):
            t = summarise(rs)
            a, y = t["agent"], t["you"]
            body.append([f"{label} {g}", f"{t['done']}/{t['tasks']}", fmt(a["est"]), fmt(a["spent"]),
                         fmt(a["forecast"]), fmt(y["est"]) if y["tasks"] else "-",
                         fmt(y["spent"]) if y["tasks"] else "-", fmt(y["forecast"]) if y["tasks"] else "-"])
    table(["Group", "Done", "Agent est", "Agent spent", "Agent left", "Your est", "Your spent", "Your left"], body)

    done = [r for r in samples(rows)]
    if done:
        print("\nBIGGEST DEVIATIONS (done tasks, actual vs estimate)")
        done.sort(key=lambda r: abs(r["actual"] - r["cur_agent"]), reverse=True)
        table(["Task", "Estimate", "Actual", "Dev", "QA", "Fix (rounds)", "Deviation", "Human plan->QA"],
              [[row_label(r), fmt(r["cur_agent"]), fmt(r["actual"]), fmt(r["dev"]), fmt(r["qa"]),
                f"{fmt(r['fix'])} ({r['fix_rounds']})" if r["who"] == "agent" else "manual QA",
                pct(r["actual"], r["cur_agent"]),
                f"{fmt(r['cur_human'])}->{fmt(r['posthoc_human'])}"] for r in done[:8]])
    over = [r for r in rows if r["state"] != "closed" and r["cur_agent"] and r["actual"] > r["cur_agent"]]
    if over:
        print("\nOPEN TASKS ALREADY OVER ESTIMATE")
        table(["Task", "Estimate", "Spent so far"],
              [[row_label(r), fmt(r["cur_agent"]), fmt(r["actual"])] for r in over])
    untracked = [r for r in rows if r["state"] == "closed" and not r["tracked"]]
    if untracked:
        print(f"\nNote: {len(untracked)} closed task(s) have no recorded time "
              "(built before tracking, or outside /factory:build); they are left out of the learning.")
    print_calibration(m["cal"], short=True)


def print_calibration(cal: dict, short: bool = False) -> None:
    keys = [k for k in cal if k != "human"]
    print("\nCALIBRATION (actual / estimate, median of done items; 'manual' = your QA checks)")
    if not keys:
        print("  No history yet: estimates are used as-is (factor 1.0).")
    else:
        keys.sort(key=lambda k: (k != "all", k.count("/"), k))
        body = [[k, str(cal[k]["n"]), f"x{cal[k]['factor']:.2f}", f"x{cal[k]['p80']:.2f}",
                 "" if cal[k]["n"] >= MIN_SAMPLES else "too few to use"]
                for k in keys[: (8 if short else None)]]
        table(["Category", "Samples", "Typical", "80% within", ""], body)
    if "human" in cal:
        h = cal["human"]
        print(f"  Human-dev estimates: QA's after-the-fact estimate is typically x{h['factor']:.2f} "
              f"of the plan ({h['n']} tasks).")


def short_title(title: str) -> str:
    return re.sub(r"^\[[^]]+\]\s*", "", title)[:48]


def cmd_calibration() -> None:
    m = build_model()
    cal = m["cal"]
    print("ESTIMATION HISTORY FOR THIS PRODUCT (use it when estimating new tasks)")
    print("  Categories: <be|fe>/<feature|ops|sync|skeleton|bug>/<S|M>; broader rows apply when a "
          f"specific one has fewer than {MIN_SAMPLES} samples.")
    print_calibration(cal)
    done = sorted(samples(m["rows"]), key=lambda r: r["closed"] or m["now"], reverse=True)
    if done:
        print("\nRECENT DONE TASKS (estimate -> actual agent time; human plan -> QA's after-the-fact)")
        table(["Task", "Category", "Agent est", "Actual", "Fix rounds", "Human plan", "Human (QA)"],
              [[f"{r['tid']} {short_title(r['title'])}", cats(r)[0],
                fmt(r["cur_agent"]), fmt(r["actual"]), str(r["fix_rounds"]),
                fmt(r["cur_human"]), fmt(r["posthoc_human"])] for r in done[:12]])
        print("\nApply it: estimate as usual, then multiply agent hours by the factor of the most specific "
              "category with enough samples, and say so in PLAN.md (e.g. `agent 2h x1.3 = 2.6h`).")
    else:
        print("\nNo finished tasks with recorded time yet: estimate from the task size and say the "
              "estimates are uncalibrated.")


def cmd_unlogged() -> None:
    m = build_model()
    miss = [r for r in m["rows"] if r["state"] == "closed" and not r["tracked"]]
    print(f"UNLOGGED: {len(miss)} closed task(s) without agent time" + (":" if miss else "."))
    for r in miss:
        print(f"  {r['ref']} {r['tid']} {r['title']}")


# ---------------------------------------------------------------- export (Grafana)

def metrics_path() -> Path:
    d = os.environ.get("FACTORY_METRICS_DIR")
    base = Path(d).expanduser() if d else Path.home() / ".factory" / "metrics"
    return base / "factory.db"


SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  product TEXT, ref TEXT, tid TEXT, title TEXT, who TEXT, type TEXT, kind TEXT, repo_kind TEXT, size TEXT,
  category TEXT, story TEXT, scope TEXT, version TEXT, milestone TEXT, state TEXT, status TEXT,
  est_h REAL, rev_h REAL, cur_h REAL, actual_h REAL, remaining_h REAL, forecast_h REAL,
  deviation_h REAL, deviation_pct REAL, dev_h REAL, qa_h REAL, fix_h REAL, manual_h REAL, fix_rounds INTEGER,
  est_human_h REAL, cur_human_h REAL, posthoc_human_h REAL, factor REAL, tracked INTEGER,
  created_time INTEGER, closed_time INTEGER, first_start_time INTEGER);
CREATE TABLE IF NOT EXISTS stages (
  product TEXT, ref TEXT, tid TEXT, who TEXT, scope TEXT, version TEXT, stage TEXT,
  time INTEGER, end_time INTEGER, hours REAL, result TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS burndown (
  product TEXT, scope TEXT, version TEXT, time INTEGER, agent_remaining_h REAL, agent_spent_h REAL,
  your_remaining_h REAL, your_spent_h REAL, done_tasks INTEGER);
CREATE TABLE IF NOT EXISTS calibration (
  product TEXT, category TEXT, samples INTEGER, factor REAL, p80 REAL);
CREATE TABLE IF NOT EXISTS summary (
  product TEXT, scope TEXT, version TEXT, tasks INTEGER, done INTEGER,
  agent_est_h REAL, agent_spent_h REAL, agent_remaining_h REAL, agent_forecast_h REAL,
  your_est_h REAL, your_spent_h REAL, your_remaining_h REAL, your_forecast_h REAL,
  est_human_h REAL, unestimated INTEGER, time INTEGER);
"""
OLD_TABLES = ("tasks", "stages", "burndown", "calibration", "calibration_history", "summary")


def versions() -> dict:
    """{scope: version} for change requests: a `**Version:**` line in the CR's requirements, else
    "Version X.Y.Z" in its title. The baseline is "baseline"."""
    out = {"main": "baseline"}
    state = gate.load() or {}
    for cid, c in state.get("changes", {}).items():
        v = None
        folder = gate.CHANGES
        req = sorted(folder.glob(f"{cid}-*/01-requirements/*.md")) if folder.exists() else []
        for f in req:
            for line in f.read_text(encoding="utf-8").splitlines():
                if re.match(r"\W*version\W*[:|]", line.strip(), re.I):
                    m = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", line)
                    if m:
                        v = m.group(1)
                        break
            if v:
                break
        if not v:
            m = re.search(r"\bversion\s+v?(\d+\.\d+(?:\.\d+)?)\b", c.get("title", ""), re.I)
            v = m.group(1) if m else None
        if v and v.count(".") == 1:
            v += ".0"
        out[cid] = v or f"{cid} (no version)"
    return out


def burndown(rows: list[dict], now: _dt.datetime) -> list[tuple]:
    """Remaining and spent hours over time, from when the estimated work was planned until now:
    the planning moment, then the end of each day, then now. Scopes with no estimates have no line."""
    est = [r for r in rows if r["cur_agent"] is not None]
    if not est:
        return []
    starts = [r["created"] for r in est if r["created"]] + [r["first_start"] for r in rows if r["first_start"]]
    first = min(starts) if starts else now
    times, day = [first], first.replace(hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(days=1)
    while day < now:
        times.append(day)
        day += _dt.timedelta(days=1)
    if now > first:
        times.append(now)
    points = []
    for end in times:
        vals = []
        for who in ("agent", "you"):
            rs = [r for r in rows if r["who"] == who]
            rem = sum(max((r["cur_agent"] or 0) - sum(x["hours"] for x in r["segments"]
                                                      if x["end"] and x["end"] <= end), 0)
                      for r in rs if (not r["created"] or r["created"] <= end)
                      and not (r["closed"] and r["closed"] <= end))
            spent = sum(x["hours"] for r in rs for x in r["segments"] if x["end"] and x["end"] <= end)
            vals += [round(rem, 3), round(spent, 3)]
        done = sum(1 for r in rows if r["closed"] and r["closed"] <= end)
        points.append((epoch(end), *vals, done))
    return points


def _prepare(con) -> None:
    has = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ver = None
    if "meta" in has:
        row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        ver = int(row[0]) if row else None
    if has and ver != SCHEMA_VERSION:  # older layout: rebuilt; other products refill on their next export
        for t in OLD_TABLES + ("meta",):
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))


def cmd_export(quiet: bool = False, model: dict | None = None) -> None:
    m = model or build_model()
    rows, cal, now = m["rows"], m["cal"], m["now"]
    product = m["data"]["product"].split("/")[-1].removesuffix("-product")
    vers = versions()
    for r in rows:
        r["version"] = vers.get(r["scope"], r["scope"])
    dest = metrics_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="factory-", suffix=".db", dir=str(dest.parent))
    os.close(fd)
    try:
        con = sqlite3.connect(tmp)
        if dest.exists():  # keep the other products' rows
            src = sqlite3.connect(str(dest))
            src.backup(con)
            src.close()
        _prepare(con)
        for tbl in ("tasks", "stages", "burndown", "calibration", "summary"):
            con.execute(f"DELETE FROM {tbl} WHERE product = ?", (product,))
        for r in rows:
            dev = (r["actual"] - r["cur_agent"]) if (r["tracked"] and r["cur_agent"] and r["state"] == "closed") else None
            vals = (product, r["ref"], r["tid"], r["title"], r["who"], r["type"], r["kind"], r["repo_kind"], r["size"],
                    cats(r)[0], r["story"], r["scope"], r["version"], r["milestone"], r["state"], r["status_label"],
                    r["est_agent"], r["rev_agent"], r["cur_agent"], r["actual"], r["remaining"], r["forecast"],
                    round(dev, 3) if dev is not None else None,
                    round(dev / r["cur_agent"] * 100, 1) if dev is not None else None,
                    r["dev"], r["qa"], r["fix"], r["manual"], r["fix_rounds"],
                    r["est_human"], r["cur_human"], r["posthoc_human"], r["factor"], int(r["tracked"]),
                    epoch(r["created"]), epoch(r["closed"]), epoch(r["first_start"]))
            con.execute(f"INSERT INTO tasks VALUES ({','.join('?' * len(vals))})", vals)
            for x in r["segments"]:
                con.execute("INSERT INTO stages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    product, r["ref"], r["tid"], r["who"], r["scope"], r["version"], x["stage"],
                    epoch(x["start"]), epoch(x["end"]), x["hours"], x["result"], x["state"]))
        groups = [("all", "all", rows)]
        groups += [(sc, vers.get(sc, sc), [r for r in rows if r["scope"] == sc]) for sc in sorted({r["scope"] for r in rows})]
        for scope, version, rs in groups:
            for p in burndown(rs, now):
                con.execute("INSERT INTO burndown VALUES (?,?,?,?,?,?,?,?,?)", (product, scope, version, *p))
            t = summarise(rs)
            a, y = t["agent"], t["you"]
            con.execute("INSERT INTO summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                product, scope, version, t["tasks"], t["done"],
                a["est"], a["spent"], a["remaining"], a["forecast"],
                y["est"], y["spent"], y["remaining"], y["forecast"],
                t["est_human"], a["unestimated"] + y["unestimated"], epoch(now)))
        for c, g in cal.items():
            con.execute("INSERT INTO calibration VALUES (?,?,?,?,?)", (product, c, g["n"], g["factor"], g["p80"]))
        con.commit()
        con.close()
        os.replace(tmp, dest)  # atomic: Grafana never sees a half-written file
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not quiet:
        print(f"EXPORT: {len(rows)} item(s) of '{product}' written to {dest}")


# ---------------------------------------------------------------- entry point

def _opt(args: list[str], name: str):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1].lower()
    return None


def is_manual(task: str) -> bool:
    return "/man-" in task


def cmd_manual(which: str) -> None:
    m = build_model(agent=False)
    vers = versions()
    rows = [r for r in m["rows"] if r["who"] == "you"]
    if which and which.lower() not in ("open", "all"):
        rows = [r for r in rows if r["scope"].lower() == which.lower()]
    elif which.lower() != "all":
        rows = [r for r in rows if r["status"] != "passed"]
    if not rows:
        print("MANUAL QA: nothing to do. (Checks are tracked for open change requests, or once they have an "
              "estimate or time: `gate time estimate MAN-NN <hours>`.)")
        return
    s = summarise(rows)["you"]
    print(f"MANUAL QA CHECKS (your hours): {s['done']}/{s['tasks']} passed · estimated {fmt(s['est'])} · "
          f"spent {fmt(s['spent'])} · remaining {fmt(s['remaining'])} -> forecast {fmt(s['forecast'])}")
    table(["Check", "Version", "Status", "Estimate", "Spent", "Left", "What"],
          [[r["ref"], vers.get(r["scope"], r["scope"]), r["status_label"], fmt(r["cur_agent"]), fmt(r["actual"]),
            fmt(r["forecast"]), r["title"][:70]] for r in rows])


def cmd(args: list[str]) -> None:
    args = [a for a in args if a.strip()]
    sub = args[0].lower() if args else "report"
    try:
        if sub in ("start", "stop"):
            if len(args) < 2:
                print(f"Usage: gate time {sub} <api#N|web#N|owner/repo#N> <{'|'.join(AGENT_STAGES)}> | "
                      f"gate time {sub} MAN-NN" + (" [--result pass|fail|<word>]" if sub == "stop" else ""))
                sys.exit(2)
            task = norm_task(args[1])
            if is_manual(task):
                stage = "manual"
            elif len(args) < 3 or args[2].lower() not in AGENT_STAGES:
                print(f"Usage: gate time {sub} <task> <{'|'.join(AGENT_STAGES)}>")
                sys.exit(2)
            else:
                stage = args[2].lower()
            was_open = any(x["stage"] == stage and x["state"] == "running"
                           for x in segments(read_log()).get(task, []))
            ev = {"ts": iso(now_dt()), "event": sub, "task": task, "stage": stage}
            result = _opt(args, "--result")
            if sub == "stop" and result:
                ev["result"] = result
            if sub == "stop" and not was_open:
                print(f"TIME: no running {stage} on {task} (already stopped); nothing to add"
                      + (". To record the result and the time, use `time log`." if stage == "manual" else ""))
                return
            if sub == "start" and was_open:
                print(f"TIME: {stage} on {task} already running; keeping the earlier start")
                return
            append(ev)
            if sub == "stop":
                seg = segments(read_log()).get(task, [])
                last = next((x for x in reversed(seg) if x["stage"] == stage and x["state"] == "closed"), None)
                print(f"TIME: {stage} on {task} stopped" + (f" ({fmt(last['hours'])})" if last else "")
                      + (f", result {result}" if result else ""))
            else:
                print(f"TIME: {stage} on {task} started")
        elif sub == "log":
            if len(args) < 3 or parse_hours(args[2]) is None:
                print("Usage: gate time log <task> <hours, e.g. 45m or 1.5h> [--result pass|fail] [--stage dev|qa|fix]")
                sys.exit(2)
            task, hours = norm_task(args[1]), parse_hours(args[2])
            stage = "manual" if is_manual(task) else (_opt(args, "--stage") or "dev")
            if stage not in STAGES:
                raise ValueError(f"stage must be one of {', '.join(STAGES)}")
            ev = {"ts": iso(now_dt()), "event": "log", "task": task, "stage": stage, "hours": hours}
            if _opt(args, "--result"):
                ev["result"] = _opt(args, "--result")
            append(ev)
            print(f"TIME: logged {fmt(hours)} of {stage} on {task}" + (f", result {ev['result']}" if "result" in ev else ""))
        elif sub == "estimate":
            if len(args) < 3 or parse_hours(args[2]) is None:
                print("Usage: gate time estimate MAN-NN <hours> [note]")
                sys.exit(2)
            task = norm_task(args[1])
            if not is_manual(task):
                raise ValueError("factory task estimates live in the issue body (**Estimate:** line); "
                                 "`time estimate` is for manual QA checks")
            hours = parse_hours(args[2])
            append({"ts": iso(now_dt()), "event": "estimate", "task": task, "hours": hours,
                    "note": " ".join(a for a in args[3:])[:300]})
            print(f"TIME: estimate for {task}: {fmt(hours)} (your time)")
        elif sub == "manual":
            cmd_manual(args[1] if len(args) > 1 else "open")
        elif sub == "human":
            if len(args) < 3 or parse_hours(args[2]) is None:
                print("Usage: gate time human <task> <hours, e.g. 6h or 90m> [note]")
                sys.exit(2)
            task, hours = norm_task(args[1]), parse_hours(args[2])
            append({"ts": iso(now_dt()), "event": "human", "task": task, "hours": hours,
                    "note": " ".join(args[3:])[:300]})
            print(f"TIME: human-dev estimate after the work for {task}: {fmt(hours)}")
        elif sub == "report":
            pos = [a for a in args[1:] if not a.startswith("--")]
            cmd_report(pos[0] if pos else "all", export="--export" in args)
        elif sub in ("calibration", "calibrate"):
            cmd_calibration()
        elif sub == "export":
            cmd_export("--quiet" in args)
        elif sub == "unlogged":
            cmd_unlogged()
        else:
            print(__doc__)
            sys.exit(2)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as e:  # never break the calling skill
        print(f"TIME UNAVAILABLE: {e}")


if __name__ == "__main__":
    cmd(sys.argv[1:])
