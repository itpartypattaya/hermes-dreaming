"""Upgrade real job shapes without silently activating or duplicating work."""
import contextlib
import contextvars
import copy
import io
import json
import shutil
import sys
import types
import unittest
from unittest.mock import patch

import test_install as support
import test_cron_prompt_sync as prompts

ic = support.install_cron


class UpgradeTests(unittest.TestCase):
    setUp = support.InstallCronTests.setUp
    tearDown = support.InstallCronTests.tearDown
    _run_main = support.InstallCronTests._run_main
    _install_plugin = prompts.PromptSyncTests._install_plugin

    def ready(self):
        for script in (ic.DREAM_SCRIPT, ic.EXTRACT_SCRIPT, ic.SWEEP_SCRIPT):
            shutil.copyfile(support.ROOT / "scripts" / script, self.home / "scripts" / script)
        (self.home / "skills/dreaming/SKILL.md").write_text("# skill", encoding="utf-8")

    def job(self, role="night", **kw):
        script, prompt = {"night": (ic.DREAM_SCRIPT, ic.DREAM_PROMPT),
                          "extract": (ic.EXTRACT_SCRIPT, ic.EXTRACT_PROMPT),
                          "sweep": (ic.SWEEP_SCRIPT, "")}[role]
        j = dict(id=role, script=script, prompt=prompt, no_agent=role == "sweep",
                 enabled=True, state="scheduled", skills=[] if role == "sweep" else [support.PLUGIN_SKILL],
                 model="expensive", provider="local", deliver="local", attach_to_session=False,
                 schedule={"kind": "cron", "expr": "0 3 * * *"},
                 repeat={"times": None, "completed": 8}, next_run_at="2027-01-01T03:00:00Z")
        j.update(kw)
        return j

    def run_one(self, jobs, *args):
        return self._run_main(jobs, "--extract-schedule", "", "--sweep-schedule", "", *args)

    def expected(self, roles):
        (self.home / "dreaming.json").write_text(json.dumps({"cron": {"expected_roles": roles}}), encoding="utf-8")

    def test_explicit_settings_warn_without_applying(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job()])
        rc, out = self.run_one(jobs, "--model", "cheap", "--provider", "new", "--deliver", "origin",
                               "--dream-schedule", "7 4 * * *", "--name-prefix", "Budget")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [])
        for field in ("model", "provider", "deliver", "schedule", "name"):
            self.assertIn(field, out)
        self.assertIn("--update-existing", out)

    def test_update_existing_changes_only_explicit_model(self):
        self.ready()
        job = self.job(enabled=False, state="paused", next_run_at=None)
        before = copy.deepcopy(job)
        jobs = prompts.StoredJobs([job])
        rc, out = self.run_one(jobs, "--update-existing", "--model", "cheap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("night", {"model": "cheap"})])
        self.assertEqual(job, dict(before, model="cheap"))

    def test_delivery_update_and_model_reset_are_explicit(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job()])
        rc, out = self.run_one(jobs, "--update-existing", "--deliver", "origin", "--inherit-model")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("night", {"deliver": "origin", "attach_to_session": True,
                                                  "model": None, "provider": None})])

    def test_same_cron_expression_does_not_request_update(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job()])
        rc, out = self.run_one(jobs, "--update-existing", "--dream-schedule", "0 3 * * *")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [])

    def test_targeted_update_never_creates_other_roles(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job()])
        rc, out = self._run_main(jobs, "--job-id", "night", "--update-existing", "--model", "cheap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.created, [])
        self.assertEqual(jobs.updated, [("night", {"model": "cheap"})])

    def test_a_broken_other_job_does_not_block_a_named_one(self):
        """3.8.2 (Codex review of 3.3.2..3.8.0): `--job-id` limits the whole operation to the named jobs (README), and
        a refused job's message says to name the other jobs with it. A job of another role the installer cannot
        classify, or that runs in the wrong mode, blocked the named job all the same."""
        self.ready()
        others = {"unknown state": self.job("sweep", state="weird"),
                  "wrong mode": self.job("extract", no_agent=True)}
        for what, other in others.items():
            with self.subTest(what):
                jobs = prompts.StoredJobs([self.job(), other])
                rc, out = self._run_main(jobs, "--job-id", "night", "--update-existing", "--model", "cheap")
                self.assertEqual(rc, 0, out)
                self.assertEqual(jobs.updated, [("night", {"model": "cheap"})])
                self.assertIn(f"job {other['id']}", out, "the other job's problem is still reported")

    def test_a_broken_named_job_still_blocks_and_so_does_an_untargeted_run(self):
        self.ready()
        for args in (("--job-id", "night"), ()):
            with self.subTest(args=args):
                jobs = prompts.StoredJobs([self.job(state="weird"), self.job("sweep")])
                rc, out = self._run_main(jobs, *args, "--update-existing", "--model", "cheap")
                self.assertEqual(rc, 1, out)
                self.assertEqual((jobs.created, jobs.updated), ([], []))

    def test_empty_schedules_explain_existing_jobs_continue_running(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job(), self.job("extract"), self.job("sweep")])
        rc, out = self.run_one(jobs)
        self.assertEqual(rc, 0, out)
        self.assertIn("extract", out)
        self.assertIn("sweep", out)
        self.assertIn("still running", out)
        self.assertIn("hermes cron pause", out)
        self.assertEqual(jobs.updated, [])

    def test_expected_roles_controls_creation_and_is_not_overwritten(self):
        self.ready()
        self.expected(["night"])
        original = (self.home / "dreaming.json").read_bytes()
        jobs = prompts.StoredJobs([])
        rc, out = self._run_main(jobs)
        self.assertEqual(rc, 0, out)
        self.assertEqual([j["script"] for j in jobs.created], [ic.DREAM_SCRIPT])
        self.assertEqual((self.home / "dreaming.json").read_bytes(), original)

    def test_invalid_config_stops_before_all_mutations(self):
        self.ready()
        for value in ('{', '[]', '{"cron":null}', '{"cron":{"expected_roles":"night"}}',
                      '{"cron":{"expected_roles":["unknown"]}}'):
            with self.subTest(value=value):
                (self.home / "dreaming.json").write_text(value, encoding="utf-8")
                jobs = prompts.StoredJobs([])
                rc, out = self._run_main(jobs)
                self.assertEqual(rc, 1, out)
                self.assertEqual((jobs.created, jobs.updated), ([], []))

    def test_unknown_api_object_cannot_be_treated_as_no_jobs(self):
        self.ready()
        class Unknown(prompts.StoredJobs):
            def list_jobs(self, include_disabled=False):
                return {"error": "unavailable"}
        jobs = Unknown([])
        with self.assertRaises(SystemExit):
            self._run_main(jobs)
        self.assertEqual((jobs.created, jobs.updated), ([], []))

    def test_duplicate_targets_block_all_mutations_including_gate_copies(self):
        self.ready()
        gate = self.home / "scripts" / ic.DREAM_SCRIPT
        gate.write_text("stale gate", encoding="utf-8")
        jobs = prompts.StoredJobs([self.job(prompt=prompts.OLD["DREAM_PROMPT"]), self.job(id="second")])
        rc, out = self._run_main(jobs, "--upgrade")
        self.assertEqual(rc, 1, out)
        self.assertIn("duplicate", out)
        self.assertEqual((jobs.created, jobs.updated), ([], []))
        self.assertEqual(gate.read_text(encoding="utf-8"), "stale gate")

    def test_duplicate_exact_target_updates_only_that_job(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job(), self.job(id="second")])
        rc, out = self._run_main(jobs, "--job-id", "second", "--update-existing", "--model", "cheap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("second", {"model": "cheap"})])
        self.assertEqual(jobs.created, [])
        self.assertIn("duplicate", out)

    def test_force_warns_about_new_duplicate(self):
        self.ready()
        jobs = prompts.StoredJobs([self.job()])
        rc, out = self.run_one(jobs, "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("additional", out)
        self.assertIn("2", out)
        self.assertEqual(len(jobs.created), 1)

    def test_stale_gate_blocks_direct_apply(self):
        self.ready()
        (self.home / "scripts" / ic.DREAM_SCRIPT).write_text("stale", encoding="utf-8")
        jobs = prompts.StoredJobs([self.job(prompt=prompts.OLD["DREAM_PROMPT"])])
        rc, out = self.run_one(jobs)
        self.assertEqual(rc, 1, out)
        self.assertIn("differs", out)
        self.assertEqual(jobs.updated, [])

    def test_upgrade_repairs_gates_then_old_prompts_and_checks_results(self):
        self.ready()
        self.expected(["night"])
        gate = self.home / "scripts" / ic.DREAM_SCRIPT
        gate.write_text("stale", encoding="utf-8")
        parent = self
        class Ordered(prompts.StoredJobs):
            def update_job(self, jid, patch):
                parent.assertEqual(gate.read_bytes(), (support.ROOT / "scripts" / ic.DREAM_SCRIPT).read_bytes())
                return super().update_job(jid, patch)
        jobs = Ordered([self.job(prompt=prompts.OLD["DREAM_PROMPT"])])
        rc, out = self._run_main(jobs, "--upgrade")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.listed[0]["prompt"], ic.DREAM_PROMPT)
        self.assertIn("final", out)
        self.assertIn("active", out)

    def test_upgrade_dry_run_has_no_writes(self):
        self.ready()
        self.expected(["night"])
        gate = self.home / "scripts" / ic.DREAM_SCRIPT
        gate.write_text("stale", encoding="utf-8")
        path = self.home / "cron/jobs.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"jobs": [self.job(prompt=prompts.OLD["DREAM_PROMPT"])]}), encoding="utf-8")
        jobs = prompts.StoredJobs([])
        rc, out = self._run_main(jobs, "--upgrade", "--dry-run")
        self.assertEqual(rc, 0, out)
        planned = json.loads(out)
        self.assertTrue(any(j["action"] == "copy-gate" for j in planned))
        self.assertTrue(any(j["action"] == "update" for j in planned))
        self.assertEqual(gate.read_text(encoding="utf-8"), "stale")
        self.assertEqual((jobs.created, jobs.updated), ([], []))

    def test_core_schedule_rejection_happens_before_gate_copies_or_creates(self):
        self.ready()
        gate = self.home / "scripts" / ic.DREAM_SCRIPT
        gate.write_text("old gate", encoding="utf-8")
        class InvalidSchedule(prompts.StoredJobs):
            def parse_schedule(self, value):
                raise ValueError("core rejected invalid cron expression")
        jobs = InvalidSchedule([])
        rc, out = self._run_main(jobs, "--upgrade", "--dream-schedule", "99 99 * * *")
        self.assertEqual(rc, 1, out)
        self.assertIn("core rejected", out)
        self.assertEqual((jobs.created, jobs.updated), ([], []))
        self.assertEqual(gate.read_text(encoding="utf-8"), "old gate")

    def test_unknown_state_is_not_reported_active(self):
        self.assertEqual(ic.job_state(self.job(state="garbage")), "unknown")

    def test_home_override_is_scoped_and_restored(self):
        current = contextvars.ContextVar("test_hermes_home", default="old-home")
        core = types.ModuleType("hermes_constants")
        core.get_hermes_home = lambda: current.get()
        core.set_hermes_home_override = current.set
        core.reset_hermes_home_override = current.reset
        with patch.dict(sys.modules, hermes_constants=core):
            with ic.home_environment(self.home):
                self.assertEqual(core.get_hermes_home(), str(self.home))
            self.assertEqual(core.get_hermes_home(), "old-home")

    def test_invalid_config_does_not_copy_gates_in_regular_installer(self):
        (self.home / "dreaming.json").write_text("[]", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = support.install.main(["--hermes-home", str(self.home)])
        self.assertEqual(rc, 1)
        self.assertFalse((self.home / "scripts" / ic.DREAM_SCRIPT).exists())

    def test_blank_or_reused_create_id_is_not_a_success(self):
        self.ready()
        for returned in ("   ", "night"):
            with self.subTest(returned=returned):
                class Unconfirmed(prompts.StoredJobs):
                    def create_job(self, **kwargs):
                        return {"id": returned}
                jobs = Unconfirmed([self.job()])
                rc, out = self.run_one(jobs, "--force")
                self.assertEqual(rc, 1, out)
                self.assertNotIn("OK: created", out)


class InventoryTests(unittest.TestCase):
    setUp = support.CheckJobsTests.setUp
    tearDown = support.CheckJobsTests.tearDown
    _regular_skill = support.CheckJobsTests._regular_skill
    _check = support.CheckJobsTests._check
    job = UpgradeTests.job

    def check(self, listed, roles=None):
        self._regular_skill()
        if roles is not None:
            (self.home / "dreaming.json").write_text(json.dumps({"cron": {"expected_roles": roles}}), encoding="utf-8")
        for j in listed:
            if not j.get("no_agent"):
                j["skills"] = ["dreaming"]
        (self.home / "cron/jobs.json").write_text(json.dumps({"jobs": listed}), encoding="utf-8")
        return self._check()

    def test_paused_is_visible_without_claiming_active(self):
        r, out = self.check([self.job(enabled=False, state="paused")], ["night"])
        self.assertEqual(r.bad_n, 0, out)
        self.assertGreater(r.warn_n, 0)
        self.assertIn("paused", out)
        self.assertNotIn("night: active", out)

    def test_incorrect_agent_and_sweep_modes_are_errors(self):
        r, out = self.check([self.job(no_agent=True), self.job("sweep", no_agent=False)], ["night", "sweep"])
        self.assertGreaterEqual(r.bad_n, 2, out)
        self.assertIn("execution mode", out)

    def test_duplicates_are_not_all_green(self):
        r, out = self.check([self.job(), self.job(id="other")], ["night"])
        self.assertGreater(r.bad_n, 0, out)
        self.assertIn("other", out)
        self.assertIn("duplicate", out)

    def test_missing_extraction_is_named(self):
        r, out = self.check([self.job(), self.job("sweep")])
        self.assertGreater(r.warn_n, 0, out)
        self.assertIn("extract", out)
        self.assertIn("missing", out)

    def test_explicit_omissions_are_neutral(self):
        r, out = self.check([], [])
        self.assertEqual((r.bad_n, r.warn_n), (0, 0), out)
        self.assertIn("intentionally omitted", out)

    def test_sweep_only_install_is_fully_inspected(self):
        r, out = self.check([self.job("sweep", enabled=False)], ["sweep"])
        self.assertGreater(r.warn_n, 0, out)
        self.assertIn("paused", out)
        self.assertIn("sweep", out)

    def test_malformed_jobs_are_reported_without_traceback(self):
        for data in ({"jobs": None}, {"jobs": [5]}, {"unknown": []}, {"jobs": [{"id": ""}]}):
            with self.subTest(data=data):
                (self.home / "cron/jobs.json").write_text(json.dumps(data), encoding="utf-8")
                r, out = self._check()
                self.assertGreater(r.bad_n, 0, out)

    def test_config_root_must_be_an_object(self):
        for text in ('[]', 'null', '42', '"text"'):
            with self.subTest(text=text):
                path = self.home / "dreaming.json"
                path.write_text(text, encoding="utf-8")
                r = support.install.Report()
                with contextlib.redirect_stdout(io.StringIO()):
                    support.install.check_config(path, True, r)
                self.assertGreater(r.bad_n, 0)
