"""3.7.0: repeated problems in the task-outcome summary (`--memory-outcomes`, `"task_outcomes": true`).

The journals are written in the provider's wire format (as in test_dream_experience); the attempt and event ids,
the corrections and the tasks are made up. No real memory is quoted.

Requirements, each with its tests:
- the summary shows problem types by store and reason: one report is one store, named in it ("profile", or the
  key of the one promoted store asked for); two stores are never counted together;
- attempts are counted, not reports: an attempt's revisions (dead_end → corrected → useful) are its history and
  count once, under the reason of its active outcome, even when an earlier revision gave another reason;
- one attempt is shown as a single report, two attempts with the same result as repeated reports, different
  results as mixed — none of them as a verdict on a fact: the summary carries counts, reason and outcome codes
  only, no fact id, no correction or task text.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from test_dream import ROOT

DREAM = ROOT / "scripts" / "dream.py"
JOURNAL = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
INSERT INTO meta VALUES('experience_schema','1');
CREATE TABLE memory_attempts(attempt_id TEXT PRIMARY KEY,created_at REAL,kind TEXT,session_key TEXT,
  stages TEXT,emitted TEXT,trace_status TEXT);
CREATE TABLE memory_outcomes(event_id TEXT PRIMARY KEY,attempt_id TEXT,created_at REAL,
  origin_signal_id TEXT,supersedes TEXT,payload TEXT);
"""
CORRECTION = "Луна перепутала ветку: рабочие задачи идут в 4410"
TASK = "Ответить, куда писать рабочие задачи"
STATUSES = {"single_agent_report", "repeated_agent_reports", "mixed_agent_reports"}


class Journal:
    """One store's experience journal, as the provider writes it."""

    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as con:
            con.executescript(JOURNAL)
        con.close()

    def attempt(self, aid, *outcomes):
        """An attempt and its outcome history: (event id, outcome, reason), each revising the one before."""
        con = sqlite3.connect(self.path)
        con.execute("INSERT INTO memory_attempts VALUES(?,?,?,?,?,?,?)",
                    (aid, time.time(), "search", "opaque-session", "[]", "[]", "not_recorded"))
        before = None
        for eid, outcome, reason in outcomes:
            data = dict(event_id=eid, outcome=outcome, reason=reason, actor="agent", human_verified=False,
                        correction=CORRECTION, task=TASK, supersedes=before)
            con.execute("INSERT INTO memory_outcomes VALUES(?,?,?,?,?,?)",
                        (eid, aid, time.time(), f"{aid}:{eid}", before, json.dumps(data, ensure_ascii=False)))
            before = eid
        con.commit()
        con.close()


class RepeatedProblemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.profile = Journal(self.home / "memory_store.db")

    def outcomes(self, *extra, scope=None):
        config = self.home / "dreaming.json"
        cfg = {"fact_source": "holographic", "fact_store_path": str(self.profile.path)}
        if scope:
            (self.home / "config.yaml").write_text(
                f"memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n    scope: {scope}\n",
                encoding="utf-8")
            cfg["chat_stores"] = {"read": True, "promote_from": ["telegram:-100777", "telegram:-100888"]}
        config.write_text(json.dumps(cfg), encoding="utf-8")
        run = subprocess.run([sys.executable, str(DREAM), "--hermes-home", str(self.home), "--config", str(config),
                              "--memory-outcomes", *extra], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        for private in (CORRECTION, TASK, "opaque-session"):
            self.assertNotIn(private, run.stdout)
        return json.loads(run.stdout)

    def test_the_summary_names_its_store(self):
        self.profile.attempt("p1", ("p1-e1", "dead_end", "stale_version"))
        report = self.outcomes()
        self.assertEqual(report["store"], "profile")
        self.assertEqual([(g["reason"], g["attempts"]) for g in report["groups"]], [("stale_version", 1)])

    def test_two_stores_are_never_counted_together(self):
        """Two promoted chats, each with its journal: a report reads the one asked for and says which."""
        chats = self.home / "holographic-chats"
        chats.mkdir()
        first, second = Journal(chats / "telegram--100777.db"), Journal(chats / "telegram--100888.db")
        first.attempt("a1", ("a1-e1", "dead_end", "wrong_entity"))
        second.attempt("b1", ("b1-e1", "dead_end", "wrong_entity"))
        second.attempt("b2", ("b2-e1", "dead_end", "wrong_entity"))
        report = self.outcomes("--memory-store", "telegram:-100777", scope="chat")
        self.assertEqual(report["store"], "telegram--100777")
        self.assertEqual(report["groups"], [{"reason": "wrong_entity", "attempts": 1, "outcomes": {"dead_end": 1},
                                             "status": "single_agent_report"}])
        report = self.outcomes("--memory-store", "telegram:-100888", scope="chat")
        self.assertEqual(report["store"], "telegram--100888")
        self.assertEqual(report["groups"], [{"reason": "wrong_entity", "attempts": 2, "outcomes": {"dead_end": 2},
                                             "status": "repeated_agent_reports"}])

    def test_revisions_are_one_attempt_under_the_reason_it_ended_with(self):
        """Regression/coverage (passes on 3.6.2): three revisions with two reasons are one attempt, not three
        signals and not a stale_version problem."""
        self.profile.attempt("p1", ("p1-e1", "dead_end", "stale_version"), ("p1-e2", "corrected", "wrong_entity"),
                             ("p1-e3", "useful", "wrong_entity"))
        self.assertEqual(self.outcomes()["groups"], [
            {"reason": "wrong_entity", "attempts": 1, "outcomes": {"useful": 1}, "status": "single_agent_report"}])

    def test_single_repeated_and_mixed_stay_agent_reports(self):
        """Regression/coverage (passes on 3.6.2): one attempt is single, two alike are repeated, two different are
        mixed; the summary holds codes and counts only, no fact and no verdict."""
        self.profile.attempt("p1", ("p1-e1", "dead_end", "irrelevant"))
        self.profile.attempt("p2", ("p2-e1", "dead_end", "missing_evidence"))
        self.profile.attempt("p3", ("p3-e1", "dead_end", "missing_evidence"))
        self.profile.attempt("p4", ("p4-e1", "dead_end", "wrong_entity"))
        self.profile.attempt("p5", ("p5-e1", "dead_end", "stale_version"), ("p5-e2", "useful", "wrong_entity"))
        groups = self.outcomes()["groups"]
        self.assertEqual(groups, [
            {"reason": "irrelevant", "attempts": 1, "outcomes": {"dead_end": 1}, "status": "single_agent_report"},
            {"reason": "missing_evidence", "attempts": 2, "outcomes": {"dead_end": 2},
             "status": "repeated_agent_reports"},
            {"reason": "wrong_entity", "attempts": 2, "outcomes": {"dead_end": 1, "useful": 1},
             "status": "mixed_agent_reports"},
        ])
        for g in groups:
            self.assertEqual(set(g), {"reason", "attempts", "outcomes", "status"})
            self.assertIn(g["status"], STATUSES)


if __name__ == "__main__":
    unittest.main()
