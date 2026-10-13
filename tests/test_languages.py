"""Languages beyond Russian and English (dreaming 2.5.0): n-grams for scripts written without spaces, Latin
stems without diacritics, function words and months, dated one-off events and numbers in every language the
holographic store reads.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
ROOT = _HERE / "skills" / "dreaming" if (_HERE / "skills" / "dreaming" / "SKILL.md").is_file() else _HERE


def _load():
    # Built-in defaults only: no household config may add stopwords or aliases.
    old = os.environ.get("HERMES_HOME"), os.environ.get("DREAM_CONFIG")
    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="dream-lang-")
    os.environ.pop("DREAM_CONFIG", None)
    try:
        spec = importlib.util.spec_from_file_location("dream_languages", ROOT / "scripts/dream.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name, value in zip(("HERMES_HOME", "DREAM_CONFIG"), old):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


dream = _load()


def mentioned(fact, message):
    msgs = [{"tokens": dream.sig_tokens(message), "day": "2026-10-01", "ts": 1, "content": message}]
    return dream.corroborate(dream.sig_tokens(fact), msgs)[0] == 1


class Tokens(unittest.TestCase):
    def test_unsegmented_runs_become_ngrams(self):
        toks = dream.sig_tokens("莉娜对花生过敏")
        self.assertIn("花生", toks)
        self.assertIn("过敏", toks)
        self.assertNotIn("莉娜对花生过敏", toks)

    def test_latin_inside_chinese_is_a_word(self):
        toks = dream.sig_tokens("ACME的老板是王丽")
        self.assertIn("acme", toks)
        self.assertIn("老板", toks)

    def test_function_pairs_and_fillers_are_dropped(self):
        self.assertNotIn("什么", dream.sig_tokens("我们什么时候去"))
        self.assertEqual(dream.sig_tokens("ありがとうございます"), set())
        self.assertEqual(dream.sig_tokens("ขอบคุณครับ"), set())

    def test_thai_is_compared_by_triples(self):
        self.assertTrue(all(len(t) == 3 for t in dream.sig_tokens("ลีน่าแพ้ถั่วลิสง")))

    def test_russian_and_english_tokens_unchanged(self):
        self.assertEqual(dream.sig_tokens("Илья переехал в Лиссабон"), {"переехал", "лиссабон"})
        self.assertEqual(dream.sig_tokens("Martin rides a Vespa scooter"), {"martin", "rides", "vespa", "scooter"})


class Stems(unittest.TestCase):
    def test_diacritics_do_not_split_one_word(self):
        self.assertEqual(dream._stem("szkoła"), dream._stem("szkole"))
        self.assertEqual(dream._stem("öğretmen"), dream._stem("ogretmeni"))
        self.assertEqual(dream.latin_fold("Größe".lower()), "grosse")

    def test_cyrillic_keeps_its_stem(self):
        self.assertEqual(dream._stem("лиссабоне"), dream._stem("лиссабон"))
        self.assertEqual(dream.latin_fold("лиссабон"), "лиссабон")


class Mentions(unittest.TestCase):
    CASES = {  # fact, a message about it, a message about something else
        "zh": ("家里的猫是一只英国短毛猫，名叫团子", "团子这只英国短毛猫最近掉毛很厉害", "英国的天气今天特别冷"),
        "ja": ("レナは毎週土曜日の朝に水泳教室がある", "レナの水泳教室、今週は休みだって", "土曜日は雨らしいよ"),
        "ko": ("레나는 매주 토요일 아침에 수영 수업이 있다", "레나 수영 수업 이번 주는 쉰대", "토요일에 비 온대"),
        "th": ("ลีน่าแพ้ถั่วลิสง ห้ามกินขนมที่มีถั่ว", "อย่าลืมว่าลีน่าแพ้ถั่วลิสงนะ", "วันนี้ฝนตกหนักมาก"),
        "de": ("Lena hat jeden Dienstag Schwimmtraining im Westbad", "Lena, Schwimmtraining fällt diese Woche aus",
               "Am Dienstag ist der Markt geschlossen"),
        "pl": ("Zosia chodzi do szkoły podstawowej przy ulicy Długiej", "Zosia dziś nie idzie do szkoły, ma katar",
               "Dobra, to do zobaczenia wieczorem"),
        "tr": ("Elif her salı akşamı pilates dersine gidiyor", "Elif bu salı pilatese gitmeyecek",
               "Salı günü market erken kapanıyor"),
    }

    def test_mention_and_quiet_in_each_language(self):
        for lang, (fact, about, other) in self.CASES.items():
            with self.subTest(lang=lang):
                self.assertTrue(mentioned(fact, about), about)
                self.assertFalse(mentioned(fact, other), other)

    def test_a_name_alone_is_not_a_mention(self):
        # The three pairs of ソフィア are one name, not three shared words.
        self.assertFalse(mentioned("ソフィアは砂糖なしのブラックコーヒーを飲む", "ソフィアはどこ？"))

    def test_a_shared_five_letter_prefix_is_not_a_rare_word(self):
        # «commercial» is long, but «comment» only shares its first five letters.
        self.assertFalse(mentioned("Claire recherche un responsable commercial", "Comment ça va ?"))
        self.assertTrue(mentioned("Ilya moved to Lisbon for a transcription job", "the transcriptions are done"))

    def test_a_latin_prefix_never_meets_a_mixed_token(self):
        self.assertFalse(mentioned("Илья получает уведомления только на telegram-юзернейм",
                                   "добавь telegram_react в список"))

    def test_a_month_is_not_a_topic(self):
        self.assertFalse(mentioned("Lena hat am 14. November einen Termin beim Zahnarzt Dr. Brandt",
                                   "Mann, regnet das heute wieder, typisch November"))


class Events(unittest.TestCase):
    EVENTS = {
        "en": "Exam on 14 November, bring the notebook.",
        "de": "Lena hat am 14. November einen Termin beim Zahnarzt.",
        "es": "Lena tiene cita con el dentista el 14 de noviembre.",
        "fr": "Examen le 14 novembre, apporter le cahier.",
        "pt": "A Lena tem consulta no dentista em 14 de novembro.",
        "it": "Lena ha un appuntamento dal dentista il 14 novembre.",
        "tr": "Lena'nın 14 Kasım'da dişçi randevusu var.",
        "pl": "Sprawdzian 14 listopada, przynieść zeszyt.",
        "nl": "Lena heeft op 14 november een afspraak bij de tandarts.",
        "id": "Ujian tanggal 14 November, bawa buku tulis.",
        "ja": "レナは11月14日に歯医者の予約がある。",
        "zh": "莉娜11月14日约了牙医。",
        "ko": "레나는 11월 14일에 치과 예약이 있다.",
        "th": "ลีน่ามีนัดหมอฟัน 14 พฤศจิกายน",
    }

    def test_one_off_event_and_its_date(self):
        for lang, text in self.EVENTS.items():
            with self.subTest(lang=lang):
                self.assertTrue(dream.is_ephemeral_fact(text))
                certain, _ = dream.extract_event_dates(text, date(2026, 11, 1))
                self.assertIn(date(2026, 11, 14), certain)
                self.assertTrue(dream.is_expired_event(text, date(2026, 11, 15)))
                self.assertFalse(dream.is_expired_event(text, date(2026, 11, 13)))

    def test_a_birthday_is_not_a_one_off(self):
        for text in ("Oma Gisela hat am 3. Dezember Geburtstag.", "La abuela cumple años el 3 de diciembre.",
                     "奶奶的生日是12月3日。", "할머니 생신은 12월 3일이다."):
            with self.subTest(text=text):
                self.assertFalse(dream.is_ephemeral_fact(text))


class Numbers(unittest.TestCase):
    def test_numbers_against_unspaced_script(self):
        self.assertEqual(dream._numbers("每月500美元"), {"500"})
        self.assertEqual(dream._numbers("ราคา800บาท"), {"800"})
        self.assertEqual(dream._numbers("a5b v0.21.6"), {"6"})

    def test_conflict_in_chinese(self):
        old = "莉娜每周六上午在酒店游泳池上游泳课，每节课800泰铢。"
        new = "莉娜每周六上午在酒店游泳池上游泳课，每节课900泰铢。"
        self.assertEqual(dream.find_conflicts(new, old)[0]["fact_numbers"], ["900"])

    def test_month_markers_are_whole_words(self):
        self.assertEqual(dream._numbers("Мама купила машину у Марины"), set())
        self.assertEqual(dream._numbers("maybe the market"), set())
        self.assertEqual(dream._numbers("в мае"), {"month:05"})
        self.assertEqual(dream._numbers("am 3. Mai"), {"3", "month:05"})
        self.assertEqual(dream._numbers("12 листопада"), {"12"})  # Ukrainian is not parsed, not mistaken either


class ReviewFixes(unittest.TestCase):
    """2.5.1: the external review of 2.5.0 (2026-10-13), one test per finding."""

    def test_uppercase_turkish_month_does_not_crash(self):
        self.assertEqual(dream._numbers("14 KASIM 2026"), {"14", "2026", "month:11"})
        self.assertIn(date(2026, 11, 14), dream.extract_event_dates("14 KASIM sınav", date(2026, 11, 1))[0])

    def test_folded_month_names_do_not_collide(self):
        for name, num in dream.OTHER_MONTHS.items():
            self.assertEqual(dream.other_month(name.upper()), num, name)

    def test_since_a_date_is_a_lasting_state(self):
        for text in ("Tom is a doctor since 14 November 2020.", "Илья врач с 14 ноября 2020 года.",
                     "Since 14 November 2020 Tom can do 20 pushups.", "Lena geht seit 1. September zur Schule."):
            with self.subTest(text=text):
                self.assertFalse(dream.is_ephemeral_fact(text))

    def test_a_profession_is_not_a_visit(self):
        self.assertFalse(dream.is_ephemeral_fact("Tom is a doctor, born on 14 November 1990."))
        self.assertTrue(dream.is_ephemeral_fact("Lily goes to the doctor on 14 November."))
        self.assertTrue(dream.is_ephemeral_fact("Тёме к врачу 5 ноября в 16:30"))

    def test_a_deadline_needs_a_whole_date(self):
        self.assertFalse(dream.is_ephemeral_fact("On 14 November Tom could do 20 pushups."))
        self.assertTrue(dream.is_ephemeral_fact("Транспортный налог надо заплатить до 1 декабря"))
        self.assertTrue(dream.is_ephemeral_fact("Bu formu 30/11'e kadar teslim et"))

    def test_long_numbers_stay_fast(self):
        import time
        start = time.perf_counter()
        dream.is_ephemeral_fact("14 November " + "1" * 20000)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_japanese_particles_are_not_shared_words(self):
        self.assertEqual(dream.gram_weight("には"), 0.0)
        self.assertFalse(mentioned("apples には ので とも へや", "oranges には ので とも へや"))

    def test_english_content_words_are_not_foreign_stopwords(self):
        self.assertIn("solo", dream.sig_tokens("Martin prefers solo travel"))
        self.assertIn("fare", dream.sig_tokens("the bus fare went up"))
        self.assertFalse(dream._store_duplicates("Martin prefers solo travel abroad", "Martin prefers travel abroad"))

    def test_stretches_are_indexed(self):
        import time
        grams = {chr(0x4E00 + i) + chr(0x4E00 + i + 1) for i in range(3000)}
        start = time.perf_counter()
        self.assertEqual(len(dream.gram_stretches(grams)), 1)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_names_are_not_months(self):
        certain, _ = dream.extract_event_dates("Call Jane 14 times on December 20 2026", date(2026, 12, 21))
        self.assertEqual(certain, [date(2026, 12, 20)])
        self.assertEqual(dream.extract_event_dates("Mary 5 and Mark 3", date(2026, 12, 21)), ([], []))


class ReviewFixes251(unittest.TestCase):
    """2.5.2: the external review of 2.5.1 (2026-10-13)."""

    def test_a_range_is_still_an_event(self):
        for text in ("Egzaminy od 14 listopada 2026 do 16 listopada 2026", "Экзамен с 15.11.2026 по 17.11.2026"):
            with self.subTest(text=text):
                self.assertTrue(dream.is_ephemeral_fact(text))
                self.assertTrue(dream.is_expired_event(text, date(2026, 12, 21)))

    def test_a_time_is_not_a_since_date(self):
        self.assertTrue(dream.is_ephemeral_fact("Экзамен с 15.00 завтра"))

    def test_since_takes_out_only_its_own_phrase(self):
        text = "Call on 14 November 2026; Tom has worked here since 14 November 2020."
        self.assertTrue(dream.is_ephemeral_fact(text))
        self.assertTrue(dream.is_expired_event(text, date(2026, 12, 21)))
        self.assertFalse(dream.is_expired_event(text, date(2026, 11, 1)))

    def test_deadline_with_dashes(self):
        for text in ("Pay tax by 14-11-2026", "Оплатить налог до 14-11-2026"):
            with self.subTest(text=text):
                self.assertTrue(dream.is_expired_event(text, date(2026, 12, 21)))

    def test_doctor_visits(self):
        for text in ("See a doctor on November 14 2026", "Doctor visit on November 14 2026",
                     "Врач придёт 14 ноября 2026"):
            with self.subTest(text=text):
                self.assertTrue(dream.is_expired_event(text, date(2026, 12, 21)))

    def test_stretches_with_a_shared_character_are_linear(self):
        import time
        grams = {chr(0x4E01 + i) + "中" for i in range(3000)} | {"中" + chr(0x4E01 + i) for i in range(3000)}
        start = time.perf_counter()
        self.assertEqual(len(dream.gram_stretches(grams)), 1)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_same_first_character_is_not_a_chain(self):
        # 学校 and 学生 share 学 but neither continues the other: two stretches, as before.
        self.assertEqual(len(dream.gram_stretches({"学校", "学生"})), 2)
        self.assertEqual(len(dream.gram_stretches({"搬到", "到上", "上海"})), 1)


class Themes(unittest.TestCase):
    def test_only_han_word_pairs_are_themes(self):
        self.assertTrue(dream._theme_gram("学校"))
        self.assertFalse(dream._theme_gram("アン"))
        self.assertFalse(dream._theme_gram("나는"))
        self.assertFalse(dream._theme_gram("รีย"))
        self.assertFalse(dream._theme_gram("的猫"))


if __name__ == "__main__":
    unittest.main()
