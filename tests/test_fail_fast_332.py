"""3.3.2: what cannot be read is not reported as empty (second pass over «10 принципов кода для AI-агентов»).

- A busy chat database is "messages unavailable", not "nobody said anything": no relevance questions that night,
  one alert instead (A1).
- A memory file that exists but cannot be read stops the pass (A2); so does one of the dream's own state files,
  and nothing is written over it (P1).
- A config value of the wrong type is reported, never taken silently: a "false" string no longer turns trust on
  (P3), a bad environment value or number is named in stderr (A6, A7), a string where a list of keys belongs no
  longer keeps the gate shut (A8).
- `dream-reject.py` tells a broken fact store from a missing fact (A4); the screen copier refuses a source that
  lacks a root it copies (A9); "Sept 5" is a date for the event check too (B2).
- The copies the gates must keep of dream.py's rules agree with it (B5).
"""

import contextlib
import importlib.util
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_dream import DreamFixture, ROOT, dream, precheck, reject  # noqa: E402  (same directory)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


extract = _load("dream_extract_332", ROOT / "scripts/dream-extract-precheck.py")
vendor = _load("_vendor_screen_332", ROOT / "tools" / "vendor_screen.py")
install = _load("_install_332", ROOT / "scripts" / "install.py")
install_cron = _load("_install_cron_332", ROOT / "scripts" / "install_cron.py")

# Old enough to be asked about, never mentioned: a question on any night the messages are readable.
MEMORY = "\n§\n".join([
    "Марина работает удалённо из дома по понедельникам и пятницам",
    "Пароль от вайфая на даче записан в синем блокноте на кухне",
    "Виктор не ест морепродукты: аллергия на креветки с детства",
    "Абонемент в бассейн оформлен на Ольгу до конца декабря",
])


def _quick_conn(path):
    """dream._conn without its 5-second wait for a lock: the same read-only open, a test does not idle."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.1)


class MessagesUnavailable(DreamFixture):
    """A1: a locked state.db used to read as "nobody talked" — every memory entry became "still relevant?"."""

    def setUp(self):
        super().setUp()
        self.mem_md.write_text(MEMORY, encoding="utf-8")
        self.add_fact("Ольга собирает марки с острова Мадейра с прошлого лета", trust=0.5, rc=0, days_old=200)
        self.asked = self.home / "cache" / "asked.json"
        self.seen = self.home / "cache" / "seen.json"

    @contextlib.contextmanager
    def locked_chats(self):
        """Another writer holds state.db exclusively (rollback journal: WAL would let a reader through)."""
        con = sqlite3.connect(self.home / "state.db", isolation_level=None)
        con.execute("BEGIN EXCLUSIVE")
        try:
            yield
        finally:
            con.execute("ROLLBACK")
            con.close()

    def test_a_locked_chat_database_asks_nothing_and_says_so(self):
        err = io.StringIO()
        with self.locked_chats(), mock.patch.object(dream, "_conn", _quick_conn), contextlib.redirect_stderr(err):
            result = self.run_dream(asked_state=str(self.asked), seen_state=str(self.seen), ack=False)
        self.assertEqual(result["md_decays"], [])
        self.assertEqual(result["fact_decays"], [])
        kinds = [a.get("kind") for a in result["alerts"]]
        self.assertIn("messages_unavailable", kinds)
        self.assertGreaterEqual(result["stats"]["alerts"], 1, "the gate wakes the agent on stats.alerts")
        self.assertFalse(self.asked.exists(), "no question asked, so none recorded as asked")

        # The same home with the messages readable does ask: the night above was quiet because of the lock.
        result = self.run_dream(asked_state=str(self.asked), seen_state=str(self.seen))
        self.assertTrue(result["md_decays"])
        self.assertTrue(result["fact_decays"])
        self.assertNotIn("messages_unavailable", [a.get("kind") for a in result["alerts"]])

    def test_explain_and_extraction_take_unavailable_messages(self):
        with mock.patch.object(dream, "load_messages", return_value=None):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(dream.explain("1", str(self.mem_md), 60), 0)
            self.assertIn("mentions 0", out.getvalue())
            self.assertIn("messages", err.getvalue())
            payload, cursor, count = extract.build_payload(dream, {}, {"since_ts": 1000.0, "since_id": 7},
                                                           2_000_000_000.0)
        self.assertIsNone(payload)
        self.assertEqual((cursor, count), ((1000.0, 7), 0), "the cursor stays where it was")


class UnreadableMemoryStopsThePass(DreamFixture):
    """A2: MEMORY.md that cannot be read was "no memory": every entry looked lost and the snapshot was emptied."""

    def test_unreadable_memory_file_stops_the_pass_and_keeps_the_snapshot(self):
        self.mem_md.write_text(MEMORY, encoding="utf-8")
        snapshot = self.home / "cache" / "snapshot.json"
        snapshot.parent.mkdir()
        dream.run(14, 60, 60, str(self.mem_md), snapshot_state_path=str(snapshot))
        before = snapshot.read_text(encoding="utf-8")
        self.mem_md.unlink()
        self.mem_md.mkdir()  # there, but not a readable file (a permission problem reads the same)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                dream.run(14, 60, 60, str(self.mem_md), snapshot_state_path=str(snapshot))
        self.assertEqual(snapshot.read_text(encoding="utf-8"), before)


class _Unreadable:
    """`open()` of one path fails as a file without read permission does (the rest of the file system is real)."""

    def __init__(self, path):
        self.path, self.real = os.path.abspath(path), open

    def __call__(self, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and os.path.abspath(file) == self.path:
            raise PermissionError(13, "Permission denied", str(file))
        return self.real(file, *args, **kwargs)


class UnreadableStateIsNotOverwritten(DreamFixture):
    """P1: a state file that exists but cannot be read was read as empty, and the next write replaced it."""

    def test_reject_refuses_to_write_over_a_list_it_could_not_read(self):
        state = self.home / "cache" / "rejected.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"abc": {"content": "старый отказ", "at": "2026-09-01"}}), encoding="utf-8")
        before = state.read_text(encoding="utf-8")
        err = io.StringIO()
        with mock.patch("builtins.open", _Unreadable(state)), contextlib.redirect_stderr(err):
            code = reject.main(["--content", "новый отказ", "--state", str(state), "--hermes-home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("cannot be read", err.getvalue())
        self.assertEqual(state.read_text(encoding="utf-8"), before)

    def test_the_nightly_pass_stops_instead_of_resetting_the_cooldowns(self):
        seen = self.home / "cache" / "seen.json"
        seen.parent.mkdir(exist_ok=True)
        seen.write_text(json.dumps({"new_facts": {"x": {"at": "2026-10-20T00:00:00+00:00"}}}), encoding="utf-8")
        before = seen.read_text(encoding="utf-8")
        with mock.patch("builtins.open", _Unreadable(seen)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                self.run_dream(seen_state=str(seen), ack=False)
        self.assertEqual(seen.read_text(encoding="utf-8"), before)


class ConfigValuesOfTheWrongType(unittest.TestCase):
    """P3, A6, A7: a value the dream cannot use is named in stderr; a trust switch never opens by mistake."""

    def setUp(self):
        self._cfg = dream.SETTINGS.CONFIG

    def tearDown(self):
        dream.configure(self._cfg)

    def configure(self, **over):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, over))
        return err.getvalue()

    def test_a_trust_switch_written_as_a_string_trusts_nothing(self):
        err = self.configure(trust_private_chats="false", trust_sessions_without_chat="no")
        self.assertFalse(dream.SETTINGS.TRUST_PRIVATE)
        self.assertFalse(dream.SETTINGS.TRUST_NO_CHAT)
        self.assertFalse(dream._trusted_message("telegram", "dm", "42"))
        self.assertFalse(dream._trusted_message("telegram", None, None))
        self.assertIn("trust_private_chats", err)
        self.assertIn("trust_sessions_without_chat", err)

    def test_real_booleans_still_work(self):
        self.assertEqual(self.configure(trust_private_chats=False, trust_sessions_without_chat=True), "")
        self.assertFalse(dream.SETTINGS.TRUST_PRIVATE)
        self.assertTrue(dream.SETTINGS.TRUST_NO_CHAT)

    def test_a_bad_environment_value_is_named(self):
        with mock.patch.dict(os.environ, {"DREAM_MIN_SCORE": "0,6"}):
            err = self.configure(gates={"min_score": 0.7})
        self.assertEqual(dream.SETTINGS.MIN_SCORE, 0.7)
        self.assertIn("DREAM_MIN_SCORE", err)

    def test_one_bad_pending_value_keeps_the_other(self):
        err = self.configure(pending={"alert_min": 3, "alert_days": "x"})
        self.assertEqual(dream.SETTINGS.PENDING_ALERT_MIN, 3)
        self.assertEqual(dream.SETTINGS.PENDING_ALERT_DAYS, 7)
        self.assertIn("alert_days", err)

    def test_bad_numbers_are_named_and_fall_back(self):
        cases = {
            "feedback_cap": ({"gates": {"feedback_cap": "x"}}, lambda s: s.FEEDBACK_CAP, 5),
            "keywords.cap": ({"keywords": {"cap": "x"}}, lambda s: s.KEYWORDS_CAP, 10),
            "reject_reask_days": ({"reject_reask_days": "x"}, lambda s: s.REJECT_REASK_DAYS, 180),
            "memory_loss_alert_fraction nan": ({"memory_loss_alert_fraction": math.nan},
                                               lambda s: s.MEMORY_LOSS_ALERT_FRACTION, 0.25),
            "memory_loss_alert_fraction 1.5": ({"memory_loss_alert_fraction": 1.5},
                                               lambda s: s.MEMORY_LOSS_ALERT_FRACTION, 0.25),
        }
        for what, (over, read, want) in cases.items():
            with self.subTest(what):
                err = self.configure(**over)
                self.assertEqual(read(dream.SETTINGS), want)
                self.assertIn(what.split()[0], err)


class PrecheckActionableKeys(unittest.TestCase):
    """A8: `"actionable_keys": "promotions"` became the keys p, r, o… and the gate never woke."""

    def _keys(self, section):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dreaming.json"
            path.write_text(json.dumps({"precheck": section}), encoding="utf-8")
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"DREAM_CONFIG": str(path)}), contextlib.redirect_stderr(err):
                keys = precheck._precheck_config()[0]
        return keys, err.getvalue()

    def test_a_string_is_reported_and_the_default_keys_wake_the_agent(self):
        keys, err = self._keys({"actionable_keys": "promotions"})
        self.assertEqual(keys, precheck.DEFAULT_ACTIONABLE_KEYS)
        self.assertIn("actionable_keys", err)
        self.assertIsNotNone(precheck.compact_payload({"stats": {"promotions": 1}}, actionable_keys=keys))

    def test_an_empty_list_still_means_the_default(self):
        keys, err = self._keys({"actionable_keys": []})
        self.assertEqual(keys, precheck.DEFAULT_ACTIONABLE_KEYS)
        self.assertEqual(err, "")

    def test_a_list_of_names_is_taken(self):
        self.assertEqual(self._keys({"actionable_keys": ["promotions"]}), (("promotions",), ""))


class RejectTellsABrokenStoreFromAMissingFact(DreamFixture):
    """A4: a store that could not be read sent the human to `--content` for a fact that was there."""

    def test_a_broken_store_is_reported_as_such(self):
        (self.home / "memory_store.db").write_bytes(b"this is not a database at all" * 40)
        old_home = reject.HOME
        reject.HOME = str(self.home)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                code = reject.main(["1", "--state", str(self.home / "cache" / "rejected.json")])
        finally:
            reject.HOME = old_home
        self.assertEqual(code, 1)
        self.assertIn("cannot read the fact store", err.getvalue())
        self.assertNotIn("not found", err.getvalue())


class ScreenCopierNeedsEveryRoot(unittest.TestCase):
    """A9: a provider that renamed `_secret_in` gave a copy without it, and the tool reported success."""

    def test_a_source_without_a_root_is_refused(self):
        src = "import re\n\n\ndef _recall_problem(text):\n    return None\n"
        with self.assertRaises(ValueError) as caught:
            vendor.copy(src, "9.9.9", "abc1234")
        self.assertIn("_secret_in", str(caught.exception))


class EnglishDatesAreOneRule(unittest.TestCase):
    """B2: the event check had its own English months and missed "Sept" and "Dec." that the expiry check reads."""

    def test_abbreviated_months_make_an_event(self):
        for text in ("Dentist appointment Sept 5", "Dentist appointment Dec. 5", "Dentist appointment Sep 5"):
            with self.subTest(text):
                self.assertTrue(dream.is_ephemeral_fact(text))

    def test_a_durable_fact_is_still_not_an_event(self):
        self.assertFalse(dream.is_ephemeral_fact("Marina has been going to the same dentist since 2019"))


class GateCopiesAgreeWithTheDream(unittest.TestCase):
    """B5: the gates are copied into <home>/scripts and cannot import dream.py, so they keep copies of its rules.
    These pass today; they fail when one side changes without the other."""

    def test_precheck_defaults_are_the_dreams(self):
        section = dream.DEFAULT_CONFIG["precheck"]
        self.assertEqual(precheck.DEFAULT_ACTIONABLE_KEYS, tuple(section["actionable_keys"]))
        self.assertEqual(precheck.DEFAULT_MAX_CONTENT, section["max_content"])

    def test_the_gate_lists_name_the_same_scripts(self):
        self.assertEqual(set(install.GATES),
                         {install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT, install_cron.SWEEP_SCRIPT})
        self.assertEqual(set(install.SKILL_GATES), {install_cron.DREAM_SCRIPT, install_cron.EXTRACT_SCRIPT})
        self.assertEqual(set(dream.DREAM_JOB_SCRIPTS), set(install.SKILL_GATES))

    def test_every_gate_finds_its_home_the_same_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / "scripts").mkdir(parents=True)
            (home / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
            for gate in install.GATES:
                with self.subTest(gate):
                    shutil.copy(ROOT / "scripts" / gate, home / "scripts" / gate)
                    env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
                    with mock.patch.dict(os.environ, env, clear=True), \
                            contextlib.redirect_stderr(io.StringIO()):
                        mod = _load(f"_gate_{gate.replace('-', '_')[:-3]}_332", home / "scripts" / gate)
                        self.assertEqual(mod._hermes_home().resolve(), home.resolve(), "installed: its own home")
                    with mock.patch.dict(os.environ, {"HERMES_HOME": str(Path(tmp) / "other")}):
                        self.assertEqual(mod._hermes_home(), Path(tmp) / "other", "HERMES_HOME wins")


if __name__ == "__main__":
    unittest.main()
