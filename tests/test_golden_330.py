"""3.3.0, phase 1 of the cloud-judge plan: two nights of the 3.2.0 pass frozen as a golden record.

The refactor that makes room for the judge (one predicate per pair, one pure decision function, explicit inputs)
must not change what a night decides. This home exercises every section that reads the mention count and the
state the pass keeps between nights: a corroborated promotion, a dated one-off event, an expired one, a secret in
quarantine, a rejected fact said again, a rejected one the store still trusts, a possible update of a memory
entry, a fact nobody mentions any more, a memory entry nobody mentions, a confirmation, a new fact, a promoted chat
store, reply quotes, a voice message, a negation, an observed participant, a private chat, an untrusted chat and a
machine session. The second night runs after the agent acknowledged the first, so the cooldowns show.

All data is fictional (a table-tennis club). Time is frozen and the zone is a fixed offset, so the record is the
same on every platform and Python version of the CI matrix.

The record was taken from 3.2.0 (e730fba) before the refactor, with one fix the record itself found: tied themes
are ordered by the word, not by PYTHONHASHSEED (ThemeOrderTests). Regenerate it only on purpose:
    DREAM_GOLDEN_UPDATE=1 python -m unittest test_golden_330      (from tests/)
"""

import json
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from test_chat_stores import CHAT_A, CHAT_B, KEY_A, KEY_B, ChatStoreBase
from test_dream import EXTERNAL_CHAT, FAMILY_CHAT, dream, reject

GOLDEN = Path(__file__).resolve().parent / "golden" / "330" / "two_nights.json"
FROZEN = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)  # 12:00 in the home's zone
ZONE = timezone(timedelta(hours=7))

PROMOTED = "Гоша тренирует юниоров клуба по средам в 18:00 в малом зале."
QUOTED = "Фёдор отвечает за закупку мячей для клуба."
NEGATED = "Ирина ведёт бухгалтерию клуба и сверяет кассу."
FORGOTTEN_TABLE = "Старый стол для пинг-понга стоит в подвале у входа."
CONFIRMED = "Лиля оплачивает аренду зала первого числа каждого месяца."
TOURNAMENT = "Встреча юниоров клуба с тренером сборной 2026-11-08, собрать заявки к пятнице."
EXPIRED = "Субботник в клубе 2026-09-12, принести перчатки и мешки."
SECRET = "Пароль от сейфа клуба: Zx9!pL2026"
MOVING = "Клуб переезжает в новый зал на улице Садовой."
RENT = "Аренда зала клуба 12000 бат в месяц по карте Лили."
NET = "Фёдор купил новую сетку для третьего стола."
COFFEE = "Гоша не пьёт кофе перед тренировкой."
CHANNEL = "Лиля ведёт канал клуба в Telegram и пишет анонсы."
CAT = "Кот клуба Барсик ест только влажный корм марки Лапка."

MEMORY = (
    "§\n" + COFFEE + "\n"
    "§\nАренда зала клуба 9000 бат в месяц по карте Лили.\n"
    "§\nСтаринный граммофон клуба хранится в кладовке на верхней полке слева.\n"
    "§\nТренер Гоша ведёт группу взрослых по понедельникам вечером.\n"
)


def _stamp(days_ago):
    return (FROZEN - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


class GoldenScenario(ChatStoreBase):
    """The home of the golden record. Phase-2 contract tests reuse it."""

    SCOPE = "chat"

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                 if not k.startswith("DREAM_")}, clear=True)
        self._env.start()
        super().setUp()
        self._old_now, self._old_tz = dream._now, dream.SETTINGS.LOCAL_TZ
        self.clock = FROZEN
        dream._now = lambda: self.clock
        dream.SETTINGS.LOCAL_TZ = ZONE
        cache = self.home / "cache"
        cache.mkdir()
        self.paths = {name: str(cache / f"dream-{name}.json") for name in ("asked", "rejected", "seen", "snapshot")}
        self.build()

    def tearDown(self):
        dream._now, dream.SETTINGS.LOCAL_TZ = self._old_now, self._old_tz
        super().tearDown()
        self._env.stop()

    # --- the home ------------------------------------------------------------------------------------------------
    def fact(self, content, trust=0.9, rc=0, helpful=0, days_old=1.0, span_days=0.0):
        con = sqlite3.connect(self.home / "memory_store.db")
        con.execute("insert into facts (content, trust_score, retrieval_count, helpful_count, created_at, updated_at)"
                    " values (?,?,?,?,?,?)", (content, trust, rc, helpful, _stamp(days_old + span_days),
                                               _stamp(days_old)))
        con.commit()
        con.close()

    def msg(self, content, days_ago, source="telegram", chat_type="group", chat_id=FAMILY_CHAT, role="user",
            observed=0, session=None):
        sid = session or f"s-{source}-{chat_id}-{chat_type}"
        con = sqlite3.connect(self.home / "state.db")
        con.execute("insert or ignore into sessions (id, source, chat_type, chat_id, thread_id) values (?,?,?,?,?)",
                    (sid, source, chat_type, chat_id, None))
        con.execute("insert into messages (session_id, role, content, timestamp, observed) values (?,?,?,?,?)",
                    (sid, role, content, (FROZEN - timedelta(days=days_ago)).timestamp(), observed))
        con.commit()
        con.close()

    def chat_store(self, key, facts):
        folder = self.home / "holographic-chats"
        folder.mkdir(exist_ok=True)
        con = sqlite3.connect(folder / f"{key}.db")
        con.execute("create table facts (fact_id integer primary key autoincrement, content text,"
                    " category text default 'general', tags text default '', trust_score real default 0.5,"
                    " retrieval_count integer default 0, helpful_count integer default 0,"
                    " created_at timestamp, updated_at timestamp)")
        con.execute("create table forgotten (fact_id integer primary key, forgotten_at text)")
        for text, days_old in facts:
            con.execute("insert into facts (content, trust_score, created_at, updated_at) values (?,?,?,?)",
                        (text, 0.9, _stamp(days_old), _stamp(days_old)))
        con.commit()
        con.close()

    def build(self):
        con = sqlite3.connect(self.home / "memory_store.db")
        con.execute("create table fact_confirmations (fact_id integer, confirmed_at text, session_id text,"
                    " user_id text, via text)")
        con.commit()
        con.close()
        self.mem_md.write_text(MEMORY, encoding="utf-8")

        self.fact(PROMOTED, rc=2, helpful=2, days_old=10)
        self.fact(QUOTED, rc=1, days_old=10)
        self.fact(NEGATED, rc=1, days_old=10)
        self.fact(FORGOTTEN_TABLE, trust=0.4, days_old=45)
        self.fact(CONFIRMED, rc=1, days_old=20)
        self.fact(TOURNAMENT, rc=3, helpful=2, days_old=5)
        self.fact(EXPIRED, days_old=40)
        self.fact(SECRET, days_old=3)
        self.fact(MOVING, rc=1, days_old=25)
        self.fact(RENT, days_old=2)
        self.fact(NET, days_old=1)
        self.fact(COFFEE, rc=1, days_old=12)
        self.fact(CHANNEL, trust=0.8, days_old=30)

        rejected = {}
        # An old rejection of a fact said again since (asked about once more), and a recent one the store still
        # trusts (a trust_feedback call).
        for key, content, days in (("9", MOVING, 200), ("13", CHANNEL, 20)):
            rejected[key] = {"fact_id": key, "content": content, "fingerprint": reject._fingerprint(content),
                             "reason": "устарело", "at": (FROZEN - timedelta(days=days)).isoformat()}
        Path(self.paths["rejected"]).write_text(json.dumps(rejected, ensure_ascii=False), encoding="utf-8")

        # The trusted family chat: real mentions, an observed participant, a voice message.
        for d, text in ((2, "Гоша сегодня опять тренирует юниоров в малом зале, по средам в 18:00 как всегда"),
                        (4, "юниоры клуба у Гоши в малом зале, среда, 18:00 — всё в силе"),
                        (6, "Гоша тренирует юниоров клуба по средам, в 18:00 в малом зале")):
            self.msg(text, d)
        self.msg("Гоша и в эту среду тренирует юниоров в малом зале в 18:00", 8, observed=1)
        self.msg('[The user sent a voice message~ Here\'s what they said: "Гоша тренирует юниоров по средам '
                 'в малом зале"]', 9)
        # A reply whose quote carries the fact and whose own words do not.
        for d in (1, 3):
            self.msg('[Replying to: "Напоминаю: Фёдор отвечает за закупку мячей для клуба, заказ до пятницы."]'
                     "\n\nспасибо, а что с расписанием на выходные?", d)
        self.msg("Ирина больше не ведёт бухгалтерию клуба и не сверяет кассу", 2)
        for d in (3, 8):
            self.msg("Лиля как обычно оплачивает аренду зала первого числа каждого месяца", d)
        for d in (1, 2, 4):
            self.msg("Собираем заявки юниоров клуба на встречу с тренером сборной к пятнице", d)
        for d in (2, 5):
            self.msg("Клуб переезжает в новый зал на улице Садовой, уже смотрели помещение", d)
        self.msg("Гоша, как и раньше, не пьёт кофе перед тренировкой", 4)
        self.msg("Тренер Гоша ведёт группу взрослых по понедельникам вечером, запись открыта", 5)
        # A private chat counts (trust_private_chats); an untrusted group and a machine session do not.
        self.msg("Лиля оплачивает аренду зала первого числа каждого месяца, напомни ей", 6, chat_type="dm",
                 chat_id="555")
        for d in (1, 2, 3):
            self.msg("Гоша тренирует юниоров клуба по средам в малом зале", d, chat_id=EXTERNAL_CHAT)
        self.msg("Фёдор купил новую сетку для третьего стола", 1, source="cron", chat_type=None, chat_id=None,
                 session="cron_digest_1")

        # A promoted chat store: its fact is corroborated by its own chat (not in the trusted list).
        self.chat_store(KEY_A, [(CAT, 6)])
        self.chat_store(KEY_B, [("Собака соседей по залу лает по вечерам", 4)])
        for d in (1, 2, 3):
            self.msg("Барсик опять ест только влажный корм марки Лапка", d, chat_id=CHAT_A)
        self.msg("Собака соседей по залу снова лаяла", 2, chat_id=CHAT_B)

    # --- the nights ----------------------------------------------------------------------------------------------
    def night(self):
        return dream.run(14, 60, 60, str(self.mem_md), asked_state_path=self.paths["asked"],
                         rejected_state_path=self.paths["rejected"], seen_state_path=self.paths["seen"],
                         snapshot_state_path=self.paths["snapshot"])

    def ack(self, result):
        """What Hermes leaves in state.db after the dream job: its prompt with the payload, then an answer."""
        sid = f"cron_dreamjob_{result['generated_at']}"
        at = self.clock.timestamp() + 60
        con = sqlite3.connect(self.home / "state.db")
        con.execute("insert or ignore into sessions (id, source, chat_type, chat_id) values (?,?,?,?)",
                    (sid, "cron", None, None))
        prompt = 'skill text… {"note":"…","generated_at":"' + result["generated_at"] + '","stats":{}}'
        con.execute("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)",
                    (sid, "user", prompt, at))
        con.execute("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)",
                    (sid, "assistant", "Saved one fact, asked about the gramophone.", at + 30))
        con.commit()
        con.close()

    def state_files(self):
        out = {}
        for name, path in sorted(self.paths.items()):
            try:
                out[name] = json.loads(Path(path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                out[name] = None
        return out

    def normalized(self, data):
        text = json.dumps(data, ensure_ascii=False, sort_keys=True)
        for home in {str(self.home), str(self.home).replace("\\", "/"), json.dumps(str(self.home))[1:-1]}:
            text = text.replace(home, "<HOME>")
        return json.loads(text)

    def two_nights(self):
        first = self.night()
        record = {"night1": {"result": first, "state": self.state_files()}}
        self.ack(first)
        self.clock = FROZEN + timedelta(days=1)
        second = self.night()
        record["night2"] = {"result": second, "state": self.state_files()}
        return self.normalized(record)


def _first_difference(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}: only in {'golden' if key in a else 'now'}"
            found = _first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = _first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


class GoldenRecordTests(GoldenScenario):
    def test_two_nights_match_the_record_of_3_2_0(self):
        now = self.two_nights()
        if os.environ.get("DREAM_GOLDEN_UPDATE") == "1" or self._update:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8", newline="\n")
        self.assertTrue(GOLDEN.is_file(), f"no golden record at {GOLDEN}")
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertIsNone(_first_difference(golden, now))

    def test_the_record_covers_every_section_the_judge_touches(self):
        """A record of empty sections would pass any refactor: the home must really produce them."""
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        first = golden["night1"]["result"]
        for section in ("promotions", "ephemeral_events", "expired_events", "quarantined", "new_facts", "conflicts",
                        "fact_decays", "md_decays", "confirm", "trust_feedback"):
            self.assertTrue(first[section], f"night 1 has no {section}")
        self.assertTrue(any(p.get("previously_rejected") for p in first["promotions"] + first["new_facts"]),
                        "the rejected fact said again is not in the record")
        self.assertTrue(any(str(p["fact_id"]).startswith(KEY_A + "#") for p in first["promotions"]),
                        "the promoted chat store's fact is not in the record")
        self.assertLess(golden["night2"]["result"]["stats"]["promotions"], first["stats"]["promotions"],
                        "the second night shows no cooldown")


class ThemeOrderTests(unittest.TestCase):
    """Found by the golden record: themes with the same count came out in an order that depended on
    PYTHONHASHSEED (the tokens of a message are a set), so two runs of the same night disagreed — and under the
    publish cap, on which themes were shown at all."""

    SCRIPT = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('dream', sys.argv[1])\n"
        "dream = importlib.util.module_from_spec(spec); sys.modules['dream'] = dream; spec.loader.exec_module(dream)\n"
        "texts = ['ракетка сетка мячи тренер зал', 'ракетка сетка мячи тренер зал', 'ракетка сетка мячи тренер зал']\n"
        "msgs = [{'content': t, 'tokens': dream.sig_tokens(t), 'ts': dream._now().timestamp()} for t in texts]\n"
        "print(json.dumps([t['theme'] for t in dream.extract_themes(msgs, 14)]))\n"
    )

    def test_tied_themes_come_out_in_the_same_order_whatever_the_hash_seed(self):
        import subprocess
        import sys
        from test_dream import ROOT
        outputs = set()
        for seed in range(8):
            env = {k: v for k, v in os.environ.items() if not k.startswith("DREAM_")}
            env["PYTHONHASHSEED"] = str(seed)  # no -I: isolated mode ignores this very variable
            done = subprocess.run([sys.executable, "-c", self.SCRIPT, str(ROOT / "scripts" / "dream.py")],
                                  capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            outputs.add(done.stdout.strip())
        self.assertEqual(len(outputs), 1, outputs)
        self.assertGreaterEqual(len(json.loads(outputs.pop())), 3)  # the tie is real: several themes, one count


# Read here, not in the test: setUp cleans DREAM_* out of the environment.
GoldenRecordTests._update = os.environ.get("DREAM_GOLDEN_UPDATE") == "1"


if __name__ == "__main__":
    unittest.main()
