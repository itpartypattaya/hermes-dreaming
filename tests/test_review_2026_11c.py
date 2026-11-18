"""Third batch of the code review of 2026-11-17: the small items left after 3.8.8 (plans/2026-11-17-code-review.md,
finding 21 and the note on the loss-guard snapshot).

- The loss-guard snapshot (`cache/dream-snapshot.json`) names every memory entry by a 60-character preview. An entry
  written from a fact the human then asked to forget kept its preview there, and the next pass's loss alert named it
  again: the forget sweep cleans the snapshot like the dream's other copies.
- A diary rotation cut between appending the old sections to the archive and rewriting the diary appended the same
  sections again on the next night: the archive holds each section once.
- A generic fact source (`sqlite`, `jsonl`) that keeps its times as an epoch in a string or as "17.11.2026" lost the
  fact's age without a word, and an epoch in microseconds ended the pass (Windows: OSError, elsewhere ValueError).
- A reply quote with a "]" inside it ("…the Vespa [1] is…") ended at that bracket: the rest of the agent's quoted
  message counted as the person's own words, and a bare "ок" under it corroborated what the agent had said.
"""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import test_fact_sources
import test_forget_sweep
from test_dream import dream
from test_forget_sweep import FORGOTTEN_OLD

# Modules, not their classes: a TestCase bound to a name of this module would run its tests here a second time.


class SnapshotLosesAForgottenFact(unittest.TestCase):
    # The store, the home and the helpers of ForgetSweep — and its own copy of dream.py, whose HOME its setUp points
    # at the temporary home (test_dream's copy keeps the real one).
    setUp, tearDown = test_forget_sweep.ForgetSweep.setUp, test_forget_sweep.ForgetSweep.tearDown
    path, forget = test_forget_sweep.ForgetSweep.path, test_forget_sweep.ForgetSweep.forget
    _no_trace_of = test_forget_sweep.ForgetSweep._no_trace_of
    dream = test_forget_sweep.dream

    OTHERS = ["Лео живёт в Лиссабоне с весны", "Нина работает в школе учителем",
              "Ветка 30 для рабочих задач Лео", "Абонемент в зал стоит 1500 в месяц", "Кошку зовут Мурка"]

    def test_the_sweep_takes_the_preview_out_of_the_snapshot(self):
        self.assertEqual(self.dream.HOME, str(self.home), "the sweep must clean the test's home, not a real one")
        self.dream.forget_sweep(db=str(self.db))  # the diary index, while the fact exists
        mem = self.path("memories/MEMORY.md")
        snap = self.path("cache/dream-snapshot.json")
        mem.write_text("\n§\n".join([FORGOTTEN_OLD] + self.OTHERS) + "\n", encoding="utf-8")
        self.assertEqual(self.dream.check_memory_loss(str(mem), str(snap)), [])
        self.assertIn(FORGOTTEN_OLD[:40], snap.read_text(encoding="utf-8"), "the snapshot does hold the preview")

        # The human asks to forget the fact; the entry goes from memory with three others before the next pass.
        mem.write_text("\n§\n".join(self.OTHERS[:2]) + "\n", encoding="utf-8")
        self.forget(2)
        summary = self.dream.forget_sweep(db=str(self.db))
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["snapshot"], 1)
        self._no_trace_of(FORGOTTEN_OLD)
        self.assertEqual(self.dream.forget_sweep(db=str(self.db))["snapshot"], 0, "a second sweep changes nothing")

        alerts = [a for a in self.dream.check_memory_loss(str(mem), str(snap)) if a["kind"] == "memory_loss"]
        self.assertEqual(len(alerts), 1, "the loss itself is still news")
        text = json.dumps(alerts, ensure_ascii=False)
        self.assertNotIn(FORGOTTEN_OLD[:40], text)
        self.assertIn(self.OTHERS[2][:30], text, "the other lost entries keep their names")
        self.assertEqual(alerts[0]["lost"], 4, "the forgotten entry still counts as lost")


class DiaryRotationCutHalfway(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "DREAMS.md"
        self.archive = Path(str(self.path) + ".archive.md")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _section(day):
        return f"## Сон 2026-05-{day:02d}\n- текст {day}\n\n"

    def _rotate_killed_after_the_archive(self):
        real = dream._write_private

        def write(path, text, append=False):
            if not append:
                raise OSError("No space left on device")  # the diary rewrite, after the archive append
            return real(path, text, append=append)
        with mock.patch.object(dream, "_write_private", write), self.assertRaises(OSError):
            dream.rotate_diary(str(self.path), keep=3)

    def _sections(self, path):
        return dream.SETTINGS.DIARY_SECTION_RE.findall(path.read_text(encoding="utf-8"))

    def test_the_next_rotation_does_not_archive_the_same_sections_again(self):
        for rerun in ("the same night", "the next night"):
            with self.subTest(rerun=rerun):
                self.archive.unlink(missing_ok=True)
                self.path.write_text("".join(self._section(d) for d in range(1, 11)), encoding="utf-8")
                self._rotate_killed_after_the_archive()
                self.assertEqual(len(self._sections(self.archive)), 7, "the cut came after the archive append")
                if rerun == "the next night":
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(self._section(11))
                self.assertTrue(dream.rotate_diary(str(self.path), keep=3))
                archived = self._sections(self.archive)
                self.assertEqual(len(archived), len(set(archived)), f"a section archived twice: {archived}")
                self.assertEqual(len(self._sections(self.path)), 3)
                body = self.archive.read_text(encoding="utf-8") + self.path.read_text(encoding="utf-8")
                last = 11 if rerun == "the next night" else 10
                for d in range(1, last + 1):
                    self.assertEqual(body.count(f"- текст {d}\n"), 1, f"section {d}")


class GenericSourceTimes(test_fact_sources.FactSourceFixture):
    STORED = datetime(2026, 11, 17, 9, 30, tzinfo=timezone.utc)

    def _load(self, created):
        path = Path(self.tmp.name) / "facts.jsonl"
        path.write_text(json.dumps({"id": "m-1", "content": "Марина любит жасминовый чай", "created_at": created},
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        self.use(fact_source="jsonl", fact_store_path=str(path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            facts = dream.load_facts()
        return facts[0], err.getvalue()

    def test_an_epoch_in_any_unit_or_a_string_is_the_time_it_says(self):
        sec = int(self.STORED.timestamp())
        for created in (str(sec), f"{sec}.25", sec * 1000, str(sec * 1000), sec * 1_000_000, sec * 1_000_000_000):
            with self.subTest(created=created):
                fact, _ = self._load(created)
                self.assertEqual(fact["created_at"], "2026-11-17 09:30:00")
                self.assertEqual(dream._parse_ts(fact["created_at"]), self.STORED)

    def test_a_day_first_date_with_dots_is_that_day(self):
        for created, want in (("17.11.2026", "2026-11-17 00:00:00"), ("17.11.2026 09:30", "2026-11-17 09:30:00"),
                              ("17.11.2026 09:30:15", "2026-11-17 09:30:15")):
            with self.subTest(created=created):
                self.assertEqual(self._load(created)[0]["created_at"], want)

    def test_a_time_that_cannot_be_read_is_said_not_silently_dropped(self):
        for created in ("вчера вечером", 10 ** 30, "99.99.2026"):
            with self.subTest(created=created):
                fact, err = self._load(created)
                self.assertIsNone(fact["created_at"])
                self.assertIn("cannot be read", err)
                self.assertIn("created_at", err)


class ReplyQuoteWithABracket(unittest.TestCase):
    QUOTE = "Your Vespa {} is parked in the garage downtown, are you still riding it?"

    def _reply(self, quote, own, who=""):
        return f'[Replying to{who}: "{quote}"]\n\n{own}'

    def test_a_bracket_inside_the_quote_does_not_end_it(self):
        for who in ("", " your previous message"):
            for own in ("ок", "Да, всё ещё на Vespa"):
                with self.subTest(who=who, own=own):
                    plain = self._reply(self.QUOTE.format(""), own, who)
                    bracket = self._reply(self.QUOTE.format("[1]"), own, who)
                    self.assertEqual(dream.sig_tokens(bracket), dream.sig_tokens(plain))
                    self.assertEqual(dream._own_words(bracket).split(), own.split())

    def test_a_bare_ok_under_such_a_quote_corroborates_nothing(self):
        self.assertEqual(dream.sig_tokens(self._reply(self.QUOTE.format("[1]"), "ок")), set())

    def test_a_quote_without_its_closing_quote_mark_still_ends_at_its_bracket(self):
        text = '[Replying to: "Your Vespa is parked in the garage]\n\nок'
        self.assertEqual(dream.sig_tokens(text), set())
        self.assertEqual(dream._own_words(text).split(), ["ок"])


if __name__ == "__main__":
    unittest.main()
