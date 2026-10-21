"""Per-chat / per-user fact stores (2.6.0, `chat_stores`): holographic `scope: user / chat / thread` keeps facts in
one database per chat; the dream lists them (counts only) and promotes facts of the listed chats only, each
corroborated by its own chat.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from test_dream import DreamFixture, dream  # noqa: E402

CHAT_A = "-100777"   # promoted, not in the trusted list
CHAT_B = "-100888"   # not promoted
KEY_A = "telegram--100777"
KEY_B = "telegram--100888"


def _stamp(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


class ChatStoreBase(DreamFixture):
    SCOPE = "chat"

    def setUp(self):
        super().setUp()
        self._cfg = dream.SETTINGS.CONFIG
        (self.home / "config.yaml").write_text(
            f"memory:\n  provider: holographic\nplugins:\n  hermes-memory-store:\n    scope: {self.SCOPE}\n",
            encoding="utf-8")
        self.configure(read=True, promote=[f"telegram:{CHAT_A}"])

    def tearDown(self):
        dream.configure(self._cfg)
        super().tearDown()

    def configure(self, read, promote, **extra):
        cfg = dict(self._cfg, fact_source="holographic", chat_stores={"read": read, "promote_from": promote}, **extra)
        dream.configure(cfg)
        dream.SETTINGS.TRUSTED_CHAT_IDS = {"-100111"}
        dream.SETTINGS.EXCLUDED_THREADS = {}

    def chat_db(self, key, facts, forgotten=()):
        folder = self.home / "holographic-chats"
        folder.mkdir(exist_ok=True)
        con = sqlite3.connect(folder / f"{key}.db")
        con.execute(
            "create table if not exists facts (fact_id integer primary key autoincrement,"
            " content text, category text default 'general', tags text default '',"
            " trust_score real default 0.5, retrieval_count integer default 0,"
            " helpful_count integer default 0, created_at timestamp, updated_at timestamp)")
        con.execute("create table if not exists forgotten (fact_id integer primary key, forgotten_at text)")
        for text in facts:
            con.execute("insert into facts (content, trust_score, created_at, updated_at) values (?,?,?,?)",
                        (text, 0.9, _stamp(2), _stamp(2)))
        for fid in forgotten:
            con.execute("insert into forgotten values (?, ?)", (fid, _stamp(0)))
        con.commit()
        con.close()

    def item(self, result, fact_id):
        for section in ("promotions", "new_facts", "conflicts", "fact_decays", "ephemeral_events"):
            for it in result.get(section) or []:
                if str(it.get("fact_id")) == fact_id:
                    return it
        return None

class ChatStoreTests(ChatStoreBase):
    # --- keys -------------------------------------------------------------------------------------------------
    def test_store_key_is_the_plugin_file_name(self):
        self.assertEqual(dream.store_key("telegram", CHAT_A), KEY_A)
        self.assertEqual(dream.store_key("telegram", CHAT_A, "42"), KEY_A + "-t42")
        odd = dream.store_key("discord", "room one")
        self.assertTrue(odd.startswith("discord-room_one-") and len(odd) == len("discord-room_one-") + 10)
        self.assertEqual(dream._promote_key(f"telegram:{CHAT_A}"), KEY_A)
        self.assertEqual(dream._promote_key(f"telegram:{CHAT_A}:42"), KEY_A + "-t42")
        self.assertEqual(dream._promote_key(KEY_A + ".db"), KEY_A)

    # --- overview -------------------------------------------------------------------------------------------
    def test_overview_counts_without_text_or_ids(self):
        self.chat_db(KEY_A, ["Секретный рецепт пирога хранится у бабушки Зои"])
        self.chat_db(KEY_B, ["Пароль от домофона у соседей сверху 4512", "Собака соседей лает по ночам"])
        self.configure(read=True, promote=[])
        result = self.run_dream()
        cs = result["chat_stores"]
        self.assertEqual([(r["store"], r["facts"], r["promote"]) for r in cs["stores"]],
                         [(KEY_A, 1, False), (KEY_B, 2, False)])
        dumped = json.dumps(result, ensure_ascii=False)
        for text in ("рецепт", "домофона", "Собака", "#1"):
            self.assertNotIn(text, dumped.replace("[#", ""))
        self.assertIn(f"Chat stores ({self.SCOPE}): 2 database(s), 3 fact(s)", result["diary"])

    def test_missing_promoted_store_is_named(self):
        self.configure(read=False, promote=["telegram:-100999"])
        self.chat_db(KEY_A, ["Кот Барсик ест только влажный корм"])
        cs = self.run_dream()["chat_stores"]
        self.assertEqual(cs["stores"], [])
        self.assertEqual(cs["missing"], ["telegram--100999"])

    # --- promotion ---------------------------------------------------------------------------------------------
    def test_promoted_fact_is_corroborated_by_its_own_chat_only(self):
        text = "Кот Барсик ест только влажный корм марки Лапка"
        self.chat_db(KEY_A, [text])
        self.chat_db(KEY_B, [text])  # the same words in a chat that is not promoted: never read
        for d in (1, 2, 3):
            self.add_message("Барсик опять не ест сухой корм, только Лапка", days_ago=d, chat_id=CHAT_B)
        item = self.item(self.run_dream(), f"{KEY_A}#1")
        self.assertIsNotNone(item)
        self.assertEqual(item["store"], KEY_A)
        self.assertEqual(item["mentions"], 0)          # chat B speaks about it — that is not chat A
        for d in (1, 2, 3):
            self.add_message("Барсик съел влажный корм Лапка, сухой не трогает", days_ago=d, chat_id=CHAT_A)
        item = self.item(self.run_dream(), f"{KEY_A}#1")
        self.assertEqual((item["mentions"], item["ref_days"]), (3, 3))
        self.assertIsNone(self.item(self.run_dream(), f"{KEY_B}#1"))

    def test_promoted_chat_messages_corroborate_nothing_else(self):
        self.add_fact("Кот Барсик ест только влажный корм марки Лапка")
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        for d in (1, 2, 3):
            self.add_message("Барсик съел влажный корм Лапка", days_ago=d, chat_id=CHAT_A)
        result = self.run_dream()
        shared = self.item(result, "1")
        self.assertEqual(shared["mentions"], 0)
        self.assertEqual(result["stats"]["messages_window"], 0)

    def test_chat_facts_get_no_store_calls(self):
        self.configure(read=False, promote=[f"telegram:{CHAT_A}"], keywords={"enabled": True, "cap": 10})
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        self.add_fact("Самокат хранится на балконе")
        result = self.run_dream()
        ids = {str(i["fact_id"]) for i in result.get("keywords_needed") or []}
        self.assertIn("1", ids)
        self.assertNotIn(f"{KEY_A}#1", ids)

    def test_explain_finds_a_chat_fact(self):
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        self.assertEqual(dream.explain(f"{KEY_A}#1", str(self.mem_md), 60), 0)

    # --- forgetting -------------------------------------------------------------------------------------------
    def test_a_fact_forgotten_in_its_chat_is_swept(self):
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        result = self.run_dream()
        self.assertIsNotNone(self.item(result, f"{KEY_A}#1"))
        con = sqlite3.connect(self.home / "holographic-chats" / f"{KEY_A}.db")
        con.execute("insert into forgotten values (1, ?)", (_stamp(0),))
        con.commit()
        con.close()
        self.assertIn(f"{KEY_A}#1", {str(i) for i in dream.load_forgotten()})
        self.assertGreaterEqual(dream.scrub_result(result), 1)
        self.assertIsNone(self.item(result, f"{KEY_A}#1"))

    def test_composite_tags_in_the_diary_are_scrubbed(self):
        text = f"## Сон 2026-10-13\n- (0.7, mentions 3/3d) Велосипед хранится в подвале [#{KEY_A}#1]\n- другое [#2]\n"
        cleaned, removed = dream.scrub_diary_text(text, {f"{KEY_A}#1"}, {})
        self.assertEqual(removed, 1)
        self.assertNotIn("Велосипед", cleaned)
        self.assertIn("[#2]", cleaned)


class ChatStoreReviewTests(ChatStoreBase):
    """The external review of the 2.6.0 draft (Codex, 2026-10-13), one test per finding."""

    def _sweep_state(self):
        return json.loads((self.home / "cache" / "dream-forget-sweep.json").read_text(encoding="utf-8"))

    def test_a_store_leaving_the_list_is_still_swept(self):
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        dream.forget_sweep()                                 # remembers the promoted store
        self.assertIn(KEY_A, self._sweep_state()["stores"])
        payload = self.home / "cache" / "dream.json"
        payload.write_text(json.dumps({"promotions": [{"fact_id": f"{KEY_A}#1", "content": "x"}]}),
                           encoding="utf-8")
        self.configure(read=False, promote=[])                # the chat leaves the list…
        con = sqlite3.connect(self.home / "holographic-chats" / f"{KEY_A}.db")
        con.execute("insert into forgotten values (1, ?)", (_stamp(0),))   # …and its fact is forgotten
        con.commit()
        con.close()
        summary = dream.forget_sweep()
        self.assertEqual(summary["payload"], 1)
        self.assertEqual(json.loads(payload.read_text(encoding="utf-8"))["promotions"], [])

    def test_one_unreadable_store_does_not_stop_the_others(self):
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"], forgotten=[7])
        self.configure(read=False, promote=[f"telegram:{CHAT_A}", f"telegram:{CHAT_B}"])
        (self.home / "holographic-chats" / f"{KEY_B}.db").write_bytes(b"not a database at all" * 100)
        errors = []
        self.assertIn(f"{KEY_A}#7", dream.load_forgotten(errors=errors))
        self.assertTrue(errors and KEY_B in errors[0])
        self.assertTrue(dream.forget_sweep()["errors"])      # the gate fails and retries

    def test_message_keys_strip_ids_like_the_plugin(self):
        self.assertEqual(dream._message_store("telegram", " -100777 ", None, None, "chat"), KEY_A)
        self.assertEqual(dream._message_store("telegram", "-100777", " 42 ", None, "thread"), KEY_A + "-t42")
        self.assertEqual(dream._message_store("telegram", "-100777", "42", None, "chat"), KEY_A)
        self.assertEqual(dream._message_store("telegram", None, None, " 7 ", "user"), "telegram-7")
        self.assertIsNone(dream._message_store("telegram", "  ", None, None, "chat"))

    def test_forgotten_twin_leaves_duplicates(self):
        payload = {"promotions": [{"fact_id": 1, "duplicates": [f"{KEY_A}#1", 5]}]}
        self.assertEqual(dream._drop_fact_items(payload, {f"{KEY_A}#1"}), 1)
        self.assertEqual(payload["promotions"][0]["duplicates"], [5])

    def test_chat_facts_are_indexed_without_a_shared_store(self):
        (self.home / "memory_store.db").unlink()
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        dream.forget_sweep()
        index = json.loads((self.home / "cache" / "dream-diary-index.json").read_text(encoding="utf-8"))
        self.assertIn(f"{KEY_A}#1", index)

    def test_reject_finds_a_chat_fact_with_a_broken_shared_store(self):
        from test_dream import reject
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        (self.home / "memory_store.db").write_bytes(b"broken" * 100)
        saved = (reject._DREAM, reject.HOME)
        reject._DREAM, reject.HOME = dream, str(self.home)
        try:
            self.assertEqual(reject.fetch_content(f"{KEY_A}#1"), "Велосипед хранится в подвале")
        finally:
            reject._DREAM, reject.HOME = saved

    def test_overview_counts_without_loading_texts(self):
        self.chat_db(KEY_A, ["Велосипед хранится в подвале"])
        self.chat_db(KEY_B, ["Самокат на балконе", "Лыжи в кладовке"])
        self.configure(read=True, promote=[])
        saved = dream._load_holographic

        def boom(path):
            raise AssertionError(f"the overview read texts of {path}")
        dream._load_holographic = boom
        try:
            cs = dream.chat_stores_overview(14)
        finally:
            dream._load_holographic = saved
        self.assertEqual([(r["store"], r["facts"], r["new"]) for r in cs["stores"]], [(KEY_A, 1, 1), (KEY_B, 2, 2)])

    def test_output_unchanged_when_the_feature_is_off(self):
        (self.home / "config.yaml").write_text("memory:\n  provider: holographic\n", encoding="utf-8")
        dream.configure(dict(self._cfg, fact_source="holographic"))
        result = self.run_dream()
        self.assertNotIn("chat_stores", result)
        self.assertNotIn("fact_store_note", result)


class ThreadStoreTests(ChatStoreTests):
    SCOPE = "thread"

    def test_thread_is_its_own_store(self):
        key = KEY_A + "-t42"
        self.configure(read=False, promote=[f"telegram:{CHAT_A}:42"])
        self.chat_db(key, ["Кот Барсик ест только влажный корм марки Лапка"])
        for d in (1, 2, 3):
            self.add_message("Барсик съел влажный корм Лапка", days_ago=d, chat_id=CHAT_A, thread_id="43")
        self.assertEqual(self.item(self.run_dream(), f"{key}#1")["mentions"], 0)
        for d in (1, 2, 3):
            self.add_message("Барсик съел влажный корм Лапка", days_ago=d, chat_id=CHAT_A, thread_id="42")
        self.assertEqual(self.item(self.run_dream(), f"{key}#1")["mentions"], 3)


if __name__ == "__main__":
    import unittest
    unittest.main()
