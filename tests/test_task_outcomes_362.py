"""3.6.2: task outcomes end to end, against a store holographic itself wrote (>= 0.20.0, experience journal).

Synthetic household as in test_duplicate_night_340 (Виктор, Ольга, Лена, the agent Луна); no real memory is quoted.

Requirements, each with its tests:
- with `"task_outcomes": true` the nightly pass adds a summary of the agent's task reports: counts per reason and
  outcome, one active outcome per attempt (a revision counts once), and nothing else — no correction, no task text,
  no attempt / event id, no session key, in the result or in any file the pass writes;
- the summary is live: a fact the provider forgets takes its attempts and their outcomes out of the next night's
  summary (the provider's own delete triggers, not a copy kept by the dream);
- the nightly gate does not hand the summary to the agent (it is no work), so the job prompt need not describe it;
- `--memory-format text` prints the summary and an attempt's explanation as short lines, without correction text;
- an attempt row of a shape the reader does not know (a damaged or foreign journal) is `unavailable`, in JSON and in
  text, with exit 1 and no traceback — the same as a damaged outcome row.

The provider is HOLOGRAPHIC_PROVIDER (its directory), else the directory of HOLOGRAPHIC_SRC when that one has the
experience journal; without one the tests skip. CI runs them against a pinned provider release.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest

from test_dream import ROOT, dream, precheck
from test_duplicate_night_340 import BRIDGE, CALLS, NightBase

DREAM = ROOT / "scripts" / "dream.py"
SESSION = "telegram:-1009876543210:4410"
CORRECTION = "Луна перепутала: мост ведёт к Claude Code Виктора, а не Ольги"
CORRECTION_2 = "Правильный ответ — ветка 4410, созвоны Ольги утром"
TASK = "Ответить Ольге, куда писать рабочие задачи"


def _provider_dir():
    explicit = os.environ.get("HOLOGRAPHIC_PROVIDER")
    if explicit:
        if not (Path(explicit) / "experience.py").is_file():
            raise FileNotFoundError(f"HOLOGRAPHIC_PROVIDER={explicit} is no holographic >= 0.20.0 (no experience.py)")
        return Path(explicit)
    src = os.environ.get("HOLOGRAPHIC_SRC")
    if src and (Path(src).parent / "experience.py").is_file():
        return Path(src).parent
    return None


class TaskOutcomeNightTests(NightBase):
    """The store module is loaded without the plugin's `__init__` (which needs Hermes core); the core's WAL helper,
    which only sets the journal mode, is stood in for — as in test_duplicate_review_340.ProviderMergeTests."""

    PACKAGE = "_holo_provider_experience"

    @classmethod
    def setUpClass(cls):
        where = _provider_dir()
        if where is None:
            raise unittest.SkipTest("no holographic >= 0.20.0 at hand: set HOLOGRAPHIC_PROVIDER to its directory")
        cls._saved = {k: sys.modules.get(k) for k in ("hermes_state_wal", cls.PACKAGE)}
        wal = types.ModuleType("hermes_state_wal")
        wal.apply_wal_with_fallback = lambda conn, db_label=None: conn.execute("PRAGMA journal_mode=WAL")
        sys.modules.setdefault("hermes_state_wal", wal)
        pkg = types.ModuleType(cls.PACKAGE)
        pkg.__path__ = [str(where)]
        sys.modules[cls.PACKAGE] = pkg
        cls.store_mod = importlib.import_module(cls.PACKAGE + ".store")

    @classmethod
    def tearDownClass(cls):
        for name in [m for m in sys.modules if m.startswith(cls.PACKAGE)]:
            del sys.modules[name]
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)

    def setUp(self):
        super().setUp()
        self.db.unlink()  # the fixture's tables: the provider makes its own
        self.store = self.store_mod.MemoryStore(db_path=str(self.db), hrr_dim=64)
        dream.configure(dict(dream.SETTINGS.CONFIG, task_outcomes=True))
        self.bridge = self.store.add_fact(BRIDGE[0])
        self.calls = self.store.add_fact(CALLS[0])
        # A search that returned the bridge fact; the agent first called it a dead end, then corrected itself.
        self.a1 = self.attempt([(self.bridge, BRIDGE[0])])
        self.store.record_outcome(self.a1, "ev-bridge-1", "dead_end", "wrong_entity", correction=CORRECTION,
                                  task=TASK, session_key=SESSION)
        self.store.record_outcome(self.a1, "ev-bridge-2", "corrected", "wrong_entity", correction=CORRECTION_2,
                                  task=TASK, session_key=SESSION, supersedes="ev-bridge-1")
        # Another search, over Ольга's calls: a stale version, said once.
        self.a2 = self.attempt([(self.calls, CALLS[0])])
        self.store.record_outcome(self.a2, "ev-calls-1", "dead_end", "stale_version", task=TASK,
                                  session_key=SESSION)

    def tearDown(self):
        self.store.close()
        super().tearDown()

    def attempt(self, facts):
        found = [{"fact_id": fid, "content": text} for fid, text in facts]
        return self.store.record_attempt("search", found, [("fts", found), ("rerank", found[:1])], SESSION,
                                         trace=True)

    def private_strings(self):
        return (CORRECTION, CORRECTION_2, TASK, SESSION, self.a1, self.a2, "ev-bridge-1", "ev-bridge-2", "ev-calls-1")

    def files_the_pass_wrote(self):
        for path in sorted(self.home.rglob("*")):
            if path.is_file() and not path.name.startswith(("memory_store.db", "state.db")):
                yield path

    def test_the_night_counts_reports_and_copies_no_text(self):
        result = self.night()
        outcomes = result["task_outcomes"]
        self.assertEqual(outcomes["status"], "ok")
        self.assertEqual(outcomes["groups"], [
            {"reason": "stale_version", "attempts": 1, "outcomes": {"dead_end": 1}, "status": "single_agent_report"},
            {"reason": "wrong_entity", "attempts": 1, "outcomes": {"corrected": 1}, "status": "single_agent_report"},
        ])
        text = json.dumps(result, ensure_ascii=False)
        for private in self.private_strings():
            self.assertNotIn(private, text)
        for key in ("event_id", "attempt_id", "correction", "task", "session_key"):
            self.assertNotIn(f'"{key}"', json.dumps(outcomes))
        written = list(self.files_the_pass_wrote())
        self.assertTrue(any(p.name == "dream-seen.json" for p in written), [p.name for p in written])
        for path in written:
            data = path.read_bytes().decode("utf-8", "replace")
            for private in self.private_strings():
                self.assertNotIn(private, data, f"{path.name} keeps a copy")

    def test_a_forgotten_fact_takes_its_reports_out_of_the_next_night(self):
        self.night()
        self.assertTrue(self.store.remove_fact(self.bridge))  # the provider's forget, with its triggers
        groups = self.night(days=1)["task_outcomes"]["groups"]
        self.assertEqual([g["reason"] for g in groups], ["stale_version"])

    def test_the_gate_does_not_hand_the_summary_to_the_agent(self):
        result = self.night()
        stats = dict(result["stats"], new_facts=1)  # work for the agent tonight, whatever the facts scored
        payload = precheck.compact_payload(dict(result, stats=stats), actionable_keys=("new_facts",))
        self.assertIsNotNone(payload)
        self.assertNotIn("task_outcomes", payload)

    # --- the manual commands ------------------------------------------------------------------------------------
    def cli(self, *args):
        config = self.home / "dreaming.json"
        config.write_text(json.dumps({"fact_source": "holographic", "fact_store_path": str(self.db)}),
                          encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(DREAM), "--hermes-home", str(self.home), "--config", str(config),
                               *args], capture_output=True, text=True, encoding="utf-8", env=env)

    def version(self, fid):
        return self.sql("select version_id from fact_versions where fact_id = ? and status = 'current'", (fid,))[0][0]

    def test_text_format_prints_the_summary_and_an_explanation(self):
        summary = self.cli("--memory-outcomes", "--memory-format", "text")
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(summary.stdout.splitlines(), [
            "Memory experience: ok",
            "stale_version: 1 attempt(s), {'dead_end': 1} · single_agent_report",
            "wrong_entity: 1 attempt(s), {'corrected': 1} · single_agent_report",
        ])
        explained = self.cli("--memory-explain", self.a1, "--memory-format", "text")
        self.assertEqual(explained.returncode, 0, explained.stderr)
        ref = f"#{self.bridge}@{self.version(self.bridge)}"
        self.assertEqual(explained.stdout.splitlines(), [
            "Memory experience: ok",
            f"attempt {self.a1} · search",
            "trace recorded",
            f"fts: {ref}",
            f"rerank: {ref}",
            f"emitted: {ref}",
            "dead_end: wrong_entity (agent report; active=False)",
            "corrected: wrong_entity (agent report; active=True)",
        ])
        for private in (CORRECTION, CORRECTION_2, TASK, SESSION):
            self.assertNotIn(private, summary.stdout + explained.stdout)

    def test_an_attempt_of_an_unknown_shape_is_unavailable_not_a_traceback(self):
        # The journal row as a writer that stored the in-memory (name, facts) pairs would leave it, and a row whose
        # stages field is cut off: the reader knows neither shape.
        for stages in (json.dumps([["fts", [[self.bridge, self.version(self.bridge)]]]]), json.dumps([{"stage": "fts"}]),
                       json.dumps({"fts": []})):
            with self.subTest(stages=stages):
                self.sql("update memory_attempts set stages = ? where attempt_id = ?", (stages, self.a1))
                for fmt in ("text", "json"):
                    run = self.cli("--memory-explain", self.a1, "--memory-format", fmt)
                    self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
                    if fmt == "text":
                        self.assertEqual(run.stdout.splitlines()[0], "Memory experience: unavailable")
                    else:
                        self.assertEqual(json.loads(run.stdout)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
