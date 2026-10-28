"""Read-through experience reports. No model, writes, persistent lessons, or cross-store joins."""
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time

TTL = 30 * 86400


def read_report(path, attempt_id=None):
    if attempt_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", attempt_id):
        return {"status": "not_recorded"}
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")  # one consistent view, including deletion by the provider
            capability = conn.execute("SELECT value FROM meta WHERE key='experience_schema'").fetchone()
            if not capability or capability[0] != "1":
                return {"status": "unsupported"}
            args = [time.time() - TTL]
            where = "created_at > ?"
            if attempt_id:
                where += " AND attempt_id = ?"
                args.append(attempt_id)
            attempts = conn.execute("SELECT * FROM memory_attempts WHERE " + where +
                                    " ORDER BY created_at DESC,rowid DESC LIMIT 1000", args).fetchall()
            if attempt_id:
                if not attempts:
                    return {"status": "not_recorded"}
                row = attempts[0]
                if not isinstance(row["kind"], str) or not isinstance(row["trace_status"], str):
                    raise ValueError("Invalid experience attempt")
                return {"status": "ok", "schema": 1, "attempt_id": attempt_id, "kind": row["kind"],
                        "trace_status": row["trace_status"], "stages": _stages(json.loads(row["stages"])),
                        "emitted": _refs(json.loads(row["emitted"])), "outcomes": _events(conn, attempt_id),
                        "source": "live_provider_snapshot", "interpretation": "Agent reports; not truth votes"}
            groups = defaultdict(list)
            for row in attempts:
                active = [e for e in _events(conn, row["attempt_id"]) if e["active"]]
                if active:
                    groups[active[0]["reason"]].append(active[0])
            summaries = []
            for reason, events in sorted(groups.items()):
                counts = dict(Counter(e["outcome"] for e in events))
                summaries.append({"reason": reason, "attempts": len(events), "outcomes": counts,
                    "status": "mixed_agent_reports" if len(counts) > 1 else
                              "single_agent_report" if len(events) == 1 else "repeated_agent_reports"})
            return {"status": "ok", "schema": 1, "groups": summaries,
                    "source": "live_provider_snapshot", "truncated_at": 1000,
                    "interpretation": "One active outcome per attempt; no ranking or trust changes"}
    except sqlite3.OperationalError as exc:
        # A legacy database is unsupported; inaccessible/busy/corrupt is not an empty successful report.
        if "no such table" in str(exc):
            return {"status": "unsupported"}
        return {"status": "unavailable", "error_kind": type(exc).__name__}
    except (sqlite3.Error, OSError, TypeError, ValueError, KeyError) as exc:
        return {"status": "unavailable", "error_kind": type(exc).__name__}


def _events(conn, aid):
    rows = conn.execute("SELECT payload FROM memory_outcomes WHERE attempt_id=? ORDER BY rowid LIMIT 10",
                        (aid,)).fetchall()
    data = [json.loads(r[0]) for r in rows]
    replaced = {e["supersedes"] for e in data if e.get("supersedes")}
    # Only bounded enums and opaque ids cross this boundary. Corrections remain in holographic's erased journal.
    allowed = {"useful", "dead_end", "corrected", "unknown"}
    reasons = {"wrong_entity", "stale_version", "irrelevant", "missing_evidence", "answer_error", "other"}
    result = []
    for e in data:
        if e["outcome"] not in allowed or e["reason"] not in reasons \
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", e["event_id"]):
            raise ValueError("Invalid experience event")
        result.append({"event_id": e["event_id"], "outcome": e["outcome"], "reason": e["reason"],
                       "actor": "agent", "human_verified": False, "active": e["event_id"] not in replaced})
    return result


def _refs(value):
    """Fact/version id pairs, as the provider records them; any other shape is a damaged journal."""
    if not isinstance(value, list) or any(
            not isinstance(r, dict) or any(type(r.get(k)) is not int for k in ("fact_id", "version_id"))
            for r in value):
        raise ValueError("Invalid experience attempt")
    return [{"fact_id": r["fact_id"], "version_id": r["version_id"]} for r in value]


def _stages(value):
    """Recorded retrieval stages: a name and its fact refs each. Only these fields cross the boundary."""
    if not isinstance(value, list) or any(not isinstance(s, dict) or not isinstance(s.get("stage"), str)
                                          for s in value):
        raise ValueError("Invalid experience attempt")
    return [{"stage": s["stage"], "facts": _refs(s.get("facts")), "truncated": s.get("truncated") is True}
            for s in value]


def render(report):
    """Short text counterpart to the stable JSON, with explicit unavailable states."""
    lines = ["Memory experience: " + report["status"]]
    if report.get("attempt_id"):
        lines.append("attempt " + report["attempt_id"] + " · " + report["kind"])
        lines.append("trace " + report["trace_status"])
        for stage in report.get("stages", []):
            lines.append(stage["stage"] + ": " + ", ".join(
                f"#{r['fact_id']}@{r['version_id']}" for r in stage["facts"]))
        lines.append("emitted: " + ", ".join(f"#{r['fact_id']}@{r['version_id']}" for r in report["emitted"]))
        for e in report["outcomes"]:
            lines.append(f"{e['outcome']}: {e['reason']} (agent report; active={e['active']})")
    for group in report.get("groups", []):
        lines.append(f"{group['reason']}: {group['attempts']} attempt(s), {group['outcomes']} · {group['status']}")
    return "\n".join(lines)
