"""Does anything run the night? (Fable's review of agent-dreaming 0.8, carried over to Hermes.)

Requirements, each with its tests:
- `install.py --check` names an active dreaming job that Hermes will not fire on its own: one whose `next_run_at` has
  passed by more than an hour (cron jobs fire only while the gateway runs, and the gateway moves `next_run_at` on
  after every fire; a gateway that is down leaves it in the past), or one whose `next_run_at` is null (Hermes: "an
  enabled, unpaused record must never persist without next_run_at — it would never fire"). A job a few minutes
  late, due later, paused (already reported as paused), running now, or a record that has no `next_run_at` field at
  all is not named — the last one says nothing either way;
- a human who answers a dream question or asks about the dream can be told that no night is coming: the night
  itself cannot say so, a job that does not fire sends no report. `dream.py --nightly-status` says in one line per
  problem what keeps the nightly dream from running (no job, paused, finished or failed, overdue, no next run) and
  what to run, and exits 1; on a healthy job it says so and exits 0; a night left out of `cron.expected_roles` is
  not a problem. `dream.py --dry-run` carries the same problem lines. SKILL.md tells the agent to check.

Run:  python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from test_install import ROOT, install, install_cron

BANGKOK = timezone(timedelta(hours=7))


def at(**delta):
    """An ISO time as Hermes writes it in jobs.json (local offset, microseconds), *delta* from now."""
    return (datetime.now(BANGKOK) + timedelta(**delta)).isoformat()


def job(job_id, script, *, next_run_at=..., state="scheduled", enabled=True):
    """A record shaped like a live Hermes 0.22 jobs.json entry."""
    record = {"id": job_id, "name": f"Dreaming: {script}", "script": script, "state": state, "enabled": enabled,
              "schedule": {"kind": "cron", "expr": "0 3 * * *", "display": "0 3 * * *"},
              "last_run_at": at(days=-1), "last_status": "ok", "created_at": at(days=-30)}
    if script != install_cron.SWEEP_SCRIPT:
        record.update(skills=["dreaming"], skill="dreaming",
                      prompt=install_cron.DREAM_PROMPT if script == install_cron.DREAM_SCRIPT
                      else install_cron.EXTRACT_PROMPT)
    else:
        record["no_agent"] = True
    if next_run_at is not ...:
        record["next_run_at"] = next_run_at
    return record


class Home:
    def __init__(self, test):
        self.path = Path(tempfile.mkdtemp(prefix="nightly-run-"))
        test.addCleanup(shutil.rmtree, self.path, True)
        (self.path / "skills" / "dreaming").mkdir(parents=True)
        (self.path / "skills" / "dreaming" / "SKILL.md").write_text("# skill", encoding="utf-8")
        patch = mock.patch.dict(os.environ)
        patch.start()
        test.addCleanup(patch.stop)
        os.environ.pop("DREAM_CONFIG", None)

    def jobs(self, *records, roles=("night", "extract", "sweep")):
        (self.path / "cron").mkdir(exist_ok=True)
        (self.path / "cron" / "jobs.json").write_text(json.dumps({"jobs": list(records)}), encoding="utf-8")
        (self.path / "dreaming.json").write_text(json.dumps({"cron": {"expected_roles": list(roles)}}),
                                                 encoding="utf-8")


class CheckNamesAJobThatWillNotFireTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)

    def check(self, *records):
        self.home.jobs(*records, roles=("night",))
        r, out = install.Report(), io.StringIO()
        with contextlib.redirect_stdout(out):
            install.check_jobs(self.home.path, True, r)
        return [ln for ln in out.getvalue().splitlines() if ln.lstrip().startswith("!")], out.getvalue()

    def named(self, lines, job_id):
        return [ln for ln in lines if f"job {job_id}" in ln and ("gateway" in ln or "will not fire" in ln)]

    def test_a_job_due_hours_ago_points_at_the_gateway(self):
        lines, out = self.check(job("n1", install_cron.DREAM_SCRIPT, next_run_at=at(hours=-3)))
        found = self.named(lines, "n1")
        self.assertTrue(found, out)
        self.assertIn("hermes gateway status", found[0])

    def test_a_job_with_no_next_run_will_not_fire(self):
        lines, out = self.check(job("n1", install_cron.DREAM_SCRIPT, next_run_at=None))
        self.assertTrue(self.named(lines, "n1"), out)

    def test_a_job_on_time_or_minutes_late_is_not_named(self):
        for name, when in (("due tomorrow", at(hours=20)), ("30 minutes late", at(minutes=-30))):
            with self.subTest(name):
                lines, out = self.check(job("n1", install_cron.DREAM_SCRIPT, next_run_at=when))
                self.assertFalse(self.named(lines, "n1"), out)

    def test_a_paused_or_running_job_is_not_called_overdue(self):
        for name, kwargs in (("paused", {"state": "paused", "enabled": False}), ("running", {"state": "running"})):
            with self.subTest(name):
                lines, out = self.check(job("n1", install_cron.DREAM_SCRIPT, next_run_at=at(days=-2), **kwargs))
                self.assertFalse(self.named(lines, "n1"), out)

    def test_a_record_without_the_field_says_nothing_either_way(self):
        lines, out = self.check(job("n1", install_cron.DREAM_SCRIPT))
        self.assertFalse(self.named(lines, "n1"), out)


class NightlyStatusTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)

    def dream(self, *argv):
        dream = install._load_dream(self.home.path, self.home.path / "dreaming.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = dream.main(["--hermes-home", str(self.home.path), *argv])
        return rc, out.getvalue()

    def test_no_job_at_all(self):
        rc, out = self.dream("--nightly-status")
        self.assertEqual(rc, 1, out)
        self.assertIn("no cron job runs the nightly dream", out)
        self.assertIn("install_cron.py", out)

    def test_a_paused_job_says_how_to_resume_it(self):
        self.home.jobs(job("n1", install_cron.DREAM_SCRIPT, state="paused", enabled=False, next_run_at=None))
        rc, out = self.dream("--nightly-status")
        self.assertEqual(rc, 1, out)
        self.assertIn("paused", out)
        self.assertIn("hermes cron resume n1", out)

    def test_an_overdue_job_points_at_the_gateway(self):
        self.home.jobs(job("n1", install_cron.DREAM_SCRIPT, next_run_at=at(days=-2)))
        rc, out = self.dream("--nightly-status")
        self.assertEqual(rc, 1, out)
        self.assertIn("hermes gateway status", out)

    def test_a_healthy_job_is_said_to_be_fine(self):
        self.home.jobs(job("n1", install_cron.DREAM_SCRIPT, next_run_at=at(hours=8)))
        rc, out = self.dream("--nightly-status")
        self.assertEqual(rc, 0, out)
        self.assertIn("n1", out)
        self.assertNotIn("gateway", out)

    def test_a_night_left_out_on_purpose_is_not_a_problem(self):
        self.home.jobs(roles=("extract",))
        rc, out = self.dream("--nightly-status")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("no cron job", out)

    def test_the_dry_run_carries_the_problem(self):
        rc, out = self.dream("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nightly run:", out)
        self.assertIn("no cron job runs the nightly dream", out)

    def test_the_skill_tells_the_agent_to_check(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--nightly-status", text)


if __name__ == "__main__":
    unittest.main()
