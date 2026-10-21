"""State files after the review of 2.9.8 (articles «10 принципов» / vibe-coding checklist).

- A file the dream reads as its own state and that is not a JSON object is no longer read as "no state" in silence:
  it is moved aside to `<file>.corrupt-<time>` with a warning. A missing file is still a fresh install, no noise.
- A whole-file write goes to a temporary file and is renamed over the target: a write that dies half-way leaves the
  old file, not an empty one.
- `dream-reject.py` stores no text when its screen cannot be loaded.
- Hermes' own `cron/jobs.json` is read, never moved aside.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_dream import DreamFixture, dream, reject  # noqa: E402  (same directory)


def _aside(path):
    path = Path(path)
    return sorted(p.name for p in path.parent.iterdir() if p.name.startswith(path.name + ".corrupt-"))


class BrokenStateIsSetAside(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, loader, text, name="state.json"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            data = loader(str(path))
        return path, data, err.getvalue()

    def test_every_state_loader_moves_a_broken_file_aside(self):
        loaders = {"reject list": dream.load_rejected, "cooldowns": dream.load_seen,
                   "questions": dream._load_asked_state, "snapshot": dream.load_snapshot,
                   "dream-reject": reject.load_state}
        for what, loader in loaders.items():
            with self.subTest(what):
                path, data, err = self._load(loader, '{"abc": {"at": "2026-10-2', name=f"{what}.json")
                self.assertEqual(data, {})
                self.assertFalse(path.exists(), "the broken file must not stay under its name")
                self.assertEqual(len(_aside(path)), 1)
                self.assertIn("not valid JSON", err)
                self.assertIn("moved to", err)

    def test_not_an_object_is_broken_too(self):
        path, data, err = self._load(dream.load_rejected, "[1, 2, 3]")
        self.assertEqual(data, {})
        self.assertEqual(len(_aside(path)), 1)
        self.assertIn("not a JSON object (a list)", err)

    def test_an_empty_file_is_broken_too(self):
        """What an interrupted in-place write left behind before 2.9.9."""
        path, data, err = self._load(dream.load_seen, "")
        self.assertEqual(data, {})
        self.assertEqual(len(_aside(path)), 1)

    def test_not_utf8_is_broken_too(self):
        path = self.dir / "state.json"
        path.write_bytes(b'{"a": "\xff\xfe"}')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dream.load_rejected(str(path)), {})
        self.assertIn("not UTF-8", err.getvalue())
        self.assertEqual(len(_aside(path)), 1)

    def test_a_missing_file_is_a_fresh_install_without_noise(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dream.load_rejected(str(self.dir / "none.json")), {})
            self.assertEqual(dream.load_seen(""), {})
            self.assertEqual(dream.load_snapshot(None), {})
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_good_file_is_read_as_before(self):
        path, data, err = self._load(dream.load_rejected, json.dumps({"k": {"content": "x"}}))
        self.assertEqual(data, {"k": {"content": "x"}})
        self.assertTrue(path.exists())
        self.assertEqual(err, "")

    def test_the_rejections_survive_the_next_rejection(self):
        """Read as {} a broken reject list was overwritten by the next `dream-reject.py`, and every earlier
        rejection was gone for good. Now the old file is kept beside the new one."""
        path, data, _ = self._load(reject.load_state, '{"old": {"content": "Thread 9 is not needed"')
        reject.save_state(str(path), {"new": {"content": "Thread 10"}})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": {"content": "Thread 10"}})
        (aside,) = _aside(path)
        self.assertIn("Thread 9", (self.dir / aside).read_text(encoding="utf-8"))

    def test_list_without_the_lock_never_moves_the_file(self):
        """Codex review of the 2.9.9 changes: `--list` runs without the state lock. Moving a broken list aside
        there raced a locked writer — it could move away the good list the writer had just put in its place, and
        every rejection came back the next night. `--list` now only reads; a locked run moves the file."""
        path = self.dir / "dream-rejected.json"
        broken = '{"old": {"content": "Thread 9 is not needed"'
        path.write_text(broken, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(reject.main(["--list", "--state", str(path)]), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), broken, "left exactly as it was")
        self.assertEqual(_aside(path), [])
        self.assertIn("left as it is", err.getvalue())
        self.assertIn("reject list is empty", out.getvalue())

    def test_the_reader_reports_and_touches_nothing(self):
        path = self.dir / "state.json"
        path.write_text("[1]", encoding="utf-8")
        self.assertEqual(dream._read_state_file(str(path)), ({}, "not a JSON object (a list)"))
        self.assertTrue(path.exists())
        self.assertEqual(_aside(path), [])

    def test_hermes_jobs_file_is_read_never_moved(self):
        """`cron/jobs.json` is Hermes' live scheduler state, not the dream's: a broken one is read as no jobs and
        left exactly where it is."""
        (self.dir / "cron").mkdir()
        jobs = self.dir / "cron" / "jobs.json"
        jobs.write_text("{broken", encoding="utf-8")
        self.assertEqual(dream._dream_job_dirs(str(self.dir)), [])
        self.assertTrue(jobs.exists())
        self.assertEqual(_aside(jobs), [])


class WholeFileWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dream-seen.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_write_that_dies_leaves_the_old_file(self):
        self.path.write_text('{"old": 1}', encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("No space left on device")):
            with self.assertRaises(OSError):
                dream._write_private(str(self.path), '{"new": 2}')
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"old": 1}')
        self.assertEqual(os.listdir(self.tmp.name), ["dream-seen.json"], "no temporary file is left")

    def test_a_write_replaces_the_whole_file(self):
        self.path.write_text('{"old": "a much longer previous content"}', encoding="utf-8")
        dream._write_private(str(self.path), '{"n": 1}')
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"n": 1}')
        self.assertEqual(os.listdir(self.tmp.name), ["dream-seen.json"])

    def test_append_still_appends(self):
        dream._write_private(str(self.path), "a\n", append=True)
        dream._write_private(str(self.path), "b\n", append=True)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "a\nb\n")

    def test_a_symlinked_state_file_is_written_through(self):
        real = Path(self.tmp.name) / "real.json"
        real.write_text("{}", encoding="utf-8")
        try:
            os.symlink(real, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        dream._write_private(str(self.path), '{"n": 1}')
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), '{"n": 1}')


class ThePassRunsOnPastABrokenState(DreamFixture):
    def test_broken_cooldowns_are_kept_aside_and_rewritten(self):
        seen = self.home / "cache" / "dream-seen.json"
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text('{"new_facts": {"abc": {"at": "2026-10-2', encoding="utf-8")
        self.add_fact("Виктор предпочитает зелёный чай без сахара по утрам")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            dream.run(14, 60, 60, str(self.mem_md), seen_state_path=str(seen))
        self.assertIn("moved to", err.getvalue())
        self.assertIsInstance(json.loads(seen.read_text(encoding="utf-8")), dict)
        (aside,) = _aside(seen)
        self.assertIn('"abc"', (seen.parent / aside).read_text(encoding="utf-8"))


class ConfirmStampsAgeOut(DreamFixture):
    def test_old_confirm_stamps_are_pruned_fresh_ones_kept(self):
        """The `confirm` cooldowns were stamped and never pruned: the seen-state grew by one key per fact offered
        for a confirmation, forever. Pruned now like `trust_feedback` and `keywords_needed`."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        seen = self.home / "cache" / "dream-seen.json"
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text(json.dumps({"confirm": {
            "old": {"at": (now - timedelta(days=60)).isoformat(timespec="seconds"), "fact_id": 1},
            "fresh": {"at": (now - timedelta(days=1)).isoformat(timespec="seconds"), "fact_id": 2}}}),
            encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            dream.run(14, 60, 60, str(self.mem_md), seen_state_path=str(seen))
        bucket = json.loads(seen.read_text(encoding="utf-8"))["confirm"]
        self.assertNotIn("old", bucket)
        self.assertIn("fresh", bucket)


class BrokenConfigStopsThePass(unittest.TestCase):
    """A dreaming.json that is there but broken stops the nightly pass (exit 2 → `dream_error` in the gate) instead
    of a night on the defaults; a missing one still means the defaults, and `--dry-run` still reads past it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "memories").mkdir()
        self.saved = (dream.HOME, dream.CONFIG)

    def tearDown(self):
        dream.HOME = self.saved[0]
        dream.configure(self.saved[1])
        self.tmp.cleanup()

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dream.main(["--hermes-home", str(self.home), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_problem_is_named(self):
        cfg = self.home / "dreaming.json"
        self.assertIsNone(dream.config_problem(str(cfg)))
        cfg.write_text('{"timezone": "Asia/Bangkok",}', encoding="utf-8")
        self.assertIn("cannot be used", dream.config_problem(str(cfg)))
        cfg.write_text("[]", encoding="utf-8")
        self.assertIn("not an object", dream.config_problem(str(cfg)))
        cfg.write_text("{}", encoding="utf-8")
        self.assertIsNone(dream.config_problem(str(cfg)))

    def test_the_pass_stops_on_a_broken_config(self):
        (self.home / "dreaming.json").write_text('{"trusted_chat_ids": ["-100111",]}', encoding="utf-8")
        out_file = self.home / "cache" / "dream.json"
        rc, out, err = self._main("--out", str(out_file), "--diary", str(self.home / "memories" / "DREAMS.md"))
        self.assertEqual(rc, 2)
        self.assertIn("[dream] error: config", err)
        self.assertFalse(out_file.exists(), "no payload from a night on the defaults")
        self.assertFalse((self.home / "memories" / "DREAMS.md").exists())

    def test_dry_run_still_reads_past_it(self):
        (self.home / "dreaming.json").write_text("{broken", encoding="utf-8")
        rc, out, err = self._main("--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Dry run", out)
        self.assertIn("ignored", err)

    def test_a_missing_config_is_the_defaults(self):
        out_file = self.home / "cache" / "dream.json"
        out_file.parent.mkdir()
        rc, out, err = self._main("--out", str(out_file))
        self.assertIn(rc, (0, None))
        self.assertTrue(out_file.exists())


class RejectForAnotherHome(unittest.TestCase):
    def test_hermes_home_brings_its_own_config(self):
        """B3, reproduced in the review of 2.9.8: dream.py configured itself at import for $HERMES_HOME, so
        `dream-reject.py <id> --hermes-home B` looked for the fact in A's source and said "not found"."""
        import subprocess
        import sys
        from test_dream import ROOT
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            Path(a, "dreaming.json").write_text(json.dumps({"fact_source": "none"}), encoding="utf-8")
            Path(b, "dreaming.json").write_text(json.dumps({"fact_source": "jsonl", "fact_store_path": "facts.jsonl"}),
                                                encoding="utf-8")
            Path(b, "facts.jsonl").write_text(json.dumps({"id": 7, "content": "Thread 9 is the child's thread"}) + "\n",
                                              encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("DREAM_")}
            env.update(HERMES_HOME=a, PYTHONIOENCODING="utf-8")
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "dream-reject.py"), "7", "--hermes-home", b,
                                   "--reason", "outdated"], capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(Path(b, "cache", "dream-rejected.json").exists())
            self.assertFalse(Path(a, "cache", "dream-rejected.json").exists())


class UtcNeedsNoZoneDatabase(unittest.TestCase):
    def test_utc_by_any_name_is_quiet(self):
        """Without `tzdata` (Windows) the default "UTC" was reported as an unknown zone on every run."""
        from datetime import timezone
        for name in ("UTC", "utc", "Etc/UTC", "GMT", "Z", None, ""):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIs(dream._tz(name), timezone.utc)
            self.assertEqual(err.getvalue(), "", name)

    def test_an_unknown_zone_still_warns(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            dream._tz("Mars/Olympus_Mons")
        self.assertIn("unknown timezone 'Mars/Olympus_Mons'", err.getvalue())


class RejectWithoutTheScreen(unittest.TestCase):
    def test_no_screen_means_no_text_is_stored(self):
        err = io.StringIO()
        with mock.patch.object(reject._dream(), "classify_unsafe", side_effect=RuntimeError("screen broke")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(reject._classify_unsafe("Код от калитки 4512"), "secret")
        self.assertIn("screen broke", err.getvalue())


if __name__ == "__main__":
    unittest.main()
