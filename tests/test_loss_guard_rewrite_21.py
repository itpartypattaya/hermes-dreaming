"""#21: the loss guard took the nightly pass's own work for a loss.

The pass is told to compress and merge durable entries to keep MEMORY.md under its char limit. Every rewritten entry
gets a new key (md5 of its first 200 normalized characters), so three nights in a row "compressed 4 entries, merged 1"
came back as "5 of 16 entries are gone — check whether that was intended", and the agent asked the human about a
deletion nobody made.

Requirement: an entry whose text is still in memory — reworded, compressed, folded into another entry — is not a
loss and raises nothing. An entry whose text is nowhere is still a loss: it is counted, named, and the human is
asked. A snapshot written by an older version (no signatures) still works.
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_dream import dream  # noqa: E402

# A MEMORY.md the way the agent keeps it: one fact per entry, names repeat across entries.
MEMORY = [
    "Leo prefers short answers in Russian; code, commands and file names stay in English.",
    "Server hr-box runs Debian 12. Before every deploy run nginx -t, and never restart the hermes-* units "
    "outside a session about Hermes itself.",
    "Masha (daughter, 7) goes to ballet on Mondays and Thursdays at 17:30; Nina picks her up afterwards.",
    "The family plans to move to Vila Real in spring 2027; the school search for Masha starts in January.",
    "Weekly grocery order goes out on Saturdays from Lidl; Leo pays and Nina writes the shopping list.",
    "Vespa GTS needs a service every 6000 km; the last service was at 18400 km in October.",
    "The book club is on the first Friday of the month: guest talk at 22:00, then a jazz set until 01:00.",
    "Nina runs HireLab, a recruiting agency for sales specialists; she decides on candidates, Leo on marketing.",
    "Masha's doctor is Dr. Costa at Riverside Hospital Lisbon; appointments are booked through WhatsApp.",
    "Rent for the Alfama condo is due on the 5th, by transfer to the owner's Millennium account.",
    "Leo dislikes long bulleted lists in articles and prefers a narrative structure with a clear storyline.",
    "Leo created the Telegram chat for Costa Nova residents and is its main moderator.",
]


class LossGuardRewriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "memories").mkdir()
        self.mem = self.home / "memories" / "MEMORY.md"
        self.user = self.home / "memories" / "USER.md"
        self.snap = self.home / "cache" / "dream-snapshot.json"
        self.snap.parent.mkdir()
        self._old_home = dream.HOME
        dream.HOME = str(self.home)

    def tearDown(self):
        dream.HOME = self._old_home
        self.tmp.cleanup()

    def _write(self, entries, path=None):
        (path or self.mem).write_text(dream.MEMORY_ENTRY_DELIMITER.join(entries), encoding="utf-8")

    def _check(self):
        return [a for a in dream.check_memory_loss(str(self.mem), str(self.snap))
                if a["kind"] in ("memory_loss", "entry_truncated")]

    def _night(self, before, after):
        self._write(before)
        self.assertEqual(self._check(), [])
        self._write(after)
        return self._check()

    # --- the nightly pass's own work: no alert ------------------------------------------------------------------

    def test_reworded_entries_are_not_a_loss(self):
        after = list(MEMORY)
        after[1] = ("hr-box: Debian 12. Run nginx -t before every deploy; restart hermes-* units only "
                    "in a session about Hermes.")
        after[2] = "Masha (7, daughter): ballet on Mondays and Thursdays at 17:30, Nina picks her up."
        after[3] = "Spring 2027: the family moves to Vila Real; Masha's school search starts in January."
        after[5] = "Vespa GTS: service every 6000 km, last one in October at 18400 km."
        after[7] = "HireLab (Nina's recruiting agency, sales specialists): candidates are her call, marketing is Leo's."
        # 5 of 12 keys moved (42 %) — over the 25 % threshold
        self.assertEqual(self._night(MEMORY, after), [])

    def test_entries_merged_into_one_are_not_a_loss(self):
        merged = [
            "Leo: short answers in Russian (code, commands, file names in English); in articles a narrative "
            "structure with a clear storyline, no long bulleted lists.",
            "Masha (daughter, 7): ballet on Mondays and Thursdays at 17:30, Nina picks her up; doctor — "
            "Dr. Costa, Riverside Hospital Lisbon, booked through WhatsApp.",
            "Household: grocery order from Lidl on Saturdays (Leo pays, Nina writes the list); rent for the "
            "Alfama condo due on the 5th by transfer to the owner's Millennium account.",
        ]
        after = merged + [MEMORY[i] for i in (1, 3, 5, 6, 7, 11)]
        # 6 entries folded into 3: 6 of 12 keys gone (50 %)
        self.assertEqual(self._night(MEMORY, after), [])

    def test_compressed_entries_are_not_a_loss(self):
        before = [
            "Masha (our daughter, she is 7 years old) goes to ballet classes twice a week, on Mondays and on "
            "Thursdays, the lessons start at 17:30 and Nina usually picks her up afterwards and brings her home.",
            "The family is planning to move to Vila Real, most likely in the spring of 2027; because of that the "
            "search for a school for Masha is going to start in January, Nina is collecting the options.",
            "The Vespa GTS has to be serviced every 6000 km according to the manual; the last service was done in "
            "October at 18400 km at the dealer in Lisbon, the next one is due around 24400 km.",
        ] + MEMORY[6:]
        after = [
            "Masha (daughter, 7): ballet on Mondays and Thursdays at 17:30, Nina picks her up and brings her home.",
            "Move to Vila Real likely in spring 2027; school search for Masha starts in January, Nina collects "
            "options.",
            "Vespa GTS: service every 6000 km per the manual; last in October at 18400 km at the Lisbon dealer, "
            "next due ~24400 km.",
        ] + MEMORY[6:]
        # 3 of 9 entries rewritten, 47 % of the characters under the old keys
        self.assertEqual(self._night(before, after), [])

    def test_an_entry_moved_to_user_md_is_not_a_loss(self):
        old_paths = dream.SETTINGS.DURABLE_MEMORY_PATHS
        dream.SETTINGS.DURABLE_MEMORY_PATHS = ["memories/USER.md"]
        try:
            self._write(["The human's name is Leo."], self.user)
            moved = [MEMORY[0], MEMORY[10], MEMORY[11], MEMORY[4]]
            after = [e for e in MEMORY if e not in moved]
            self._write(MEMORY)
            self.assertEqual(self._check(), [])
            self._write(after)
            self._write(["The human's name is Leo."] + moved, self.user)
            self.assertEqual(self._check(), [])
        finally:
            dream.SETTINGS.DURABLE_MEMORY_PATHS = old_paths

    # --- a real loss: still caught, and named -------------------------------------------------------------------

    def test_three_short_entries_among_many_are_still_a_loss(self):
        before = [e for i, e in enumerate(MEMORY) if i not in (6, 10)]
        after = [e for i, e in enumerate(MEMORY) if i not in (6, 10, 8, 9, 11)]
        alerts = self._night(before, after)    # 3 of 10: over the 25 % threshold
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertEqual(alerts[0]["lost"], 3)

    def test_one_big_entry_is_still_a_loss(self):
        big = ("House rules for the Alfama condo: the cleaner comes on Tuesdays and Fridays, the key is with the "
               "building guard, the air conditioners are washed every three months by the company the owner chose, "
               "the water filter cartridge is replaced in March and September, garbage goes out in the evening "
               "only, guests register at the front desk with a passport, the pool closes at 21:00, the gym card "
               "is in the drawer by the door, and the internet contract is with True under the owner's name.")
        before = [big] + MEMORY[:6]
        alerts = self._night(before, MEMORY[:6])
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertGreater(alerts[0]["lost_chars"], 400)

    def test_an_entry_that_differs_only_by_its_numbers_is_a_loss(self):
        codes = [f"Door code for building {b}: {c}." for b, c in (("A", 4417), ("B", 9032), ("C", 2785),
                                                                  ("D", 6150))]
        before = codes + MEMORY[:4]
        after = codes[:1] + MEMORY[:4]
        alerts = self._night(before, after)
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertEqual(alerts[0]["lost"], 3)

    def test_rewrites_and_a_real_loss_are_told_apart(self):
        after = list(MEMORY)
        after[1] = ("hr-box: Debian 12. Run nginx -t before every deploy; restart hermes-* units only "
                    "in a session about Hermes.")
        after[3] = "Spring 2027: the family moves to Vila Real; Masha's school search starts in January."
        after[5] = "Vespa GTS: service every 6000 km, last one in October at 18400 km."
        before = [e for i, e in enumerate(MEMORY) if i not in (6, 10)]
        after = [e for i, e in enumerate(after) if i not in (6, 10, 8, 9, 11)]
        alerts = self._night(before, after)    # 3 of 10 gone, 3 rewritten
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        alert = alerts[0]
        self.assertEqual(alert["lost"], 3)
        self.assertEqual(alert["rewritten"], 3)
        message = alert["message"]
        # the lost entries are named, so the human knows what to restore
        for gone in ("Masha's doctor is Dr. Costa", "Rent for the Alfama condo", "Leo created the Telegram"):
            self.assertIn(gone, message)
        # the rewritten ones are not presented as something to ask about
        self.assertNotIn("Server hr-box", message)
        self.assertIn("rewritten or merged", message)
        self.assertLess(message.index("Check whether"), message.index("rewritten or merged"))

    def test_a_lost_entry_holding_a_secret_is_counted_but_not_quoted(self):
        secret = "Router admin password: Tr0ub4dor&3xyz, the router is in the hall closet."
        before = [secret, MEMORY[8]] + MEMORY[:4]
        alerts = self._night(before, MEMORY[:4])
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertNotIn("Tr0ub4dor", alerts[0]["message"])
        self.assertNotIn("Tr0ub4dor", self.snap.read_text(encoding="utf-8"))

    def test_the_snapshot_keeps_no_memory_text_beyond_a_short_preview(self):
        self._write(MEMORY)
        self._check()
        raw = self.snap.read_text(encoding="utf-8")
        self.assertNotIn("Millennium", raw)       # past the preview of its entry
        self.assertNotIn("kasikorn", raw)       # nor as a stem

    # --- an older snapshot ----------------------------------------------------------------------------------------

    def test_a_snapshot_without_signatures_still_works(self):
        self._write(MEMORY)
        sizes = {"entries": {dream._entry_key(e): len(e) for e in MEMORY},
                 "heads": {dream._entry_head_key(e): len(e) for e in MEMORY}}
        self.snap.write_text(json.dumps({"memories/MEMORY.md": sizes}), encoding="utf-8")   # 3.8.3's format
        self._write(MEMORY[:6])
        alerts = self._check()
        self.assertEqual([a["kind"] for a in alerts], ["memory_loss"], alerts)
        self.assertEqual(alerts[0]["lost"], 6)
        # the snapshot is rewritten with signatures, so the next night's rewrite is told apart
        after = list(MEMORY[:6])
        after[1] = ("hr-box: Debian 12. Run nginx -t before every deploy; restart hermes-* units only "
                    "in a session about Hermes.")
        after[2] = "Masha (7, daughter): ballet on Mondays and Thursdays at 17:30, Nina picks her up."
        self._write(after)
        self.assertEqual(self._check(), [])


if __name__ == "__main__":
    unittest.main()
