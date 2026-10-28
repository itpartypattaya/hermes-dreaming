"""Issue 3: reuse number parsing across real fact scoring without changing decisions."""
import unittest
from unittest import mock

from test_dream import dream


class NumberCacheTests(unittest.TestCase):
    def test_scoring_reuses_message_parsing_and_keeps_results(self):
        text = "Тренировка клуба начинается в 18:00 по средам " + "данные " * 500
        message = {"content": text, "tokens": dream.sig_tokens(text), "day": "2026-10-20", "ts": 1792500000}
        facts = [{"content": f"Тренировка клуба начинается в 18:00 по средам, группа {n}."} for n in range(4)]
        original = dream._own_words
        with mock.patch.object(dream, "_own_words", wraps=original) as own:
            scored = dream._score_facts(facts, [message], None, [], "", "")
        self.assertEqual([s["meta"]["mentions"] for s in scored], [1, 1, 1, 1])
        self.assertEqual(sum(call.args == (text,) for call in own.call_args_list), 1)

    def test_cached_and_direct_checks_match_on_realistic_text(self):
        cases = [
            ("Встреча в 09:00 по средам", "Встреча в 9:00 по средам"),
            ("Встреча 07.03.2026", "Встреча 8.3.2026"),
            ("Взнос 1200", '[Replying to: "взнос 900"] взнос 1200 [ID 123]'),
            ("Взнос 1200", "взнос 1300 [незакрытая скобка " + "x" * 2000),
            ("Встреча в понедельник", "Встреча во вторник"),
            ("No numbers", "No numbers"),
        ]
        direct = [dream.numbers_changed(fact, text) for fact, text in cases]
        @dream._number_cache_pass
        def cached():
            return [[dream.numbers_changed(fact, text) for fact, text in cases] for _ in range(2)]
        self.assertEqual(cached(), [direct, direct])
        self.assertIsNone(dream._NUMBER_CACHE.get())

    def test_changed_text_is_not_stale_and_cache_is_bounded(self):
        @dream._number_cache_pass
        def check():
            with mock.patch.object(dream, "_NUMBER_CACHE_ENTRIES", 2):
                self.assertFalse(dream.numbers_changed("тренировка 1200", "тренировка 1200"))
                self.assertTrue(dream.numbers_changed("тренировка 1200", "тренировка 1300"))
                for n in range(10):
                    dream.numbers_changed("тренировка 1200", f"тренировка {n}")
                self.assertLessEqual(len(dream._NUMBER_CACHE.get()["entries"]), 2)
        check()
        self.assertIsNone(dream._NUMBER_CACHE.get())

    def test_cache_is_released_when_scoring_raises(self):
        @dream._number_cache_pass
        def broken():
            dream.numbers_changed("цена 1200", "цена 1300")
            raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            broken()
        self.assertIsNone(dream._NUMBER_CACHE.get())
