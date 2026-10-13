"""The holographic store the dream reads cannot fill up with `scope: user / chat / thread` or `cron_writes: false`
(2.5.3): the report, the diary, `install.py --check` and the extraction gate say so instead of staying silent.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import contextlib
import io
import json
import unittest

from test_dream import DreamFixture, dream  # noqa: E402
from test_extract_precheck import extract  # noqa: E402

CONFIG = "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n{body}"


class StoreScopeTests(DreamFixture):
    def setUp(self):
        super().setUp()
        self._cfg = dream.CONFIG
        dream.configure(dict(dream.CONFIG, fact_source="holographic"))

    def tearDown(self):
        dream.configure(self._cfg)
        super().tearDown()

    def _config(self, body):
        (self.home / "config.yaml").write_text(CONFIG.format(body=body), encoding="utf-8")

    def test_profile_scope_is_quiet(self):
        self._config("    db_path: memory_store.db\n")
        self.assertIsNone(dream.fact_store_note())
        self._config("    scope: profile\n")
        self.assertIsNone(dream.fact_store_note())

    def test_split_scopes_are_named(self):
        for scope in ("user", "chat", "thread"):
            with self.subTest(scope=scope):
                self._config(f"    scope: {scope}\n")
                note = dream.fact_store_note()
                self.assertIn(f"scope: {scope}", note)
                self.assertIn("fact_source", note)

    def test_cron_writes_false_is_named(self):
        self._config("    cron_writes: false\n")
        self.assertIn("cron_writes", dream.fact_store_note())

    def test_other_fact_sources_are_quiet(self):
        self._config("    scope: chat\n")
        dream.configure(dict(dream.CONFIG, fact_source="none"))
        self.assertIsNone(dream.fact_store_note())

    def test_note_reaches_the_report_and_the_diary(self):
        self._config("    scope: chat\n")
        result = dream.run(14, 60, 60, str(self.mem_md))
        self.assertIn("scope: chat", result["fact_store_note"])
        self.assertIn("scope: chat", result["diary"])
        self.assertIn("note: holographic runs with scope: chat", dream.dry_run_summary(result, "holographic"))
        self.assertEqual(result["stats"]["alerts"], 0)  # information, not work: the gate stays shut

    def test_install_check_warns(self):
        from test_install import install
        self._config("    scope: thread\n")
        (self.home / "dreaming.json").write_text(json.dumps({"fact_source": "holographic"}), encoding="utf-8")
        mod = install._load_dream(self.home, self.home / "dreaming.json")
        report = install.Report()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.check_fact_source(self.home, mod, report)
        self.assertIn("scope: thread", out.getvalue())
        self.assertGreaterEqual(report.warn_n, 1)

    def test_extraction_gate_stops_and_keeps_the_cursor(self):
        self._config("    scope: chat\n")
        for i in range(10):
            self.add_message(f"Сообщение {i} про утренний кофе", days_ago=1)
        state = self.home / "cache" / "dream-extract-state.json"
        saved = (extract._dream, extract.STATE)
        extract._dream, extract.STATE = (lambda: dream), state
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                extract.main()
        finally:
            extract._dream, extract.STATE = saved
        self.assertEqual(json.loads(out.getvalue().strip().splitlines()[-1]), {"wakeAgent": False})
        self.assertIn("scope: chat", err.getvalue())
        self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
