"""The holographic store the dream reads cannot fill up with `scope: user / chat / thread` or `cron_writes: false`
(2.5.3): the report, the diary, `install.py --check` and the extraction gate say so instead of staying silent.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import contextlib
import io
import json
import unittest

from test_dream import DreamFixture, dream, precheck  # noqa: E402
from test_extract_precheck import extract  # noqa: E402

CONFIG = "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n{body}"


class StoreScopeTests(DreamFixture):
    def setUp(self):
        super().setUp()
        self._cfg = dream.SETTINGS.CONFIG
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="holographic"))

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

    def test_odd_plugins_shapes_read_as_unset(self):
        for body in ("plugins:\n  - holographic\n", "plugins:\n  hermes-memory-store: on\n",
                     "plugins:\n  hermes-memory-store:\n    scope:\n      - chat\n", "plugins: []\n"):
            with self.subTest(body=body):
                (self.home / "config.yaml").write_text("memory:\n  provider: holographic\n" + body, encoding="utf-8")
                self.assertIsNone(dream._hermes_plugin_setting("scope"))
                self.assertIsNone(dream.fact_store_note())

    def test_cron_writes_false_is_named(self):
        self._config("    cron_writes: false\n")
        self.assertIn("cron_writes", dream.fact_store_note())

    def test_other_fact_sources_are_quiet(self):
        self._config("    scope: chat\n")
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="none"))
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


class ProviderTrustFloorTests(DreamFixture):
    """`trust_feedback` measures by the floor the provider itself measures by.

    holographic recalls nothing below its `min_trust_threshold` — not by words, not
    structurally, not by meaning (0.14: `by_meaning(..., min_trust=...)`). That
    threshold is configurable, while the pass and the prompt before 2.7.1 named a
    hard-coded 0.3. A Codex review of 2.7.1 found five ways to end up with the wrong
    floor — each one is pinned here.
    """

    FACT = "Марина ходит на утренние тренировки по вторникам в зале у дома"

    def setUp(self):
        super().setUp()
        self._cfg = dream.SETTINGS.CONFIG
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="holographic"))

    def tearDown(self):
        dream.configure(self._cfg)
        super().tearDown()

    def _config(self, body, home=None):
        (home or self.home).joinpath("config.yaml").write_text(CONFIG.format(body=body), encoding="utf-8")

    def _gates(self, **gates):
        dream.configure(dict(dream.SETTINGS.CONFIG, fact_source="holographic",
                             gates=dict(dream.DEFAULT_CONFIG["gates"], **gates)))

    def _items(self, trust):
        suppressed = [{"fact_id": 7, "content": self.FACT, "trust_score": trust}]
        return dream.trust_feedback_items(suppressed, {}, "holographic")

    def test_provider_floor_is_used_when_ours_is_unset(self):
        self._config("    min_trust_threshold: 0.5\n")
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.5)

    def test_a_fact_below_the_provider_floor_is_not_offered(self):
        self._config("    min_trust_threshold: 0.5\n")
        self.assertEqual(self._items(0.4), [], "below the floor recall will not surface it anyway")
        items = self._items(0.6)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["floor"], 0.5, "the floor travels to the agent in the item itself")

    def test_our_explicit_value_wins_over_the_provider(self):
        self._config("    min_trust_threshold: 0.5\n")
        self._gates(feedback_trust_floor=0.2)
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.2)

    def test_a_bad_cap_keeps_an_explicit_floor(self):
        """2.7.1: a `feedback_cap` that failed to parse reset the floor too — an explicit 0.2
        became the provider's 0.9."""
        self._config("    min_trust_threshold: 0.9\n")
        self._gates(feedback_trust_floor=0.2, feedback_cap="bad")
        self.assertEqual(dream.SETTINGS.FEEDBACK_CAP, 5)
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.2)

    def test_an_out_of_range_override_is_no_override(self):
        self._config("    min_trust_threshold: 0.5\n")
        for bad in (2.0, -0.1, "abc"):
            with self.subTest(bad=bad):
                self._gates(feedback_trust_floor=bad)
                self.assertFalse(dream.SETTINGS.FEEDBACK_FLOOR_EXPLICIT)
                self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.5)

    def test_other_sources_keep_our_default(self):
        self._config("    min_trust_threshold: 0.9\n")
        self.assertEqual(dream.feedback_floor("sqlite", str(self.home)), 0.3)

    def test_unparsable_provider_value_falls_back_to_our_default(self):
        """A config we failed to read must not turn the section into "offer everything"."""
        for body in ("    min_trust_threshold: abc\n", "    min_trust_threshold: 1.5\n",
                     "    min_trust_threshold:\n", "    scope: profile\n"):
            with self.subTest(body=body.strip()):
                self._config(body)
                self.assertIsNone(dream.holographic_min_trust(str(self.home)))
                self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.3)

    def test_nested_keys_and_a_list_are_not_the_setting(self):
        """2.7.1: parsed YAML without a direct key fell through to the line scanner, which found
        `store.diagnostics.min_trust_threshold`. A floor set too high hides facts recall does surface."""
        for body in ("    diagnostics:\n      min_trust_threshold: 0.9\n",
                     "    - min_trust_threshold: 0.9\n"):
            with self.subTest(body=body.strip()):
                self._config(body)
                self.assertIsNone(dream.holographic_min_trust(str(self.home)))

    def test_the_scanner_without_pyyaml(self):
        """Without PyYAML: a last line with no newline and the flow form are valid YAML the
        old scanner skipped; a nested key is still not the setting."""
        saved = dream._hermes_config_parsed
        dream._hermes_config_parsed = lambda home=None: ({}, False)
        try:
            cases = {
                "memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n"
                "    min_trust_threshold: 0.4": "0.4",
                "plugins:\n  hermes-memory-store: {scope: chat, min_trust_threshold: 0.4}\n": "0.4",
                "plugins:\n  hermes-memory-store:\n    diagnostics:\n      min_trust_threshold: 0.9\n": None,
                "plugins:\n  hermes-memory-store:\n    scope: chat\n    min_trust_threshold: \"0.45\"\n": "0.45",
            }
            for text, want in cases.items():
                with self.subTest(text=text):
                    (self.home / "config.yaml").write_text(text, encoding="utf-8")
                    self.assertEqual(dream._hermes_plugin_setting("min_trust_threshold", str(self.home)), want)
        finally:
            dream._hermes_config_parsed = saved

    def test_the_floor_follows_the_config_between_runs_and_homes(self):
        """2.7.1 kept the floor in a module cache shared by every home and every run."""
        other = self.home / "other-home"
        other.mkdir()
        self._config("    min_trust_threshold: 0.5\n")
        self._config("    min_trust_threshold: 0.7\n", home=other)
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.5)
        self.assertEqual(dream.feedback_floor("holographic", str(other)), 0.7)
        first = dream.run(14, 60, 60, str(self.mem_md))["gates"]["feedback_trust_floor"]
        self._config("    min_trust_threshold: 0.6\n")
        second = dream.run(14, 60, 60, str(self.mem_md))["gates"]["feedback_trust_floor"]
        self.assertEqual((first, second), (0.5, 0.6))

    def test_reconfigure_drops_an_override(self):
        self._config("    min_trust_threshold: 0.5\n")
        self._gates(feedback_trust_floor=0.2)
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.2)
        self._gates()
        self.assertEqual(dream.feedback_floor("holographic", str(self.home)), 0.5)

    def test_the_floor_reaches_the_agent(self):
        """The agent's payload carries no `gates` — the floor has to travel in the items."""
        data = {"stats": {"promotions": 1},
                "promotions": [{"fact_id": 1, "content": "Проект Аврора переезжает в сентябре"}],
                "trust_feedback": [{"fact_id": 7, "content": self.FACT, "trust": 0.6, "floor": 0.45}]}
        compact = precheck.compact_payload(data)
        self.assertNotIn("gates", compact)
        self.assertEqual(compact["trust_feedback"][0]["floor"], 0.45)


class HolographicSettingsCheckTests(DreamFixture):
    """`install.py --check` on the holographic 0.14 settings: search by meaning, memory mirror, floor."""

    def _check(self, body, dreaming=None, raw=None):
        from test_install import install
        path = self.home / "config.yaml"
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(CONFIG.format(body=body), encoding="utf-8")
        (self.home / "dreaming.json").write_text(
            json.dumps(dreaming or {"fact_source": "holographic"}), encoding="utf-8")
        mod = install._load_dream(self.home, self.home / "dreaming.json")
        report = install.Report()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install._check_holographic_settings(self.home, mod, report)
        return out.getvalue(), report

    def test_search_by_meaning_is_named_as_the_provider_s_business(self):
        out, report = self._check('    embeddings: "on"\n')
        self.assertIn("search by meaning is on", out)
        self.assertIn("reads words only", out)
        self.assertEqual(report.warn_n, 0, "information, not a problem")

    def test_memory_mirror_is_a_warning(self):
        out, report = self._check("    mirror_memory_writes: true\n")
        self.assertIn("mirror_memory_writes", out)
        self.assertIn("promotions come back as new facts", out)
        self.assertGreaterEqual(report.warn_n, 1)

    def test_a_non_default_floor_is_named(self):
        out, _ = self._check("    min_trust_threshold: 0.5\n")
        self.assertIn("min_trust_threshold is 0.5", out)
        self.assertIn("uses that floor", out)

    def test_an_explicit_override_is_named_not_the_provider(self):
        """2.7.1 said "trust_feedback uses that floor" even when dreaming.json overrode it."""
        out, _ = self._check("    min_trust_threshold: 0.9\n",
                             dreaming={"fact_source": "holographic", "gates": {"feedback_trust_floor": 0.2}})
        self.assertIn("overrides it", out)
        self.assertIn("0.2", out)
        self.assertNotIn("uses that floor", out)

    def test_nested_settings_are_not_read(self):
        out, report = self._check("    advanced:\n      embeddings: \"on\"\n      mirror_memory_writes: true\n")
        self.assertEqual(out.strip(), "")
        self.assertEqual(report.warn_n, 0)

    def test_a_config_that_is_not_utf8_does_not_crash(self):
        out, report = self._check(None, raw=b"plugins:\n  hermes-memory-store:\n    embeddings: \xff\xfe on\n")
        self.assertEqual(out.strip(), "")
        self.assertEqual(report.warn_n, 0)

    def test_defaults_stay_quiet(self):
        out, report = self._check('    embeddings: "off"\n    mirror_memory_writes: false\n')
        self.assertEqual(out.strip(), "")
        self.assertEqual(report.warn_n, 0)


if __name__ == "__main__":
    unittest.main()
