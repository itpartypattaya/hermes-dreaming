"""What the night sees, not what the installing terminal sees (found by comparing with agent-dreaming 0.8.2-0.8.3).

Requirements, each with its tests:
- the cloud judge's key counts for `install.py --check` only where the nightly pass can read it. Hermes keeps
  provider keys out of the environment of cron scripts (and `terminal.env_passthrough` cannot let them through), so
  the gate reads the key's own line in <home>/.env and nothing else. A key exported only in the installing terminal
  must not be reported as present: the check says the night will not see it and where to put it;
- a DREAM_* setting exported only in the installing terminal is named: the gates run in the gateway's environment,
  which loads <home>/.env, not this terminal's, so the night reads other settings (another config, the default
  score). A variable with the same value in <home>/.env is what the night sees too and is not named (the false
  warning agent-dreaming 0.8.3 fixed);
- the "Next" command `install.py` prints runs as printed: Hermes' own interpreter as it is on this install (a
  Windows venv keeps it in Scripts\\python.exe, not bin/python), every path quoted for the shell it is pasted into,
  a home with a space or an apostrophe included.

Run:  python -m unittest discover -s tests
"""
import contextlib
import io
import os
import re
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_dream import dream
from test_install import install

KEY = "sk-or-v1-" + "7f3a" * 12


def _clean_environment():
    """os.environ without the judge's keys and without any DREAM_* setting, restored afterwards."""
    patch = mock.patch.dict(os.environ)
    patch.start()
    for name in list(os.environ):
        if name.startswith("DREAM_") or name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            del os.environ[name]
    return patch


class JudgeKeySeenAtNightTests(unittest.TestCase):
    ON = {"mode": "shadow", "providers": ["jev"], "send_from": "trusted", "send_sources": ["telegram"]}

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="judge-night-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(setattr, dream, "SETTINGS", dream.SETTINGS)
        self.addCleanup(_clean_environment().stop)

    def check(self):
        with contextlib.redirect_stderr(io.StringIO()):
            dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, {"judge": self.ON}))
        report = install.Report()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            install.check_judge(self.home, dream, report)
        return report, out.getvalue()

    def test_a_key_only_in_this_terminal_is_not_a_key_the_night_has(self):
        os.environ["OPENROUTER_API_KEY"] = KEY
        report, out = self.check()
        self.assertEqual(report.bad_n, 1, out)  # no provider the night can use: every night degraded
        self.assertIn("OPENROUTER_API_KEY", out)
        self.assertIn(str(self.home / ".env"), out)
        self.assertIn("terminal", out)
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY[-6:], out)

    def test_a_key_in_the_env_file_is_there_at_night(self):
        os.environ["OPENROUTER_API_KEY"] = KEY  # also exported here: changes nothing
        (self.home / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\n", encoding="utf-8")
        report, out = self.check()
        self.assertEqual((report.bad_n, report.warn_n), (0, 0), out)
        self.assertNotIn("terminal", out)


class DreamSettingsSeenAtNightTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="dream-env-night-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(_clean_environment().stop)

    def run_install(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            install.main(["--hermes-home", str(self.home)])
        return out.getvalue()

    def warnings_about(self, out, name):
        return [line for line in out.splitlines() if line.lstrip().startswith("!") and name in line]

    def test_a_setting_only_in_this_terminal_is_named(self):
        os.environ["DREAM_MIN_SCORE"] = "0.7"
        os.environ["DREAM_CONFIG"] = str(self.home / "elsewhere.json")
        out = self.run_install()
        for name in ("DREAM_MIN_SCORE", "DREAM_CONFIG"):
            with self.subTest(name=name):
                self.assertTrue(self.warnings_about(out, name), out)
        line = self.warnings_about(out, "DREAM_CONFIG")[0]
        self.assertIn(str(self.home / ".env"), line)

    def test_the_same_setting_in_the_env_file_is_not_named(self):
        os.environ["DREAM_MIN_SCORE"] = "0.7"
        (self.home / ".env").write_text("# dreaming\nexport DREAM_MIN_SCORE='0.7'\n", encoding="utf-8")
        out = self.run_install()
        self.assertFalse(self.warnings_about(out, "DREAM_MIN_SCORE"), out)

    def test_another_value_in_the_env_file_is_named(self):
        os.environ["DREAM_MIN_SCORE"] = "0.7"
        (self.home / ".env").write_text("DREAM_MIN_SCORE=0.5\n", encoding="utf-8")
        out = self.run_install()
        self.assertTrue(self.warnings_about(out, "DREAM_MIN_SCORE"), out)


def _pwsh_words(line):
    """The single-quoted words of a PowerShell line, as PowerShell reads them ('' is one quote)."""
    return [w.replace("''", "'") for w in re.findall(r"'((?:[^']|'')*)'", line)]


class NextCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="next-command-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(_clean_environment().stop)
        self.home = self.root / "Leo's hermes home"
        self.home.mkdir()

    def next_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = install.main(["--hermes-home", str(self.home)])
        self.assertEqual(rc, 0, out.getvalue())
        lines = [ln for ln in out.getvalue().splitlines() if "--deliver" in ln]
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertIn("install_cron.py", lines[0])  # the interpreter on the same line: no shell-specific continuation
        return lines[0].strip()

    def words(self, line):
        return _pwsh_words(line) if os.name == "nt" else shlex.split(line)

    def test_a_windows_venv_is_run_from_scripts(self):
        python = self.home / "hermes-agent" / "venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        line = self.next_command()
        self.assertNotIn("venv/bin/python", line)
        self.assertEqual(self.words(line)[0], str(python))

    def test_a_home_with_a_space_and_an_apostrophe_reaches_install_cron_whole(self):
        python = self.home / "hermes-agent" / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        words = self.words(self.next_command())
        self.assertEqual(words[0], str(python))
        self.assertTrue(words[1].endswith("install_cron.py"), words)
        self.assertEqual(words[words.index("--hermes-home") + 1], str(self.home))


if __name__ == "__main__":
    unittest.main()
