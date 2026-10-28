"""3.7.1: one nightly pass reads each message once, however much text the window holds (issue #3).

A busy desktop's 60-day window held 1 562 human messages / 17.6 MiB. 3.6.1 kept a message's numbers in a cache of
4 MiB of text that stopped taking new ones when full, so every message past it was parsed again for every fact, and
the negation check split every message into sentences again for every fact: the pass outran the gate's 600 s. The
requirement: within a pass a message's numbers, sentences and the rest are read once, and every decision stays the
one the rules for two texts (polarity_flip, numbers_changed, contradicts) give.

All people, places and numbers below are made up."""
import contextlib
import gc
import os
import random
import string
import sys
import tracemalloc
import unittest
import weakref
from collections import Counter
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from test_dream import dream  # noqa: E402  (same directory)

DAY = "2026-10-20"
NIGHT = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)  # the night's clock: scores read the time


def _message(text, n=0, day=DAY):
    return {"content": text, "tokens": dream.sig_tokens(text), "day": day, "ts": 1_792_500_000 + n,
            "session_id": "s1", "key": f"m{n}"}


class _CountingSplitter:
    """The sentence pattern, counting the texts it is asked to split (by split or finditer); everything else as is."""

    def __init__(self, pattern):
        self.pattern, self.texts = pattern, Counter()

    def split(self, text, *args, **kwargs):
        self.texts[text] += 1
        return self.pattern.split(text, *args, **kwargs)

    def finditer(self, text, *args, **kwargs):
        self.texts[text] += 1
        return self.pattern.finditer(text, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.pattern, name)


def _pasted_table(rows, seed):
    """A pasted table of wide padded columns, as command output arrives in a chat: many lines, few words."""
    r = random.Random(seed)
    cells = ["статус", "очередь", "задание", "сервер", "пакет", "журнал", "status", "queue", "worker", "backup"]
    return "".join(f"| {r.choice(cells):<130}| {r.choice(cells):<130}| {r.choice(cells):<130}|\n"
                   for _ in range(rows))


class ReadOncePerPassTests(unittest.TestCase):
    FACTS = [{"fact_id": i, "content": f"Марина не ест арахис, тренировка группы {i} по средам в 18:00",
              "trust_score": 0.6} for i in range(1, 5)]

    def rules(self):
        return dream.rules_context(f["content"] for f in self.FACTS)

    def _window(self):
        # Every message mentions every fact (Марина, арахис, тренировка, группа, среда), on one day: the evidence
        # sample of each fact is the first, short message. The pasted tables make the window larger than 4 MiB.
        messages = [_message("Марина спросила про арахис для тренировки группы по средам", 0),
                    _message('[Replying to: "тренировка группы по средам"] Марина берёт арахис на тренировку', 1)]
        for n in range(2, 8):
            messages.append(_message("Марина прислала таблицу про арахис и тренировки группы по средам.\n"
                                     + _pasted_table(1_830, n), n))
        return messages

    def test_each_message_is_parsed_once_however_large_the_window(self):
        messages = self._window()
        self.assertGreater(sum(len(m["content"]) for m in messages), 4 * 1024 * 1024)
        splitter = _CountingSplitter(dream._SENTENCE_SPLIT_RE)
        with mock.patch.object(dream, "_number_kinds", wraps=dream._number_kinds) as numbers, \
                mock.patch.object(dream, "_SENTENCE_SPLIT_RE", splitter):
            scored = dream._score_facts(self.FACTS, messages, None, [], "", "", now=NIGHT, rules=self.rules())
        # Every message mentions every fact: the person spoke of them, no number of theirs changed, nothing negated.
        self.assertEqual([s["meta"]["mentions"] for s in scored], [len(messages)] * len(self.FACTS))
        parsed = Counter(call.args[0] for call in numbers.call_args_list)
        for n, m in enumerate(messages):
            own_words = dream.re.sub(r"\[[^\]]*\]", " ", dream._own_words(m["content"]))
            with self.subTest(message=n, chars=len(m["content"])):
                self.assertEqual(parsed[own_words], 1, "numbers of the message parsed once a pass")
                self.assertEqual(splitter.texts[m["content"]], 1, "sentences of the message split once a pass")

    def test_a_second_pass_reads_again(self):
        """What a pass read is not carried into the next one: a message is read once in each."""
        messages = self._window()[:3]
        with mock.patch.object(dream, "_number_kinds", wraps=dream._number_kinds) as numbers:
            dream._score_facts(self.FACTS, messages, None, [], "", "", now=NIGHT, rules=self.rules())
            dream._score_facts(self.FACTS, messages, None, [], "", "", now=NIGHT, rules=self.rules())
        own_words = dream.re.sub(r"\[[^\]]*\]", " ", dream._own_words(messages[2]["content"]))
        self.assertEqual(sum(call.args[0] == own_words for call in numbers.call_args_list), 2)


def reference_pair_counts(probe, m, content=None):
    """The 3.7.0 rule of _pair_counts, word for word, over the rules for two texts (contradicts)."""
    generation = (dream.SETTINGS_GENERATION, id(dream.SETTINGS))
    own = dream._own_stems(m.get("content") or "", generation, probe.wide)
    if own is not None and probe.own_item.isdisjoint(own):
        return False
    msg_stems = dream._stems(m["tokens"])
    if probe.wide:
        msg_stems = msg_stems | dream._wide_stems(m.get("content") or "", generation)
    inter = probe.item_stems & msg_stems
    rare = bool(probe.distinctive) and not probe.distinctive.isdisjoint(
        dream._distinctive_stem(t) for t in m["tokens"] if not dream.is_gram(t))
    if not rare and probe.own_item.isdisjoint(inter):
        return False
    return bool(rare or dream._mentions(inter)) and not (content and dream.contradicts(content, m["content"]))


NAMES = ["Марина", "Фёдор", "Ирина", "Гоша", "Alice", "Bob", "Lena"]
PLACES = ["Лиссабон", "Казань", "Porto", "Bergen"]
THINGS_RU = ["арахис", "кофе", "тренировки", "абонемент", "ракетку", "велосипед", "бассейн"]
THINGS_EN = ["peanuts", "coffee", "training", "membership", "racket", "bicycle", "swimming"]
MONTHS = ["марта", "апреля", "октября"]
DAYS_RU = ["понедельник", "среду", "пятницу"]
DAYS_EN = ["Monday", "Wednesday", "Friday"]
TURNS = [("не ест", "ест"), ("никогда не курил", "курит"), ("любит", "никогда не любила"), ("пьёт", "не пьёт"),
         ("does not eat", "eats"), ("likes", "does not like"), ("listens to", "has not listened to"),
         ("王芳不吃肉了", "王芳吃肉"), ("周日打网球", "周日再也不打网球了"), ("王芳抽烟", "王芳从来没抽过烟"),
         ("对花生过敏", "对花生不过敏")]


def seeded_store(seed=371, facts=36, messages=180):
    """Facts and messages of one made-up household: Russian, English and Chinese; times, dates, prices, months and
    weekdays; negations in another tense; replies, voice messages, bracketed metadata, many sentences and one."""
    r = random.Random(seed)

    def ru():
        n, t, p = r.choice(NAMES[:4]), r.choice(THINGS_RU), r.choice(PLACES)
        return r.choice([
            f"{n} не ест {t} и просит не заказывать {r.choice(THINGS_RU)}",
            f"{n} любит {t} по выходным в {p}",
            f"Абонемент {n} на {t} стоит {r.choice([1200, 1500, 25000])} рублей в месяц",
            f"У {n} тренировка по {r.choice(DAYS_RU)} в {r.choice(['18:00', '19:30', '09:00'])}",
            f"{n} переезжает в {p} {r.randint(1, 28)} {r.choice(MONTHS)}",
            f"{n} никогда не курил",
            f"Гоша пьёт {r.choice(['кофе', 'чай'])}",
        ])

    def en():
        n, t, p = r.choice(NAMES[4:]), r.choice(THINGS_EN), r.choice(PLACES)
        return r.choice([
            f"{n} does not eat {t} at {p}",
            f"{n} likes {t} on {r.choice(DAYS_EN)} evenings",
            f"{n}'s {t} costs {r.choice([40, 45, 60])} dollars a month",
            f"{n} meets the coach on 2026-{r.randint(1, 12):02d}-{r.randint(1, 28):02d} at {r.choice(['7:00', '07:00', '8:30'])}",
            f"{n} listens to {t} podcasts",
            "Bob likes tea",
        ])

    def zh():
        return r.choice(["王芳不吃肉了", "李雷周日打网球", "王芳抽烟", "李雷对花生过敏", "李雷周日再也不打网球了"])

    def turned(text):
        """The same said the other way, often in another tense."""
        for said, other in TURNS:
            if said in text:
                return text.replace(said, other, 1)
        return text

    fact_texts = [r.choice([ru, ru, en, zh])() for _ in range(facts)]
    texts = []
    for _ in range(messages):
        base = r.choice(fact_texts)
        roll = r.random()
        if roll < 0.12:  # said again
            text = base
        elif roll < 0.27:  # said the other way
            text = turned(base)
        elif roll < 0.37:  # a new number for the same thing
            text = base.replace("1200", "1500").replace("18:00", "19:00").replace("45", "50") + ", теперь так"
        elif roll < 0.55:  # many sentences: one near the fact, the others about something else, some negated
            others = [ru() for _ in range(r.randint(1, 4))] + [en() for _ in range(r.randint(0, 2))]
            parts = others + [r.choice([base, turned(base)])]
            r.shuffle(parts)
            text = r.choice([". ", "!\n", "; ", "\n"]).join(parts) + r.choice(["", ".", "?"])
        elif roll < 0.65:
            text = f'[Replying to: "{r.choice(fact_texts)}"] {r.choice([ru(), en(), "да, всё так", "нет"])}'
        elif roll < 0.71:
            text = f"[The user sent a voice message~ Here's what they said: \"{base}\"]"
        elif roll < 0.78:
            text = f"[id={r.randint(100, 999)}] {base} [сб 18.10 в 9:00]"
        elif roll < 0.85:
            text = zh() + "。" + turned(zh())
        else:
            text = r.choice([ru, en])()
        texts.append(text)
    facts = [{"fact_id": i + 1, "content": t, "trust_score": 0.6} for i, t in enumerate(fact_texts)]
    days = [f"2026-10-{d:02d}" for d in range(1, 21)]
    msgs = [_message(t, n, r.choice(days)) for n, t in enumerate(texts)]
    return facts, msgs


class SameDecisionsTests(unittest.TestCase):
    """What a pass decides from what it read once is what the rules for two texts decide, pair by pair."""

    @classmethod
    def setUpClass(cls):
        cls.facts, cls.messages = seeded_store()
        cls.rules = dream.rules_context(f["content"] for f in cls.facts)

    def test_every_pair_is_decided_as_by_the_rules_for_two_texts(self):
        seen = Counter()

        @dream._message_pass
        def check():
            for f in self.facts:
                content = f["content"]
                probe = dream.fact_probe(dream.fact_tokens(content), self.rules)
                for m in self.messages:
                    d = dream._message_data(m)
                    flip, contradicts = dream.polarity_flip(content, m["content"]), dream.contradicts(content,
                                                                                                    m["content"])
                    counts = reference_pair_counts(probe, m, content)
                    with self.subTest(fact=content, message=m["content"]):
                        self.assertEqual(dream._message_flip(content, d), flip)
                        self.assertEqual(dream._message_contradicts(content, d), contradicts)
                        self.assertEqual(dream._pair_counts(probe, m, content), counts)
                    seen["flip"] += flip is not None
                    seen["contradicts"] += contradicts
                    seen["counts"] += counts
                    seen["counts, numbers aside"] += reference_pair_counts(probe, m) and not counts
                    seen["many sentences, flipped"] += flip is not None and d.sentences().count > 1
        check()
        # The window is no easy one: mentions, flips (in messages of many sentences too) and new numbers all happen.
        self.assertGreaterEqual(seen["counts"], 300)
        self.assertGreaterEqual(seen["flip"], 20)
        self.assertGreaterEqual(seen["many sentences, flipped"], 10)
        self.assertGreater(seen["contradicts"], seen["flip"])
        self.assertGreaterEqual(seen["counts, numbers aside"], 100)

    def test_a_night_over_all_facts_is_the_night_of_each_fact_alone(self):
        """Facts share what was read from a message, and none of them sees another's answer: the scores of all facts
        in one pass are those of each fact in a pass of its own (wide and plain facts, replies, negations)."""
        def scores(facts):
            counted = set()
            scored = dream._score_facts(facts, self.messages, None, [], "", "", now=NIGHT, rules=self.rules, counted=counted)
            return [(s["fact_id"], s["score"], s["meta"]) for s in scored], counted

        together, counted = scores(self.facts)
        alone, counted_alone = [], set()
        for f in self.facts:
            one, c = scores([f])
            alone += one
            counted_alone |= c
        self.assertEqual(together, alone)
        self.assertEqual(counted, counted_alone)
        self.assertTrue(any(meta["mentions"] for _, _, meta in together))

    def test_a_message_given_another_text_is_read_again(self):
        """A message changed in place within a pass is not judged by what was read from its old text."""
        fact = "Абонемент Ирины на бассейн стоит 1200 рублей в месяц"
        probe = dream.fact_probe(dream.fact_tokens(fact))
        m = _message("Ирина продлила абонемент на бассейн, 1200 рублей в месяц")

        @dream._message_pass
        def check():
            self.assertTrue(dream._pair_counts(probe, m, fact))
            m["content"] = "Ирина продлила абонемент на бассейн, теперь 1500 рублей в месяц"
            m["tokens"] = dream.sig_tokens(m["content"])
            self.assertFalse(dream._pair_counts(probe, m, fact))
        check()


@contextlib.contextmanager
def memory_limit(mb):
    """The pass's limit set as an install sets it: `corroboration.memory_mb` in dreaming.json."""
    saved = dream.SETTINGS.CONFIG
    dream.configure(dict(saved, corroboration={"memory_mb": mb}))
    try:
        yield
    finally:
        dream.configure(saved)


def pasted_ids_window(messages=60, words=600, seed=7):
    """Messages of pasted identifiers and log tokens: random 10-letter words, nearly all of them seen once in the
    whole window (the most a pass can keep per character), a few of them the words of the facts; some messages negate,
    every one has a number. Facts: two of those words, a negation, a number and a weekday."""
    r = random.Random(seed)

    def word():
        return "".join(r.choice(string.ascii_lowercase) for _ in range(10))
    shared = [word() for _ in range(24)]
    window = []
    for n in range(messages):
        ws = [word() for _ in range(words)]
        for _ in range(3):
            ws[r.randrange(words)] = r.choice(shared)
        text = " ".join(ws[:words // 2]) + ".\n" + " ".join(ws[words // 2:]) + (" не 1500" if n % 3 == 0 else " 1200")
        window.append(_message(text, n))
    facts = [{"fact_id": i + 1, "content": f"{shared[2 * i]} {shared[2 * i + 1]} не 1200 по понедельникам",
              "trust_score": 0.6} for i in range(12)]
    return facts, window


class MemoryLimitTests(unittest.TestCase):
    """What a pass keeps is held under `corroboration.memory_mb`; past it parts are let go and read again, and no
    decision changes. 6c55540 kept everything it read until the pass ended (a window of unique words: 13 MB for
    0.4 MiB of text)."""

    @classmethod
    def setUpClass(cls):
        cls.facts, cls.messages = pasted_ids_window()
        cls.rules = dream.rules_context(f["content"] for f in cls.facts)

    def scores(self):
        counted = set()
        scored = dream._score_facts(self.facts, self.messages, None, [], "", "", now=NIGHT, rules=self.rules, counted=counted)
        return [(s["fact_id"], s["score"], s["meta"]) for s in scored], counted

    def held_by_a_pass(self):
        """Bytes the pass holds at its end and lets go with it, and the most it held at any time (tracemalloc; the
        window read once before, so that the process's own word caches are warm and not counted)."""
        @dream._message_pass
        def night():
            dream._score_facts(self.facts, self.messages, None, [], "", "", now=NIGHT, rules=self.rules)
            return tracemalloc.get_traced_memory()[0]
        night()
        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]
            inside = night()
            after, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return inside - after, peak - base

    def test_what_a_pass_keeps_stays_under_the_limit(self):
        mib = 1024 * 1024
        unlimited, _ = self.held_by_a_pass()
        self.assertGreater(unlimited, 3 * mib)  # the window needs more than the limit below
        with memory_limit(1):
            held, peak = self.held_by_a_pass()
        self.assertLess(held, 1.5 * mib)
        self.assertLess(peak, 2.5 * mib)

    def test_past_the_limit_the_decisions_are_the_same(self):
        together, counted = self.scores()
        with memory_limit(1):
            limited, counted_limited = self.scores()
        self.assertEqual(limited, together)
        self.assertEqual(counted_limited, counted)
        self.assertTrue(any(meta["mentions"] for _, _, meta in together))
        seen = Counter()
        with memory_limit(1):
            @dream._message_pass
            def check():
                for f in self.facts:
                    content = f["content"]
                    probe = dream.fact_probe(dream.fact_tokens(content), self.rules)
                    for m in self.messages:
                        counts = reference_pair_counts(probe, m, content)
                        with self.subTest(fact=content, message=m["key"]):
                            self.assertEqual(dream._pair_counts(probe, m, content), counts)
                        seen["counts"] += counts
                        seen["counts, numbers or negation aside"] += reference_pair_counts(probe, m) and not counts
            check()
        self.assertGreater(seen["counts"], 0)
        self.assertGreater(seen["counts, numbers or negation aside"], 0)

    def test_past_the_limit_what_was_let_go_is_read_again(self):
        """A real count of how often each message's stems are taken: once a pass under the default limit; more than
        once for some messages under a limit the window does not fit in (let go, then read again for a later fact)."""
        def stem_reads():
            with mock.patch.object(dream, "_stems", wraps=dream._stems) as stems:
                dream._score_facts(self.facts, self.messages, None, [], "", "", now=NIGHT, rules=self.rules)
            reads = Counter(id(call.args[0]) for call in stems.call_args_list)
            return [reads[id(m["tokens"])] for m in self.messages]

        self.assertEqual(stem_reads(), [1] * len(self.messages))
        with memory_limit(1):
            self.assertGreater(max(stem_reads()), 1)


class MemoryLimitSettingTests(unittest.TestCase):
    def test_the_limit_is_read_from_dreaming_json(self):
        for raw, expected in ((64, 64), (0.5, 256), (0, 256), (-5, 256), ("abc", 256), (True, 256),
                              (float("nan"), 256), (float("inf"), 256), (None, 256), ("96", 96)):
            with self.subTest(raw=raw), mock.patch("sys.stderr"):
                with memory_limit(raw):
                    self.assertEqual(dream.SETTINGS.PASS_MEMORY_MB, expected)

    def test_the_default_is_256(self):
        self.assertEqual(dream.DEFAULT_CONFIG["corroboration"], {"memory_mb": 256})
        saved = dream.SETTINGS.CONFIG
        try:
            dream.configure({k: v for k, v in saved.items() if k != "corroboration"})
            self.assertEqual(dream.SETTINGS.PASS_MEMORY_MB, 256)
        finally:
            dream.configure(saved)


class ReleasedAfterThePassTests(unittest.TestCase):
    def _pass_reads(self, fail=False):
        facts, messages = seeded_store(seed=5, facts=6, messages=40)
        refs = []

        @dream._message_pass
        def night():
            dream._score_facts(facts, messages, None, [], "", "", now=NIGHT, rules=dream.rules_context(f["content"] for f in facts))
            refs.extend(weakref.ref(d) for d in dream._PASS_DATA.get().messages.values())
            if fail:
                raise RuntimeError("interrupted")
        if fail:
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                night()
        else:
            night()
        return refs, messages

    def test_what_a_pass_read_is_dropped_after_it(self):
        refs, messages = self._pass_reads()
        self.assertEqual(len(refs), len(messages))  # it did read every message, once each
        self.assertIsNone(dream._PASS_DATA.get())
        gc.collect()
        self.assertEqual([r for r in refs if r() is not None], [])

    def test_what_a_pass_read_is_dropped_when_it_fails(self):
        refs, _ = self._pass_reads(fail=True)
        self.assertTrue(refs)
        self.assertIsNone(dream._PASS_DATA.get())
        gc.collect()
        self.assertEqual([r for r in refs if r() is not None], [])


if __name__ == "__main__":
    unittest.main()
