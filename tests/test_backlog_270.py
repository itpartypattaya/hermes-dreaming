"""2.7.0: a rejection is asked about again after half a year when the fact was said again; probes that must stay in
memory; a recurring event with another date or another day of the week is an update, never a duplicate.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from test_dream import DreamFixture, dream, reject  # noqa: E402

FACT = "Oliver goes to ballet lessons at the studio on Lesnaya street every week"
MESSAGE = "Oliver ballet lessons studio Lesnaya street again this week"


class RejectionReaskTests(DreamFixture):
    def setUp(self):
        super().setUp()
        self.state = self.home / "dream-rejected.json"
        self._reask = dream.SETTINGS.REJECT_REASK_DAYS

    def tearDown(self):
        dream.SETTINGS.REJECT_REASK_DAYS = self._reask
        super().tearDown()

    def _reject(self, days_ago):
        at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
        self.state.write_text(json.dumps({"k": {"fact_id": "3", "content": FACT, "fingerprint": reject._fingerprint(FACT),
                                                "reason": "stopped ballet", "at": at}}), encoding="utf-8")

    def _said_again(self, fact_days_old=2):
        self.add_fact(FACT, trust=0.9, rc=2, helpful=2, days_old=fact_days_old)
        for d in (1, 3, 5):
            self.add_message(MESSAGE, days_ago=d)

    def test_a_fresh_rejection_stays_silent(self):
        self._reject(days_ago=40)
        self._said_again()
        result = self.run_dream(rejected_state=str(self.state))
        self.assertEqual(result["promotions"], [])
        self.assertEqual(result["stats"]["rejected_suppressed"], 1)

    def test_an_old_rejection_said_again_is_a_question(self):
        self._reject(days_ago=200)
        self._said_again()
        result = self.run_dream(rejected_state=str(self.state))
        items = result["promotions"] + result["new_facts"]
        self.assertEqual([i["content"] for i in items][:1], [FACT])
        self.assertEqual(items[0]["previously_rejected"]["reason"], "stopped ballet")
        self.assertEqual(result["stats"]["rejected_suppressed"], 0)

    def test_the_row_rejected_back_then_stays_silent(self):
        self._reject(days_ago=200)
        self._said_again(fact_days_old=250)  # the same row: created before the rejection
        result = self.run_dream(rejected_state=str(self.state))
        self.assertEqual(result["stats"]["rejected_suppressed"], 1)

    def test_no_mention_since_the_rejection_stays_silent(self):
        self._reject(days_ago=200)
        self.add_fact(FACT, trust=0.9, rc=2, helpful=2, days_old=2)
        result = self.run_dream(rejected_state=str(self.state))
        self.assertEqual(result["stats"]["rejected_suppressed"], 1)

    def test_zero_turns_it_off(self):
        dream.SETTINGS.REJECT_REASK_DAYS = 0
        self._reject(days_ago=400)
        self._said_again()
        result = self.run_dream(rejected_state=str(self.state))
        self.assertEqual(result["stats"]["rejected_suppressed"], 1)

    def test_the_latest_rejection_is_chosen_by_time_not_by_text(self):
        recs = {"a": {"content": FACT, "fingerprint": reject._fingerprint(FACT), "at": "2026-01-01T01:00:00+00:00"},
                "b": {"content": FACT, "fingerprint": reject._fingerprint(FACT), "at": "2026-01-01T00:30:00-02:00"},
                "c": {"content": FACT, "fingerprint": reject._fingerprint(FACT), "at": "not a date"}}
        self.assertEqual(dream.rejection_of(FACT, recs)["at"], "2026-01-01T00:30:00-02:00")

    def test_a_second_rejection_starts_the_count_over(self):
        self._reject(days_ago=200)
        self._said_again()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["k2"] = dict(state["k"], at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.state.write_text(json.dumps(state), encoding="utf-8")
        result = self.run_dream(rejected_state=str(self.state))
        self.assertEqual(result["stats"]["rejected_suppressed"], 1)


class ProbeTests(DreamFixture):
    def setUp(self):
        super().setUp()
        self._probes = dream.SETTINGS.PROBES
        self.snapshot = self.home / "dream-snapshot.json"

    def tearDown(self):
        dream.SETTINGS.PROBES = self._probes
        super().tearDown()

    def _memory(self, *entries):
        self.mem_md.write_text("\n§\n".join(entries), encoding="utf-8")

    def _alerts(self):
        text = dream.load_durable_memory_text(str(self.mem_md))
        return [a for a in dream.check_memory_loss(str(self.mem_md), str(self.snapshot), text)
                if a["kind"] == "probe_missing"]

    def test_a_probe_is_found_in_any_form(self):
        self.assertTrue(dream.probe_found("аллергия на арахис", "У Лены аллергии на арахис и киви."))
        self.assertTrue(dream.probe_found("Peanut allergy", "Oliver: PEANUT  allergy, carries an EpiPen"))
        self.assertFalse(dream.probe_found("аллергия на арахис", "У Лены аллергия на киви."))

    def test_a_probe_must_stand_in_one_sentence(self):
        self.assertFalse(dream.probe_found("allergic to peanuts", "Alice is allergic to pollen; Bob likes peanuts."))
        self.assertTrue(dream.probe_found("allergic to peanuts", "Bob likes tea. Alice is allergic to peanuts."))

    def test_without_a_snapshot_probes_stay_quiet(self):
        dream.SETTINGS.PROBES = ["absent fact"]
        self.assertEqual(dream.check_memory_loss(None, None, "other"), [])

    def test_a_lost_probe_alerts_once_and_again_after_it_comes_back(self):
        dream.SETTINGS.PROBES = ["аллергия на арахис"]
        self._memory("У Лены аллергия на арахис, с собой антигистаминное.", "Балет по понедельникам.")
        self.assertEqual(self._alerts(), [])
        self._memory("Балет по понедельникам.")
        first = self._alerts()
        self.assertEqual(len(first), 1)
        self.assertIn("аллергия на арахис", first[0]["message"])
        self.assertEqual(self._alerts(), [])  # still missing: said once
        self._memory("У Лены аллергия на арахис.", "Балет по понедельникам.")
        self.assertEqual(self._alerts(), [])
        self._memory("Балет по понедельникам.")
        self.assertEqual(len(self._alerts()), 1)

    def test_no_probes_no_state(self):
        dream.SETTINGS.PROBES = []
        self._memory("Балет по понедельникам.")
        self.assertEqual(self._alerts(), [])
        self.assertNotIn("_probes_missing", json.loads(self.snapshot.read_text(encoding="utf-8")))


class RepeatedEventTests(unittest.TestCase):
    def test_two_dates_of_one_event_are_not_store_duplicates(self):
        self.assertFalse(dream._store_duplicates(
            "Oliver has a dentist appointment on 2026-09-15 at the city clinic.",
            "Oliver has a dentist appointment on 2026-10-20 at the city clinic."))
        self.assertTrue(dream._store_duplicates(
            "Oliver has a dentist appointment on 2026-10-20 at the city clinic.",
            "Oliver has a dentist appointment on 2026-10-20 at the city clinic!"))

    def test_another_date_is_a_conflict_not_a_duplicate(self):
        mem = "Oliver has a dentist appointment on 2026-09-15 at the city clinic."
        fact = "Oliver has a dentist appointment on 2026-10-20 at the city clinic."
        self.assertFalse(dream.exact_or_alias_in_memory(fact, mem))
        self.assertEqual(len(dream.find_conflicts(fact, mem)), 1)
        zh_mem, zh_fact = "奥利弗9月15日在城市诊所看牙医。", "奥利弗10月20日在城市诊所看牙医。"
        self.assertEqual(len(dream.find_conflicts(zh_fact, zh_mem)), 1)

    def test_another_day_of_the_week_is_a_conflict(self):
        cases = [("Ballet lessons for Oliver are on Mondays at 17:00 in the studio.",
                  "Ballet lessons for Oliver are on Tuesdays at 17:00 in the studio."),
                 ("Балетная студия Лены на Лесной улице: занятия по понедельникам в 17:00, тренер Анна.",
                  "Балетная студия Лены на Лесной улице: занятия по вторникам в 17:00, тренер Анна."),
                 ("Oliver geht montags um 17 Uhr ins Ballettstudio in der Waldstraße zum Training.",
                  "Oliver geht dienstags um 17 Uhr ins Ballettstudio in der Waldstraße zum Training.")]
        for mem, fact in cases:
            with self.subTest(fact=fact):
                self.assertEqual(len(dream.find_conflicts(fact, mem)), 1)
        same = "Ballet lessons for Oliver are on Mondays at 17:00 in the studio, teacher Anna."
        self.assertEqual(dream.find_conflicts(same, cases[0][0]), [])

    def test_weekday_markers(self):
        for text, days in [("по средам и пятницам", {3, 5}), ("среднее образование", set()), ("środek", set()),
                           ("рабочая среда разработки", set()), ("в среде Python", set()), ("в среду", {3}),
                           ("субботник во дворе", set()), ("воскресение Христово", set()), ("Пятницкая улица", set()),
                           ("в субботу и воскресенье", {6, 7}), ("до пятницы", {5}),
                           ("piątek", {5}), ("Salı günü", {2}), ("cumartesi", {6}), ("Pazar", set()),
                           ("星期三", {3}), ("周日", {7}), ("金曜日", {5}), ("월요일", {1}), ("2026-10-20", set())]:
            with self.subTest(text=text):
                self.assertEqual(dream._weekday_markers(dream._norm(text)), {f"weekday:{d}" for d in days})


if __name__ == "__main__":
    unittest.main()
