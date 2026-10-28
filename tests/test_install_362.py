"""3.6.2, the installers after the review of 3.5.0–3.6.1.

Requirements, each with its tests:
- a gate copy interrupted half-way (an I/O error after part of the file was written) leaves the previous gate in
  scripts/ byte for byte, and no temporary file beside it: Hermes runs whatever file is there, the forget sweep
  every 15 minutes, so a truncated script must never take the old one's place. Both `install_cron.py --upgrade`
  and `install.py` copy gates;
- a job the installer refuses to change (a finished or failed job that needs a prompt update, a record whose state
  it cannot classify) is named in the error with its role, the state it has and what to do about it; nothing is
  written and the run exits non-zero. Refusing the whole run is the 3.5.0 contract ("all targets ... are checked
  before gate writes"; corrupt job shapes stop writes), kept as it is.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""
import builtins
import contextlib
import errno
import io
import os
import unittest
from pathlib import Path
from unittest import mock

import test_cron_prompt_sync as prompts
import test_cron_upgrade as upgrade
import test_install as support

ic = support.install_cron
PREVIOUS = "#!/usr/bin/env python3\n# the gate of the previous release, still working\nprint('{\"wakeAgent\": false}')\n"


class _CutOff(io.RawIOBase):
    """The skill's copy of a gate, read until an I/O error after the first kilobyte (a bad sector, a vanished
    mount). No file descriptor: the copy has to read through it, as a plain stream."""

    def __init__(self, data):
        self._data, self._given = data, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        if self._given >= 1024:
            raise OSError(errno.EIO, "Input/output error")
        chunk = self._data[self._given:min(self._given + len(buffer), 1024)]
        buffer[:len(chunk)] = chunk
        self._given += len(chunk)
        return len(chunk)


@contextlib.contextmanager
def reading_the_gate_fails_midway(script):
    source = (support.ROOT / "scripts" / script).resolve()
    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if "r" in mode and "b" in mode and isinstance(file, (str, os.PathLike)) and Path(file).resolve() == source:
            return io.BufferedReader(_CutOff(source.read_bytes()))
        return real_open(file, mode, *args, **kwargs)

    with mock.patch("builtins.open", fake_open):
        yield


class GateCopyTests(unittest.TestCase):
    setUp = support.InstallCronTests.setUp
    tearDown = support.InstallCronTests.tearDown
    _run_main = support.InstallCronTests._run_main
    _install_plugin = prompts.PromptSyncTests._install_plugin
    ready = upgrade.UpgradeTests.ready
    job = upgrade.UpgradeTests.job
    expected = upgrade.UpgradeTests.expected

    def assert_previous_gate_kept(self, script):
        gate = self.home / "scripts" / script
        self.assertEqual(gate.read_text(encoding="utf-8"), PREVIOUS, "a cut-off copy replaced the working gate")
        leftovers = sorted(p.name for p in gate.parent.iterdir()
                           if p.name not in (ic.DREAM_SCRIPT, ic.EXTRACT_SCRIPT, ic.SWEEP_SCRIPT))
        self.assertEqual(leftovers, [], "a temporary file stayed in scripts/")

    def test_upgrade_cut_off_while_copying_keeps_the_previous_gate(self):
        self.ready()
        self.expected(["night", "sweep"])
        (self.home / "scripts" / ic.SWEEP_SCRIPT).write_text(PREVIOUS, encoding="utf-8")
        jobs = prompts.StoredJobs([self.job(prompt=prompts.OLD["DREAM_PROMPT"]), self.job("sweep")])
        with reading_the_gate_fails_midway(ic.SWEEP_SCRIPT):
            rc, out = self._run_main(jobs, "--upgrade", "--extract-schedule", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("Input/output error", out)
        self.assert_previous_gate_kept(ic.SWEEP_SCRIPT)
        self.assertEqual(jobs.updated, [], "a failed gate copy stops the job updates after it")
        rc, out = self._run_main(jobs, "--upgrade", "--extract-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.home / "scripts" / ic.SWEEP_SCRIPT).read_bytes(),
                         (support.ROOT / "scripts" / ic.SWEEP_SCRIPT).read_bytes(), "a retry completes the copy")

    def test_install_cut_off_while_copying_keeps_the_previous_gate(self):
        (self.home / "scripts" / ic.SWEEP_SCRIPT).write_text(PREVIOUS, encoding="utf-8")
        out = io.StringIO()
        with reading_the_gate_fails_midway(ic.SWEEP_SCRIPT), contextlib.redirect_stdout(out):
            rc = support.install.main(["--hermes-home", str(self.home)])
        self.assertEqual(rc, 1, out.getvalue())
        self.assertIn(f"cannot copy {ic.SWEEP_SCRIPT}", out.getvalue())
        self.assert_previous_gate_kept(ic.SWEEP_SCRIPT)

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX")
    def test_a_copied_gate_is_executable(self):
        self.ready()
        (self.home / "scripts" / ic.SWEEP_SCRIPT).write_text(PREVIOUS, encoding="utf-8")
        self.expected(["sweep"])
        rc, out = self._run_main(prompts.StoredJobs([self.job("sweep")]), "--upgrade",
                                 "--dream-schedule", "", "--extract-schedule", "")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.home / "scripts" / ic.SWEEP_SCRIPT).stat().st_mode & 0o777, 0o755)


class RefusedJobTests(unittest.TestCase):
    """Records as Hermes keeps them in cron/jobs.json: a recurring job whose next run could not be computed is left
    in state "error" with last_error (cron/jobs.py), a hand-edited record can carry a state Hermes never writes."""
    setUp = support.InstallCronTests.setUp
    tearDown = support.InstallCronTests.tearDown
    _run_main = support.InstallCronTests._run_main
    _install_plugin = prompts.PromptSyncTests._install_plugin
    ready = upgrade.UpgradeTests.ready
    job = upgrade.UpgradeTests.job

    def test_a_failed_job_that_needs_a_new_prompt_is_named_with_its_fix(self):
        self.ready()
        (self.home / "scripts" / ic.EXTRACT_SCRIPT).write_text(PREVIOUS, encoding="utf-8")
        night = self.job(id="a1b2c3d4e5f6", prompt=prompts.OLD["DREAM_PROMPT"], state="error", next_run_at=None,
                         last_status="error",
                         last_error="Failed to compute next run for recurring schedule (is the 'croniter' package "
                                    "installed in the gateway's Python env?)")
        extract = self.job("extract", id="0f9e8d7c6b5a", prompt=prompts.OLD["EXTRACT_PROMPT"])
        jobs = prompts.StoredJobs([night, extract, self.job("sweep", id="5a6b7c8d9e0f")])
        rc, out = self._run_main(jobs, "--upgrade")
        self.assertEqual(rc, 1, out)
        self.assertEqual((jobs.created, jobs.updated), ([], []))
        self.assertEqual((self.home / "scripts" / ic.EXTRACT_SCRIPT).read_text(encoding="utf-8"), PREVIOUS,
                         "the refusal comes before any gate write")
        self.assertIn("job a1b2c3d4e5f6 (night)", out)
        self.assertIn("state 'error'", out)
        self.assertIn("hermes cron resume a1b2c3d4e5f6", out)
        self.assertIn("hermes cron remove a1b2c3d4e5f6", out)
        self.assertIn("--job-id", out)

    def test_a_record_of_an_unknown_state_is_named_with_its_fix(self):
        self.ready()
        night = self.job(id="a1b2c3d4e5f6", state="disabled", enabled="false")
        jobs = prompts.StoredJobs([night, self.job("extract", id="0f9e8d7c6b5a"), self.job("sweep", id="5a6b7c8d9e0f")])
        rc, out = self._run_main(jobs, "--upgrade")
        self.assertEqual(rc, 1, out)
        self.assertEqual((jobs.created, jobs.updated), ([], []))
        self.assertIn("job a1b2c3d4e5f6 (night)", out)
        self.assertIn("'disabled'", out)
        self.assertIn("hermes cron list --all", out)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
