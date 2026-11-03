"""Review of 3.8.4 (the loss guard that tells a rewrite from a loss, #21): what it still got wrong.

Requirements, in the human's words:
- An entry that is gone counts as lost unless the night rewrote it. An entry nobody touched is not a rewrite of
  anything: a deleted entry that happens to look like a sibling still standing in memory is a loss, and it must not
  pull the share of losses under the alert threshold.
- One entry the night split into two is not a loss: its text is still there, across both.
- Numbers are compared as numbers: a numbered list turned into bullets, "9,000" written as "9000", a date written as
  "3 November 2026" instead of "2026-11-03" — nothing was lost.
- An entry of short words only ("Cat is Dan. Pet is dog.") is judged by its words, too.
- A snapshot saved with a UTF-8 BOM (Windows editors, PowerShell 5) is still the baseline of the next pass.
- An alert compared with a snapshot of an older version does not claim the text is nowhere: that was never checked.

And the guards: a real loss — entries deleted among many, short ones, half of a split entry, the file emptied or
gone — still raises the alert.
"""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_dream import dream  # noqa: E402
from test_loss_guard_rewrite_21 import MEMORY  # noqa: E402

BALLET = "Masha (daughter, 7) goes to ballet on Mondays at 17:30; Ksenia picks her up afterwards."
SWIMMING = "Masha (daughter, 7) goes to swimming on Thursdays at 17:30; Ksenia picks her up afterwards."


class _Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "memories").mkdir()
        self.mem = self.home / "memories" / "MEMORY.md"
        self.snap = self.home / "cache" / "dream-snapshot.json"
        self.snap.parent.mkdir()
        self._old_home = dream.HOME
        dream.HOME = str(self.home)

    def tearDown(self):
        dream.HOME = self._old_home
        self.tmp.cleanup()

    def _write(self, entries):
        self.mem.write_text(dream.MEMORY_ENTRY_DELIMITER.join(entries), encoding="utf-8")

    def _check(self):
        return [a for a in dream.check_memory_loss(str(self.mem), str(self.snap))
                if a["kind"] in ("memory_loss", "entry_truncated")]

    def _night(self, before, after):
        self._write(before)
        self.assertEqual(self._check(), [])
        self._write(after)
        return self._check()

    def assertLoss(self, alerts, lost):
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertEqual(alerts[0]["lost"], lost, alerts)


class AnUntouchedSiblingIsNotARewrite(_Guard):
    """Finding 1: a deleted entry was compared with every entry in memory, the ones nobody touched included."""

    BEFORE = [BALLET, SWIMMING] + [MEMORY[i] for i in (0, 1, 3, 5, 6, 7, 10, 11)]

    def test_a_deleted_entry_like_a_kept_sibling_is_still_a_loss(self):
        after = [e for e in self.BEFORE if e not in (BALLET, MEMORY[3], MEMORY[6])]
        alerts = self._night(self.BEFORE, after)      # 3 of 10 deleted, the sibling of one of them kept
        self.assertLoss(alerts, 3)
        self.assertIn("Masha (daughter, 7) goes to ballet", alerts[0]["message"])
        self.assertEqual(alerts[0]["rewritten"], 0)

    def test_an_entry_moved_verbatim_to_another_file_is_still_not_a_loss(self):
        user = self.home / "memories" / "USER.md"
        old_paths = dream.SETTINGS.DURABLE_MEMORY_PATHS
        dream.SETTINGS.DURABLE_MEMORY_PATHS = ["memories/USER.md"]
        try:
            user.write_text("The human's name is Anton.", encoding="utf-8")
            self._write(self.BEFORE)
            self.assertEqual(self._check(), [])
            moved = [MEMORY[0], MEMORY[10], MEMORY[11]]
            self._write([e for e in self.BEFORE if e not in moved])
            user.write_text(dream.MEMORY_ENTRY_DELIMITER.join(["The human's name is Anton."] + moved),
                            encoding="utf-8")
            self.assertEqual(self._check(), [])
        finally:
            dream.SETTINGS.DURABLE_MEMORY_PATHS = old_paths


class ASplitEntryIsNotALoss(_Guard):
    """Finding 2: an entry split in two was a loss, since neither half holds most of its words."""

    COMBINED = [
        "Masha goes to ballet on Mondays at 17:30 and Ksenia picks her up. Anton pays 9000 baht rent on the 5th "
        "by transfer to the owner's Kasikorn account.",
        "Honda Forza needs a service every 6000 km, the last one was at 18400 km. The IT party is on the first "
        "Friday of the month, with an expert hour at 22:00.",
    ]
    SPLIT = [
        "Masha goes to ballet on Mondays at 17:30 and Ksenia picks her up.",
        "Anton pays 9000 baht rent on the 5th by transfer to the owner's Kasikorn account.",
        "Honda Forza needs a service every 6000 km, the last one was at 18400 km.",
        "The IT party is on the first Friday of the month, with an expert hour at 22:00.",
    ]
    REST = [MEMORY[i] for i in (0, 1, 3, 7)]

    def test_an_entry_split_in_two_is_not_a_loss(self):
        # 2 of 6 keys moved (33 %), and every word and number is still there
        self.assertEqual(self._night(self.COMBINED + self.REST, self.SPLIT + self.REST), [])

    def test_half_of_a_split_entry_gone_is_a_loss(self):
        after = [self.SPLIT[0], self.SPLIT[2]] + self.REST     # the rent and the IT party are nowhere now
        self.assertLoss(self._night(self.COMBINED + self.REST, after), 2)


class NumbersAreComparedAsNumbers(_Guard):
    """Finding 3: every run of digits had to come back as is — list numbers and the spelling of a number included."""

    REST = [MEMORY[i] for i in (0, 1, 3, 7)]

    def _reformatted(self, before, after):
        return self._night(before + self.REST, after + self.REST)    # 2 of 6 keys moved (33 %)

    def test_a_numbered_list_turned_into_bullets_is_not_a_loss(self):
        before = ["Schedule:\n1. Masha (daughter, 7) has ballet at 17:30\n2. Ksenia picks her up afterwards",
                  "Deploy checklist for hr-box:\n1. Run nginx -t\n2. Reload nginx\n3. Watch the logs for a minute"]
        after = ["Schedule:\n- Masha (daughter, 7) has ballet at 17:30\n- Ksenia picks her up afterwards",
                 "Deploy checklist for hr-box:\n- Run nginx -t\n- Reload nginx\n- Watch the logs for a minute"]
        self.assertEqual(self._reformatted(before, after), [])

    def test_thousands_written_with_and_without_a_separator_are_the_same_number(self):
        before = ["Rent for the Jomtien condo is 9,000 baht, due on the 5th to the owner's Kasikorn account.",
                  "Аренда кондо в Джомтьене — 18 000 бат в месяц, депозит 36 000 бат."]
        after = ["Jomtien condo rent: 9000 baht on the 5th, to the owner's Kasikorn account.",
                 "Кондо в Джомтьене: аренда 18000 бат в месяц, депозит 36000 бат."]
        self.assertEqual(self._reformatted(before, after), [])

    def test_a_date_written_with_the_month_in_words_is_the_same_date(self):
        before = ["Masha's school interview in Chiang Mai is on 2026-11-03 at 10:00, Ksenia goes with her.",
                  "Продление визы Антона — 15.01.2027, документы собирает Ксения."]
        after = ["Masha's school interview in Chiang Mai: 3 November 2026, 10:00; Ksenia goes with her.",
                 "Продление визы Антона: 15 января 2027 года, документы собирает Ксения."]
        self.assertEqual(self._reformatted(before, after), [])

    def test_a_date_moved_to_another_day_is_still_a_loss(self):
        before = ["Masha's school interview in Chiang Mai is on 2026-11-03 at 10:00, Ksenia goes with her.",
                  "Rent for the Jomtien condo is 9,000 baht, due on the 5th to the owner's Kasikorn account."]
        after = ["Masha's school interview in Chiang Mai: 4 November 2026, 10:00; Ksenia goes with her.",
                 "Rent for the Jomtien condo is 9,500 baht, due on the 5th to the owner's Kasikorn account."]
        self.assertLoss(self._reformatted(before, after), 2)


class AnEntryOfShortWords(_Guard):
    """Finding 4: an entry of no word stems (only short words) was lost whenever anything rewrote it."""

    SHORT = ["Cat is Dan. Pet is dog.", "Кот — Дан, пёс — Рекс."]
    REST = [MEMORY[i] for i in (0, 1, 3, 7)]

    def test_a_reworded_entry_of_short_words_is_not_a_loss(self):
        after = ["Cat named Dan. Pet is a dog.", "Кота зовут Дан, пёс у нас — Рекс."]
        self.assertEqual(self._night(self.SHORT + self.REST, after + self.REST), [])

    def test_a_deleted_entry_of_short_words_is_still_a_loss(self):
        self.assertLoss(self._night(self.SHORT + self.REST, self.REST), 2)

    def test_an_entry_of_short_words_replaced_by_another_is_a_loss(self):
        after = ["Car is red. Key is on top.", "Ключ — у Ани, код — 12."]
        self.assertLoss(self._night(self.SHORT + self.REST, after + self.REST), 2)


class ASnapshotSavedWithABom(_Guard):
    """Finding 5: the snapshot was read as plain UTF-8, so a BOM sent it aside as corrupt with the baseline in it."""

    def test_a_snapshot_with_a_bom_is_still_the_baseline(self):
        self._write(MEMORY)
        self.assertEqual(self._check(), [])
        # what PowerShell 5's Set-Content -Encoding UTF8 or a Windows editor leaves behind
        self.snap.write_bytes(b"\xef\xbb\xbf" + self.snap.read_bytes())
        self._write(MEMORY[:6])
        self.assertLoss(self._check(), 6)
        self.assertEqual(list(self.snap.parent.glob("*.corrupt-*")), [])

    def test_another_state_file_with_a_bom_is_read_too(self):
        path = self.home / "cache" / "dream-cooldown.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"abc": "2026-11-01"}).encode("utf-8"))
        self.assertEqual(dream._load_state_file(str(path)), {"abc": "2026-11-01"})


class AnOldSnapshotSaysWhatItChecked(_Guard):
    """Finding 6: against a snapshot of 3.8.3 (no signatures) the alert said the text was nowhere — unchecked."""

    def test_the_alert_does_not_claim_text_it_could_not_check_is_gone(self):
        self._write(MEMORY)
        sizes = {"entries": {dream._entry_key(e): len(e) for e in MEMORY},
                 "heads": {dream._entry_head_key(e): len(e) for e in MEMORY}}
        self.snap.write_text(json.dumps({"memories/MEMORY.md": sizes}), encoding="utf-8")   # 3.8.3's format
        self._write(MEMORY[:6])
        alerts = self._check()
        self.assertLoss(alerts, 6)
        message = alerts[0]["message"]
        self.assertNotIn("nowhere in memory", message)
        self.assertIn("older version", message)
        self.assertIn("Check whether that was intended.", message)


class TheFirstNightAfterTheUpdate(_Guard):
    """A snapshot written by 3.8.4 hashed every run of digits ("9,000" → 9 and 0): its rewrites are still told apart.

    Both rewrites keep the spelling of their numbers; read the new way ("9,000" → 9000) they would not match."""

    @staticmethod
    def _sig_384(entry):
        def h(s):
            return hashlib.md5(s.encode("utf-8")).hexdigest()[:8]
        return {"stems": sorted({h(s) for s in dream._stems(dream.sig_tokens(entry))}),
                "nums": sorted({h(n.lstrip("0") or "0") for n in re.findall(r"\d+", dream._fold(entry))}),
                "preview": re.sub(r"\s+", " ", entry).strip()[:60]}

    BEFORE = ["Rent for the Jomtien condo is 9,000 baht, due on the 5th to the owner's Kasikorn account.",
              "Аренда кондо в Джомтьене — 18 000 бат в месяц, депозит 36 000 бат."] + MEMORY[:4]

    def _snapshot_of_3_8_4(self):
        self._write(self.BEFORE)
        self.snap.write_text(json.dumps({"memories/MEMORY.md": {
            "entries": {dream._entry_key(e): len(e) for e in self.BEFORE},
            "heads": {dream._entry_head_key(e): len(e) for e in self.BEFORE},
            "sigs": {dream._entry_key(e): self._sig_384(e) for e in self.BEFORE}}}), encoding="utf-8")

    def test_a_rewrite_against_a_snapshot_of_3_8_4_is_not_a_loss(self):
        self._snapshot_of_3_8_4()
        self._write(["Jomtien condo rent: 9,000 baht on the 5th, to the owner's Kasikorn account.",
                     "Кондо в Джомтьене: 18 000 бат в месяц, депозит 36 000 бат."] + MEMORY[:4])
        self.assertEqual(self._check(), [])

    def test_a_loss_against_a_snapshot_of_3_8_4_is_still_a_loss(self):
        self._snapshot_of_3_8_4()
        self._write(MEMORY[:4])
        alerts = self._check()
        self.assertLoss(alerts, 2)
        self.assertIn("nowhere in memory", alerts[0]["message"])     # 3.8.4's signatures did say what was there


class ARealLossIsStillCaught(_Guard):
    """Guards: what the fixes above must not let through."""

    def test_the_file_emptied(self):
        self.assertLoss(self._night(MEMORY, []), len(MEMORY))

    def test_the_file_gone(self):
        self._write(MEMORY)
        self.assertEqual(self._check(), [])
        self.mem.unlink()
        self.assertLoss(self._check(), len(MEMORY))

    def test_short_entries_deleted_among_many(self):
        short = ["Bob: tea, no ice.", "Cat is Dan. Pet is dog.", "Wi-Fi: 5 GHz, hall.", "Car: red, key on top."]
        before = short + MEMORY[:7]
        self.assertLoss(self._night(before, short[:1] + MEMORY[:7]), 3)    # 3 of 11 (27 %), short ones: 6 % of chars

    def test_entries_deleted_while_others_were_rewritten_and_added(self):
        after = list(MEMORY)
        after[1] = ("hr-box: Debian 12. Run nginx -t before every deploy; restart hermes-* units only "
                    "in a session about Hermes.")
        after = [e for i, e in enumerate(after) if i not in (8, 9, 11, 4)]
        after.append("Masha now also has piano lessons on Wednesdays; Ksenia found the teacher through LINE.")
        alerts = self._night(MEMORY, after)    # 4 of 12 gone, 1 rewritten, 1 new entry with shared words
        self.assertLoss(alerts, 4)
        self.assertEqual(alerts[0]["rewritten"], 1)


if __name__ == "__main__":
    unittest.main()
