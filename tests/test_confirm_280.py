"""2.8.0: `confirm` — facts people said again on different days, offered to fact_feedback(action="confirmed")
(holographic >= 0.15.0).

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import os
import sqlite3
import tempfile
import unittest

from test_dream import dream  # noqa: E402  (same directory)

FACT = "Ilya moved to Lisbon with his family"


def _msg(text, day):
    return {"content": text, "tokens": dream.sig_tokens(text), "day": day, "ts": 1_790_000_000.0}


def _fact(fact_id=1, content=FACT, ref_days=2, **extra):
    return {"fact_id": fact_id, "content": content, "meta": {"ref_days": ref_days}, **extra}


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "memory_store.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE fact_confirmations (fact_id INTEGER, confirmed_at TEXT, session_id TEXT, "
                    "user_id TEXT, via TEXT)")
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, facts, messages, source="holographic"):
        return dream.confirm_items(facts, messages, None, source, db=self.db)

    def test_said_again_on_two_days_is_offered(self):
        msgs = [_msg("Ilya has moved to Lisbon now", "2026-10-01"),
                _msg("since Ilya moved to Lisbon the family is happier", "2026-10-05")]
        got = self.items([_fact()], msgs)
        self.assertEqual([(i["fact_id"], i["days"]) for i in got], [(1, 2)])
        self.assertEqual(got[0]["call"], {"tool": "fact_feedback", "action": "confirmed", "fact_id": 1})

    def test_naming_the_topic_is_not_saying_it_again(self):
        msgs = [_msg("Ilya called from Lisbon", "2026-10-01"), _msg("Ilya sent photos from Lisbon", "2026-10-05")]
        self.assertEqual(self.items([_fact()], msgs), [])  # 2 of the fact's 4 stems: below CONFIRM_MIN_COVERAGE

    def test_one_day_is_a_topic_not_an_agreement(self):
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya moved to Lisbon, yes", "2026-10-01")]
        self.assertEqual(self.items([_fact()], msgs), [])

    def test_a_message_that_negates_the_fact_does_not_count(self):
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya has not moved to Lisbon after all", "2026-10-05")]
        self.assertEqual(self.items([_fact()], msgs), [])

    def test_nothing_without_the_confirmations_table(self):
        os.remove(self.db)
        sqlite3.connect(self.db).close()  # an older provider's file: no fact_confirmations
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya moved to Lisbon", "2026-10-05")]
        self.assertEqual(self.items([_fact()], msgs), [])

    def test_a_recently_confirmed_fact_rests(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO fact_confirmations VALUES (1, datetime('now', '-3 days'), 's', 'u', 'feedback')")
        con.commit()
        con.close()
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya moved to Lisbon", "2026-10-05")]
        self.assertEqual(self.items([_fact()], msgs), [])

    def test_chat_store_facts_other_sources_and_unsafe_facts_get_no_call(self):
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya moved to Lisbon", "2026-10-05")]
        self.assertEqual(self.items([_fact(store="telegram--100")], msgs), [])
        self.assertEqual(self.items([_fact(unsafe="secret")], msgs), [])
        self.assertEqual(self.items([_fact()], msgs, source="sqlite"), [])

    def test_a_low_ref_days_fact_is_not_rechecked(self):
        msgs = [_msg("Ilya moved to Lisbon", "2026-10-01"), _msg("Ilya moved to Lisbon", "2026-10-05")]
        self.assertEqual(self.items([_fact(ref_days=1)], msgs), [])

    def test_the_precheck_passes_confirm_along(self):
        from test_dream import precheck
        data = {"stats": {"promotions": 1}, "confirm": [{"fact_id": 7, "days": 3, "content": "x" * 900,
                                                        "fingerprint": "f"}]}
        out = precheck.compact_payload(data, max_content=300)
        self.assertEqual(out["confirm"], [{"fact_id": 7, "days": 3, "content": "x" * 300}])
        quiet = {"stats": {"confirm": 1}, "confirm": data["confirm"]}  # alone it never wakes the agent
        self.assertIsNone(precheck.compact_payload(quiet))


if __name__ == "__main__":
    unittest.main()
