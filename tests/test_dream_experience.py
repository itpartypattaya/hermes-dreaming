"""Read-through task reports must not create a second private memory cache."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/dreaming/scripts/dream_experience.py"


class ExperienceReports(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("dream_experience", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "memory.db"
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        # Wire format of the provider's additive experience schema, not a model of its implementation.
        self.conn.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO meta VALUES('experience_schema','1');
        CREATE TABLE memory_attempts(attempt_id TEXT PRIMARY KEY,created_at REAL,kind TEXT,session_key TEXT,
          stages TEXT,emitted TEXT,trace_status TEXT);
        CREATE TABLE memory_outcomes(event_id TEXT PRIMARY KEY,attempt_id TEXT,created_at REAL,
          origin_signal_id TEXT,supersedes TEXT,payload TEXT);
        """)
        self.add_attempt("a1")

    def add_attempt(self, aid):
        self.conn.execute("INSERT INTO memory_attempts VALUES(?,?,?,?,?,?,?)",
                          (aid, time.time(), "search", "opaque-session", "[]", "[]", "not_recorded"))
        self.conn.commit()

    def add_outcome(self, eid, aid="a1", outcome="dead_end", reason="wrong_entity", supersedes=None):
        data = dict(event_id=eid, outcome=outcome, reason=reason, actor="agent", human_verified=False,
                    correction="a private correction", supersedes=supersedes)
        self.conn.execute("INSERT INTO memory_outcomes VALUES(?,?,?,?,?,?)",
                          (eid, aid, time.time(), eid, supersedes, json.dumps(data)))
        self.conn.commit()

    def test_revisions_count_once_and_no_text_is_copied(self):
        self.add_outcome("e1")
        self.add_outcome("e2", outcome="useful", supersedes="e1")
        report = self.module.read_report(self.db)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["groups"][0]["attempts"], 1)
        self.assertEqual(report["groups"][0]["outcomes"], {"useful": 1})
        self.assertNotIn("private correction", json.dumps(report))
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), ["memory.db"])

    def test_repeated_problem_is_not_claimed_true_and_mixed_results_stay_mixed(self):
        self.add_outcome("e1")
        self.add_attempt("a2")
        self.add_outcome("e2", "a2", outcome="useful")
        group = self.module.read_report(self.db)["groups"][0]
        self.assertEqual(group["status"], "mixed_agent_reports")
        self.assertEqual(group["attempts"], 2)
        self.assertNotIn("trust", group)

    def test_forget_or_expiry_invalidates_live_view(self):
        self.add_outcome("e1")
        self.conn.execute("DELETE FROM memory_attempts WHERE attempt_id='a1'")
        self.conn.commit()
        self.assertEqual(self.module.read_report(self.db)["groups"], [])
        self.assertEqual(self.module.read_report(self.db, "a1")["status"], "not_recorded")
        self.add_attempt("old")
        self.add_outcome("e2", "old")
        self.conn.execute("UPDATE memory_attempts SET created_at=0")
        self.conn.commit()
        self.assertEqual(self.module.read_report(self.db)["groups"], [])

    def test_unavailable_is_not_an_empty_success_and_no_file_is_created(self):
        missing = self.db.parent / "missing.db"
        self.assertEqual(self.module.read_report(missing)["status"], "unavailable")
        self.assertFalse(missing.exists())
        self.conn.execute("DELETE FROM meta")
        self.conn.commit()
        self.assertEqual(self.module.read_report(self.db)["status"], "unsupported")

    def test_explain_is_the_original_snapshot_without_replaying_search(self):
        self.add_outcome("e1")
        report = self.module.read_report(self.db, "a1")
        self.assertEqual(report["kind"], "search")
        self.assertEqual(report["trace_status"], "not_recorded")
        self.assertEqual(report["outcomes"][0]["event_id"], "e1")
        self.assertNotIn("private correction", json.dumps(report))

    def test_a_stage_keeps_how_many_candidates_changed_before_the_record(self):
        """holographic 0.21+ writes `changed` on a stage when another writer changed or forgot a candidate between
        retrieval and the record: an empty stage and one whose candidates all changed must not read alike."""
        stages = [{"stage": "lexical_candidates", "facts": [], "truncated": False, "changed": 1},
                  {"stage": "rerank", "facts": [], "truncated": False},
                  {"stage": "bad", "facts": [], "changed": "2"}, {"stage": "worse", "facts": [], "changed": True}]
        self.conn.execute("UPDATE memory_attempts SET stages=? WHERE attempt_id='a1'", (json.dumps(stages),))
        self.conn.commit()
        report = self.module.read_report(self.db, "a1")
        self.assertEqual([s.get("changed") for s in report["stages"]], [1, None, None, None])
        self.assertIn("lexical_candidates: (1 changed before the record)", self.module.render(report))

    def test_actual_cli_reads_only_the_configured_store(self):
        self.add_outcome("e1")
        config = self.db.parent / "dreaming.json"
        config.write_text(json.dumps({"fact_source": "holographic", "fact_store_path": str(self.db)}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT.with_name("dream.py")), "--hermes-home",
            str(self.db.parent), "--config", str(config), "--memory-explain", "a1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcomes"][0]["event_id"], "e1")
        result = subprocess.run([sys.executable, str(SCRIPT.with_name("dream.py")), "--hermes-home",
            str(self.db.parent), "--config", str(config), "--memory-explain", "a1", "--memory-store", "not-approved"],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "unsupported_scope")


if __name__ == "__main__":
    unittest.main()
