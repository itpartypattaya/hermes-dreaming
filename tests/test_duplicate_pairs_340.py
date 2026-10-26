"""3.4.0, part A: duplicates across the whole fact store, put to the human as a "merge?" question.

All texts are synthetic — a made-up household (Виктор, Ольга, their daughter Лена, the agent Луна); no real
person's memory is quoted. Each requirement in words first, then its tests.

The rule (`duplicate_pair`) generates questions, not identities:
- a retelling of a stored fact in other words is a pair (P1–P9); a fact absorbed by a longer one is a pair, and the
  longer one is kept (S1, S2);
- other numbers, another person, the opposite polarity, a translation — no pair (N1–N11);
- one word swapped («утром»/«вечером», «разрешает»/«запрещает», «мама»/«папа», «до»/«после») — no pair (the
  substitution guard); a weekday or a month swapped — no pair (`_numbers` knows them) (X1–X16);
- names added on one side and numbers on the other — no pair (X17); shared numbers swapped — no pair (N6);
- a short fact («@viktor_k — Виктор») pairs by its handle only;
- tests named `test_limit_…` fix what the rule cannot tell apart (N5 roles, X15 tense, a translation is missed).
A protective test is red without the guard it names: `test_each_guard_alone_holds_its_pairs` switches the guards
off one by one, and the guards were also taken out of the code by hand (see the report of 3.4.0).

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import contextlib
import unittest
from unittest import mock

from test_dream import dream  # noqa: E402  (same directory)

RETELLINGS = [  # (name, a, b): a pair
    ("P1", "Ветка 4410 служит прямым рабочим мостом между Claude Code Виктора и Луной.",
     "Ветка 4410 — прямой рабочий мост между Claude Code Виктора и Луной; сообщения оттуда — рабочие задачи от "
     "Виктора."),
    ("P3", "Ольга — международный HR-рекрутер, ведёт агентство QX9 по подбору sales-специалистов.",
     "Ольга ведёт агентство QX9: международный HR-рекрутер, подбор sales-специалистов."),
    ("P3b", "Агентство QX9 ведёт Ольга, международный HR-рекрутер по подбору sales-специалистов.",
     "Ольга — международный HR-рекрутер, ведёт агентство QX9 по подбору sales-специалистов."),
    ("P4", "Telegram Виктора — @viktor_k.", "Юзернейм Виктора в Telegram: @viktor_k."),
    ("P5", "Виктор сказал, что любит ездить в Абрау-Дюрсо на скутере Vespa Primavera по выходным.",
     "Виктор любит ездить в Абрау-Дюрсо на скутере Vespa Primavera по выходным."),
    ("P6", "Viktor rides a Vespa Primavera scooter around Anapa and Vityazevo.",
     "Viktor gets around Anapa and Vityazevo on his Vespa Primavera scooter."),
    ("P7 stamps", "2026-10-20 Виктор сказал, что ветка 4410 — прямой рабочий мост между Claude Code Виктора и "
                  "Луной.",
     "2026-10-24 Ветка 4410 служит прямым рабочим мостом между Claude Code Виктора и Луной."),
    ("P8", "Ветка 4410 служит прямым рабочим мостом между Claude Code Виктора и Луной.",
     "Ветка 4410 служит прямым рабочим мостом между Claude Виктора и Луной."),
    ("P9", "Ветка 30 — рабочие задачи Виктора: сообщения оттуда считать рабочими задачами.",
     "Ветка 30 служит для рабочих задач Виктора; её сообщения — рабочие задачи."),
    ("S1 absorption", "Ветка 30 — рабочие задачи Виктора.",
     "Ветка 30 — рабочие задачи Виктора, ветка 33 — домашние дела."),
    ("S2 absorption", "Виктор живёт в Анапе.",
     "Виктор живёт в Анапе с семьёй и планирует переезд в Казань весной 2027."),
]

# (name, a, b, the guards that reject it — each of them alone does)
NOT_PAIRS = [
    ("N1 71/74", "Ветка 71 — доходы семьи: сюда пишут поступления денег.",
     "Ветка 74 — расходы семьи: сюда пишут траты денег.", ("numbers", "substitution")),
    ("N2 threads 30/31", "Ветка 30 — ветка для рабочих задач Виктора в семейно-рабочем чате.",
     "Ветка 31 — ветка для домашних задач Виктора в семейно-рабочем чате.", ("numbers", "substitution")),
    ("N3 negation", "Виктор работает в компании QX9 как маркетолог по контенту и продвижению.",
     "Виктор не работает в компании QX9 как маркетолог по контенту и продвижению.", ("polarity",)),
    ("N4 other subject", "Виктор помогает Ольге с продвижением агентства QX9 и контентом в Telegram.",
     "Луна помогает Ольге с продвижением агентства QX9 и контентом в Telegram.", ("names", "substitution")),
    ("N6 numbers swapped", "В ветке 71 семья пишет доходы, а в ветке 74 — расходы.",
     "В ветке 74 семья пишет доходы, а в ветке 71 — расходы.", ("order",)),
    ("N7 translation", "Виктор ездит на скутере Vespa Primavera по Анапе и Витязеву.",
     "Viktor rides a Vespa Primavera scooter around Anapa and Vityazevo.", ()),
    ("N9 another person, three words", "Виктор любит кофе.", "Ольга любит кофе.", ()),
    ("N10 other subject, name first",
     "Виктор живёт в Анапе в районе Витязево и передвигается на скутере Vespa Primavera.",
     "Ольга живёт в Анапе в районе Витязево и передвигается на скутере Vespa Primavera.", ("names", "substitution")),
    ("N11 handle", "Telegram-аккаунт Виктора для рабочих вопросов и связи по проектам — @viktor_k.",
     "Telegram-аккаунт Виктора для рабочих вопросов и связи по проектам — @vik_work.", ("numbers", "substitution")),
    ("X1 утром/вечером", "Ольга предпочитает созвоны с кандидатами утром.",
     "Ольга предпочитает созвоны с кандидатами вечером.", ("substitution",)),
    ("X2 разрешает/запрещает", "Виктор разрешает Луне писать в ветку 30 без подтверждения.",
     "Виктор запрещает Луне писать в ветку 30 без подтверждения.", ("substitution",)),
    ("X5 вторник/четверг", "Балет у Лены по вторникам после школы.", "Балет у Лены по четвергам после школы.",
     ("numbers", "substitution")),
    ("X13 до/после", "Лена ложится спать до 22:00 по будням.", "Лена ложится спать после 22:00 по будням.",
     ("substitution",)),
    ("X14 мама/папа", "В школу Лену отвозит мама по будням.", "В школу Лену отвозит папа по будням.",
     ("substitution",)),
    ("X16 январь/март", "Семья переезжает в Казань в январе.", "Семья переезжает в Казань в марте.",
     ("numbers", "substitution")),
    ("X17 direction", "Виктор и Ольга ведут ветку 30 в рабочем чате.", "Виктор ведёт ветки 30 и 31 в рабочем чате.",
     ("direction", "substitution")),
]

# Each guard switched off, for the test that proves the guards hold the pairs (not some other part of the rule).
GUARDS_OFF = {
    "substitution": {"DUP_SUBSTITUTION_MAX": 0},
    "polarity": {"polarity_differs": lambda a, b: False},
    "order": {"_same_order": lambda x, y: True},
    "numbers": {"_dup_anchors": lambda t: set(), "_dup_anchor_seq": lambda t: []},
    "names": {"_dup_name_seq": lambda t: []},
    "direction": {"_dup_directions": lambda a, b: {"a", "b"}},
}


class RuleTests(unittest.TestCase):
    def test_retellings_are_pairs(self):
        for name, a, b in RETELLINGS:
            with self.subTest(name):
                ok, why = dream.duplicate_pair(a, b)
                self.assertTrue(ok, f"{name}: {why}")
                self.assertTrue(dream.duplicate_pair(b, a)[0], "the rule is symmetric")

    def test_what_differs_is_not_a_pair(self):
        for name, a, b, _ in NOT_PAIRS:
            with self.subTest(name):
                ok, why = dream.duplicate_pair(a, b)
                self.assertFalse(ok, f"{name}: {why}")
                self.assertFalse(dream.duplicate_pair(b, a)[0], "the rule is symmetric")

    def test_each_guard_alone_holds_its_pairs(self):
        """With every guard a pair names switched off the pair comes through (so the case really exercises them),
        and each named guard alone rejects it: without the substitution guard X1/X2/X13/X14 are pairs (one word of
        six differs); without `_numbers` knowing weekdays and months X5/X16 fall to the substitution guard only;
        without the order of shared numbers N6 is a pair; without the common direction X17 is; without polarity N3
        is; without names N4/N10 are."""
        for name, a, b, guards in NOT_PAIRS:
            if not guards:
                continue  # containment and the short path are the rule itself, not a guard on it
            with self.subTest(name):
                every = {k: v for g in guards for k, v in GUARDS_OFF[g].items()}
                with mock.patch.multiple(dream, **every):
                    self.assertTrue(dream.duplicate_pair(a, b)[0], f"{name} is held by a guard it does not name")
                for kept in guards:
                    rest = {k: v for g in guards if g != kept for k, v in GUARDS_OFF[g].items()}
                    with mock.patch.multiple(dream, **rest) if rest else contextlib.nullcontext():
                        self.assertFalse(dream.duplicate_pair(a, b)[0], f"{name}: {kept} alone does not hold it")

    def test_limit_tense_of_one_verb_is_not_seen(self):
        """X15: «работает» / «работал» share one stem — the rule finds the pair and the human decides."""
        self.assertTrue(dream.duplicate_pair("Виктор работает в агентстве маркетологом по контенту.",
                                             "Виктор работал в агентстве маркетологом по контенту.")[0])

    def test_limit_two_names_in_swapped_roles_are_found_and_the_order_is_named(self):
        """N5: a bag of words cannot tell «Виктор помогает Борису» from «Борис помогает Виктору»; the question
        carries both texts whole and says that the names come in another order."""
        ok, why = dream.duplicate_pair("Виктор помогает Борису с переездом семьи в Казань весной.",
                                       "Борис помогает Виктору с переездом семьи в Казань весной.")
        self.assertTrue(ok)
        self.assertIn("another order", why)

    def test_limit_a_translation_is_not_found(self):
        """N7: the rule reads words; a fact retold in another language shares only its names."""
        self.assertFalse(dream.duplicate_pair(NOT_PAIRS[5][1], NOT_PAIRS[5][2])[0])

    def test_a_short_fact_pairs_by_its_handle_only(self):
        self.assertTrue(dream.duplicate_pair("@viktor_k — Виктор", "Юзернейм Виктора в Telegram: @viktor_k.")[0])
        self.assertFalse(dream.duplicate_pair("@viktor_k — Виктор", "Юзернейм Виктора в Telegram: @vik_work.")[0])
        self.assertFalse(dream.duplicate_pair("Виктор", "Виктор живёт в Анапе.")[0], "no anchor, not the same text")

    def test_a_fact_without_significant_words_pairs_only_with_its_own_text(self):
        self.assertTrue(dream.duplicate_pair("4410", "4410 ")[0])
        self.assertFalse(dream.duplicate_pair("4410", "Ветка 4410 — мост.")[0])

    def test_different_date_stamps_are_provenance_not_numbers(self):
        """P7: two copies stamped on different days are one fact; a date in the body is a number."""
        self.assertTrue(dream.duplicate_pair(*RETELLINGS[6][1:])[0])
        self.assertFalse(dream.duplicate_pair("Виктор переезжает в Казань 2026-11-01 вместе с семьёй и котом.",
                                              "Виктор переезжает в Казань 2026-12-01 вместе с семьёй и котом.")[0])

    def test_why_names_the_shared_numbers_and_the_containment(self):
        ok, why = dream.duplicate_pair(*RETELLINGS[0][1:])
        self.assertTrue(ok)
        self.assertRegex(why, r"containment 0\.\d\d")
        self.assertIn("4410", why)

    def test_the_settings_are_read_and_a_bad_one_is_ignored(self):
        cfg = dream.SETTINGS.CONFIG
        try:
            dream.configure(dict(cfg, duplicates={"min_containment": 0.95, "per_night": "x", "enabled": False}))
            self.assertEqual(dream.SETTINGS.DUP_MIN_CONTAINMENT, 0.95)
            self.assertEqual(dream.SETTINGS.DUP_PER_NIGHT, 1)
            self.assertFalse(dream.SETTINGS.DUP_ENABLED)
            self.assertFalse(dream.duplicate_pair(*RETELLINGS[0][1:])[0], "0.95 is above P1's containment")
            dream.configure(dict(cfg, duplicates={"min_containment": 1.5, "max_pair_checks": 0}))
            self.assertEqual(dream.SETTINGS.DUP_MIN_CONTAINMENT, 0.7)
            self.assertEqual(dream.SETTINGS.DUP_MAX_PAIR_CHECKS, 200000)
        finally:
            dream.configure(cfg)
        self.assertTrue(dream.SETTINGS.DUP_ENABLED)
        self.assertEqual(dream.SETTINGS.DUP_COOLDOWN_DAYS, 180)


class CandidateTests(unittest.TestCase):
    """The index finds what the rule would find comparing every pair, and checks far fewer pairs."""

    def facts(self, texts):
        return [{"fact_id": i + 1, "content": t} for i, t in enumerate(texts)]

    def test_the_index_finds_every_pair_the_rule_accepts(self):
        texts = [t for case in RETELLINGS for t in case[1:]] + [t for case in NOT_PAIRS for t in case[1:3]] + [
            "@viktor_k — Виктор", "4410", "4410 "]
        facts = self.facts(texts)
        found, _checks = dream.find_duplicate_pairs(facts)
        brute = {(a["fact_id"], b["fact_id"]) for n, a in enumerate(facts) for b in facts[n + 1:]
                 if dream.duplicate_pair(a["content"], b["content"])[0]}
        self.assertEqual({(p["a"], p["b"]) for p in found}, brute)
        self.assertGreaterEqual(len(brute), len(RETELLINGS))

    def test_a_short_fact_and_a_long_one_meet_through_their_handle(self):
        """One shared stem is not enough for the index: the short path finds the pair by the handle."""
        found, _ = dream.find_duplicate_pairs(self.facts(["@viktor_k — Виктор",
                                                          "Юзернейм Виктора в Telegram: @viktor_k."]))
        self.assertEqual([(p["a"], p["b"]) for p in found], [(1, 2)])

    def test_the_budget_stops_the_search(self):
        facts = self.facts([t for case in RETELLINGS for t in case[1:]])
        _found, checks = dream.find_duplicate_pairs(facts)
        self.assertGreater(checks, 2)
        with self.assertRaises(dream.DupBudget):
            dream.find_duplicate_pairs(facts, max_checks=2)
        dream.find_duplicate_pairs(facts, max_checks=checks)  # exactly enough: no exception


if __name__ == "__main__":
    unittest.main()
