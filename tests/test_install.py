"""Tests of the installer surface: missing databases, the cron-job installer and
the optional core patch. These are the parts another agent touches first, so a
silent failure here costs the whole install."""

import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_dream import dream  # noqa: E402  (same directory)

ROOT = Path(__file__).parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


install_cron = _load("install_cron", ROOT / "scripts/install_cron.py")
patch_cron = _load("patch_cron_memory", ROOT / "scripts/patch_cron_memory.py")


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
        self.assertTrue(any("SKILL.md" in p for p in problems))

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


class PatchCronMemoryTests(unittest.TestCase):
    """The optional core patch: idempotent, reversible, and refusing to touch a
    file it does not recognise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sched = Path(self.tmp.name) / "cron" / "scheduler.py"
        self.sched.parent.mkdir(parents=True)
        self.sched.write_text(
            "def run_job(job):\n"
            "    return start(\n"
            + patch_cron.OLD + "\n"
            "    )\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_then_idempotent_then_revert(self):
        self.assertEqual(patch_cron.main([str(self.sched), "--check"]), 1)
        self.assertEqual(patch_cron.main([str(self.sched)]), 0)
        text = self.sched.read_text(encoding="utf-8")
        self.assertIn("allow_memory", text)
        self.assertTrue(self.sched.with_suffix(".py.bak").exists())
        self.assertEqual(patch_cron.main([str(self.sched), "--check"]), 0)
        self.assertEqual(patch_cron.main([str(self.sched)]), 0)          # second run: no-op
        self.assertEqual(patch_cron.main([str(self.sched), "--revert"]), 0)
        self.assertNotIn("allow_memory", self.sched.read_text(encoding="utf-8"))

    def test_unknown_file_is_refused(self):
        self.sched.write_text("def run_job(job):\n    return start(skip_memory=False)\n",
                              encoding="utf-8")
        self.assertEqual(patch_cron.main([str(self.sched)]), 2)
        self.assertNotIn("allow_memory", self.sched.read_text(encoding="utf-8"))

    def test_directory_argument_resolves_to_scheduler(self):
        self.assertEqual(patch_cron.main([str(self.sched.parents[1]), "--check"]), 1)

    def test_missing_target_reports_cleanly(self):
        self.assertEqual(patch_cron.main([str(self.sched.parent / "nope.py"), "--check"]), 2)


class InstallScriptTests(unittest.TestCase):
    """`install.sh --check` must be honest on a broken install (no bash on
    Windows — skipped there)."""

    @unittest.skipUnless(os.name == "posix", "bash installer is POSIX-only")
    def test_check_fails_when_gates_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HERMES_HOME=tmp)
            proc = subprocess.run(["bash", str(ROOT / "install.sh"), "--check"],
                                  capture_output=True, text=True, env=env)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cron cannot run it", proc.stdout)

    @unittest.skipUnless(os.name == "posix", "bash installer is POSIX-only")
    def test_untouched_example_config_is_flagged_until_edited(self):
        """The most common half-install: seeded config nobody edited. It must be
        a warning on install AND on --check, and disappear once edited."""
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HERMES_HOME=tmp)
            first = subprocess.run(["bash", str(ROOT / "install.sh")],
                                   capture_output=True, text=True, env=env)
            self.assertEqual(first.returncode, 0, first.stdout)
            self.assertIn("seeded", first.stdout)
            check = subprocess.run(["bash", str(ROOT / "install.sh"), "--check"],
                                   capture_output=True, text=True, env=env)
            self.assertEqual(check.returncode, 0, check.stdout)
            self.assertIn("still the untouched example", check.stdout)
            cfg = Path(tmp) / "dreaming.json"
            data = json.loads(cfg.read_text(encoding="utf-8"))
            data["timezone"] = "Asia/Tokyo"
            cfg.write_text(json.dumps(data), encoding="utf-8")
            edited = subprocess.run(["bash", str(ROOT / "install.sh"), "--check"],
                                    capture_output=True, text=True, env=env)
            self.assertNotIn("untouched example", edited.stdout)
            self.assertIn("is valid JSON", edited.stdout)


if __name__ == "__main__":
    unittest.main()
