"""Second batch of the code review of 2026-11-17 (plans/2026-11-17-code-review.md; plans/2026-11-18-review-fixes-2.md).

7.  A voice message sent as a reply is the person's speech: it corroborates like the same voice message alone.
9.  A dashed slug that contains "sk-" (risk-management-…, task-management-…) is no API key.
12. A home path with '#' or '%' is opened read-only at that path, and nothing is created beside it.
10. A fact store that cannot be read (empty file, not a database, an older table) costs the night its facts, not the
    whole night: the memory-file review still runs and the report says why the facts are missing.
8.  A date without a year is read in the year that follows the day the fact was stored: "the flight on 20 June",
    stored in November, is next June's, and "the test on 3 May", stored in April, has passed by November.
11. A timezone name with spaces around it is that zone, not UTC.
21. A pass in a home without cache/ still writes its state.
22. A broken extraction cursor does not send the gate `backfill_days` back: what was handed over before is not handed
    over again (an add of a stored text counts as the person saying it again).
24. The judge's key in .env is read the way Hermes reads the file: quotes with a comment after them, a BOM, the last
    of two lines.
"""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from test_dream import ROOT, DreamFixture, _load, dream

FACTS_TABLE = ("create table facts (fact_id integer primary key autoincrement, content text, category text default "
               "'general', tags text default '', trust_score real default 0.5, retrieval_count integer default 0, "
               "helpful_count integer default 0, created_at timestamp, updated_at timestamp)")
RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
             "ноября", "декабря"]


def _ru_date(d):
    return f"{d.day} {RU_MONTHS[d.month - 1]}"


class VoiceReplyTests(unittest.TestCase):
    VOICE = ('[The user sent a voice message~ Here\'s what they said: "У меня Vespa Primavera, езжу на ней каждый '
             'день"]')

    def test_voice_reply_has_the_words_of_the_voice_message(self):
        reply = '[Replying to: "Какой у тебя сейчас байк?"] ' + self.VOICE
        self.assertTrue(dream.sig_tokens(self.VOICE))
        self.assertTrue(dream.sig_tokens(self.VOICE) <= dream.sig_tokens(reply))

    def test_bare_ok_under_a_quote_still_says_nothing(self):
        self.assertEqual(dream.sig_tokens('[Replying to: "Какой у тебя сейчас байк?"] ок'), set())


class SlugIsNoKeyTests(unittest.TestCase):
    def test_slugs_are_not_secrets(self):
        for text in ("Статья про риски: https://example.com/blog/risk-management-for-startups",
                     "Доска задач называется task-management-board-team42",
                     "По пятницам идут ask-me-anything-sessions для команды"):
            self.assertIsNone(dream.classify_unsafe(text), text)

    def test_a_key_is_still_a_secret(self):
        self.assertEqual(dream.classify_unsafe("ключ проекта sk-proj-abcdefghijklmnop1234"), "secret")
        self.assertEqual(dream.classify_unsafe("OPENAI=sk-abcdefghijklmnopqrstu"), "secret")


class OddPathTests(unittest.TestCase):
    def _store(self, folder):
        os.makedirs(folder)
        db = os.path.join(folder, "memory_store.db")
        con = sqlite3.connect(db)
        con.execute(FACTS_TABLE)
        con.execute("insert into facts (content) values ('Лео живёт в Лиссабоне')")
        con.commit()
        con.close()
        return db

    def test_hash_and_percent_in_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("my#home", "my%41home", "my?home" if os.name != "nt" else "my&home"):
                db = self._store(os.path.join(tmp, name))
                before = sorted(os.listdir(tmp))
                self.assertEqual([f["content"] for f in dream.load_facts(db=db)], ["Лео живёт в Лиссабоне"], name)
                self.assertEqual(sorted(os.listdir(tmp)), before, f"{name}: a file was created beside the home")

    def test_the_store_is_opened_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._store(os.path.join(tmp, "a#b"))
            con = dream._conn(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute("insert into facts (content) values ('x')")
            finally:
                con.close()


class UnreadableStoreTests(DreamFixture):
    def _night(self):
        return dream.run(14, 60, 60, str(self.mem_md))

    def _check(self, broken):
        self.mem_md.write_text("Лео живёт в Лиссабоне с весны\n", encoding="utf-8")
        broken(self.home / "memory_store.db")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = self._night()
        kinds = [a["kind"] for a in result["alerts"]]
        self.assertIn("fact_store_unreadable", kinds)
        self.assertEqual(result["stats"]["facts"], 0)
        self.assertIn("memory_usage", result)  # the memory-file review ran

    def test_empty_file(self):
        self._check(lambda p: p.write_bytes(b""))

    def test_not_a_database(self):
        self._check(lambda p: p.write_bytes(b"not a database" * 300))

    def test_older_table(self):
        def older(p):
            p.unlink()
            con = sqlite3.connect(p)
            con.execute("create table facts (fact_id integer primary key, content text)")
            con.commit()
            con.close()
        self._check(older)


class YearOfADateTests(DreamFixture):
    def test_flight_next_june_stored_in_november(self):
        self.assertFalse(dream.is_expired_event("Летим в отпуск 20 июня, вылет из Лиссабона",
                                                today=date(2026, 11, 17), said=date(2026, 11, 10)))

    def test_test_in_may_stored_in_april_has_passed(self):
        self.assertTrue(dream.is_expired_event("Контрольная по математике 3 мая, принести тетрадь",
                                               today=date(2026, 11, 17), said=date(2026, 4, 20)))

    def test_a_late_extraction_keeps_the_year(self):
        # The extraction job runs weekly and goes back up to 60 days: a fact may be stored weeks after it was said.
        self.assertTrue(dream.is_expired_event("Контрольная по математике 3 октября, принести тетрадь",
                                               today=date(2026, 11, 17), said=date(2026, 11, 10)))

    def test_the_night_reads_the_store_date(self):
        today = datetime.now(timezone.utc).date()
        ahead = today + timedelta(days=210)
        past = today - timedelta(days=210)
        self.add_fact(f"Летим в отпуск {_ru_date(ahead)}, вылет из Лиссабона", days_old=1)
        self.add_fact(f"Контрольная по математике {_ru_date(past)}, принести тетрадь", days_old=205)
        expired = [e["content"] for e in self.run_dream()["expired_events"]]
        self.assertNotIn(f"Летим в отпуск {_ru_date(ahead)}, вылет из Лиссабона", expired)
        self.assertIn(f"Контрольная по математике {_ru_date(past)}, принести тетрадь", expired)


class TimezoneNameTests(unittest.TestCase):
    def test_spaces_around_the_name(self):
        seen = []
        with mock.patch.object(dream, "ZoneInfo", lambda name: seen.append(name) or timezone.utc):
            dream._tz(" Asia/Tokyo ")
        self.assertEqual(seen, ["Asia/Tokyo"])


class NoCacheFolderTests(DreamFixture):
    def test_state_is_written_without_cache(self):
        seen = self.home / "cache" / "dream-seen.json"
        snap = self.home / "cache" / "dream-snapshot.json"
        self.assertFalse((self.home / "cache").exists())
        dream.run(14, 60, 60, str(self.mem_md), seen_state_path=str(seen), snapshot_state_path=str(snap))
        self.assertTrue(seen.exists())
        self.assertTrue(snap.exists())


extract = _load("dream_extract_b", ROOT / "scripts/dream-extract-precheck.py")


class BrokenCursorTests(DreamFixture):
    def setUp(self):
        super().setUp()
        (self.home / "config.yaml").write_text("memory:\n  provider: holographic\n", encoding="utf-8")
        (self.home / "dreaming.json").write_text(json.dumps(
            {"trusted_chat_ids": ["-100111"], "extract": {"min_messages": 3, "backfill_days": 30}}), encoding="utf-8")
        self.state = self.home / "cache" / "dream-extract-state.json"
        self.saved = (extract.HOME, extract.STATE, extract.DREAM)
        extract.HOME, extract.STATE, extract.DREAM = self.home, self.state, ROOT / "scripts" / "dream.py"

    def tearDown(self):
        extract.HOME, extract.STATE, extract.DREAM = self.saved
        super().tearDown()

    def _gate(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            extract.main()
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_broken_cursor_hands_nothing_over_again(self):
        for i in range(5):
            self.add_message(f"Сообщение номер {i} про утренний кофе и тренировки", days_ago=10 - i)
        self.state.parent.mkdir(parents=True)
        self.state.write_text('{"since_ts": 17', encoding="utf-8")
        out = self._gate()
        self.assertNotIn("messages", out)
        self.assertIn("dream_error", out)  # someone has to hear the cursor was lost
        # …and the next run starts after now, not backfill_days ago
        self.assertNotIn("messages", self._gate())

    def test_a_missing_cursor_is_a_first_run(self):
        for i in range(5):
            self.add_message(f"Сообщение номер {i} про утренний кофе и тренировки", days_ago=10 - i)
        self.assertEqual(len(self._gate()["messages"]), 5)


judge = _load("dream_judge_b", ROOT / "scripts/dream_judge.py")


class EnvFileKeyTests(unittest.TestCase):
    def _key(self, text, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_bytes(text.encode(encoding))
            return judge.env_file_key("OPENROUTER_API_KEY", tmp)

    def test_quoted_with_a_comment(self):
        self.assertEqual(self._key('OPENROUTER_API_KEY="sk-or-v1-abc" # judge\n'), "sk-or-v1-abc")

    def test_bom(self):
        self.assertEqual(self._key("OPENROUTER_API_KEY=sk-or-v1-abc\n", "utf-8-sig"), "sk-or-v1-abc")

    def test_the_last_line_wins(self):
        self.assertEqual(self._key("OPENROUTER_API_KEY=\nOPENROUTER_API_KEY=sk-or-v1-abc\n"), "sk-or-v1-abc")
        self.assertEqual(self._key("OPENROUTER_API_KEY=sk-old\nOPENROUTER_API_KEY=sk-or-v1-abc\n"), "sk-or-v1-abc")

    def test_plain_forms_still_work(self):
        self.assertEqual(self._key("export OPENROUTER_API_KEY='sk-or-v1-abc'\n"), "sk-or-v1-abc")
        self.assertEqual(self._key("OPENROUTER_API_KEY=sk-or-v1-abc # judge\n"), "sk-or-v1-abc")
        self.assertIsNone(self._key("OTHER=1\n"))


class InstallCheckOnALegacyCodePageTests(unittest.TestCase):
    """23: `install.py --check` piped on a code page without ✓ ! ✗ must say what is wrong, not die."""

    def test_check_prints_on_cp1252(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HERMES_HOME=tmp, PYTHONIOENCODING="cp1252")
            env.pop("PYTHONUTF8", None)
            proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"), "--check",
                                   "--hermes-home", tmp], env=env, capture_output=True, timeout=120)
        err = proc.stderr.decode("utf-8", "replace")
        self.assertNotIn("UnicodeEncodeError", err)
        self.assertTrue(proc.stdout.strip())


if __name__ == "__main__":
    unittest.main()
