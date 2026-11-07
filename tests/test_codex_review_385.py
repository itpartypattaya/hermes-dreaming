"""Codex review of 3.8.4–3.8.5 (gpt-6-astra, 2026-11-07): what the nightly prompt and the loss guard still got wrong.

Requirements, in the human's words:
- The night changes the entry it means and no other. Core picks the entry a patch's old_text names by a whole-entry
  exact match first: an old_text that is the whole text of another, shorter entry patches THAT entry. The prompt says
  so, and has the night check the entry the answer says it changed (`patched_entry_before`).
- A failed batch retried one operation at a time never removes an entry whose text was to go into another one before
  that other one succeeded: a failed merge must not end with the merged entry deleted.
- A loss stays a loss when the night also touched an entry that looks like the lost one: a changed entry counts as
  the lost one's rewrite only when it says what was the lost entry's own (its words no other entry had), not just
  the words they shared yesterday. A real merge still counts.
- A date stays the date it was: "2026-11-03" rewritten as "2026-03-11" is a loss, though the same numbers are there;
  "3 November 2026" for "2026-11-03" is not.
- An entry the night split in two, its beginning kept in the first half, is not a truncated entry.
- Not changed, on purpose: an entry rewritten into its opposite ("not allergic") is what the night is told to do with
  a conflict whose newer statement is trustworthy; the guard looks for text that is gone, not for a change of mind.
- The test that the prompt asks only for actions core offers sees every way the prompt can name one.
"""

import unittest


from test_install import install_cron
from test_loss_guard_review_385 import BALLET, SWIMMING, _Guard
from test_loss_guard_rewrite_21 import MEMORY  # noqa: E402
from test_memory_patch_386 import CORE_022_ACTIONS, prompt_memory_actions


class ThePromptNamesTheEntryItChanges(unittest.TestCase):
    PROMPT = install_cron.DREAM_PROMPT

    def test_old_text_must_not_be_another_entry_whole(self):
        self.assertRegex(self.PROMPT, r"whole text of another entry")
        self.assertIn("patched_entry_before", self.PROMPT)

    def test_a_remove_waits_for_the_change_that_takes_its_text(self):
        self.assertRegex(self.PROMPT, r"one at a time.{0,200}patch(es)? and add(s)? first")
        self.assertRegex(self.PROMPT, r"failed, keep the entry")

    def test_the_skill_says_the_same(self):
        skill = (install_cron.Path(install_cron.__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"whole text of another entry")
        self.assertIn("patched_entry_before", skill)
        self.assertRegex(skill, r"failed, keep the entry")


class TheActionCheckSeesEveryPhrasing(unittest.TestCase):
    """Finding 7: `memory delete` was caught, `the memory tool with action=delete` was not."""

    def test_every_phrasing_is_read(self):
        for text, action in (("Then memory delete the rest.", "delete"),
                             ("For cleanup call the memory tool with action=delete.", "delete"),
                             ("call memory with action: update", "update"),
                             ('operations: [{"action": "replace", "target": "memory"}]', "replace")):
            with self.subTest(text=text):
                self.assertIn(action, prompt_memory_actions(text))

    def test_other_tools_actions_are_not_memorys(self):
        self.assertEqual(prompt_memory_actions("call fact_store action=update; fact_feedback with action=confirmed"),
                         set())

    def test_the_prompt_passes_the_wider_check(self):
        named = prompt_memory_actions(install_cron.DREAM_PROMPT)
        self.assertTrue(named)
        self.assertLessEqual(named, CORE_022_ACTIONS, named)


class ATouchedSiblingDoesNotHideALoss(_Guard):
    """Finding 3: a small edit to the entry that looks like the lost one made the loss a "rewrite"."""

    BEFORE = [BALLET, SWIMMING] + [MEMORY[i] for i in (0, 1, 3, 5, 6, 7, 10, 11)]

    def test_a_deleted_entry_stays_lost_when_its_sibling_was_edited(self):
        touched = SWIMMING.replace("afterwards.", "afterwards, usually.")
        after = [touched if e == SWIMMING else e for e in self.BEFORE if e not in (BALLET, MEMORY[3], MEMORY[6])]
        alerts = self._night(self.BEFORE, after)
        self.assertLoss(alerts, 3)
        self.assertIn("Masha (daughter, 7) goes to ballet", alerts[0]["message"])

    def test_a_real_merge_into_the_sibling_is_still_not_a_loss(self):
        merged = ("Masha (daughter, 7) goes to swimming on Thursdays and to ballet on Mondays, both at 17:30; "
                  "Nina picks her up afterwards.")
        after = [merged if e == SWIMMING else e for e in self.BEFORE if e not in (BALLET, MEMORY[3], MEMORY[6])]
        self.assertEqual(self._night(self.BEFORE, after), [])     # 2 of 10 lost: under the threshold


class ADateIsComparedAsADate(_Guard):
    """Finding 5: the numbers of a date were a set — day and month swapped kept all of them."""

    REST = [MEMORY[i] for i in (0, 1, 3, 7)]
    BEFORE = ["Masha's school interview in Vila Real is on 2026-11-03 at 10:00, Nina goes with her.",
              "Leo's residence card renewal is on 2026-12-04; Nina collects the documents."]

    def test_day_and_month_swapped_is_a_loss(self):
        after = ["Masha's school interview in Vila Real is on 2026-03-11 at 10:00, Nina goes with her.",
                 "Leo's residence card renewal is on 2026-04-12; Nina collects the documents."]
        self.assertLoss(self._night(self.BEFORE + self.REST, after + self.REST), 2)

    def test_the_same_date_in_other_words_is_not_a_loss(self):
        after = ["Masha's school interview in Vila Real: 3 November 2026, 10:00; Nina goes with her.",
                 "Leo's residence card renewal: December 4, 2026; Nina collects the documents."]
        self.assertEqual(self._night(self.BEFORE + self.REST, after + self.REST), [])


class AnHonestSplitIsNotATruncation(_Guard):
    """Finding 6: a long entry split into its first sentence and the rest raised entry_truncated."""

    LONG = ("Masha (daughter, 7) goes to ballet on Mondays at 17:30 at the Alfama studio. "
            "The studio asks for soft shoes and a white leotard, the teacher is Senhora Ribeiro, the monthly fee of "
            "60 euro goes to the studio's Millennium account by the 5th, and Nina keeps the receipts in the blue "
            "folder. Lessons are cancelled on school holidays; the studio posts changes in its Telegram channel, "
            "which Nina reads. The end-of-year show is in June at the Tivoli theatre, tickets are 12 euro, and "
            "grandmother Ana always comes. Leo drives on show days, Nina does the hair and the make-up.")
    REST = [MEMORY[i] for i in (0, 1, 3, 7)]

    def test_split_with_the_head_kept_is_not_truncated(self):
        first, rest = self.LONG.split(" The studio asks", 1)
        after = [first, "Ballet studio notes: the studio asks" + rest]
        self.assertGreater(len(self.LONG), 2.5 * len(first))
        self.assertEqual(self._night([self.LONG] + self.REST, after + self.REST), [])

    def test_the_rest_dropped_is_still_truncated(self):
        first = self.LONG.split(" The studio asks", 1)[0]
        kinds = [a["kind"] for a in self._night([self.LONG] + self.REST, [first] + self.REST)]
        self.assertIn("entry_truncated", kinds)


class AConflictUpdateIsNotALoss(_Guard):
    """Finding 4, not changed: the night is told to patch a conflict into its newer statement, a "not" included."""

    REST = [MEMORY[i] for i in (0, 1, 3, 7)]

    def test_an_entry_rewritten_into_its_newer_opposite_is_not_asked_about(self):
        before = ["Masha is allergic to peanuts; Nina keeps an antihistamine in her bag.",
                  "Leo drinks coffee in the morning, two cups before nine."]
        after = ["Masha is not allergic to peanuts any more (tested in October); Nina keeps an antihistamine in "
                 "her bag.", "Leo does not drink coffee in the morning now, two cups before nine stopped."]
        self.assertEqual(self._night(before + self.REST, after + self.REST), [])


if __name__ == "__main__":
    unittest.main()
