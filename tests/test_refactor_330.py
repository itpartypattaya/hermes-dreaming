"""3.3.0, phase 1 of the cloud-judge plan: the contract of the refactor (phase 2), written before the code.

What the judge needs from the pass, without the judge yet:
- one predicate says whether a (fact, message) pair counts, and the counting uses it — two implementations must
  not drift apart;
- a pair has a stable id, and a set of vetoed ids removes exactly those pairs from the count, everywhere the count
  is read (promotion, confirmations), while the memory-entry questions (md_decays) stay on the rules;
- a message keeps where it came from (source, chat, thread, session) and whether the trust rules let it in;
- the night's decisions are one function of explicit inputs: called twice it gives the same answer, it writes
  nothing, reads no clock, database, environment or config file, and leaves the settings alone;
- the store's "recently confirmed" query uses the night's time, not the database clock.

Until phase 2 these tests fail because the contract is missing (AttributeError / TypeError); the golden record
(test_golden_330) must stay green before and after.
"""

import builtins
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from test_golden_330 import CONFIRMED, FROZEN, PROMOTED, GoldenScenario
from test_dream import FAMILY_CHAT, dream


def _forbidden(*_a, **_k):
    raise AssertionError("the decision function touched the world")


class _Home(GoldenScenario):
    def facts(self):
        return dream.load_all_facts()

    def messages(self):
        return dream.load_messages(os.path.join(dream.HOME, "state.db"), 60)

    def main_rules(self, facts=None):
        return dream.rules_context(f["content"] for f in (facts or self.facts()) if not f.get("store"))

    def fact_by(self, content):
        return next(f for f in self.facts() if f["content"] == content)


class PairPredicateTests(_Home):
    def test_the_pair_predicate_agrees_with_the_count_on_every_pair_of_the_golden_home(self):
        facts, messages = self.facts(), self.messages()
        rules = self.main_rules(facts)
        by_store = dream.messages_by_store(messages)
        checked = 0
        for f in facts:
            tokens = dream.fact_tokens(f["content"])
            probe = dream.fact_probe(tokens, rules)
            for m in dream.messages_for(f, messages, by_store):
                counted = dream.corroborate(tokens, [m], f["content"], rules=rules)[0]
                self.assertEqual(bool(counted), dream._pair_counts(probe, m, f["content"]),
                                 (f["fact_id"], m["content"][:60]))
                checked += 1
        self.assertGreater(checked, 50)


class VetoTests(_Home):
    def _count(self, fact, veto=frozenset()):
        rules = self.main_rules()
        tokens = dream.fact_tokens(fact["content"])
        return dream.corroborate(tokens, self.messages(), fact["content"], rules=rules, veto=veto,
                                 fact_id=fact["fact_id"])[0]

    def _pairs(self, fact):
        rules = self.main_rules()
        probe = dream.fact_probe(dream.fact_tokens(fact["content"]), rules)
        counted, not_counted = [], []
        for m in self.messages():
            pid = dream.PairId(fact["fact_id"], dream.message_key(m))
            (counted if dream._pair_counts(probe, m, fact["content"]) else not_counted).append(pid)
        return counted, not_counted

    def test_a_vetoed_pair_stops_counting_and_only_that_pair(self):
        fact = self.fact_by(PROMOTED)
        counted, not_counted = self._pairs(fact)
        self.assertGreaterEqual(len(counted), 3)
        base = self._count(fact)
        self.assertEqual(base, len(counted))
        self.assertEqual(self._count(fact, frozenset({counted[0]})), base - 1)
        self.assertEqual(self._count(fact, frozenset(not_counted)), base)  # a veto adds nothing and takes no other

    def test_a_veto_of_another_fact_changes_nothing(self):
        fact, other = self.fact_by(PROMOTED), self.fact_by(CONFIRMED)
        self.assertEqual(self._count(fact, frozenset(self._pairs(other)[0])), self._count(fact))

    def test_confirmations_skip_a_vetoed_pair(self):
        fact = self.fact_by(CONFIRMED)
        safe = [dict(fact, meta={"ref_days": 3})]
        messages = self.messages()
        rules = self.main_rules()
        db = os.path.join(dream.HOME, "memory_store.db")
        got = dream.confirm_items(safe, messages, None, "holographic", db=db, rules=rules, now=FROZEN)
        self.assertEqual([i["fact_id"] for i in got], [fact["fact_id"]])
        counted, _ = self._pairs(fact)
        got = dream.confirm_items(safe, messages, None, "holographic", db=db, rules=rules, now=FROZEN,
                                  veto=frozenset(counted))
        self.assertEqual(got, [])

    def test_memory_entry_questions_take_the_rules_and_no_veto(self):
        rules = self.main_rules()
        asked = os.path.join(self.tmp.name, "asked-a.json")
        again = os.path.join(self.tmp.name, "asked-b.json")
        trusted = dream.messages_by_store(self.messages())[None]
        plain = dream.md_decays(str(self.mem_md), trusted, 60, asked)
        with_rules = dream.md_decays(str(self.mem_md), trusted, 60, again, rules=rules)
        self.assertEqual(with_rules, plain)
        self.assertEqual(len(plain), 1)  # the entry nobody mentions — and only it — is asked about
        self.assertIn("граммофон", plain[0]["entry"])


class MessageProvenanceTests(_Home):
    def test_a_message_keeps_where_it_came_from(self):
        msgs = self.messages()
        family = next(m for m in msgs if m["content"].startswith("Гоша сегодня опять"))
        for key, value in (("source", "telegram"), ("chat_id", FAMILY_CHAT), ("thread_id", None),
                           ("session_found", True), ("locally_trusted", True), ("own_only", False)):
            self.assertEqual(family[key], value, key)
        self.assertTrue(family["session_id"])
        own = next(m for m in msgs if m["content"].startswith("Барсик опять"))
        self.assertEqual((own["locally_trusted"], own["own_only"]), (False, True))

    def test_a_message_without_its_session_row_is_marked(self):
        # The trust rules let it in as a session without chat (the default, set here explicitly); the record must
        # still say that its session row is missing — the judge sends nothing of unknown origin.
        old = dream.SETTINGS.TRUST_NO_CHAT
        dream.SETTINGS.TRUST_NO_CHAT = True
        self.addCleanup(setattr, dream.SETTINGS, "TRUST_NO_CHAT", old)
        con = sqlite3.connect(os.path.join(dream.HOME, "state.db"))
        con.execute("insert into messages (session_id, role, content, timestamp, observed) values (?,?,?,?,?)",
                    ("s-gone", "user", "Сообщение сессии, которой больше нет в таблице",
                     (FROZEN - timedelta(days=1)).timestamp(), 0))
        con.commit()
        con.close()
        lost = [m for m in self.messages() if m["content"].startswith("Сообщение сессии")]
        self.assertEqual(len(lost), 1)
        self.assertEqual((lost[0]["session_found"], lost[0]["source"], lost[0]["session_id"]),
                         (False, None, "s-gone"))

    def test_messages_come_in_database_order_and_their_key_is_stable(self):
        msgs = self.messages()
        self.assertEqual([m["id"] for m in msgs], sorted(m["id"] for m in msgs))
        keys = [dream.message_key(m) for m in msgs]
        self.assertEqual(keys, [dream.message_key(m) for m in self.messages()])
        self.assertEqual(len(set(keys)), len(keys))


class DecisionFunctionTests(_Home):
    def inputs(self):
        return dream.load_inputs(14, 60, str(self.mem_md), rejected_state_path=self.paths["rejected"],
                                 seen_state_path=self.paths["seen"])

    def test_two_calls_give_the_same_decisions(self):
        inputs = self.inputs()
        self.assertEqual(dream._decide(inputs, frozenset()), dream._decide(inputs, frozenset()))

    def test_the_inputs_cannot_be_changed(self):
        inputs = self.inputs()
        with self.assertRaises(Exception):
            inputs.now = FROZEN + timedelta(days=3)

    def test_the_decision_function_touches_nothing_outside_its_inputs(self):
        inputs = self.inputs()
        before = dream.settings_fingerprint()
        seen_before = repr(inputs.seen)
        with mock.patch.object(builtins, "open", _forbidden), mock.patch.object(os, "replace", _forbidden), \
                mock.patch.object(sqlite3, "connect", _forbidden), mock.patch.object(dream, "_now", _forbidden), \
                mock.patch.object(os.environ, "get", _forbidden), mock.patch.object(os, "getenv", _forbidden), \
                mock.patch.object(dream, "configure", _forbidden):
            decisions = dream._decide(inputs, frozenset())
        self.assertEqual(dream.settings_fingerprint(), before)
        self.assertEqual(repr(inputs.seen), seen_before)
        self.assertTrue(decisions.promotions)

    def test_the_settings_fingerprint_sees_a_change_made_in_place(self):
        before = dream.settings_fingerprint()
        old = dream.SETTINGS.MIN_MENTIONS
        dream.SETTINGS.MIN_MENTIONS = old + 1
        try:
            self.assertNotEqual(dream.settings_fingerprint(), before)
        finally:
            dream.SETTINGS.MIN_MENTIONS = old

    def test_the_first_decisions_are_what_the_night_publishes(self):
        decisions = dream._decide(self.inputs(), frozenset())
        result = self.night()
        for section in ("promotions", "new_facts", "ephemeral_events", "conflicts", "fact_decays", "confirm",
                        "trust_feedback"):
            self.assertEqual([str(i["fact_id"]) for i in getattr(decisions, section)],
                             [str(i["fact_id"]) for i in result[section]], section)

    def test_a_veto_only_takes_away_counted_pairs(self):
        inputs = self.inputs()
        free = dream._decide(inputs, frozenset())
        promoted = self.fact_by(PROMOTED)
        one = next(p for p in free.counted["promotion"] if p.fact_id == promoted["fact_id"])
        vetoed = dream._decide(inputs, frozenset({one}))
        self.assertLess(vetoed.counted["promotion"], free.counted["promotion"])  # a strict subset
        self.assertNotIn(one, vetoed.counted["promotion"])
        mentions = {s["fact_id"]: s["meta"]["mentions"] for s in free.scored}
        for s in vetoed.scored:
            self.assertLessEqual(s["meta"]["mentions"], mentions[s["fact_id"]])


class NightClockTests(unittest.TestCase):
    def test_recent_confirmations_use_the_nights_time_not_the_database_clock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory_store.db")
            con = sqlite3.connect(db)
            con.execute("create table fact_confirmations (fact_id integer, confirmed_at text, session_id text,"
                        " user_id text, via text)")
            con.execute("insert into fact_confirmations values (7, '2029-12-01 10:00:00', 's', 'u', 'feedback')")
            con.commit()
            con.close()
            night = datetime(2030, 1, 15, tzinfo=timezone.utc)  # 45 days after the confirmation
            self.assertEqual(dream.recent_confirmations(db, 30, now=night), set())
            self.assertEqual(dream.recent_confirmations(db, 60, now=night), {7})


if __name__ == "__main__":
    unittest.main()
