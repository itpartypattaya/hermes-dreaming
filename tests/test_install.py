"""Tests of the installer surface: missing databases, the cron-job installer and
the optional core patch. These are the parts another agent touches first, so a
silent failure here costs the whole install."""

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from test_dream import dream  # noqa: E402  (same directory)

# The skill directory: `skills/dreaming/` in the plugin repository, or the
# parent of `tests/` in a copy that keeps the tests next to the skill.
_HERE = Path(__file__).resolve().parents[1]
ROOT = _HERE / "skills" / "dreaming" if (_HERE / "skills" / "dreaming" / "SKILL.md").is_file() else _HERE


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


install_cron = _load("install_cron", ROOT / "scripts/install_cron.py")
install = _load("dream_install", ROOT / "scripts/install.py")


class MissingDatabaseTests(unittest.TestCase):
    """A store the core has not created yet means 'nothing to do', not a crash:
    otherwise a fresh install reports a traceback-flavoured dream_error every
    night until the first fact appears."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "memories").mkdir()
        self._old_home = dream.HOME
        dream.HOME = str(self.home)

    def tearDown(self):
        dream.HOME = self._old_home
        self.tmp.cleanup()

    def test_no_databases_at_all(self):
        result = dream.run(14, 60, 60, str(self.home / "memories" / "MEMORY.md"))
        self.assertEqual(result["stats"]["facts"], 0)
        self.assertEqual(result["stats"]["messages_window"], 0)
        self.assertEqual(result["promotions"], [])

    def test_fact_store_present_but_no_transcripts(self):
        con = sqlite3.connect(self.home / "memory_store.db")
        con.execute("create table facts (fact_id integer primary key, content text,"
                    " category text, tags text, trust_score real, retrieval_count int,"
                    " helpful_count int, created_at timestamp, updated_at timestamp)")
        con.execute("insert into facts values (1,'Тестовый факт про кофе','general','',0.9,0,0,"
                    "'2026-08-01 10:00:00','2026-08-01 10:00:00')")
        con.commit(); con.close()
        result = dream.run(14, 60, 60, str(self.home / "memories" / "MEMORY.md"))
        self.assertEqual(result["stats"]["facts"], 1)
        self.assertEqual(result["stats"]["messages_window"], 0)
        # No corroboration is possible, so nothing is proposed for durable memory.
        self.assertEqual(result["promotions"], [])


class InstallCronTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "scripts").mkdir()
        (self.home / "skills" / "dreaming").mkdir(parents=True)
        self._old_home = install_cron.HOME
        install_cron.HOME = self.home

    def tearDown(self):
        install_cron.HOME = self._old_home
        self.tmp.cleanup()

    def _complete_install(self):
        for name in (install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT):
            (self.home / "scripts" / name).write_text("# gate", encoding="utf-8")
        (self.home / "skills" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")

    def test_preflight_names_every_missing_piece(self):
        problems = install_cron._preflight()
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("dream-precheck.py" in p for p in problems))
        self.assertTrue(any("the skill is not installed" in p for p in problems))

    def test_preflight_finds_the_skill_in_a_category_folder(self):
        """`hermes skills install --category memory` puts it in skills/memory/dreaming."""
        for name in (install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT):
            (self.home / "scripts" / name).write_text("# gate", encoding="utf-8")
        (self.home / "skills" / "memory" / "dreaming").mkdir(parents=True)
        (self.home / "skills" / "memory" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")
        self.assertEqual(install_cron._preflight(), [])

    def _install_plugin(self):
        plugin = self.home / "plugins" / "hermes-dreaming"
        (plugin / "skills" / "dreaming").mkdir(parents=True)
        (plugin / "plugin.json").write_text("{}", encoding="utf-8")
        return plugin

    def test_plugin_skill_wins_over_a_regular_skill(self):
        """Review of the catalog entry: the jobs must run the plugin's own,
        catalog-pinned copy — never some other skill called `dreaming`."""
        self._complete_install()                      # a regular skills/dreaming exists too
        self._install_plugin()
        old = install_cron._plugin_skill_name
        install_cron._plugin_skill_name = lambda: "agent-plugin-hermes-dreaming-0a1b2c3d:dreaming"
        try:
            self.assertEqual(install_cron.skill_ref(),
                             ("agent-plugin-hermes-dreaming-0a1b2c3d:dreaming", None))
            out = io.StringIO()
            old_out, sys.stdout = sys.stdout, out
            try:
                install_cron.main(["--dry-run"])
            finally:
                sys.stdout = old_out
            self.assertIn("agent-plugin-hermes-dreaming-0a1b2c3d:dreaming", out.getvalue())
        finally:
            install_cron._plugin_skill_name = old

    def test_installed_but_disabled_plugin_is_a_problem(self):
        self._complete_install()
        self._install_plugin()
        old = install_cron._plugin_skill_name
        install_cron._plugin_skill_name = lambda: None    # not registered = not enabled
        try:
            problems = install_cron._preflight()
        finally:
            install_cron._plugin_skill_name = old
        self.assertTrue(any("hermes plugins enable hermes-dreaming" in p for p in problems))

    def test_regular_skill_is_the_fallback(self):
        self._complete_install()
        self.assertEqual(install_cron.skill_ref(), ("dreaming", None))

    def test_no_allow_memory_and_no_core_patch(self):
        """Catalog plugins may not modify core; the per-job opt-in of the old
        core patch is gone with it."""
        src = (ROOT / "scripts" / "install_cron.py").read_text(encoding="utf-8")
        self.assertNotIn("allow_memory", src)
        self.assertFalse((ROOT / "scripts" / "patch_cron_memory.py").exists())
        for name in ("cron-job.example.json", "cron-job-extract.example.json"):
            self.assertNotIn("allow_memory", (ROOT / "examples" / name).read_text(encoding="utf-8"))

    def test_preflight_clean_when_installed(self):
        self._complete_install()
        self.assertEqual(install_cron._preflight(), [])

    def test_dry_run_plans_both_jobs_without_importing_hermes(self):
        self._complete_install()
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = install_cron.main(["--dry-run"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn(install_cron.DREAM_SCRIPT, text)
        self.assertIn(install_cron.EXTRACT_SCRIPT, text)

    def test_extraction_can_be_skipped(self):
        self._complete_install()
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            install_cron.main(["--dry-run", "--extract-schedule", ""])
        finally:
            sys.stdout = old
        self.assertNotIn(install_cron.EXTRACT_SCRIPT, out.getvalue())

    def test_prompts_carry_the_rules_that_cost_us_a_night(self):
        # Regression: the anchor rule and the honest-failure rule must be in the
        # prompt the installer writes, not only in SKILL.md.
        self.assertIn("old_text", install_cron.DREAM_PROMPT)
        self.assertIn("current_entries", install_cron.DREAM_PROMPT)
        self.assertIn("Memory is not available", install_cron.DREAM_PROMPT)
        # And extraction must derive dates from the message, not from "today".
        self.assertIn("`t` timestamp", install_cron.EXTRACT_PROMPT)
        # The fill-level gate emits memory_pressure; a prompt that never mentions
        # it wakes the agent with no idea why (live case 24.08: memory at 95 %,
        # nobody told — the limit gates writes silently).
        self.assertIn("memory_pressure", install_cron.DREAM_PROMPT)

    def test_example_cron_prompt_matches_installer(self):
        # Two shipped copies of the same contract drifted once (one phrase).
        # A user who installs via the script must get the documented prompt.
        example = json.loads((ROOT / "examples" / "cron-job.example.json").read_text(encoding="utf-8"))
        self.assertEqual(install_cron.DREAM_PROMPT.strip(), example["prompt"].strip())


class InstallScriptTests(unittest.TestCase):
    """`install.py --check` must be honest on a broken install."""

    def _run(self, home, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = install.main(["--hermes-home", str(home), *argv])
        return rc, out.getvalue()

    def test_check_fails_when_gates_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(tmp, "--check")
            self.assertNotEqual(rc, 0)
            self.assertIn("cron cannot run it", out)
            self.assertFalse((Path(tmp) / "scripts").exists())     # --check changes nothing

    def test_install_copies_gates_and_untouched_config_is_flagged_until_edited(self):
        """The most common half-install: seeded config nobody edited. It must be
        a warning on install AND on --check, and disappear once edited."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(tmp)
            self.assertEqual(rc, 0, out)
            self.assertIn("seeded", out)
            self.assertIn("dry run: fact source: holographic", out)
            for gate in install.GATES:
                self.assertTrue((Path(tmp) / "scripts" / gate).is_file())
            rc, out = self._run(tmp, "--check")
            self.assertEqual(rc, 0, out)
            self.assertIn("still the untouched example", out)
            cfg = Path(tmp) / "dreaming.json"
            data = json.loads(cfg.read_text(encoding="utf-8"))
            data["timezone"] = "Asia/Tokyo"
            cfg.write_text(json.dumps(data), encoding="utf-8")
            rc, out = self._run(tmp, "--check")
            self.assertNotIn("untouched example", out)
            self.assertIn("is valid JSON", out)

    def test_drifted_gate_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            (Path(tmp) / "scripts" / install.GATES[0]).write_text("# old copy", encoding="utf-8")
            rc, out = self._run(tmp, "--check")
            self.assertNotEqual(rc, 0)
            self.assertIn("differs from the skill copy", out)

    def test_generic_source_without_path_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            (Path(tmp) / "dreaming.json").write_text(json.dumps({"fact_source": "jsonl"}),
                                                    encoding="utf-8")
            rc, out = self._run(tmp, "--check")
            self.assertNotEqual(rc, 0)
            self.assertIn("needs fact_store_path", out)


if __name__ == "__main__":
    unittest.main()
