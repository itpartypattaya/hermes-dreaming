"""An upgrade refreshes shipped instructions, preserving local job settings."""
import copy
import json
import unittest

import test_install as support
from test_install import FakeJobs, ROOT, install_cron


OLD = json.loads((ROOT.parents[1] / "tests/fixtures/cron-prompts-340.json").read_text(encoding="utf-8"))


class StoredJobs(FakeJobs):
    def update_job(self, job_id, updates):
        super().update_job(job_id, updates)
        job = next(j for j in self.listed if j["id"] == job_id)
        job.update(updates)
        return dict(job)


class PromptSyncTests(unittest.TestCase):
    setUp = support.InstallCronTests.setUp
    tearDown = support.InstallCronTests.tearDown
    _complete_install = support.InstallCronTests._complete_install
    _run_main = support.InstallCronTests._run_main

    def _install_plugin(self):
        if (self.home / "plugins/hermes-dreaming/plugin.json").exists():
            return self.home / "plugins/hermes-dreaming/skills/dreaming/SKILL.md"
        return support.InstallCronTests._install_plugin(self)

    def run_jobs(self, jobs, *flags):
        return self._run_main(jobs, "--extract-schedule", "", "--sweep-schedule", "", *flags)

    def job(self, prompt=None, **kw):
        return dict(id="night", script=install_cron.DREAM_SCRIPT,
                    skills=["agent-plugin-hermes-dreaming-0a1b2c3d:dreaming"],
                    prompt=OLD["DREAM_PROMPT"] if prompt is None else prompt, **kw)

    def test_rerun_upgrades_stock_prompt_only_and_is_idempotent(self):
        self._complete_install()
        job = self.job(schedule={"kind": "cron", "expr": "13 4 * * *"}, deliver="origin",
                       model="local-model", provider="local", enabled=False, state="paused",
                       repeat={"times": None, "completed": 17}, next_run_at=None,
                       last_run_at="2026-01-01T00:00:00Z", name="My night", enabled_toolsets=["memory"])
        before = copy.deepcopy(job)
        jobs = StoredJobs([job])
        rc, out = self.run_jobs(jobs)
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("night", {"prompt": install_cron.DREAM_PROMPT})])
        self.assertEqual(job, dict(before, prompt=install_cron.DREAM_PROMPT))
        self.assertEqual(self.run_jobs(jobs)[0], 0)
        self.assertEqual(len(jobs.updated), 1)
        self.assertEqual(jobs.created, [])

    def test_rebind_updates_stock_prompt_and_preserves_extra_skills(self):
        self._complete_install()
        job = self.job()
        job["skills"] = ["dreaming", "house-rules"]
        jobs = StoredJobs([job])
        rc, out = self.run_jobs(jobs, "--rebind")
        self.assertEqual(rc, 0, out)
        self.assertEqual(job["prompt"], install_cron.DREAM_PROMPT)
        self.assertEqual(job["skills"], ["agent-plugin-hermes-dreaming-0a1b2c3d:dreaming", "house-rules"])

    def test_custom_empty_missing_and_other_roles_are_preserved_and_reported(self):
        self._complete_install()
        for prompt in ("My custom instructions", "", None, OLD["EXTRACT_PROMPT"]):
            with self.subTest(prompt=repr(prompt)[:60]):
                job = self.job(prompt="" if prompt is None else prompt)
                if prompt is None:
                    del job["prompt"]
                before = copy.deepcopy(job)
                jobs = StoredJobs([job])
                rc, out = self.run_jobs(jobs)
                self.assertEqual(rc, 0, out)
                self.assertEqual(job, before)
                self.assertEqual(jobs.updated, [])
                self.assertIn("custom/unknown prompt", out)

    def test_dry_run_plans_real_updates_without_writing(self):
        self._complete_install()
        job = self.job()
        path = self.home / "cron/jobs.json"
        path.parent.mkdir()
        original = json.dumps({"jobs": [job]})
        path.write_text(original, encoding="utf-8")
        jobs = StoredJobs([job])
        rc, out = self.run_jobs(jobs, "--dry-run")
        self.assertEqual(rc, 0, out)
        plan = json.loads(out)
        self.assertEqual(plan[0]["action"], "update")
        self.assertEqual(plan[0]["id"], "night")
        self.assertEqual(plan[0]["updates"], {"prompt": install_cron.DREAM_PROMPT})
        self.assertIn("---", plan[0]["prompt_diff"])
        self.assertEqual((jobs.created, jobs.updated), ([], []))
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_detected_edit_between_listing_and_update_is_not_overwritten(self):
        self._complete_install()
        class Edited(StoredJobs):
            reads = 0
            def list_jobs(self, include_disabled=False):
                self.reads += 1
                if self.reads == 2:
                    self.listed[0]["prompt"] = "Edited while installing"
                return copy.deepcopy(super().list_jobs(include_disabled))
        jobs = Edited([self.job()])
        rc, out = self.run_jobs(jobs)
        self.assertEqual(rc, 1, out)
        self.assertEqual(jobs.updated, [])
        self.assertEqual(jobs.listed[0]["prompt"], "Edited while installing")
        self.assertIn("changed during installation", out)

    def test_extraction_uses_its_own_template(self):
        self._complete_install()
        job = self.job(prompt=OLD["EXTRACT_PROMPT"])
        job["script"] = install_cron.EXTRACT_SCRIPT
        jobs = StoredJobs([job])
        rc, out = self._run_main(jobs, "--sweep-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [("night", {"prompt": install_cron.EXTRACT_PROMPT})])

    def test_custom_rebind_changes_only_skills_even_for_a_one_character_edit(self):
        self._complete_install()
        job = self.job(prompt=OLD["DREAM_PROMPT"] + " ")
        job["skills"] = ["dreaming", "house-rules"]
        before = job["prompt"]
        jobs = StoredJobs([job])
        rc, out = self.run_jobs(jobs, "--rebind")
        self.assertEqual(rc, 0, out)
        self.assertEqual(job["prompt"], before)
        self.assertEqual(jobs.updated, [("night", {"skill": support.PLUGIN_SKILL,
                                                  "skills": [support.PLUGIN_SKILL, "house-rules"]})])
        self.assertIn("custom/unknown prompt", out)

    def test_retry_after_partial_failure_updates_only_the_remaining_job(self):
        self._complete_install()
        class Interrupted(StoredJobs):
            failed = False
            def update_job(self, job_id, updates):
                if job_id == "extract" and not self.failed:
                    self.failed = True
                    raise OSError("interrupted write")
                return super().update_job(job_id, updates)
        extract = self.job(prompt=OLD["EXTRACT_PROMPT"])
        extract.update(id="extract", script=install_cron.EXTRACT_SCRIPT)
        jobs = Interrupted([self.job(), extract])
        rc, out = self._run_main(jobs, "--sweep-schedule", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("update failed", out)
        self.assertEqual([jid for jid, _ in jobs.updated], ["night"])
        rc, out = self._run_main(jobs, "--sweep-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual([jid for jid, _ in jobs.updated], ["night", "extract"])

    def test_missing_update_result_is_not_reported_as_success(self):
        self._complete_install()
        class Deleted(StoredJobs):
            def update_job(self, job_id, updates):
                return None
        rc, out = self.run_jobs(Deleted([self.job()]))
        self.assertEqual(rc, 1, out)
        self.assertIn("not confirmed", out)
        self.assertNotIn("stock prompt updated", out)

    def test_force_creates_another_job_and_never_updates_existing_one(self):
        self._complete_install()
        jobs = StoredJobs([self.job()])
        rc, out = self.run_jobs(jobs, "--force")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jobs.updated, [])
        self.assertEqual(len(jobs.created), 1)

    def test_dry_run_unreadable_or_invalid_store_never_plans_duplicates(self):
        self._complete_install()
        path = self.home / "cron/jobs.json"
        path.parent.mkdir()
        for content in ('{"jobs": [', '{"jobs": null}', '{"jobs": [null]}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                jobs = StoredJobs([self.job()])
                with self.assertRaises(SystemExit):
                    self.run_jobs(jobs, "--dry-run")
                self.assertEqual((jobs.created, jobs.updated), ([], []))


class PromptCheckTests(unittest.TestCase):
    setUp = support.CheckJobsTests.setUp
    tearDown = support.CheckJobsTests.tearDown
    _regular_skill = support.CheckJobsTests._regular_skill
    _check = support.CheckJobsTests._check

    def test_check_distinguishes_current_stale_and_custom_prompts(self):
        self._regular_skill()
        (self.home / "dreaming.json").write_text(
            json.dumps({"cron": {"expected_roles": ["night", "sweep"]}}), encoding="utf-8")
        for prompt, expected, wording in ((install_cron.DREAM_PROMPT, (1, 0, 0), "current"),
                                         (OLD["DREAM_PROMPT"], (1, 0, 1), "outdated stock prompt"),
                                         ("My rules", (1, 1, 0), "custom/unknown prompt")):
            with self.subTest(wording=wording):
                jobs = [{"id": "night", "script": install_cron.DREAM_SCRIPT,
                         "skills": ["dreaming"], "prompt": prompt},
                        {"id": "sweep", "script": install_cron.SWEEP_SCRIPT, "no_agent": True}]
                (self.home / "cron/jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
                r, out = self._check()
                self.assertEqual((r.ok_n, r.warn_n, r.bad_n), expected, out)
                self.assertIn(wording, out)
