"""The four findings of the code review of 2026-11-17 (plans/2026-11-17-code-review.md, 1–4).

1. A human's rejection silences that statement and its rewordings — not a newer value of it: another number, day,
   time or name is the update the person may want, and it used to vanish for the 180 days of `reject_reask_days`.
2. A fact that differs from a memory entry by a number is not "already in memory" because its text happens to be the
   entry's beginning («ветка 43» / «ветка 437…»): it is a conflict the agent is asked about.
3. A time written with a dot («в 10.30», «в 9.30») is no date: a standing schedule must not be read as an event of
   30 October and dropped as expired.
4. A memory-loss alert of a night the agent never answered comes back the next night, until a turn answers it.
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from test_dream import DreamFixture, dream


class RejectionKeepsNewerValueTests(DreamFixture):
    """Finding 1: `rejection_of` compared word stems only."""

    def _rejected(self, text):
        return {"k": {"content": text, "fingerprint": "другой", "at": "2026-11-01T00:00:00+00:00"}}

    def test_other_number_is_not_rejected(self):
        self.assertFalse(dream.is_rejected("Аренда дома в Лиссабоне стоит 3000 евро в месяц",
                                           self._rejected("Аренда дома в Лиссабоне стоит 2500 евро в месяц")))

    def test_other_weekday_is_not_rejected(self):
        self.assertFalse(dream.is_rejected("Нина ходит на балет в студию по четвергам",
                                           self._rejected("Нина ходит на балет в студию по вторникам")))

    def test_other_time_is_not_rejected(self):
        self.assertFalse(dream.is_rejected("Занятие в школе начинается в 9:30 утра каждый день",
                                           self._rejected("Занятие в школе начинается в 8:00 утра каждый день")))

    def test_other_name_is_not_rejected(self):
        self.assertFalse(dream.is_rejected("Ольга работает с клиентом Бета по поставкам кофе",
                                           self._rejected("Ольга работает с клиентом Альфа по поставкам кофе")))

    def test_rewording_with_the_same_number_is_still_rejected(self):
        # The list exists for this: the core re-extracts the stale statement in other words.
        self.assertTrue(dream.is_rejected("В Лиссабоне аренда дома стоит 2500 евро каждый месяц",
                                          self._rejected("Аренда дома в Лиссабоне стоит 2500 евро в месяц")))

    def test_rewording_with_another_opening_word_is_still_rejected(self):
        # The first word of a sentence is capitalized whatever it is: it is no name.
        self.assertTrue(dream.is_rejected("Школьная ветка 30 закрыта для напоминаний о домашнем задании",
                                          self._rejected("Ветка 30 школьная, закрыта для напоминаний о домашнем задании")))


class ChangedNumberIsNotInMemoryTests(DreamFixture):
    """Finding 2: `exact_or_alias_in_memory` returned on a raw substring before it compared the numbers."""

    # Four stems at least: the conflict detector does not compare shorter facts (CONFLICT_MIN_SHARED).
    MEMORY = "Уведомления рабочей команды Лео идут в ветку 437 с понедельника"

    def test_number_prefix_is_not_in_memory(self):
        self.assertFalse(dream.exact_or_alias_in_memory("Уведомления рабочей команды Лео идут в ветку 43", self.MEMORY))

    def test_number_prefix_is_a_conflict(self):
        hits = dream.find_conflicts("Уведомления рабочей команды Лео идут в ветку 43", self.MEMORY)
        self.assertTrue(hits)
        self.assertIn("43", hits[0]["fact_numbers"])

    def test_the_night_asks_about_it(self):
        self.mem_md.write_text(self.MEMORY + "\n", encoding="utf-8")
        self.add_fact("Уведомления рабочей команды Лео идут в ветку 43", trust=0.9, rc=3, helpful=1)
        for d in (1, 3):
            self.add_message("уведомления рабочей команды Лео теперь идут в ветку 43", days_ago=d)
        result = self.run_dream()
        self.assertIn("Уведомления рабочей команды Лео идут в ветку 43", [c["content"] for c in result["conflicts"]])

    def test_the_same_text_is_still_in_memory(self):
        self.assertTrue(dream.exact_or_alias_in_memory("Уведомления рабочей команды Лео идут в ветку 437", self.MEMORY))


class TimeWithADotIsNoDateTests(unittest.TestCase):
    """Finding 3: NUMERIC_DATE_RE read «10.30» as 30 October (the day-first reading has no month 30)."""

    TODAY = date(2026, 11, 17)

    def test_lesson_time_is_not_an_expired_event(self):
        self.assertFalse(dream.is_expired_event("Урок английского по вторникам в 10.30, принести тетрадь",
                                                today=self.TODAY))

    def test_meeting_time_is_not_an_expired_event(self):
        self.assertFalse(dream.is_expired_event("Созвон с командой каждый понедельник в 9.30", today=self.TODAY))

    def test_time_is_not_a_date(self):
        self.assertEqual(dream.extract_event_dates("Урок английского по вторникам в 10.30", self.TODAY), ([], []))

    def test_a_dotted_date_still_expires(self):
        self.assertTrue(dream.is_expired_event("Контрольная по математике 30.10, принести тетрадь",
                                               today=self.TODAY))

    def test_a_dotted_date_with_a_year_still_expires(self):
        self.assertTrue(dream.is_expired_event("Контрольная по математике 10.30.2026, принести тетрадь",
                                               today=self.TODAY))


class LossAlertComesBackUntilAnsweredTests(DreamFixture):
    """Finding 4: the snapshot moved on in the night that raised the alert, so a night nobody answered lost it."""

    ENTRIES = ["Лео живёт в Лиссабоне с весны", "Нина работает в школе учителем",
               "Ветка 30 для рабочих задач Лео", "Абонемент в зал стоит 1500 в месяц",
               "Пароль от роутера меняли в октябре", "Кошку зовут Мурка"]

    def setUp(self):
        super().setUp()
        self.seen = self.home / "seen.json"
        self.snap = self.home / "snapshot.json"
        # One night a day, in the past: a pass is known by its `generated_at` (to the second), and two passes in one
        # second would share the answer of either.
        self.nights = 0

    def _night(self, ack):
        self.nights += 1
        when = datetime.now(timezone.utc) - timedelta(days=10 - self.nights)
        with mock.patch.object(dream, "_now", lambda: when):
            result = dream.run(14, 60, 60, str(self.mem_md), seen_state_path=str(self.seen),
                               snapshot_state_path=str(self.snap))
        if ack:
            self.ack_agent_turn(result)
        return [a for a in result["alerts"] if a["kind"] == "memory_loss"]

    def _lose_entries(self):
        self.mem_md.write_text("\n§\n".join(self.ENTRIES[:2]) + "\n", encoding="utf-8")

    def test_unanswered_alert_comes_back(self):
        self.mem_md.write_text("\n§\n".join(self.ENTRIES) + "\n", encoding="utf-8")
        self._night(ack=True)
        self._lose_entries()
        self.assertTrue(self._night(ack=False))
        self.assertTrue(self._night(ack=True), "the night that raised it went unanswered")

    def test_answered_alert_is_said_once(self):
        self.mem_md.write_text("\n§\n".join(self.ENTRIES) + "\n", encoding="utf-8")
        self._night(ack=True)
        self._lose_entries()
        self.assertTrue(self._night(ack=True))
        self.assertEqual(self._night(ack=True), [])

    def test_alert_is_carried_once_not_doubled(self):
        self.mem_md.write_text("\n§\n".join(self.ENTRIES) + "\n", encoding="utf-8")
        self._night(ack=True)
        self._lose_entries()
        self._night(ack=False)
        self.assertEqual(len(self._night(ack=False)), 1)


if __name__ == "__main__":
    unittest.main()
