"""3.7.0: the whole-store duplicate search on large stores.

All texts are synthetic: made-up names and words built from syllables, numbers and dates drawn from a seeded
generator; no real person's memory is quoted.

Requirements, each with its tests:
- a store of thousands of facts that look alike — reminders that differ only by their date and time, one sentence
  said of many different people — still gets its "merge?" question: the search stays within the night's default
  budget of pair checks instead of being skipped, and the checks grow with the store, not with its square;
- narrowing the candidates changes nothing the rule decides: the pairs found are exactly the pairs the rule accepts
  when every pair of the store is compared, on a mixed Russian / English store with retellings, short facts and
  hard negatives;
- a hard negative — another number or date, a negation, another person, one word swapped — is never paired, and a
  retelling (a provenance stamp and "said that" added, a closing remark added) is found.

Run:  python -m unittest discover -s tests   (from the skill directory)
"""
import random
import unittest

from test_dream import dream  # noqa: E402  (same directory)

SYLLABLES_RU = "ла ро ми ка те ну ве до са ри по лю ма ки зо бе ре на ти го да ше ву мо".split()
SYLLABLES_EN = "la ro mi ka te nu ve do sa ri po lu ma ki zo be re na ti go da she vu mo".split()


def made_up(rnd, syllables, parts):
    return "".join(rnd.choice(syllables) for _ in range(parts))


def made_up_names(rnd, count):
    """Capitalized made-up names, no two of them forms of one word («Нузодо» / «Нузода» are one name to the rule)."""
    by_stem = {}
    for _ in range(count):
        name = made_up(rnd, SYLLABLES_RU, 3).capitalize()
        by_stem.setdefault(dream._stem(name.lower()), name)
    return sorted(by_stem.values())


def facts(texts):
    return [{"fact_id": n + 1, "content": t} for n, t in enumerate(texts)]


def brute_force(store):
    """Every pair of the store through the rule itself (`duplicate_pair`, its features computed once a fact)."""
    feats = [(f["fact_id"], dream._dup_features(f["content"])) for f in store]
    return {(a, b) for n, (a, fa) in enumerate(feats) for b, fb in feats[n + 1:] if dream._dup_judge(fa, fb)[0]}


def look_alike_store(size):
    """Facts a long-lived agent piles up: dated reminders and one sentence about many people. Nothing in their
    stems is rare, so a stem index alone makes every one of them a candidate of every other."""
    rnd = random.Random(370)
    names = made_up_names(rnd, size)
    texts, seen = [], set()
    while len(texts) < size:
        day = f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        hour = f"{rnd.randint(7, 22)}:{rnd.choice(('00', '30'))}"
        kind = len(texts) % 4
        if kind == 0:
            text = f"Напомнить про оплату интернета и телефона {day} в {hour}, счёт приходит на почту."
        elif kind == 1:
            text = f"Remind about the gym membership payment on {day} at {hour}, the invoice comes by email."
        elif kind == 2:
            text = f"{rnd.choice(names)} предпочитает созвоны с кандидатами утром и не любит голосовые сообщения."
        else:
            text = f"{rnd.choice(names)} prefers calls with candidates in the morning and dislikes voice messages."
        if text not in seen:
            seen.add(text)
            texts.append(text)
    return facts(texts)


TEMPLATES = [
    "{N} работает в компании {O} как {w} по {w} и {w}.",
    "{N} живёт в {P} и ездит на скутере {w} по выходным.",
    "Ветка {n} — {w} задачи {N}: сообщения оттуда считать рабочими.",
    "{N} помогает {M} с {w} агентства {O} и контентом в Telegram.",
    "Бюджет {N} на {w} — {n} рублей в месяц, без {w}.",
    "{N} works at {O} as a {w} for {w} and {w}.",
    "{N} lives in {P} and rides a {w} scooter on weekends.",
    "{N}'s budget for {w} is {n} dollars a month, without {w}.",
    "{N} meets {M} on {d} in {P} about the {w} project.",
]
SHORT = ["@{h} — {N}", "Ветка {n}", "{n} — {N}", "{N} {w}"]


class MixedStore:
    """A seeded store of plain facts, their retellings and their hard negatives (another number or date, a
    negation, another person, one word swapped), with a few short facts."""

    def __init__(self, seed, plain=160):
        rnd = self.rnd = random.Random(seed)
        self.names = made_up_names(rnd, 40)
        self.places = sorted({made_up(rnd, SYLLABLES_RU, 3).capitalize() for _ in range(12)})
        self.words_ru = sorted({made_up(rnd, SYLLABLES_RU, 3) + rnd.choice(("а", "ом", "у", "ы")) for _ in range(60)})
        self.words_en = sorted({made_up(rnd, SYLLABLES_EN, 3) + rnd.choice(("", "s", "er")) for _ in range(60)})
        base = [self.fill(rnd.choice(TEMPLATES)) for _ in range(plain)]
        base += [self.fill(rnd.choice(SHORT)) for _ in range(plain // 8)]
        self.retellings, self.negatives = [], []
        for text in base[:plain // 3]:
            self.retellings.append((text, self.retell(text)))
        for text in base[plain // 3:plain]:
            miss = self.near_miss(text)
            if miss:
                self.negatives.append((text, miss))
        texts = list(dict.fromkeys(base + [b for _, b in self.retellings] + [b for _, b in self.negatives]))
        rnd.shuffle(texts)
        self.facts = facts(texts)
        self.id_of = {f["content"]: f["fact_id"] for f in self.facts}

    def fill(self, template):
        rnd, out = self.rnd, template
        ru = any("а" <= c <= "я" for c in template)
        # the words of one fact all differ: «на A, без A» would turn a swap of one A into an added word
        words = rnd.sample(self.words_ru if ru else self.words_en, template.count("{w}"))
        person, other = rnd.sample(self.names, 2)  # and so do its two people
        for slot, value in (("{N}", lambda: person), ("{M}", lambda: other),
                            ("{P}", lambda: rnd.choice(self.places)), ("{O}", lambda: f"QX{rnd.randint(1, 9)}"),
                            ("{n}", lambda: str(rnd.randint(2, 400))), ("{h}", lambda: made_up(rnd, SYLLABLES_EN, 3)),
                            ("{d}", lambda: f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"),
                            ("{w}", words.pop)):
            while slot in out:
                out = out.replace(slot, value(), 1)
        return out

    def retell(self, text):
        ru = any("а" <= c <= "я" for c in text.lower())
        if self.rnd.random() < 0.5:
            return "2026-10-27 " + ("Пользователь сказал, что " if ru else "The user said that ") + text
        return text.rstrip(".") + (" — это важно." if ru else " — this matters.")

    def near_miss(self, text):
        rnd, words = self.rnd, text.split()
        ru = any("а" <= c <= "я" for c in text.lower())
        numbers = [i for i, w in enumerate(words) if w.strip(".,:;—").replace("-", "").isdigit()]
        people = [i for i, w in enumerate(words) if w.strip(".,:;'s") in self.names]
        plain = [i for i, w in enumerate(words) if w.strip(".,:;") in (self.words_ru if ru else self.words_en)]
        kind = rnd.randrange(4)
        if kind == 0 and numbers:  # another number or date
            i = rnd.choice(numbers)
            old = words[i].strip(".,:;—")
            new = old[:-1] + str((int(old[-1]) + rnd.randint(1, 8)) % 10)
            words[i] = words[i].replace(old, new)
        elif kind == 1 and len(words) > 2 and not words[1][:1].isupper():  # a negation of the verb
            words.insert(1, "не" if ru else "does not")
        elif kind == 2 and people:  # another person
            i = rnd.choice(people)
            name = words[i].strip(".,:;'s")
            words[i] = words[i].replace(name, rnd.choice([n for n in self.names if n != name]))
        elif kind == 3 and plain:  # one word swapped
            i = rnd.choice(plain)
            word = words[i].strip(".,:;")
            # a word the fact does not hold yet: «на A, без B» → «на B, без B» would be a fact absorbed, not a swap
            held = {dream._stem(w.strip(".,:;").lower()) for w in words}
            pool = [w for w in (self.words_ru if ru else self.words_en) if dream._stem(w) not in held]
            words[i] = words[i].replace(word, rnd.choice(pool))
        else:
            return None
        return " ".join(words)

    def pair(self, a, b):
        return tuple(sorted((self.id_of[a], self.id_of[b])))


class LargeStoreTests(unittest.TestCase):
    def test_a_store_of_look_alike_facts_stays_within_the_nights_budget(self):
        """1 600 facts of four kinds: on 3.6.2 every fact was a candidate of every other of its kind, about 320 000
        pair checks, past the night's default budget of 200 000 — the section was skipped every night, and the
        larger the store, the surer. Now the checks grow with the store."""
        store = look_alike_store(1600)
        found, checks = dream.find_duplicate_pairs(store, dream.SETTINGS.DUP_MAX_PAIR_CHECKS)
        self.assertLessEqual(checks, 5 * len(store), "the checks grow with the square of the store")
        self.assertEqual(found, [], "reminders of other days and the same sentence of other people are no pairs")

    def test_the_narrowed_search_finds_what_comparing_every_pair_finds(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                store = MixedStore(seed)
                found, checks = dream.find_duplicate_pairs(store.facts)
                every = brute_force(store.facts)
                self.assertEqual({(p["a"], p["b"]) for p in found}, every)
                self.assertGreaterEqual(len(every), len(store.retellings))
                self.assertLess(checks, len(store.facts) * (len(store.facts) - 1) // 2 // 10)

    def test_hard_negatives_are_not_paired_and_retellings_are_found(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                store = MixedStore(seed)
                found = {(p["a"], p["b"]) for p in dream.find_duplicate_pairs(store.facts)[0]}
                self.assertGreater(len(store.negatives), 30)
                for a, b in store.negatives:
                    self.assertNotIn(store.pair(a, b), found, f"{a!r} / {b!r}")
                for a, b in store.retellings:
                    self.assertIn(store.pair(a, b), found, f"{a!r} / {b!r}")


if __name__ == "__main__":
    unittest.main()
