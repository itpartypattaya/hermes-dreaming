"""Tests of the installer surface: missing databases, the cron-job installer and
`install.py --check`. These are the parts another agent touches first, so a
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

PLUGIN_SKILL = "agent-plugin-hermes-dreaming-0a1b2c3d:dreaming"


@contextlib.contextmanager
def fake_registry(entries=None, error=None):
    """Stand-in for Hermes' `hermes_cli.plugins`: `entries` maps a registered
    skill name to the SKILL.md it serves; `error` makes discovery fail the way
    a missing dependency does under the wrong interpreter."""
    import types
    entries = dict(entries or {})

    class Manager:
        def list_plugin_skill_metadata(self):
            return [{"name": name} for name in sorted(entries)]

        def find_plugin_skill(self, name):
            return Path(entries[name]) if name in entries else None

    def discover_plugins(force=False):
        if error is not None:
            raise error

    pkg, mod = types.ModuleType("hermes_cli"), types.ModuleType("hermes_cli.plugins")
    mod.discover_plugins, mod.get_plugin_manager = discover_plugins, Manager
    pkg.plugins = mod
    saved = {k: sys.modules.get(k) for k in ("hermes_cli", "hermes_cli.plugins")}
    sys.modules.update({"hermes_cli": pkg, "hermes_cli.plugins": mod})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class FakeJobs:
    """The slice of Hermes' `cron.jobs` the installer calls."""

    def __init__(self, listed=()):
        self.listed, self.created, self.updated = list(listed), [], []

    def list_jobs(self, include_disabled=False):
        return [j for j in self.listed if include_disabled or j.get("enabled", True)]

    def create_job(self, **kw):
        self.created.append(kw)
        return {"id": f"new{len(self.created)}"}

    def update_job(self, job_id, updates):
        self.updated.append((job_id, updates))
        return {"id": job_id}


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
        for name in (install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT, install_cron.SWEEP_SCRIPT):
            (self.home / "scripts" / name).write_text("# gate", encoding="utf-8")
        (self.home / "skills" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")

    def test_preflight_names_every_missing_piece(self):
        problems = install_cron._preflight()
        self.assertEqual(len(problems), 4)
        self.assertTrue(any("dream-precheck.py" in p for p in problems))
        self.assertTrue(any("dream-forget-sweep.py" in p for p in problems))
        self.assertTrue(any("the skill is not installed" in p for p in problems))
        self.assertEqual(len(install_cron._preflight(sweep=False)), 3)

    def test_preflight_finds_the_skill_in_a_category_folder(self):
        """`hermes skills install --category memory` puts it in skills/memory/dreaming."""
        for name in (install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT, install_cron.SWEEP_SCRIPT):
            (self.home / "scripts" / name).write_text("# gate", encoding="utf-8")
        (self.home / "skills" / "memory" / "dreaming").mkdir(parents=True)
        (self.home / "skills" / "memory" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")
        self.assertEqual(install_cron._preflight(), [])

    def _install_plugin(self):
        plugin = self.home / "plugins" / "hermes-dreaming"
        (plugin / "skills" / "dreaming").mkdir(parents=True)
        (plugin / "skills" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")
        (plugin / "plugin.json").write_text("{}", encoding="utf-8")
        return plugin / "skills" / "dreaming" / "SKILL.md"

    def test_plugin_skill_wins_over_a_regular_skill(self):
        """Review of the catalog entry: the jobs must run the plugin's own,
        catalog-pinned copy — never some other skill called `dreaming`."""
        self._complete_install()                      # a regular skills/dreaming exists too
        own = self._install_plugin()
        with fake_registry({PLUGIN_SKILL: own}):
            self.assertEqual(install_cron.skill_ref(), (PLUGIN_SKILL, None))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                install_cron.main(["--dry-run"])
        self.assertIn(PLUGIN_SKILL, out.getvalue())

    def test_only_the_registration_of_this_plugins_file_counts(self):
        """Another plugin's `dreaming`, or a name serving some other file under
        our directory, must not become the jobs' skill."""
        self._complete_install()
        own = self._install_plugin()
        other = self.home / "plugins" / "other" / "skills" / "dreaming" / "SKILL.md"
        nested = own.parent / "extra" / "SKILL.md"
        entries = {"aaa-first:dreaming": nested, "agent-plugin-other-11111111:dreaming": other}
        with fake_registry(dict(entries, **{PLUGIN_SKILL: own})):
            self.assertEqual(install_cron.skill_ref(), (PLUGIN_SKILL, None))
        with fake_registry(entries):                  # ours is not registered
            name, problem = install_cron.skill_ref()
        self.assertIsNone(name)
        self.assertIn("hermes plugins enable hermes-dreaming", problem)

    def test_two_names_for_our_file_is_a_problem_not_a_guess(self):
        self._complete_install()
        own = self._install_plugin()
        with fake_registry({PLUGIN_SKILL: own, "agent-plugin-copy-22222222:dreaming": own}):
            name, problem = install_cron.skill_ref()
        self.assertIsNone(name)
        self.assertIn("several registered skills", problem)

    def test_unreadable_registry_names_the_interpreter(self):
        """System python3 cannot import Hermes (no ruamel): that is not a
        disabled plugin, and the problem must say what to run instead."""
        self._complete_install()
        self._install_plugin()
        with fake_registry(error=ModuleNotFoundError("No module named 'ruamel'")):
            name, problem = install_cron.skill_ref()
        self.assertIsNone(name)
        self.assertIn("ruamel", problem)
        self.assertIn("venv/bin/python", problem)

    def test_installed_but_disabled_plugin_is_a_problem(self):
        self._complete_install()
        self._install_plugin()
        with fake_registry({}):                       # not registered = not enabled
            problems = install_cron._preflight()
        self.assertTrue(any("hermes plugins enable hermes-dreaming" in p for p in problems))

    # Existing jobs: a 2.0 job keeps the skill it was created with -------------
    def _run_main(self, fake, *argv):
        own = self._install_plugin()
        old = install_cron._import_jobs
        install_cron._import_jobs = lambda: fake
        out, err = io.StringIO(), io.StringIO()
        try:
            with fake_registry({PLUGIN_SKILL: own}), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = install_cron.main(list(argv))
        finally:
            install_cron._import_jobs = old
        return rc, out.getvalue() + err.getvalue()

    def _old_job(self, **kw):
        return dict({"id": "old1", "script": install_cron.DREAM_SCRIPT,
                     "skill": "dreaming", "skills": ["dreaming"]}, **kw)

    def test_job_bound_to_another_skill_is_reported_not_kept_quietly(self):
        self._complete_install()
        fake = FakeJobs([self._old_job(enabled=False)])   # paused counts too
        rc, out = self._run_main(fake, "--sweep-schedule", "")
        self.assertEqual(rc, 1)
        self.assertIn("--rebind", out)
        self.assertEqual(fake.updated, [])
        # No second nightly job next to the stale one; extraction did not exist yet.
        self.assertEqual([c["script"] for c in fake.created], [install_cron.EXTRACT_SCRIPT])
        self.assertEqual(fake.created[0]["skills"], [PLUGIN_SKILL])

    def test_rebind_updates_the_skill_in_place(self):
        self._complete_install()
        fake = FakeJobs([self._old_job()])
        rc, out = self._run_main(fake, "--rebind", "--extract-schedule", "", "--sweep-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual(fake.updated, [("old1", {"skill": PLUGIN_SKILL, "skills": [PLUGIN_SKILL]})])
        self.assertEqual(fake.created, [])

    def test_job_already_bound_is_left_alone(self):
        self._complete_install()
        fake = FakeJobs([self._old_job(skill=PLUGIN_SKILL, skills=[PLUGIN_SKILL])])
        rc, out = self._run_main(fake, "--rebind", "--extract-schedule", "", "--sweep-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual((fake.updated, fake.created), ([], []))
        self.assertIn("left alone", out)

    def test_forget_sweep_job_is_no_agent_and_created_once(self):
        """Regression 2.4.0: cleaning copies of forgotten facts is a no_agent job, no skill, no model."""
        self._complete_install()
        fake = FakeJobs([])
        rc, out = self._run_main(fake, "--extract-schedule", "")
        self.assertEqual(rc, 0, out)
        sweep = [c for c in fake.created if c["script"] == install_cron.SWEEP_SCRIPT]
        self.assertEqual(len(sweep), 1)
        self.assertTrue(sweep[0]["no_agent"])
        self.assertNotIn("skill", sweep[0])

    def test_existing_forget_sweep_job_is_left_alone(self):
        self._complete_install()
        fake = FakeJobs([{"id": "sw1", "script": install_cron.SWEEP_SCRIPT, "no_agent": True}])
        rc, out = self._run_main(fake, "--extract-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertNotIn(install_cron.SWEEP_SCRIPT, [c["script"] for c in fake.created])
        self.assertIn("sw1", out)

    def test_rebind_keeps_skills_attached_by_hand(self):
        job = {"skills": ["dreaming", "house-rules"]}
        self.assertEqual(install_cron.rebound_skills(job, PLUGIN_SKILL), [PLUGIN_SKILL, "house-rules"])
        self.assertEqual(install_cron.rebound_skills({"skills": ["house-rules"]}, PLUGIN_SKILL),
                         [PLUGIN_SKILL, "house-rules"])
        self.assertEqual(install_cron.job_skills({"skill": "dreaming"}), ["dreaming"])

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


class HolographicVersionTests(unittest.TestCase):
    """2.4.0: holographic 0.2.0-0.7.x silently refuses fact writes from cron; the install check says so."""

    def _check(self, version):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            if version:
                (home / "plugins" / "holographic").mkdir(parents=True)
                (home / "plugins" / "holographic" / "plugin.yaml").write_text(
                    "name: holographic\nversion: " + version + "\n", encoding="utf-8")
            r = install.Report()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                install._check_holographic_version(home, r)
            return r, out.getvalue()

    def test_versions(self):
        r, out = self._check("0.7.0")
        self.assertEqual(r.warn_n, 1)
        self.assertIn("hermes plugins update holographic", out)
        r, out = self._check("0.8.0")
        self.assertEqual((r.ok_n, r.warn_n), (1, 0))
        r, out = self._check("0.1.1")  # the copy bundled in core never refused cron
        self.assertEqual((r.ok_n, r.warn_n), (0, 0))
        r, out = self._check(None)
        self.assertEqual(out, "")


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


class CheckJobsTests(unittest.TestCase):
    """`install.py --check` reads cron/jobs.json and says whether the dreaming
    jobs load the skill this install provides — the only other sign of a
    stale binding is Hermes' 'skill not found' line in a nightly report."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "cron").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _regular_skill(self):
        (self.home / "skills" / "dreaming").mkdir(parents=True)
        (self.home / "skills" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")

    def _plugin(self):
        skill = self.home / "plugins" / "hermes-dreaming" / "skills" / "dreaming" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# skill", encoding="utf-8")
        (self.home / "plugins" / "hermes-dreaming" / "plugin.json").write_text("{}", encoding="utf-8")
        return skill

    def _jobs(self, *skills, sweep=True):
        listed = [{"id": f"j{i}", "script": install.GATES[i % 2], "skills": [s]}
                  for i, s in enumerate(skills)]
        listed.append({"id": "other", "script": "backup.py", "skills": ["dreaming-not"]})
        if sweep:
            listed.append({"id": "sweep", "script": install.GATES[2], "no_agent": True})
        (self.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": listed}), encoding="utf-8")

    def _check(self, check_only=True):
        r, out = install.Report(), io.StringIO()
        with contextlib.redirect_stdout(out):
            install.check_jobs(self.home, check_only, r)
        return r, out.getvalue()

    def test_jobs_that_load_the_installed_skill_pass(self):
        self._regular_skill()
        self._jobs("dreaming", "dreaming")
        r, out = self._check()
        self.assertEqual((r.ok_n, r.warn_n, r.bad_n), (2, 0, 0), out)

    def test_job_left_on_a_removed_plugin_is_a_problem(self):
        self._regular_skill()                         # plugin gone, regular install remains
        self._jobs(PLUGIN_SKILL)
        r, out = self._check()
        self.assertEqual(r.bad_n, 1, out)
        self.assertIn("--rebind", out)

    def test_plugin_job_passes_with_the_registry(self):
        own = self._plugin()
        self._jobs(PLUGIN_SKILL, PLUGIN_SKILL)
        with fake_registry({PLUGIN_SKILL: own}):
            r, out = self._check()
        self.assertEqual((r.ok_n, r.bad_n), (2, 0), out)

    def test_disabled_plugin_is_a_problem(self):
        self._plugin()
        self._jobs(PLUGIN_SKILL)
        with fake_registry({}):
            r, out = self._check()
        self.assertEqual(r.bad_n, 1, out)
        self.assertIn("hermes plugins enable", out)

    def test_unreadable_registry_is_a_warning_not_a_verdict(self):
        self._plugin()
        self._jobs(PLUGIN_SKILL)
        with fake_registry(error=ModuleNotFoundError("No module named 'ruamel'")):
            r, out = self._check()
        self.assertEqual((r.warn_n, r.bad_n), (1, 0), out)
        self.assertIn("venv/bin/python", out)

    def test_missing_forget_sweep_job_is_a_warning(self):
        self._regular_skill()
        self._jobs("dreaming", "dreaming", sweep=False)
        r, out = self._check()
        self.assertEqual((r.ok_n, r.warn_n, r.bad_n), (2, 1, 0), out)
        self.assertIn("dream-forget-sweep.py", out)
        self.assertIn("install_cron.py", out)

    def test_no_jobs_warns_on_check_only(self):
        self._regular_skill()
        r, _ = self._check(check_only=True)
        self.assertEqual(r.warn_n, 1)
        r, out = self._check(check_only=False)       # first install: the next step says it
        self.assertEqual((r.ok_n, r.warn_n, r.bad_n), (0, 0, 0), out)


# Issue #2 (blopa24): a top-level comment inside `memory:` is valid YAML — Hermes reads the
# provider after it — but the installer's block regex stopped there and said it was not set.
ISSUE_2 = ("memory:\n  memory_enabled: true\n\n# Section heading from the generated/example config\n"
           "# Additional explanation\n  provider: holographic\n  memory_char_limit: 5000\n  user_char_limit: 6000\n"
           "  write_approval: true\nnext_key: true\n")
ALL_FOUR = {"provider": "holographic", "memory_char_limit": 5000, "user_char_limit": 6000, "write_approval": True}

# The configs both readers must agree on — the reviews' cases among them (review of 2.9.6, Codex: a quoted parent
# set the indent by its nested key; a second `memory:` is the one YAML keeps)
AGREE = {
    "issue #2": ISSUE_2,
    "quoted parent": ('memory:\n  "extra":\n    unrelated: value\n  provider: holographic\n'
                      "  memory_char_limit: 5000\n  user_char_limit: 6000\n  write_approval: true\n"),
    "quoted keys": ('"memory":\n  "provider": "holographic"\n  \'memory_char_limit\': 5000\n'
                    "  user_char_limit: 6000\n  write_approval: true\n"),
    "second block wins": ("memory:\n# heading\n  provider: other\n  memory_char_limit: 9000\nmemory:\n"
                          "  provider: holographic\n  memory_char_limit: 5000\n  user_char_limit: 6000\n"
                          "  write_approval: true\n"),
    "BOM and CRLF": ("﻿memory:\r\n  provider: holographic\r\n  memory_char_limit: 5000\r\n"
                     "  user_char_limit: 6000\r\n# c\r\n  write_approval: true\r\n"),
    "four-space indent, nested list": ("memory:\n    extra:\n      - provider: other\n        write_approval: false\n"
                                       "    provider: holographic\n    memory_char_limit: 5000\n"
                                       "    user_char_limit: 6000\n    write_approval: true  # gate on\n"),
    "octal, hex and on": ("memory:\n  provider: holographic\n  memory_char_limit: 011610\n  user_char_limit: 0x1770\n"
                          "  write_approval: on\n"),
}

# Configs where both readers find nothing: the first `memory:` is replaced by one without the keys, whatever its form
# (second review of 2.9.6, Codex)
EMPTY = {
    "second block": "memory:\n  provider: holographic\n  memory_char_limit: 9000\nmemory:\n  memory_enabled: true\n",
    "second after a comment": ("memory:\n# heading\n  provider: holographic\n  memory_char_limit: 9000\nmemory:\n"
                               "  memory_enabled: true\n"),
    "second as null": "memory:\n  provider: holographic\nmemory: null\n",
}


class MemoryBlockTests(unittest.TestCase):
    """`memory:` of config.yaml: the scanner behind a missing PyYAML agrees with YAML on the block mappings Hermes
    writes, and the installer reads it the way the pass does (2.9.6)."""

    def test_the_scanner_skips_comments_and_reads_direct_children_only(self):
        cases = {
            ISSUE_2: {"memory_enabled": True, "provider": "holographic", "memory_char_limit": 5000,
                      "user_char_limit": 6000, "write_approval": True},
            "memory:  # the memory block\n  provider: holographic": {"provider": "holographic"},
            "memory:\n    # indented comment\n  provider: 'holographic'  # set by hand\n": {"provider": "holographic"},
            "memory:\n  extra:\n    provider: other\n  provider: holographic\n": {"extra": None,
                                                                                  "provider": "holographic"},
            "memory:\n  memory_enabled: true\nnext:\n  provider: holographic\n": {"memory_enabled": True},
            "memory:\n# a comment, then nothing indented\nprovider: holographic\n": {},
            "other: 1\n  provider: holographic\n": {},
            # "#" is a comment only after a space; a quoted value keeps it; a block scalar is not read
            "memory:\n  provider: holo#graphic\n": {"provider": "holo#graphic"},
            'memory:\n  provider: "holo #graphic" # note\n': {"provider": "holo #graphic"},
            "memory:\n  provider: |-\n    holographic\n  memory_char_limit: 5000\n": {"memory_char_limit": 5000},
            # typed as PyYAML types plain scalars: a number is no bool, "05000" is octal, a quoted number a string
            "memory:\n  write_approval: 1\n  memory_char_limit: 05000\n  user_char_limit: '6000'\n":
                {"write_approval": 1, "memory_char_limit": 2560, "user_char_limit": "6000"},
            # every top-level `memory` replaces the one before; one the scanner cannot read leaves nothing
            "memory:\n# c\n  provider: holographic\nmemory: {provider: none}\n": {},
            "memory:\n  provider: holographic\nmemory: null\n": {},
            "memory:\n  provider: holographic\n'memory':\n  provider: none\n": {"provider": "none"},
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(dream._scan_memory_block(text), want)

    def test_both_readers_agree(self):
        try:
            import yaml
        except ImportError:
            yaml = None
        for name, text in AGREE.items():
            with self.subTest(case=name):
                self.assertEqual(self._settings(text, with_yaml=False), ALL_FOUR)
                if yaml is not None:
                    self.assertEqual(self._settings(text, with_yaml=True), ALL_FOUR)

    def test_a_replaced_memory_gives_nothing(self):
        """YAML keeps the last `memory`, so one without the keys is "not set": with PyYAML its answer is final, and
        the scanner replaces the first block too, whatever form the second takes (reviews of 2.9.6, Codex)."""
        try:
            import yaml
        except ImportError:
            yaml = None
        for name, text in EMPTY.items():
            with self.subTest(case=name):
                self.assertEqual(self._settings(text, with_yaml=False), {})
                if yaml is not None:
                    self.assertEqual(self._settings(text, with_yaml=True), {})

    def test_write_approval_is_read_as_hermes_reads_it(self):
        """tools/write_approval._normalize_enabled: a bool as is, a few strings on, a number off."""
        for raw, want in (("true", True), ("on", True), ("enabled", True), ('"yes"', True), ("false", False),
                          ("off", False), ('"false"', False), ("1", False), ("0", False), ("null", False),
                          ("y", False), ("Y", False), ("n", False), ("N", False)):   # PyYAML: strings, not bools
            text = f"memory:\n  write_approval: {raw}\n"
            with self.subTest(raw=raw, pyyaml=False):
                self.assertIs(self._settings(text, with_yaml=False).get("write_approval", False), want)
            try:
                import yaml  # noqa: F401
            except ImportError:
                continue
            with self.subTest(raw=raw, pyyaml=True):
                self.assertIs(self._settings(text, with_yaml=True).get("write_approval", False), want)
        self.assertTrue(dream.hermes_enabled("approve") and not dream.hermes_enabled(1)
                        and not dream.hermes_enabled(None) and dream.hermes_enabled(True))

    def test_only_a_positive_finite_number_is_a_char_limit(self):
        """Third review of 2.9.6 (Codex): `true` is an int to Python and became a limit of one character; `.inf` and
        `.nan` made `int()` raise."""
        from unittest import mock
        try:
            import yaml  # noqa: F401
            modes = (False, True)
        except ImportError:
            modes = (False,)
        for raw in ("true", "false", ".inf", ".nan", "0", "-5", '"5000"', "y"):
            for with_yaml in modes:
                with self.subTest(raw=raw, pyyaml=with_yaml), tempfile.TemporaryDirectory() as tmp:
                    Path(tmp, "config.yaml").write_text(f"memory:\n  memory_char_limit: {raw}\n", encoding="utf-8")
                    saved = dream.HOME
                    dream.HOME = tmp
                    try:
                        with mock.patch.dict(sys.modules, {} if with_yaml else {"yaml": None}), \
                                contextlib.redirect_stderr(io.StringIO()):
                            cfg = dream.load_config(str(Path(tmp, "dreaming.json")))
                    finally:
                        dream.HOME = saved
                    self.assertNotIn("memory_char_limits", {k: v for k, v in cfg.items() if v})
        self.assertTrue(dream._a_char_limit(5000) and dream._a_char_limit(2560.0))

    def _settings(self, yaml_text, with_yaml):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.yaml").write_text(yaml_text, encoding="utf-8")
            if with_yaml:
                return dream.hermes_memory_settings(tmp)
            with mock.patch.dict(sys.modules, {"yaml": None}):   # `import yaml` raises: the scanner reads
                return dream.hermes_memory_settings(tmp)

    def test_the_pass_reads_the_issue_config_with_and_without_pyyaml(self):
        try:
            import yaml  # noqa: F401
            readers = (False, True)
        except ImportError:
            readers = (False,)
        for with_yaml in readers:
            with self.subTest(pyyaml=with_yaml):
                got = self._settings(ISSUE_2, with_yaml)
                self.assertEqual(got.get("provider"), "holographic")
                self.assertIs(got.get("write_approval"), True)

    def test_install_check_finds_the_provider_after_a_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                install.main(["--hermes-home", tmp])
            Path(tmp, "config.yaml").write_text(ISSUE_2, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                install.main(["--hermes-home", tmp, "--check"])
            self.assertNotIn("memory.provider is", out.getvalue())
            self.assertIn("memory provider 'holographic'", out.getvalue())

    def test_unreadable_config_is_still_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(install._memory_provider(Path(tmp), dream))
            Path(tmp, "config.yaml").write_text("memory:\n  provider: \n", encoding="utf-8")
            self.assertEqual(install._memory_provider(Path(tmp), dream), "")
            self.assertFalse(install._write_approval(Path(tmp), dream))


if __name__ == "__main__":
    unittest.main()
