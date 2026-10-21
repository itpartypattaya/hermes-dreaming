"""3.1.0: Russian, English and Chinese after the Antigravity review of 3.0.1, and the Codex review of 3.1.0.

A negated message is no corroboration of the same fact; "does not eat" meets "eats" and "不喜欢" meets "喜欢" as a
conflict; the fallback screen knows Chinese password markers; a state.db that is no database stops the pass instead
of a silent night."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_dream import dream  # noqa: E402  (same directory)


def _message(text):
    return {"tokens": dream.sig_tokens(text), "content": text, "day": "2026-10-20", "ts": 1_792_500_000}


class NegationStemTests(unittest.TestCase):
    def test_a_third_person_or_plural_s_is_the_same_word_where_a_negation_is_compared(self):
        for a, b in (("likes", "like"), ("eats", "eat"), ("games", "game"), ("plays", "play")):
            self.assertEqual(dream._neg_stem(a), dream._neg_stem(b), (a, b))

    def test_the_general_stem_is_unchanged(self):
        """Codex review of 3.1.0: in the general stem "fiets" / "fietsen" parted and "Dachs" met "Dach"."""
        self.assertEqual(dream._stem("fiets"), dream._stem("fietsen"))
        self.assertEqual(dream._stem("adres"), dream._stem("adresy"))
        self.assertNotEqual(dream._stem("dachs"), dream._stem("dach"))
        for word in ("class", "bus", "paris"):
            self.assertEqual(dream._neg_stem(word), dream._stem(word))


class ConflictTests(unittest.TestCase):
    def test_does_not_eat_against_eats_is_a_conflict(self):
        conflicts = dream.find_conflicts("Alice does not eat red meat at family dinners",
                                         "- Alice eats red meat at family dinners")
        self.assertEqual([c["fact_negations"] for c in conflicts], [["not eat"]])
        self.assertIsNotNone(dream.polarity_flip("Alice does not like spicy food", "- Alice likes spicy food"))

    def test_chinese_negation(self):
        for fact, entry in (("李雷不喜欢吃四川麻辣火锅", "- 李雷喜欢吃四川麻辣火锅"), ("李雷没有车", "- 李雷有车"),
                            ("李雷不同意这个方案", "- 李雷同意这个方案"), ("李雷对花生不过敏", "- 李雷对花生过敏"),
                            ("李雷无车，喜欢吃四川麻辣火锅", "- 李雷有车，喜欢吃四川麻辣火锅"),
                            ("李雷不喜欢苹果但喜欢香蕉", "- 李雷喜欢苹果但不喜欢香蕉")):
            with self.subTest(fact):
                self.assertIsNotNone(dream.polarity_flip(fact, entry))

    def test_chinese_said_the_same_way_is_no_conflict(self):
        for fact, entry in (("李雷不喜欢吃辣", "- 李雷不喜欢吃辣"), ("李雷住在无锡市", "- 李雷住在无锡市，喜欢吃辣"),
                            ("护照被没收了", "- 护照被没收了"), ("李雷喜欢吃辣", "- 李雷喜欢吃辣"),
                            ("李雷住在无锡市", "- 李雷住在无锡，喜欢吃辣")):
            with self.subTest(fact):
                self.assertIsNone(dream.polarity_flip(fact, entry))


class SettingsCacheTests(unittest.TestCase):
    """Second Codex pass: the cached stems followed an object id, which the next Settings may reuse."""

    def setUp(self):
        self._saved = dream.SETTINGS

    def tearDown(self):
        dream.SETTINGS = self._saved

    def test_new_stopwords_are_seen_after_configure(self):
        fact, chunk = "Alice likes spicy Thai food", "- Alice does not like spicy Thai food. Bananas are yellow."
        for _ in range(2):
            dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, {"extra_stopwords": []}))
            self.assertIsNotNone(dream.polarity_flip(fact, chunk))
        dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG,
                                          {"extra_stopwords": ["alice", "likes", "spicy", "thai", "food"]}))
        self.assertIsNone(dream.polarity_flip(fact, chunk))


class NegatedMessageTests(unittest.TestCase):
    CASES = [
        ("Михаил любит острое азиатское блюдо", "Михаил не любит острое азиатское блюдо",
         "Михаил снова заказал острое азиатское блюдо, любит его"),
        ("Alice likes spicy Thai food", "Alice does not like spicy Thai food", "Alice likes spicy Thai food a lot"),
        ("李雷喜欢吃四川麻辣火锅", "李雷不喜欢吃四川麻辣火锅", "李雷昨天又说喜欢吃四川麻辣火锅"),
    ]

    def test_a_negated_message_does_not_corroborate_the_same_fact(self):
        for fact, negated, plain in self.CASES:
            with self.subTest(fact):
                tokens = dream.sig_tokens(fact)
                self.assertEqual(dream.corroborate(tokens, [_message(negated)])[0], 1)  # by words alone it matches
                self.assertEqual(dream.corroborate(tokens, [_message(negated)], fact)[0], 0)
                self.assertEqual(dream.corroborate(tokens, [_message(plain)], fact)[0], 1)
                self.assertEqual(dream.corroborate_with_evidence(tokens, [_message(negated)], fact)[0], 0)

    def test_a_negation_of_something_else_still_corroborates(self):
        """Codex review of 3.1.0: the negated thing must be one the fact speaks of."""
        for fact, message in (("Alice likes spicy Thai food", "Alice likes spicy Thai food but does not like Italian food"),
                              ("李雷喜欢吃四川麻辣火锅", "李雷喜欢吃四川麻辣火锅。小王不喜欢香蕉")):
            with self.subTest(fact):
                self.assertEqual(dream.corroborate(dream.sig_tokens(fact), [_message(message)], fact)[0], 1)

    def test_the_pass_scores_with_the_fact_text(self):
        fact, negated, _ = self.CASES[0]
        _, _, meta = dream.signals_for_fact({"fact_id": 1, "content": fact, "trust_score": 0.5,
                                             "created_at": "2026-10-01 10:00:00",
                                             "updated_at": "2026-10-01 10:00:00"}, [_message(negated)])
        self.assertEqual(meta["mentions"], 0)

    def test_no_negation_on_either_side_is_checked_cheaply(self):
        self.assertFalse(dream._negates_anything("Alice likes spicy Thai food"))
        self.assertTrue(dream._negates_anything("Alice doesn't like spicy Thai food"))
        self.assertTrue(dream._negates_anything("李雷不喜欢吃辣"))


class ChinesePasswordFallbackTests(unittest.TestCase):
    def test_chinese_markers_are_caught_without_the_provider_screen(self):
        for text in ("密码: Secret123456", "wifi密码：Abc12345!", "我的密码是 Abc12345", "我的密码是 猫咪12345",
                     "私钥=0xdeadbeef1234"):
            self.assertTrue(dream._password_marker_unsafe(text), text)

    def test_a_sentence_about_a_password_is_not_one(self):
        for text in ("密码是什么我忘了", "我们昨天改了wifi密码", "请记得定期更换密码", "密码是我的生日",
                     "密码管理器是 Bitwarden", "密码算法是 SHA256", "私钥格式是 PKCS8"):
            self.assertFalse(dream._password_marker_unsafe(text), text)


class BrokenStateDbTests(unittest.TestCase):
    def test_a_file_that_is_no_database_stops_the_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.db")
            with open(path, "wb") as f:
                f.write(os.urandom(8192))
            with self.assertRaises(sqlite3.DatabaseError):
                dream.load_messages(path, 60)

    def test_a_missing_one_is_still_no_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dream.load_messages(os.path.join(tmp, "state.db"), 60), [])


if __name__ == "__main__":
    unittest.main()
