"""3.2.0: corroboration after the Laya experiment (dirty pairs and a live install, measured in aggregate only).

On a live install 46 % of human messages are replies, and the quoted text — mostly the agent's own words, full of
"чат", "ветка", "Telegram" — corroborated dozens of unrelated facts (535 fact-message mentions from 81 replies; 64 once
the quote is left out). A word nearly every fact of a home shares ("ветка", "чат") made a mention by itself as well.
Under a negation, "了"/"过" after a Chinese verb and a verb's tense ("eats" / "hasn't eaten", "курит" / "не курил") hid
a flip. A fact of short words was never mentioned, and a new number for the same thing corroborated the old one.

All people, clubs, threads and numbers below are made up (a table-tennis club and its chat)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_dream import dream  # noqa: E402  (same directory)


def _message(text):
    return {"tokens": dream.sig_tokens(text), "content": text, "day": "2026-10-20", "ts": 1_792_500_000}


def mentions(fact, text):
    return dream.corroborate(dream.sig_tokens(fact), [_message(text)], fact)[0]


QUOTE = ('[Replying to: "Готово: напоминание про турнир ушло в ветку объявлений клуба в общем Telegram-чате, '
         'остальные сообщения в эту ветку не дублирую, архив не трогаю"]\n\n')
ARCHIVE = "В общем Telegram-чате клуба ветка 140 — архив; на сообщения в этой ветке не отвечать."


class ReplyQuoteNeedsOwnWordsTests(unittest.TestCase):
    """The quote counts for a fact only when the person's own words touch that fact."""

    def test_a_quote_alone_does_not_corroborate_a_fact_the_person_did_not_speak_of(self):
        self.assertEqual(mentions(ARCHIVE, QUOTE + "собери все ответы в md файл и пришли мне"), 0)

    def test_own_words_about_the_fact_let_the_quote_count(self):
        """One shared word of the person's own lets the quote supply the rest (Codex review of 3.2.0: the first fixture
        passed without its quote)."""
        fact = "По средам у Фёдора после работы тренировка; в среду утром напоминать взять ракетку."
        quote = '[Replying to: "Напомнить Фёдору завтра утром про тренировку после работы?"]\n\n'
        self.assertEqual(mentions(fact, "да, ракетку взял"), 0)  # alone, one shared word is no mention
        self.assertEqual(mentions(fact, quote + "да, ракетку взял"), 1)

    def test_the_own_marked_pointer_and_a_discord_note_are_quotes_too(self):
        for prefix in ('[Replying to your previous message: "' + QUOTE[len('[Replying to: "'):],
                       "[Triggering message id: 123]\n\n" + QUOTE):
            with self.subTest(prefix[:30]):
                self.assertEqual(mentions(ARCHIVE, prefix + "собери все ответы в md файл и пришли мне"), 0)

    def test_a_message_without_a_quote_is_unchanged(self):
        self.assertEqual(mentions(ARCHIVE, "ветка 140 архив, на сообщения в этой ветке не отвечаем"), 1)


class HouseholdWordsTests(unittest.TestCase):
    """A stem in a large share of the home's facts makes no mention by itself."""

    FACTS = [f"В общем Telegram-чате клуба ветка {n}: сообщения считать задачами про {what}, если контекст не "
             f"говорит иное." for n, what in ((7, "расписание столов"), (12, "закупку мячей"), (19, "турнирную сетку"),
                                               (33, "взносы участников"), (41, "ремонт инвентаря"),
                                               (58, "заметки о новичках"), (72, "поиск спонсоров"),
                                               (86, "фотографии с игр"), (140, "архив без ответов"))]
    FACTS += ["Ирина любит длинные прогулки, велосипед и уютные кофейни.", "Гоша ездит на работу на электросамокате.",
              "Ирина ведёт бухгалтерию клуба.", "Ирина просит напоминать про оплату зала.",
              "Ирина не пьёт кофе после обеда.", "Ирина записана к окулисту на пятницу."]

    def setUp(self):
        dream.set_household(self.FACTS)

    def tearDown(self):
        dream.set_household([])

    def test_shared_household_words_alone_are_no_mention(self):
        self.assertEqual(mentions(self.FACTS[1], "в общем Telegram-чате клуба завела ветку для фотографий, "
                                                 "сообщения туда кидайте"), 0)

    def test_the_fact_own_words_still_make_a_mention(self):
        self.assertEqual(mentions(self.FACTS[1], "в ветке 12 теперь закупку мячей ведёт Гоша"), 1)

    def test_a_name_in_many_facts_still_counts_with_a_word_of_the_fact(self):
        """A household word is no mention by itself, but it still counts beside one of the fact's own: a name in a fifth
        of the facts plus "прогулки" is a mention, as it was before (the dev pairs of the experiment: 李雷 + 有车)."""
        self.assertIn(dream._stem("ирина"), dream.household_stems(self.FACTS))
        self.assertEqual(mentions(self.FACTS[9], "у Ирины снова прогулки по набережной"), 1)

    def test_the_household_set_is_the_frequent_stems(self):
        common = dream.household_stems(self.FACTS)
        self.assertIn(dream._stem("задачами"), common)  # "ветка" itself is a stopword already
        self.assertNotIn(dream._stem("велосипед"), common)

    def test_a_small_store_has_no_household_words(self):
        self.assertEqual(dream.household_stems(self.FACTS[:3]), frozenset())


class ChineseAspectParticleTests(unittest.TestCase):
    def test_a_particle_after_the_negated_verb_keeps_the_flip(self):
        for fact, message in (("王芳吃肉", "王芳已经半年不吃肉了"), ("王芳抽烟", "王芳从来没抽过烟"),
                              ("李雷周日打网球", "李雷周日再也不打网球了")):
            with self.subTest(message):
                self.assertIsNotNone(dream.polarity_flip(fact, message))

    def test_a_word_that_starts_with_the_particle_is_still_that_word(self):
        self.assertIsNotNone(dream.polarity_flip("李雷对花生不过敏", "李雷对花生过敏"))
        self.assertIsNotNone(dream.polarity_flip("李雷不了解这个方案", "李雷了解这个方案"))

    def test_both_negated_with_and_without_the_particle_agree(self):
        self.assertIsNone(dream.polarity_flip("王芳不吃肉了", "王芳不吃肉"))


class TenseUnderNegationTests(unittest.TestCase):
    def test_another_tense_of_the_negated_verb_is_a_flip(self):
        for fact, message in (("Alice eats meat", "Alice hasn't eaten meat for half a year now"),
                              ("Bob smokes", "Bob has never smoked"), ("Гоша курит", "Гоша никогда не курил"),
                              ("Ирина любит кофе", "Ирина никогда не любила кофе")):
            with self.subTest(message):
                self.assertIsNotNone(dream.polarity_flip(fact, message))

    def test_an_ending_on_an_ending_still_meets(self):
        """Codex review of 3.2.0: "listens" lost "-s" and "-en" (list), "listened" only "-ed" (liste) — 3.1.1 met them."""
        self.assertIsNotNone(dream.polarity_flip("Alice listens to music", "Alice has not listened to music"))
        self.assertEqual(mentions("Alice listens to music to relax every evening",
                                  "Alice has not listened to music to relax this evening"), 0)

    def test_both_negated_in_different_tenses_agree(self):
        self.assertIsNone(dream.polarity_flip("Гоша не курил", "Гоша не курит"))
        self.assertIsNone(dream.polarity_flip("Bob has never smoked", "Bob does not smoke"))

    def test_words_the_3_1_0_stem_kept_apart_stay_apart(self):
        for word in ("class", "bus", "paris"):
            self.assertEqual(dream._neg_stem(word), dream._stem(word))


def _scored(fact, text):
    """Mentions the pass gives a fact for one message (signals_for_fact: the fact's own tokens, the fact's text)."""
    _, _, meta = dream.signals_for_fact({"fact_id": 1, "content": fact, "trust_score": 0.5,
                                         "created_at": "2026-10-01 10:00:00", "updated_at": "2026-10-01 10:00:00"},
                                        [_message(text)])
    return meta["mentions"]


class ShortFactTests(unittest.TestCase):
    """A fact of short words had one token or none («Гоша пьёт кофе по утрам» → утрам): nothing ever mentioned it."""

    def test_a_short_fact_is_mentioned(self):
        self.assertEqual(_scored("Гоша пьёт кофе по утрам", "Гоша снова пьёт кофе по утрам, без сахара"), 1)
        self.assertEqual(_scored("Bob likes tea", "Bob had green tea again, he likes it"), 1)

    def test_a_short_fact_negated_is_no_mention(self):
        self.assertEqual(_scored("Гоша пьёт кофе по утрам", "Гоша не пьёт кофе по утрам, только чай"), 0)

    def test_a_short_word_alone_is_no_mention(self):
        self.assertEqual(_scored("Гоша пьёт кофе по утрам", "купи кофе домой"), 0)

    def test_a_reply_about_a_short_fact_is_read_wide_too(self):
        """Codex review of 3.2.0: the own words of a reply were read the usual way, and "Bob tea" had none."""
        self.assertEqual(_scored("Bob likes tea", "Bob tea"), 1)
        self.assertEqual(_scored("Bob likes tea", '[Replying to: "Bob likes tea"]\n\nBob tea'), 1)

    def test_short_words_count_for_the_household_too(self):
        facts = ["Bob likes tea", "Bob brews tea"] + [f"Длинный факт номер {w} про совершенно другое" for w in
                                                       ("первый", "второй", "третий", "четвёртый", "пятый", "шестой",
                                                        "седьмой", "восьмой")]
        self.assertTrue({dream._stem("bob"), dream._stem("tea")} <= dream.household_stems(facts))

    def test_short_function_words_are_not_tokens(self):
        toks = dream.fact_tokens("Bob and the cat")
        self.assertNotIn("the", toks)
        self.assertNotIn("and", toks)

    def test_a_long_fact_keeps_its_tokens(self):
        fact = "Михаил любит острое азиатское блюдо"
        self.assertEqual(dream.fact_tokens(fact), dream.sig_tokens(fact))


POOL = "Абонемент Фёдора в бассейн стоит 1200 рублей в месяц, оплата по понедельникам."
STORE = "Инвентарь со склада клуба выдают в 19:00, по пятницам выдаёт Ирина."


class ChangedNumberTests(unittest.TestCase):
    """«теперь 1500» under a fact of 1200 was a corroboration of the 1200 (0 of 14 such pairs; Laya experiment)."""

    def test_a_new_number_for_the_same_thing_is_no_mention(self):
        self.assertEqual(_scored(POOL, "Абонемент Фёдора в бассейн теперь стоит 1500 рублей в месяц"), 0)
        fact = "Бот клуба не присылает сводки в общий чат между 23:30 и 06:30, кроме срочных."
        self.assertEqual(_scored(fact, "тихий режим для сводки в общий чат теперь с 00:30 до 06:30"), 0)

    def test_the_same_number_still_corroborates(self):
        self.assertEqual(_scored(POOL, "Фёдор оплатил абонемент в бассейн, 1200 рублей, как обычно"), 1)

    def test_an_extra_number_beside_the_fact_number_still_corroborates(self):
        self.assertEqual(_scored(STORE, "в пятницу Ирина выдала инвентарь со склада клуба в 19:00, взяли 2 сетки"), 1)

    def test_the_fact_number_restated_beside_another_of_its_kind_is_no_change(self):
        """Codex review of 3.2.0: the other extra-number test used a number of another kind."""
        self.assertEqual(_scored(STORE, "в пятницу Ирина выдала инвентарь со склада клуба в 19:00, второй раз в 20:00"),
                         1)

    def test_one_time_spelled_two_ways_is_no_change(self):
        self.assertFalse(dream.numbers_changed("The swimming lesson starts at 09:00", "The swimming lesson starts at 9:00"))
        self.assertFalse(dream.numbers_changed("Сбор в 07.03 у входа", "сбор 7.3 у входа"))

    def test_a_voice_transcript_is_speech_with_its_numbers(self):
        """Codex review of 3.2.0: the [bracketed] metadata cut took the whole transcript with it."""
        voice = '[The user sent a voice message~ Here is what they said: "Абонемент Фёдора в бассейн теперь 1500 рублей"]'
        self.assertTrue(dream.numbers_changed(POOL, voice))

    def test_a_number_only_in_the_quote_does_not_count(self):
        reply = ('[Replying to: "Записала: абонемент Фёдора в бассейн 1500 рублей за два месяца"]\n\n'
                 "абонемент Фёдора в бассейн оплатили в понедельник, всё как обычно")
        self.assertEqual(_scored(POOL, reply), 1)

    def test_a_number_in_bracketed_metadata_does_not_count(self):
        """A Discord note's message id, a sender tag: metadata, as for the tokens."""
        self.assertEqual(_scored(POOL, "[Triggering message id: 1300]\n\nабонемент Фёдора в бассейн оплатили в "
                                       "понедельник, всё как обычно"), 1)

    def test_numbers_of_another_kind_are_no_change(self):
        """A date is no other weekday, a count no other time."""
        fact = "По средам у Фёдора после работы тренировка; в среду утром напоминать взять ракетку."
        self.assertEqual(_scored(fact, "ракетку для Фёдора положила, тренировка после работы завтра, 18 октября"), 1)
        self.assertEqual(_scored(STORE, "по пятницам Ирина выдаёт инвентарь со склада клуба, сегодня взяли 2 сетки"), 1)


if __name__ == "__main__":
    unittest.main()
