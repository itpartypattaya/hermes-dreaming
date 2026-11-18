"""Finding 6 of the code review of 2026-11-17: an agent's conclusion became "confirmed" by a match of words.

A fact the agent inferred (origin `inferred`) whose words people used again on two days went to `confirm`, and the
nightly agent called fact_feedback(confirmed) on it by itself: the store then read it as confirmed by a person, and the
next nights promoted it to durable memory. The requirement of 2026-10-30 (tests/test_promote_confirmed.py) says it is
offered for confirmation and promoted once a person confirmed it; Anton, 2026-11-18: the person is asked.

Requirements:
- such a conclusion is not in `confirm` (the agent applies those on its own) but in `confirm_ask`: a question to the
  person, with the call to make on their yes only;
- a fact a person said, or one stored without an origin, said again stays in `confirm` as before;
- the question rests once a night the agent answered asked it, and comes back after a night nobody answered;
- alone it does not wake the agent, as `confirm` does not; the gate passes it on cut to `max_content`;
- the stock nightly prompt says to ask and to call only on a yes, and the prompt before it is still known as stock.
"""

import json
from pathlib import Path

from test_dream import precheck
from test_promote_confirmed import USED, _Holographic


class _SaidAgain(_Holographic):
    def said_again(self, origin):
        self.add_fact(USED, trust=0.5, days_old=10)
        for d in (1, 3, 5):
            self.add_message(f"Марина снова ходила на утренние тренировки по вторникам, день {d}", days_ago=d)
        self.history({1: origin})


class AConclusionIsAskedAbout(_SaidAgain):
    def test_an_inference_said_again_is_a_question_not_a_call(self):
        self.said_again("inferred")
        result = self.run_dream()
        self.assertEqual(result["confirm"], [])
        self.assertEqual([i["fact_id"] for i in result["confirm_ask"]], [1])
        item = result["confirm_ask"][0]
        self.assertNotIn("call", item)
        self.assertEqual(item["call_on_yes"], {"tool": "fact_feedback", "action": "confirmed", "fact_id": 1})
        self.assertEqual(item["origin"], "inferred")
        self.assertEqual(result["stats"]["confirm_ask"], 1)

    def test_what_a_person_said_is_confirmed_as_before(self):
        for origin in ("said", None):
            with self.subTest(origin=origin):
                self.setUp()
                try:
                    self.said_again(origin)
                    result = self.run_dream()
                    self.assertEqual([i["fact_id"] for i in result["confirm"]], [1])
                    self.assertEqual(result["confirm_ask"], [])
                finally:
                    self.tearDown()


class TheQuestionRests(_SaidAgain):
    def test_asked_once_on_an_answered_night(self):
        self.said_again("inferred")
        seen = str(self.home / "cache" / "dream-seen.json")
        self.assertEqual(len(self.run_dream(seen_state=seen)["confirm_ask"]), 1)
        self.assertEqual(self.run_dream(seen_state=seen)["confirm_ask"], [], "rests after an answered night")

    def test_back_after_a_night_nobody_answered(self):
        self.said_again("inferred")
        seen = str(self.home / "cache" / "dream-seen.json")
        self.assertEqual(len(self.run_dream(seen_state=seen, ack=False)["confirm_ask"]), 1)
        self.assertEqual(len(self.run_dream(seen_state=seen)["confirm_ask"]), 1, "nobody was asked: ask again")


class TheGateAndThePrompt(_Holographic):
    def test_the_gate_passes_it_on_and_does_not_wake_for_it(self):
        item = {"fact_id": 7, "days": 3, "content": "x" * 900, "origin": "inferred", "fingerprint": "f",
                "call_on_yes": {"tool": "fact_feedback", "action": "confirmed", "fact_id": 7}}
        out = precheck.compact_payload({"stats": {"promotions": 1}, "confirm_ask": [item]}, max_content=300)
        self.assertEqual(out["confirm_ask"], [{"fact_id": 7, "days": 3, "content": "x" * 300, "origin": "inferred"}])
        self.assertIsNone(precheck.compact_payload({"stats": {"confirm_ask": 1}, "confirm_ask": [item]}))

    def test_the_stock_prompt_asks_and_the_previous_one_is_stock(self):
        from test_install import install_cron
        prompt = install_cron.DREAM_PROMPT
        self.assertIn("confirm_ask", prompt)
        self.assertIn("only on their yes", prompt)
        self.assertNotIn("a conclusion of yours becomes confirmed", prompt)
        history = json.loads(Path(install_cron.__file__).with_name("cron-prompt-history.json").read_text(encoding="utf-8"))
        self.assertIn(PREVIOUS_DIGEST, history["DREAM_PROMPT"], "an install on 3.8.11's prompt is updated, not kept")


# The sha256 of 3.8.11's DREAM_PROMPT, the stock prompt every install had before this change.
PREVIOUS_DIGEST = "86a2ab2b2e818105e6a078c5422b63db7349af1a1c7a94b07f8c4b176a4a27ba"
