"""3.3.0, phase 3 of the cloud-judge plan: the judge itself, tested against a provider on 127.0.0.1.

What a person who turns the judge on is promised (written before the code):
- off by default: dream_judge.py is not even imported, nothing leaves the machine, the night is 3.2.0's;
- only pairs the rules counted, only from chats and sources the owner listed, and only the fact and the person's
  own words — no ids, senders, times, links, reply quotes or secrets;
- the API key goes to its provider in one header and nowhere else (not the report, the diary, the judge's files,
  stdout or stderr);
- a provider that fails costs one attempt at the fallback, not the night; a night the judge could not cover applies
  no vetoes, and three such nights wake the agent with one alert a week;
- attempts, money and time are capped, and the gate's deadline is respected;
- a verdict is kept for 90 days and reused only for the same pair with the same text, sent again tonight;
- shadow changes nothing but the judge's own report and files; veto removes exactly the vetoed pairs from the count;
- what was sent is journalled for review and leaves every file of the judge with the fact when it is forgotten;
- the review sample and the rule "ready / not ready / continue" are reproducible.

The home is the golden record's (test_golden_330): frozen time, a trusted group, a private chat, a promoted chat
store, an untrusted group, a machine session. All data and keys are fictional.
"""

import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from test_chat_stores import CHAT_A, KEY_A
from test_dream import EXTERNAL_CHAT, FAMILY_CHAT, dream, precheck
from test_golden_330 import (CAT, CONFIRMED, COFFEE, FROZEN, MOVING, PROMOTED, RENT, TOURNAMENT, ZONE,
                             GoldenScenario)

KEY_JEV = "sk-or-v1-test-7f3a9c0d2e"  # fictional
KEY_OAI = "sk-proj-test-41b2d8e6aa"  # fictional

# Phase 0 measured the judge on exactly this question (plans/2026-10-22-cloud-judge/phase0/phase0.py); tau 0.50
# holds for these words only.
INSTRUCTIONS = "Does the message confirm the fact, contradict it, or neither?"
LABELS = {"confirms": "the message confirms the fact: the same person or thing, and the fact still holds",
          "contradicts": "the message contradicts the fact: it says the fact is not true or no longer true",
          "unrelated": "the message is about another person or thing, or neither confirms nor denies the fact"}
JEV_LETTER = {"confirms": "A", "contradicts": "B", "unrelated": "C"}


def jev_answer(label, confidence=0.9, cost=0.00002):
    letter = JEV_LETTER[label]
    rest = round((1 - confidence) / 2, 6)
    probs = {k: (confidence if k == letter else rest) for k in "ABC"}
    out = {"answers": {"rel": {"type": "choice", "choice": letter, "probabilities": probs, "confidence": confidence}},
           "model": "typesafe/jev-1.14-20261014"}
    if cost is not None:
        out["usage"] = {"cost": cost}
    return out


def openai_answer(label, confidence=0.9, tokens=210):
    rest = round((1 - confidence) / 2, 6)
    probs = [{"value": v, "probability": confidence if v == label else rest} for v in LABELS]
    return {"answers": [{"type": "choice", "name": "rel", "choice": label, "probabilities": probs,
                         "confidence": confidence}], "usage": {"input_tokens": tokens, "output_tokens": 0}}


def _sent_text(path, body):
    return body.get("state") if path.endswith("/jev") else body.get("input")


def _split(text):
    """(fact, words) of a sent text "Fact: …\\nMessage: …"."""
    head, _, words = (text or "").partition("\nMessage: ")
    return head[len("Fact: "):], words


class _Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        srv = self.server
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        srv.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                             "body": body})
        queue = srv.queue.get(self.path)
        spec = queue.pop(0) if queue else srv.answer(self.path, body)
        if spec.get("delay"):
            time.sleep(spec["delay"])
        raw = spec["raw"] if "raw" in spec else json.dumps(spec.get("json")).encode("utf-8")
        try:
            self.send_response(spec.get("status", 200))
            for k, v in (spec.get("headers") or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Provider)
        self.requests, self.queue = [], {}
        self.verdict = lambda fact, words: ("confirms", 0.9)

    def answer(self, path, body):
        if path not in ("/jev", "/openai"):
            return {"status": 404, "json": {"error": "no such path"}}
        label, confidence = self.verdict(*_split(_sent_text(path, body)))
        return {"json": jev_answer(label, confidence) if path == "/jev" else openai_answer(label, confidence)}

    def sent(self, path=None):
        """[(path, fact, words)] of the requests, in order."""
        return [(r["path"], *_split(_sent_text(r["path"], r["body"]))) for r in self.requests
                if path is None or r["path"] == path]


class JudgeHome(GoldenScenario):
    """The golden home with a judge in shadow mode, both providers on a local server."""

    def setUp(self):
        super().setUp()
        self.J = dream._load_judge()
        self.server = _Server()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.base = base
        for name in ("jev", "openai"):
            patch = mock.patch.dict(self.J.PROVIDERS[name], url=f"{base}/{name}")
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(self.J, "RETRY_BACKOFF", (0, 0))
        patch.start()
        self.addCleanup(patch.stop)
        os.environ["OPENROUTER_API_KEY"] = KEY_JEV
        os.environ["OPENAI_API_KEY"] = KEY_OAI
        cache = self.home / "cache"
        self.files = dream.JudgeFiles(cache=str(cache / "dream-judge.json"),
                                      journal=str(cache / "dream-judge-journal.jsonl"),
                                      health=str(cache / "dream-judge-health.json"),
                                      manifest=str(cache / "dream-judge-export-manifest.json"))
        self.set_judge()

    def set_judge(self, **judge):
        cfg = {"mode": "shadow", "providers": ["jev", "openai"], "send_from": "trusted", "send_sources": ["telegram"]}
        cfg.update(judge)
        self.configure(read=True, promote=[f"telegram:{CHAT_A}"], judge=cfg)
        dream.SETTINGS.LOCAL_TZ = ZONE

    def judged_night(self, live=True, deadline=None, replay=None):
        return dream.run(14, 60, 60, str(self.mem_md), asked_state_path=self.paths["asked"],
                         rejected_state_path=self.paths["rejected"], seen_state_path=self.paths["seen"],
                         snapshot_state_path=self.paths["snapshot"],
                         judge=dream.JudgeRun(self.files, live, deadline, replay))

    def journal(self):
        try:
            text = Path(self.files.journal).read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def pair_lines(self):
        return [line for line in self.journal() if line.get("kind") == "pair"]

    def fact_id(self, content):
        con = sqlite3.connect(self.home / "memory_store.db")
        try:
            return con.execute("select fact_id from facts where content = ?", (content,)).fetchone()[0]
        finally:
            con.close()

    def forget(self, fact_id):
        con = sqlite3.connect(self.home / "memory_store.db")
        con.execute("create table if not exists forgotten (fact_id integer primary key, forgotten_at text)")
        con.execute("insert or replace into forgotten values (?, ?)", (fact_id, FROZEN.isoformat()))
        con.commit()
        con.close()

    def candidates(self):
        """{PairId: message} of the pairs the rules count tonight (both paths)."""
        inputs = dream.load_inputs(14, 60, str(self.mem_md), rejected_state_path=self.paths["rejected"], seen={},
                                   now=self.clock)
        d = dream._decide(inputs)
        messages = {m["key"]: m for m in inputs.messages}
        return {p: messages[p.message_key] for path in d.counted.values() for p in path}

    def veto_facts(self, *contents):
        """The server vetoes (unrelated, 0.9) every pair of these facts and confirms the rest."""
        self.server.verdict = lambda fact, words: ("unrelated", 0.9) if fact in contents else ("confirms", 0.9)


# ---------------------------------------------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------------------------------------------

class ConfigTests(unittest.TestCase):
    def test_the_judge_is_off_by_default_and_would_send_nothing(self):
        js = dream.judge_settings(dream.DEFAULT_CONFIG["judge"])
        self.assertEqual((js.mode, js.problem), ("off", None))
        self.assertFalse(js.send_trusted)
        self.assertEqual((js.send_from, js.send_sources, js.send_fact_stores), (frozenset(), frozenset(), frozenset()))
        self.assertFalse(js.send_no_chat_sessions)

    def test_a_section_that_breaks_the_schema_turns_the_judge_off_and_says_why(self):
        good = {"mode": "shadow", "providers": ["jev"], "send_from": "trusted", "send_sources": ["telegram"]}
        for change, word in (({"base_url": "https://example.org"}, "base_url"), ({"mode": "on"}, "mode"),
                             ({"providers": []}, "providers"), ({"providers": ["gpt"]}, "providers"),
                             ({"providers": ["jev", "jev"]}, "providers"), ({"tau": 0.3}, "tau"),
                             ({"tau": True}, "tau"), ({"tau": "0.5"}, "tau"), ({"tau": 1.5}, "tau"),
                             ({"send_from": True}, "send_from"), ({"send_from": "all"}, "send_from"),
                             ({"send_from": [""]}, "send_from"), ({"send_from": [True]}, "send_from"),
                             ({"send_sources": "telegram"}, "send_sources"),
                             ({"send_fact_stores": "telegram:-1"}, "send_fact_stores"),
                             ({"send_no_chat_sessions": "yes"}, "send_no_chat_sessions"),
                             ({"max_attempts": -1}, "max_attempts"), ({"max_attempts": 2.5}, "max_attempts"),
                             ({"max_cost_usd": -0.01}, "max_cost_usd"), ({"max_cost_usd": True}, "max_cost_usd")):
            with self.subTest(change=change):
                js = dream.judge_settings(dict(dream.DEFAULT_CONFIG["judge"], **dict(good, **change)))
                self.assertEqual(js.mode, "off")
                self.assertIn(word, js.problem or "")
        self.assertEqual(dream.judge_settings("shadow").mode, "off")
        self.assertIsNone(dream.judge_settings(dict(dream.DEFAULT_CONFIG["judge"], **good)).problem)

    def test_a_good_section_is_read_as_written(self):
        js = dream.judge_settings(dict(dream.DEFAULT_CONFIG["judge"], mode="veto", providers=["jev", "openai"],
                                       tau=0.6, send_from=[-100111, "555"], send_sources=["telegram", "whatsapp"],
                                       send_fact_stores=[f"telegram:{CHAT_A}"], send_no_chat_sessions=True,
                                       max_attempts=10, max_cost_usd=0.01))
        self.assertIsNone(js.problem)
        self.assertEqual((js.mode, js.providers, js.tau), ("veto", ("jev", "openai"), 0.6))
        self.assertFalse(js.send_trusted)
        self.assertEqual(js.send_from, frozenset({"-100111", "555"}))
        self.assertEqual(js.send_fact_stores, frozenset({KEY_A}))
        self.assertEqual((js.send_no_chat_sessions, js.max_attempts, js.max_cost_usd), (True, 10, 0.01))

    def test_the_providers_are_the_two_https_endpoints_of_phase_0(self):
        J = dream._load_judge()
        self.assertEqual(set(J.PROVIDERS), set(dream.JUDGE_PROVIDERS))
        self.assertEqual((J.PROVIDERS["jev"]["url"], J.PROVIDERS["jev"]["model"], J.PROVIDERS["jev"]["env"]),
                         ("https://openrouter.ai/api/alpha/decisions", "typesafe/jev-1.14", "OPENROUTER_API_KEY"))
        self.assertEqual((J.PROVIDERS["openai"]["url"], J.PROVIDERS["openai"]["model"], J.PROVIDERS["openai"]["env"]),
                         ("https://api.openai.com/v1/decisions", "gpt-6-luna", "OPENAI_API_KEY"))


# ---------------------------------------------------------------------------------------------------------------
# Off
# ---------------------------------------------------------------------------------------------------------------

class OffTests(JudgeHome):
    def test_off_loads_no_judge_and_sends_nothing(self):
        self.set_judge(mode="off")
        with mock.patch.object(dream, "_load_judge", side_effect=AssertionError("the judge was loaded")):
            result = self.judged_night()
        self.assertEqual(self.server.requests, [])
        self.assertNotIn("judge", result["stats"])
        for path in self.files:
            self.assertFalse(os.path.exists(path), path)

    def test_a_night_off_deletes_the_journal_and_the_health_and_keeps_the_cache_and_the_manifest(self):
        self.judged_night()
        Path(self.files.manifest).write_text('{"entries": {}}', encoding="utf-8")
        for path in self.files:
            self.assertTrue(os.path.exists(path), path)
        self.set_judge(mode="off")
        with mock.patch.object(dream, "_load_judge", side_effect=AssertionError("the judge was loaded")):
            self.judged_night()
        self.assertFalse(os.path.exists(self.files.journal))
        self.assertFalse(os.path.exists(self.files.health))
        self.assertTrue(os.path.exists(self.files.cache))
        self.assertTrue(os.path.exists(self.files.manifest))

    def test_a_broken_section_reports_why_the_judge_is_off(self):
        self.set_judge(mode="shadow", tau=0.2)
        result = self.judged_night()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(result["stats"]["judge"]["mode"], "off")
        self.assertIn("tau", result["stats"]["judge"]["problem"])


# ---------------------------------------------------------------------------------------------------------------
# What may leave the machine (plan §3: one test per row of the table)
# ---------------------------------------------------------------------------------------------------------------

def _msg(**kw):
    m = {"content": "Гоша тренирует юниоров клуба по средам", "source": "telegram", "chat_type": "group",
         "chat_id": FAMILY_CHAT, "thread_id": None, "session_id": "s-1", "session_found": True,
         "locally_trusted": True, "own_only": False}
    m.update(kw)
    return m


FACT = {"fact_id": 1, "content": PROMOTED}
STORE_FACT = {"fact_id": f"{KEY_A}#1", "content": CAT, "store": KEY_A}


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self._settings = dream.SETTINGS
        dream.configure(dream._deep_merge(self._settings.CONFIG, {}))
        dream.SETTINGS.TRUSTED_CHAT_IDS = {FAMILY_CHAT}
        dream.SETTINGS.TRUST_PRIVATE = True
        dream.SETTINGS.TRUST_NO_CHAT = True

    def tearDown(self):
        dream.SETTINGS = self._settings

    def js(self, **kw):
        cfg = {"mode": "shadow", "providers": ["jev"], "send_from": "trusted", "send_sources": ["telegram"]}
        cfg.update(kw)
        js = dream.judge_settings(dict(dream.DEFAULT_CONFIG["judge"], **cfg))
        self.assertIsNone(js.problem)
        return js

    def allowed(self, fact, m, **kw):
        return dream.judge_permission(fact, m, self.js(**kw))

    def test_nothing_of_unknown_origin_leaves(self):
        for m in (_msg(session_found=False), _msg(session_id=""), _msg(session_id=None), _msg(source=""),
                  _msg(source=None)):
            with self.subTest(m=m):
                self.assertIsNotNone(self.allowed(FACT, m))
        self.assertIsNotNone(self.allowed(FACT, _msg(source="whatsapp")))  # a source not in send_sources
        self.assertIsNone(self.allowed(FACT, _msg(source="whatsapp"), send_sources=["telegram", "whatsapp"]))

    def test_a_message_of_a_promoted_chat_leaves_only_by_its_chat_id_and_its_store(self):
        own = _msg(chat_id=CHAT_A, locally_trusted=False, own_only=True)
        self.assertIsNotNone(self.allowed(STORE_FACT, own))  # "trusted" does not reach a chat outside the list
        self.assertIsNotNone(self.allowed(STORE_FACT, own, send_fact_stores=[KEY_A]))
        self.assertIsNotNone(self.allowed(STORE_FACT, own, send_from=[CHAT_A]))  # the store's text is not listed
        self.assertIsNone(self.allowed(STORE_FACT, own, send_from=[CHAT_A], send_fact_stores=[KEY_A]))
        user_scope = _msg(chat_id="", chat_type="", locally_trusted=False, own_only=True)
        self.assertIsNotNone(self.allowed(STORE_FACT, user_scope, send_from=[CHAT_A], send_fact_stores=[KEY_A]))

    def test_a_trusted_chat_leaves_with_trusted_or_by_its_id(self):
        m = _msg()
        self.assertIsNone(self.allowed(FACT, m))
        self.assertIsNone(self.allowed(FACT, m, send_from=[FAMILY_CHAT]))
        self.assertIsNotNone(self.allowed(FACT, m, send_from=["555"]))
        self.assertIsNotNone(self.allowed(FACT, m, send_from=[]))

    def test_a_private_chat_leaves_with_trusted_or_by_its_id(self):
        m = _msg(chat_type="dm", chat_id="555")
        self.assertIsNone(self.allowed(FACT, m))
        self.assertIsNone(self.allowed(FACT, m, send_from=["555"]))
        self.assertIsNotNone(self.allowed(FACT, m, send_from=[FAMILY_CHAT]))
        dream.SETTINGS.TRUST_PRIVATE = False
        self.assertIsNotNone(self.allowed(FACT, _msg(chat_type="private", chat_id="555", locally_trusted=False)))

    def test_a_session_without_a_chat_leaves_only_when_asked_for_by_name(self):
        m = _msg(chat_type="", chat_id="")
        self.assertIsNotNone(self.allowed(FACT, m))
        self.assertIsNotNone(self.allowed(FACT, _msg(chat_type=None, chat_id=None), send_from=[FAMILY_CHAT]))
        self.assertIsNone(self.allowed(FACT, m, send_no_chat_sessions=True))

    def test_a_chat_type_without_a_chat_id_never_leaves(self):
        for ctype in ("dm", "private", "thread", "group"):
            with self.subTest(ctype=ctype):
                m = _msg(chat_type=ctype, chat_id="")
                self.assertIsNotNone(self.allowed(FACT, m, send_no_chat_sessions=True))

    def test_an_untrusted_message_never_leaves(self):
        m = _msg(chat_id=EXTERNAL_CHAT, locally_trusted=False)
        self.assertIsNotNone(self.allowed(FACT, m, send_from=[EXTERNAL_CHAT]))

    def test_the_text_of_a_chat_store_fact_leaves_only_from_a_listed_store(self):
        m = _msg()
        self.assertIsNotNone(self.allowed(STORE_FACT, m))
        self.assertIsNone(self.allowed(STORE_FACT, m, send_fact_stores=[f"telegram:{CHAT_A}"]))


class SentPairsTests(JudgeHome):
    def test_in_the_golden_home_only_the_permitted_pairs_leave(self):
        result = self.judged_night()
        facts = {fact for _p, fact, _w in self.server.sent()}
        self.assertEqual(facts, {PROMOTED, RENT, COFFEE, CONFIRMED, TOURNAMENT, MOVING})
        dumped = json.dumps([r["body"] for r in self.server.requests], ensure_ascii=False)
        for text in (CAT, "граммофон", "Барсик", "Фёдор купил", "Собака"):  # chat store, memory, chat, cron, store
            self.assertNotIn(text, dumped)
        st = result["stats"]["judge"]
        self.assertEqual(st["blocked"], {"fact_store": 3})
        self.assertEqual(st["candidates"], st["eligible"] + 3)

        self.server.requests.clear()
        self.set_judge(send_from=[CHAT_A], send_fact_stores=[f"telegram:{CHAT_A}"])
        Path(self.files.cache).unlink()
        self.judged_night()
        self.assertEqual({fact for _p, fact, _w in self.server.sent()}, {CAT})


# ---------------------------------------------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------------------------------------------

class PayloadTests(JudgeHome):
    def test_the_body_is_the_one_measured_in_phase_0(self):
        text = f"Fact: {PROMOTED}\nMessage: Гоша тренирует юниоров"
        self.assertEqual(self.J.payload("jev", PROMOTED, "Гоша тренирует юниоров"), {
            "model": "typesafe/jev-1.14", "state": text,
            "questions": {"rel": {"type": "choice", "instructions": INSTRUCTIONS,
                                  "criteria": {JEV_LETTER[k]: v for k, v in LABELS.items()}}}})
        self.assertEqual(self.J.payload("openai", PROMOTED, "Гоша тренирует юниоров"), {
            "model": "gpt-6-luna", "input": text,
            "questions": [{"type": "choice", "name": "rel", "instructions": INSTRUCTIONS,
                           "choices": [{"value": v, "description": d} for v, d in LABELS.items()]}]})

    def test_only_the_fact_and_the_persons_own_words_leave(self):
        self.msg("Гоша тренирует юниоров клуба по средам в 18:00 в малом зале, фото https://example.org/g7 [photo]", 3)
        self.msg('[Replying to: "Гоша тренирует юниоров клуба по средам"]\n\nГоша тренирует юниоров клуба по средам '
                 "в малом зале, подтверждаю", 5)
        self.judged_night()
        sent = self.server.sent()
        self.assertTrue(sent)
        for r in self.server.requests:
            self.assertEqual(set(r["body"]), {"model", "state", "questions"} if r["path"] == "/jev"
                             else {"model", "input", "questions"})
            text = _sent_text(r["path"], r["body"])
            self.assertEqual(len(text.split("\n")), 2, text)
        words = [w for _p, _f, w in sent]
        for w in words:
            for bad in ("http", "example.org", "[", "]", "Replying", "s-telegram", FAMILY_CHAT, "555", "2026"):
                self.assertNotIn(bad, w)
        self.assertIn("Гоша тренирует юниоров клуба по средам в 18:00 в малом зале, фото", words)
        self.assertIn("Гоша тренирует юниоров клуба по средам в малом зале, подтверждаю", words)
        self.assertIn("Гоша тренирует юниоров по средам в малом зале", words)  # the voice message, unwrapped

    def test_links_without_a_scheme_do_not_leave_either(self):
        # Codex review of 3.3.0: only http(s):// and www. links were taken out.
        self.msg("Гоша тренирует юниоров клуба по средам в малом зале, запись t.me/klub_junior?start=abc и "
                 "klub.example/zapis?token=x9, вопросы mailto:gosha@example.org или tg://resolve?domain=klub", 3)
        self.judged_night()
        words = [w for _p, _f, w in self.server.sent()]
        # A link runs to the next space, as URL_RE's do (its trailing comma goes with it).
        self.assertIn("Гоша тренирует юниоров клуба по средам в малом зале, запись и вопросы или", words)
        for bad in ("t.me", "klub_junior", "start=abc", "klub.example", "token", "mailto", "gosha@", "tg:", "resolve"):
            self.assertFalse(any(bad in w for w in words), bad)
        # A bare domain without a path names a service and carries nothing of a link; an address is not a link.
        for text, want in (("заказали на ozon.ru, как всегда", "заказали на ozon.ru, как всегда"),
                           ("пиши Гоше на gosha@example.org", "пиши Гоше на gosha@example.org"),
                           ("см. Госуслуги.рф/запись и vk.com/klub", "см. и"),
                           ("Node.js и e.g. т.е. 18.30", "Node.js и e.g. т.е. 18.30")):
            with self.subTest(text=text):
                self.assertEqual(dream.judge_texts({"content": PROMOTED}, {"content": text})[1], want)

    def test_a_secret_an_overlong_text_or_a_forgotten_fact_does_not_leave(self):
        self.msg("Гоша тренирует юниоров клуба по средам в малом зале, пароль от зала: Qw7!zz2026", 3)
        self.msg("Гоша тренирует юниоров клуба по средам в 18:00 в малом зале. " + "Очень длинный рассказ. " * 30, 4)
        moving = self.fact_id(MOVING)
        pairs = self.candidates()
        want = (sum("Qw7!zz2026" in m["content"] for m in pairs.values()),
                sum("Очень длинный рассказ" in m["content"] for m in pairs.values()),
                sum(p.fact_id == moving for p in pairs))
        self.assertTrue(all(want), want)  # the rules count every one of them
        self.forget(moving)
        result = self.judged_night()
        dumped = json.dumps([r["body"] for r in self.server.requests], ensure_ascii=False)
        for text in ("Qw7!zz2026", "Очень длинный рассказ", MOVING):
            self.assertNotIn(text, dumped)
        blocked = result["stats"]["judge"]["blocked"]
        self.assertEqual((blocked.get("unsafe"), blocked.get("length"), blocked.get("tombstone")), want)


# ---------------------------------------------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------------------------------------------

class KeyTests(JudgeHome):
    def test_the_key_goes_in_its_providers_header_and_nowhere_else(self):
        self.server.queue["/jev"] = [{"status": 401, "json": {"error": f"bad key {KEY_JEV}"}}]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = self.judged_night()
        self.assertTrue(self.server.requests)
        for r in self.server.requests:
            want = KEY_JEV if r["path"] == "/jev" else KEY_OAI
            self.assertEqual(r["headers"]["authorization"], f"Bearer {want}")
            self.assertNotIn(want, json.dumps(r["body"]))
        written = [json.dumps(result, ensure_ascii=False), out.getvalue(), err.getvalue()]
        written += [Path(p).read_text(encoding="utf-8") for p in self.files if os.path.exists(p)]
        written += [Path(p).read_text(encoding="utf-8") for p in self.paths.values() if os.path.exists(p)]
        for text in written:
            for key in (KEY_JEV, KEY_OAI, KEY_JEV[-10:], KEY_OAI[-10:]):
                self.assertNotIn(key, text)

    def test_the_key_comes_from_the_hermes_env_file_when_the_environment_has_none(self):
        # Hermes strips provider keys from the environment of cron scripts; its .env holds them.
        del os.environ["OPENROUTER_API_KEY"]
        (self.home / ".env").write_text(f'OTHER_TOKEN=nope\nexport OPENROUTER_API_KEY="{KEY_JEV}"\r\n',
                                        encoding="utf-8")
        self.judged_night()
        jev = [r for r in self.server.requests if r["path"] == "/jev"]
        self.assertTrue(jev)
        self.assertEqual({r["headers"]["authorization"] for r in jev}, {f"Bearer {KEY_JEV}"})

    def test_without_a_key_a_provider_is_off_for_the_night(self):
        del os.environ["OPENROUTER_API_KEY"]
        result = self.judged_night()
        self.assertEqual({p for p, _f, _w in self.server.sent()}, {"/openai"})
        st = result["stats"]["judge"]
        self.assertEqual(st["outcomes"]["jev"], {"no_key": 1})
        self.assertEqual(st["effective"], "shadow")

        del os.environ["OPENAI_API_KEY"]
        self.server.requests.clear()
        Path(self.files.cache).unlink()
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(self.server.requests, [])
        self.assertEqual((st["effective"], st["stop_reason"]), ("degraded", "providers_off"))


# ---------------------------------------------------------------------------------------------------------------
# Failures of a provider
# ---------------------------------------------------------------------------------------------------------------

class FailureTests(JudgeHome):
    def test_a_server_error_is_tried_again(self):
        self.server.queue["/jev"] = [{"status": 500, "json": {}}, {"status": 503, "json": {}}]
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(self.server.sent("/openai"), [])
        self.assertEqual(st["attempts"], st["sent"] + 2)
        self.assertEqual(st["outcomes"]["jev"].get("ok"), st["sent"])

    def test_a_pair_the_primary_cannot_answer_goes_once_to_the_fallback(self):
        self.server.queue["/jev"] = [{"status": 429, "json": {}}] * 3
        st = self.judged_night()["stats"]["judge"]
        first = self.server.requests[0]["body"]["state"]
        self.assertEqual([r["path"] for r in self.server.requests[:4]], ["/jev"] * 3 + ["/openai"])
        self.assertEqual(_split(self.server.requests[3]["body"]["input"]), _split(first))
        self.assertEqual(len(self.server.sent("/openai")), 1)  # the next pairs go to the primary again
        self.assertEqual(st["outcomes"]["jev"]["fail_net"], 1)
        both = [line for line in self.pair_lines() if (line["fact"], line["words"]) == _split(first)]
        self.assertEqual([(line["provider"], line["outcome"]) for line in both], [("jev", "fail_net"),
                                                                                  ("openai", "ok")])

    def test_a_refused_key_turns_the_provider_off_for_the_rest_of_the_night(self):
        self.server.queue["/jev"] = [{"status": 401, "json": {}}]
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(len(self.server.sent("/jev")), 1)
        self.assertEqual(len(self.server.sent("/openai")), st["eligible"])
        self.assertEqual(st["outcomes"]["jev"], {"fail_auth": 1})
        self.assertEqual(st["effective"], "shadow")

    def test_a_redirect_is_not_followed(self):
        self.server.queue["/jev"] = [{"status": 302, "headers": {"Location": f"{self.base}/elsewhere"}, "raw": b""}]
        self.judged_night()
        self.assertNotIn("/elsewhere", {r["path"] for r in self.server.requests})
        self.assertEqual(len(self.server.sent("/openai")), 1)

    def test_an_answer_that_breaks_the_schema_goes_to_the_fallback(self):
        bad = jev_answer("unrelated")
        bad["answers"]["rel"]["probabilities"]["A"] = 0.5  # the probabilities no longer sum to 1
        self.server.queue["/jev"] = [{"raw": b"not json"}, {"json": bad}, {"raw": b"{" + b" " * 70000 + b"}"}]
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(st["outcomes"]["jev"]["unknown"], 3)
        self.assertEqual(len(self.server.sent("/openai")), 3)

    def test_an_answer_of_the_wrong_types_goes_to_the_fallback_and_the_night_goes_on(self):
        # Valid JSON, wrong types: Jev's API is an alpha (Codex review of 3.3.0 — a list here took the night down).
        listed = jev_answer("unrelated")
        listed["answers"]["rel"]["choice"] = ["C"]
        keyed = jev_answer("unrelated")
        keyed["answers"]["rel"]["probabilities"] = [{"value": ["A"], "probability": 0.05},
                                                    {"value": "B", "probability": 0.05},
                                                    {"value": "C", "probability": 0.9}]
        mapped = jev_answer("unrelated")
        mapped["answers"]["rel"]["choice"] = {"C": 1}
        self.server.queue["/jev"] = [{"json": listed}, {"json": keyed}, {"json": mapped}]
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(st["outcomes"]["jev"]["unknown"], 3)
        self.assertEqual(len(self.server.sent("/openai")), 3)
        self.assertEqual(st["judged"], st["eligible"])

    def test_the_validator_reads_any_json_without_failing(self):
        J = self.J
        cases = []
        for value in ([], ["A"], {"A": 1}, None, 1, True):
            jev = jev_answer("unrelated")
            jev["answers"]["rel"]["choice"] = value
            oa = openai_answer("unrelated")
            oa["answers"][0]["choice"] = value
            oa_value = openai_answer("unrelated")
            oa_value["answers"][0]["probabilities"][0]["value"] = value
            jev_list = jev_answer("unrelated")
            jev_list["answers"]["rel"]["probabilities"] = [{"value": value, "probability": 0.9}]
            cases += [("jev", jev), ("openai", oa), ("openai", oa_value), ("jev", jev_list)]
        for provider, data in cases:
            with self.subTest(provider=provider, data=data):
                self.assertIsNone(J.validate(provider, data)[0])
                self.assertIsNone(J.replay_answer({"response": data}, provider))
        for label in ([], {"x": 1}, None, 3):
            with self.subTest(label=label):
                self.assertNotEqual(J.check_answer(label, 0.9, {k: 1 / 3 for k in LABELS}), "ok")

    def test_a_judge_that_fails_unexpectedly_costs_no_night(self):
        self.set_judge(mode="veto")
        self.veto_facts(RENT)
        err = io.StringIO()
        with mock.patch.object(self.J, "judge", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(err):
            result = self.judged_night()
        st = result["stats"]["judge"]
        self.assertEqual((st["effective"], st["vetoes"], st["judged"]), ("degraded", 0, 0))
        self.assertIn("RuntimeError", st["degraded"])
        self.assertIn("cloud judge failed (RuntimeError)", err.getvalue())
        rent = self.fact_id(RENT)
        self.assertGreater(next(c["mentions"] for c in result["conflicts"] if c["fact_id"] == rent), 0)

    def test_the_validator_takes_only_a_well_formed_answer(self):
        J = self.J
        self.assertEqual(J.validate("jev", jev_answer("unrelated", 0.8))[0:2], ("unrelated", 0.8))
        self.assertEqual(J.validate("openai", openai_answer("contradicts", 0.7))[0:2], ("contradicts", 0.7))
        refusal = {"answers": {"rel": {"type": "refusal", "refusal": "no"}}}
        two = jev_answer("confirms")
        two["answers"]["other"] = two["answers"]["rel"]
        argmax = jev_answer("unrelated", 0.4)
        argmax["answers"]["rel"]["probabilities"] = {"A": 0.5, "B": 0.1, "C": 0.4}
        nan = jev_answer("unrelated")
        nan["answers"]["rel"]["confidence"] = float("nan")
        wide = jev_answer("unrelated")
        wide["answers"]["rel"]["confidence"] = 1.2
        letter = jev_answer("unrelated")
        letter["answers"]["rel"]["choice"] = "D"
        for name, data in (("refusal", refusal), ("two questions", two), ("not the argmax", argmax),
                           ("nan", nan), ("over 1", wide), ("no such label", letter), ("empty", {}),
                           ("a list", [])):
            with self.subTest(name=name):
                self.assertIsNone(J.validate("jev", data)[0])
        oa = openai_answer("unrelated")
        oa["answers"].append(dict(oa["answers"][0]))
        self.assertIsNone(J.validate("openai", oa)[0])

    def test_proxy_settings_do_not_reroute_the_judge(self):
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            os.environ[name] = "http://127.0.0.1:9"
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
        # A night's process starts with these in its environment: the judge is loaded afresh under them.
        self.addCleanup(setattr, dream, "_JUDGE", dream._JUDGE)
        fresh = dream._load_judge(str(Path(dream.__file__).with_name("dream_judge.py")))
        for name in ("jev", "openai"):
            fresh.PROVIDERS[name]["url"] = self.J.PROVIDERS[name]["url"]
        fresh.RETRY_BACKOFF = (0, 0)
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(st["outcomes"]["jev"].get("ok"), st["eligible"])

    def test_a_hanging_provider_stops_at_the_deadline_and_no_veto_is_applied(self):
        self.set_judge(mode="veto")
        self.veto_facts(PROMOTED, MOVING)
        for path in ("/jev", "/openai"):
            self.server.queue[path] = [{"delay": 5, "json": {}}] * 3
        started = time.monotonic()
        result = self.judged_night(deadline=time.time() + 92)  # leaves the judge 2 s
        self.assertLess(time.monotonic() - started, 12)
        st = result["stats"]["judge"]
        self.assertEqual((st["stop_reason"], st["effective"]), ("deadline", "degraded"))
        self.assertIn(self.fact_id(PROMOTED), [p["fact_id"] for p in result["promotions"]])

    def test_without_time_left_nothing_is_sent(self):
        st = self.judged_night(deadline=time.time() + 60)["stats"]["judge"]
        self.assertEqual(self.server.requests, [])
        self.assertEqual(st["stop_reason"], "deadline")


# ---------------------------------------------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------------------------------------------

class MoneyTests(JudgeHome):
    def test_attempts_are_capped(self):
        self.set_judge(max_attempts=3)
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual((st["attempts"], st["stop_reason"]), (3, "attempts"))

    def test_spending_stops_at_the_cap_by_what_the_provider_reports(self):
        self.set_judge(max_cost_usd=0.025)
        self.server.answer = lambda path, body: {"json": jev_answer("confirms", cost=0.01)}
        st = self.judged_night()["stats"]["judge"]
        self.assertEqual(len(self.server.requests), 3)  # 0.01, 0.02, then 0.03: the cap is soft by one call
        self.assertEqual((st["cost_usd"], st["stop_reason"]), (0.03, "cost"))

    def test_an_answer_without_a_price_counts_the_reserve(self):
        self.server.answer = lambda path, body: {"json": jev_answer("confirms", cost=None)}
        st = self.judged_night()["stats"]["judge"]
        self.assertAlmostEqual(st["cost_usd"], 0.0002 * st["attempts"], places=6)


# ---------------------------------------------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------------------------------------------

class CacheTests(JudgeHome):
    def next_night(self):
        self.clock = self.clock + timedelta(days=1)
        self.server.requests.clear()
        return self.judged_night()

    def test_a_second_night_reuses_the_verdicts(self):
        first = self.judged_night()["stats"]["judge"]
        self.assertGreater(first["sent"], 0)
        st = self.next_night()["stats"]["judge"]
        self.assertEqual(self.server.requests, [])
        self.assertEqual((st["cache_hits"], st["sent"]), (first["eligible"], 0))

    def test_an_expired_verdict_is_asked_again(self):
        self.judged_night()
        cache = json.loads(Path(self.files.cache).read_text(encoding="utf-8"))
        key = sorted(cache["entries"])[0]
        cache["entries"][key]["at"] = (self.clock - timedelta(days=91)).isoformat()
        Path(self.files.cache).write_text(json.dumps(cache), encoding="utf-8")
        st = self.next_night()["stats"]["judge"]
        self.assertEqual(st["sent"], 1)

    def test_a_verdict_belongs_to_one_pair_and_one_text(self):
        pair = dream.PairId(1, "a" * 32)
        key = self.J.cache_key(pair, PROMOTED, "слова")
        self.assertEqual(key, self.J.cache_key(dream.PairId(1, "a" * 32), PROMOTED, "слова"))
        for other in (self.J.cache_key(dream.PairId(2, "a" * 32), PROMOTED, "слова"),
                      self.J.cache_key(dream.PairId(1, "b" * 32), PROMOTED, "слова"),
                      self.J.cache_key(pair, PROMOTED + " Изменено.", "слова"),
                      self.J.cache_key(pair, PROMOTED, "другие слова")):
            self.assertNotEqual(key, other)

    def test_a_cached_verdict_counts_only_for_a_pair_that_may_be_sent_tonight(self):
        self.set_judge(mode="veto")
        self.veto_facts(RENT)
        first = self.judged_night()
        rent = self.fact_id(RENT)
        mentions = lambda r: next(c["mentions"] for c in r["conflicts"] if c["fact_id"] == rent)  # noqa: E731
        self.assertEqual((first["stats"]["judge"]["vetoes"], mentions(first)), (3, 0))  # two in the group, one dm
        self.set_judge(mode="veto", send_from=["555"])  # the group's pairs may not leave any more
        self.clock = self.clock + timedelta(days=1)
        self.server.requests.clear()
        Path(self.paths["seen"]).unlink()  # the conflict is shown again tonight
        second = self.judged_night()
        self.assertEqual(self.server.requests, [])
        self.assertEqual((second["stats"]["judge"]["vetoes"], mentions(second)), (1, 2))  # the dm's verdict only

    def test_a_cache_of_the_wrong_shape_is_read_around_and_written_anew(self):
        # A JSON object, but not the cache's: the night asks again and leaves a good cache behind.
        at = self.clock.isoformat()
        bad_entry = {"provider": "jev", "label": ["unrelated"], "confidence": 0.9, "probs": {}, "at": at,
                     "fact_ref": "1"}
        for cache in ({"version": 1, "entries": []}, {"version": 1, "entries": "x"},
                      {"version": 1, "entries": {"k": bad_entry, "j": ["not", "a", "record"]}}):
            with self.subTest(cache=cache):
                Path(self.files.cache).write_text(json.dumps(cache), encoding="utf-8")
                self.server.requests.clear()
                st = self.judged_night()["stats"]["judge"]
                self.assertEqual((st["cache_hits"], st["sent"], st["judged"]), (0, st["eligible"], st["eligible"]))
                written = json.loads(Path(self.files.cache).read_text(encoding="utf-8"))
                self.assertEqual(len(written["entries"]), st["eligible"])
                self.assertNotIn("k", written["entries"])

    def test_a_verdict_of_a_provider_no_longer_configured_is_not_used(self):
        self.judged_night()
        self.set_judge(providers=["openai"])
        st = self.next_night()["stats"]["judge"]
        self.assertEqual(st["cache_hits"], 0)
        self.assertEqual(len(self.server.sent("/openai")), st["eligible"])


# ---------------------------------------------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------------------------------------------

class ModeTests(JudgeHome):
    def snapshot(self):
        return {name: (Path(p).read_bytes() if os.path.exists(p) else None) for name, p in self.paths.items()}

    def restore(self, snap):
        for name, data in snap.items():
            path = Path(self.paths[name])
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)

    def test_shadow_changes_nothing_but_the_judges_own_report(self):
        self.veto_facts(PROMOTED, MOVING, CONFIRMED, RENT, COFFEE, TOURNAMENT)
        before = self.snapshot()
        shadow = self.judged_night()
        shadow_state = self.snapshot()
        self.assertGreater(shadow["stats"]["judge"]["vetoes"], 0)
        self.assertGreater(shadow["stats"]["judge"]["decisions_changed"], 0)
        self.restore(before)
        self.set_judge(mode="off")
        off = self.judged_night()
        self.assertEqual(self.snapshot(), shadow_state)
        judge = shadow["stats"].pop("judge")
        self.assertEqual(judge["mode"], "shadow")
        lines = shadow["diary"].splitlines(keepends=True)
        diary = [line for line in lines if not line.startswith("Judge")]
        self.assertEqual(len(diary), len(lines) - 1)
        shadow["diary"] = "".join(diary)
        self.assertEqual(shadow, off)

    def test_veto_removes_exactly_the_vetoed_pairs(self):
        rules = self.judged_night(live=False)  # cache-only, nothing cached: the rules alone
        self.set_judge(mode="veto")
        self.veto_facts(MOVING)
        self.restore({name: None for name in self.paths if name != "rejected"})
        result = self.judged_night()
        moving = self.fact_id(MOVING)
        self.assertIn(moving, [p["fact_id"] for p in rules["promotions"]])
        self.assertNotIn(moving, [p["fact_id"] for p in result["promotions"]])
        self.assertNotIn(moving, [c["fact_id"] for c in result["confirm"]])
        st = result["stats"]["judge"]
        self.assertEqual((st["effective"], st["vetoes"]), ("veto", 2))
        others = lambda r: [p["fact_id"] for p in r["promotions"] if p["fact_id"] != moving]  # noqa: E731
        self.assertEqual(others(result), others(rules))
        for line in self.pair_lines():
            self.assertEqual(line["veto"], line["fact"] == MOVING)
            if line["fact"] == MOVING:
                self.assertIn("promotions", line["fact_changed"])

    def test_a_night_the_judge_could_not_cover_applies_no_vetoes(self):
        # Both providers answer only for the two pairs that decide a promotion, and fail on every other pair.
        self.set_judge(mode="veto")

        def answer(path, body):
            if _split(_sent_text(path, body))[0] != MOVING:
                return {"status": 503, "json": {}}
            return {"json": jev_answer("unrelated") if path == "/jev" else openai_answer("unrelated")}

        self.server.answer = answer
        result = self.judged_night()
        st = result["stats"]["judge"]
        self.assertEqual((st["effective"], st["vetoes"], st["judged"]), ("degraded", 2, 2))
        self.assertGreater(st["eligible"], 4)
        self.assertIn(self.fact_id(MOVING), [p["fact_id"] for p in result["promotions"]])

    def test_an_answer_under_tau_takes_nothing_out(self):
        self.set_judge(mode="veto", tau=0.6)
        self.server.verdict = lambda fact, words: (("unrelated", 0.55) if fact == MOVING else
                                                    ("unrelated", 0.65) if fact == COFFEE else ("confirms", 0.9))
        result = self.judged_night()
        self.assertIn(self.fact_id(MOVING), [p["fact_id"] for p in result["promotions"]])
        lines = self.pair_lines()
        self.assertEqual({line["veto"] for line in lines if line["fact"] == MOVING}, {False})
        self.assertEqual({line["veto"] for line in lines if line["fact"] == COFFEE}, {True})
        self.assertEqual(result["stats"]["judge"]["vetoes"], sum(line["fact"] == COFFEE for line in lines))

    def test_the_counterfactual_night_never_reaches_the_network(self):
        real = dream._decide
        calls = []

        def guarded(*args, **kwargs):
            calls.append(1)
            with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network in _decide")):
                return real(*args, **kwargs)

        self.veto_facts(MOVING)
        with mock.patch.object(dream, "_decide", guarded):
            st = self.judged_night()["stats"]["judge"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(st["vetoes"], 2)

    def test_a_dry_run_reads_the_cache_and_writes_nothing_of_the_judge(self):
        self.set_judge(mode="veto")
        self.veto_facts(MOVING)
        self.judged_night()
        files = {p: Path(p).read_bytes() for p in self.files if os.path.exists(p)}
        self.server.requests.clear()
        self.clock = self.clock + timedelta(days=1)
        result = self.judged_night(live=False)
        self.assertEqual(self.server.requests, [])
        self.assertEqual({p: Path(p).read_bytes() for p in self.files if os.path.exists(p)}, files)
        self.assertEqual(result["stats"]["judge"]["vetoes"], 2)
        self.assertNotIn(self.fact_id(MOVING), [p["fact_id"] for p in result["promotions"]])

    def test_main_runs_the_dry_run_cache_only_and_the_night_live_with_the_gates_deadline(self):
        seen = []
        with mock.patch.object(dream, "run", side_effect=lambda *a, **kw: seen.append(kw.get("judge")) or {}), \
                mock.patch.object(dream, "dry_run_summary", return_value=""), \
                mock.patch.object(dream, "scrub_result"), mock.patch.object(dream, "forget_sweep", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            dream.main(["--hermes-home", str(self.home), "--dry-run"])
            with contextlib.suppress(Exception):
                dream.main(["--hermes-home", str(self.home), "--deadline", "1800000000", "--out",
                            str(self.home / "cache" / "out.json")])
        self.assertEqual((seen[0].live, seen[0].deadline), (False, None))
        self.assertEqual((seen[1].live, seen[1].deadline), (True, 1800000000.0))
        self.assertEqual(seen[1].files, dream.judge_files())

    def test_explain_does_not_load_the_judge(self):
        with mock.patch.object(dream, "_load_judge", side_effect=AssertionError("the judge was loaded")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dream.explain(self.fact_id(PROMOTED), str(self.mem_md), 60), 0)
        self.assertIn("score", out.getvalue())

    def test_a_replay_takes_the_recorded_answers_without_the_network(self):
        self.set_judge(mode="veto")
        self.veto_facts(MOVING)
        self.judged_night()
        recorded = [{"provider": line["provider"], "fact": line["fact"], "words": line["words"],
                     "response": jev_answer("unrelated" if line["veto"] else "confirms")}
                    for line in self.pair_lines()]
        replay = self.home / "replay.jsonl"
        replay.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recorded), encoding="utf-8")
        for path in self.files:
            Path(path).unlink(missing_ok=True)
        self.server.requests.clear()
        result = self.judged_night(live=False, replay=str(replay))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(result["stats"]["judge"]["vetoes"], 2)
        for path in self.files:
            self.assertFalse(os.path.exists(path), path)
        # Lines of the wrong types are skipped, not fatal: a list where a text goes, a label that is a list.
        odd = [dict(recorded[0], provider=["jev"]), dict(recorded[1], fact={"x": 1}),
               {k: v for k, v in recorded[2].items() if k != "response"} | {"label": ["unrelated"],
                                                                            "confidence": 0.9, "probs": {}}]
        replay.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in odd + recorded[3:]),
                          encoding="utf-8")
        st = self.judged_night(live=False, replay=str(replay))["stats"]["judge"]
        self.assertEqual(st["judged"], len(recorded) - 3)


# ---------------------------------------------------------------------------------------------------------------
# The journal and forgetting
# ---------------------------------------------------------------------------------------------------------------

class JournalTests(JudgeHome):
    def test_every_sent_pair_is_journalled_as_it_was_sent(self):
        self.veto_facts(COFFEE)
        self.judged_night()
        lines = self.pair_lines()
        sent = self.server.sent()
        self.assertEqual(sorted((line["fact"], line["words"]) for line in lines),
                         sorted((f, w) for _p, f, w in sent))
        for line in lines:
            self.assertEqual(line["fact_refs"], [str(line["pair"][0])])
            self.assertEqual(line["provider"], "jev")
            self.assertEqual(line["veto"], line["fact"] == COFFEE)
            self.assertTrue(set(line["paths"]) <= {"promotion", "confirm"})
        nights = [line for line in self.journal() if line.get("kind") == "night"]
        self.assertEqual(len(nights), 1)
        self.assertEqual(nights[0]["paths"]["promotion"]["judged"], nights[0]["paths"]["promotion"]["eligible"])
        if os.name == "posix":
            for path in (self.files.journal, self.files.cache, self.files.health):
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_entries_leave_the_journal_after_90_days(self):
        self.judged_night()
        lines = self.journal()
        old = dict(lines[0], night=(self.clock - timedelta(days=91)).isoformat(), words="старая запись")
        Path(self.files.journal).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in [old] + lines)
                                            + '{"kind": "pair", "fact": "обрыв записи', encoding="utf-8")
        self.clock = self.clock + timedelta(days=1)
        self.judged_night()
        texts = [line.get("words") for line in self.journal()]
        self.assertNotIn("старая запись", texts)
        self.assertEqual(len(self.journal()), len(lines) + 1)  # yesterday's pairs and nights, tonight's night

    def test_a_forgotten_fact_leaves_every_file_of_the_judge_even_with_the_judge_off(self):
        self.judged_night()
        promoted = self.fact_id(PROMOTED)
        cache = json.loads(Path(self.files.cache).read_text(encoding="utf-8"))
        Path(self.files.manifest).write_text(json.dumps({"entries": {
            "a1": {"fact_ref": str(promoted)}, "b2": {"fact_ref": str(self.fact_id(RENT))}}}), encoding="utf-8")
        self.assertIn(str(promoted), {e["fact_ref"] for e in cache["entries"].values()})
        self.set_judge(mode="off")
        self.forget(promoted)
        with mock.patch.object(dream, "_load_judge", side_effect=AssertionError("the judge was loaded")):
            summary = dream.forget_sweep()
        self.assertGreater(summary["judge"], 0)
        for path in (self.files.cache, self.files.journal, self.files.manifest):
            self.assertNotIn(PROMOTED, Path(path).read_text(encoding="utf-8"))
        cache = json.loads(Path(self.files.cache).read_text(encoding="utf-8"))
        self.assertTrue(cache["entries"])
        self.assertNotIn(str(promoted), {e["fact_ref"] for e in cache["entries"].values()})
        lines = self.journal()
        self.assertTrue(self.pair_lines())
        for line in lines:
            self.assertNotIn(str(promoted), line["fact_refs"])
            for change in (line.get("diff") or {}).values():
                self.assertNotIn(promoted, change["removed"] + change["added"])
        self.assertEqual(list(json.loads(Path(self.files.manifest).read_text())["entries"]), ["b2"])

    def test_a_fact_forgotten_while_the_night_ran_is_not_written(self):
        promoted = self.fact_id(PROMOTED)
        verdict = self.server.verdict

        def forget_during(fact, words):
            self.forget(promoted)
            return verdict(fact, words)

        self.server.verdict = forget_during
        self.judged_night()
        self.assertTrue(self.server.sent())
        for line in self.journal():
            self.assertNotIn(str(promoted), line.get("fact_refs", []))
            self.assertNotEqual(line.get("fact"), PROMOTED)
        cache = json.loads(Path(self.files.cache).read_text(encoding="utf-8"))
        self.assertNotIn(str(promoted), {e["fact_ref"] for e in cache["entries"].values()})


# ---------------------------------------------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------------------------------------------

class HealthTests(JudgeHome):
    def nights(self, pattern):
        """Run a night per letter (D: degraded — no keys; H: healthy), a day apart; the alerts of each."""
        out = []
        for letter in pattern:
            if letter == "D":
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = KEY_JEV
                os.environ["OPENAI_API_KEY"] = KEY_OAI
            result = self.judged_night()
            self.assertEqual(result["stats"]["judge"]["effective"] == "degraded", letter == "D")
            out.append([a for a in result["alerts"] if a.get("kind") == "judge_degraded"])
            self.clock = self.clock + timedelta(days=1)
        return out

    def test_three_degraded_nights_raise_one_alert_a_week(self):
        alerts = self.nights("DDDD")
        self.assertEqual([len(a) for a in alerts], [0, 0, 1, 0])
        self.assertEqual(alerts[2][0]["nights"], 3)
        self.clock = self.clock + timedelta(days=5)  # a week after the alert
        self.assertEqual([len(a) for a in self.nights("D")], [1])

    def test_two_healthy_nights_in_a_row_reset_the_count(self):
        self.assertEqual([len(a) for a in self.nights("DDHHDD")], [0, 0, 0, 0, 0, 0])
        self.assertEqual([len(a) for a in self.nights("HD")], [0, 1])  # D D H D: one healthy night resets nothing

    def test_a_health_file_of_the_wrong_shape_is_started_again(self):
        Path(self.files.health).write_text(json.dumps({"nights": 5, "alerted_at": ["x"]}), encoding="utf-8")
        result = self.judged_night()
        self.assertEqual(result["stats"]["judge"]["effective"], "shadow")
        health = json.loads(Path(self.files.health).read_text(encoding="utf-8"))
        self.assertEqual([n["degraded"] for n in health["nights"]], [False])

    def test_a_second_run_on_the_same_night_does_not_count_twice(self):
        os.environ.pop("OPENROUTER_API_KEY")
        os.environ.pop("OPENAI_API_KEY")
        for _ in range(3):
            result = self.judged_night()
        self.assertEqual([a for a in result["alerts"] if a.get("kind") == "judge_degraded"], [])


# ---------------------------------------------------------------------------------------------------------------
# The gate's deadline
# ---------------------------------------------------------------------------------------------------------------

class GateDeadlineTests(unittest.TestCase):
    def run_gate(self, precheck_cfg):
        tmp = Path(tempfile.mkdtemp(prefix="judge-gate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "dreaming.json").write_text(json.dumps({"precheck": precheck_cfg}), encoding="utf-8")
        argv = []

        def fake_run(cmd, **kwargs):
            argv.extend(cmd)
            (tmp / "cache").mkdir(exist_ok=True)
            (tmp / "cache" / "dream.json").write_text('{"stats": {}}', encoding="utf-8")
            return mock.Mock(stderr="")

        with mock.patch.dict(os.environ, {"DREAM_CONFIG": str(tmp / "dreaming.json")}), \
                mock.patch.object(precheck, "OUT", tmp / "cache" / "dream.json"), \
                mock.patch.object(precheck.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            started = time.time()
            precheck.main()
        return argv, started

    def test_the_gate_hands_its_deadline_to_the_pass(self):
        argv, started = self.run_gate({"timeout_sec": 600})
        deadline = float(argv[argv.index("--deadline") + 1])
        self.assertAlmostEqual(deadline, started + 600, delta=5)
        argv, _ = self.run_gate({"timeout_sec": 0})
        self.assertNotIn("--deadline", argv)


# ---------------------------------------------------------------------------------------------------------------
# Review: the export, the labels, the rule
# ---------------------------------------------------------------------------------------------------------------

class ExportTests(JudgeHome):
    def test_the_sample_holds_one_pair_per_fact_and_nothing_but_the_texts(self):
        self.veto_facts(COFFEE, MOVING)
        self.judged_night()
        out = self.home / "review.jsonl"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = dream.main(["--hermes-home", str(self.home), "--judge-export", str(out)])
        self.assertEqual(rc, 0)
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        lines = self.pair_lines()
        self.assertEqual(len(rows), len({line["fact_refs"][0] for line in lines}))
        for row in rows:
            self.assertEqual(set(row), {"id", "fact", "message"})
            self.assertIn((row["fact"], row["message"]), {(line["fact"], line["words"]) for line in lines})
        manifest = json.loads(Path(self.files.manifest).read_text(encoding="utf-8"))
        self.assertEqual(manifest["journal_sha256"],
                         hashlib.sha256(Path(self.files.journal).read_bytes()).hexdigest())
        self.assertEqual(set(manifest["entries"]), {row["id"] for row in rows})
        for entry in manifest["entries"].values():
            self.assertEqual(set(entry) >= {"pair", "fact_ref", "paths", "provider", "label", "veto", "changed"}, True)
        again = self.J.export_sample(lines, manifest["seed"])
        self.assertEqual(sorted(again[0], key=lambda r: r["id"]), sorted(rows, key=lambda r: r["id"]))
        if os.name == "posix":
            self.assertEqual(os.stat(self.files.manifest).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)

    def test_scoring_joins_the_labels_and_then_removes_the_manifest(self):
        self.veto_facts(COFFEE, MOVING)
        self.judged_night()
        out = self.home / "review.jsonl"
        with contextlib.redirect_stdout(io.StringIO()):
            dream.main(["--hermes-home", str(self.home), "--judge-export", str(out)])
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        labels = self.home / "labels.jsonl"
        labels.write_text("".join(json.dumps({"id": r["id"], "label": "U" if r["fact"] in (COFFEE, MOVING) else "S"})
                                  + "\n" for r in rows), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            rc = dream.main(["--hermes-home", str(self.home), "--judge-score", str(labels)])
        self.assertEqual(rc, 0)
        report = json.loads(printed.getvalue())
        self.assertEqual((report["vetoes"], report["wrong_vetoes"]), (2, 0))
        self.assertEqual(report["verdict"], "continue")  # two vetoes are far too few to decide
        self.assertFalse(os.path.exists(self.files.manifest))


class InstallCheckTests(unittest.TestCase):
    """`install.py --check` says whether anything leaves the machine, where to, and whether the keys are there —
    never what they are."""

    def setUp(self):
        from test_install import install
        self.install = install
        self.home = Path(tempfile.mkdtemp(prefix="judge-install-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(setattr, dream, "SETTINGS", dream.SETTINGS)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

    def check(self, **judge):
        with contextlib.redirect_stderr(io.StringIO()):
            dream.configure(dream._deep_merge(dream.DEFAULT_CONFIG, {"judge": judge}))
        report = self.install.Report()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.install.check_judge(self.home, dream, report)
        return report, out.getvalue()

    def test_off_says_nothing_leaves(self):
        report, out = self.check()
        self.assertEqual((report.bad_n, report.warn_n), (0, 0))
        self.assertIn("nothing leaves", out)

    def test_on_without_a_key_is_a_problem_and_a_key_is_never_shown(self):
        on = {"mode": "shadow", "providers": ["jev", "openai"], "send_from": "trusted", "send_sources": ["telegram"]}
        report, out = self.check(**on)
        self.assertEqual(report.bad_n, 1)
        self.assertIn("OPENROUTER_API_KEY", out)
        (self.home / ".env").write_text(f"OPENROUTER_API_KEY={KEY_JEV}\n", encoding="utf-8")
        report, out = self.check(**on)
        self.assertEqual(report.bad_n, 0)
        self.assertIn("OPENAI_API_KEY", out)  # the fallback has none: a warning
        self.assertIn("shadow", out)
        for part in (KEY_JEV, KEY_JEV[-6:]):
            self.assertNotIn(part, out)

    def test_a_section_that_sends_nothing_or_breaks_the_schema_is_named(self):
        (self.home / ".env").write_text(f"OPENROUTER_API_KEY={KEY_JEV}\n", encoding="utf-8")
        report, out = self.check(mode="shadow", send_sources=["telegram"])
        self.assertGreater(report.warn_n, 0)
        self.assertIn("send_from", out)
        report, out = self.check(mode="shadow", tau=0.2)
        self.assertEqual(report.bad_n, 1)
        self.assertIn("tau", out)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.J = dream._load_judge()

    def test_the_clopper_pearson_bounds_give_the_plans_sample_sizes(self):
        ub = self.J.cp_upper
        for k, n in ((0, 29), (1, 46), (2, 61)):
            with self.subTest(k=k, n=n):
                self.assertLessEqual(ub(k, n), 0.10)
                self.assertGreater(ub(k, n - 1), 0.10)
        self.assertAlmostEqual(ub(0, 29), 1 - 0.05 ** (1 / 29), places=6)
        self.assertEqual(self.J.cp_lower(0, 50), 0.0)
        self.assertAlmostEqual(self.J.cp_lower(30, 30), 0.05 ** (1 / 30), places=6)
        self.assertEqual(ub(0, 0), 1.0)

    def rows(self, vetoed_s=0, vetoed_other=0, kept_s=0, changed_wrong=False, provider="jev"):
        out = [{"label": "S", "veto": True, "changed": changed_wrong, "provider": provider}] * vetoed_s
        out += [{"label": "U", "veto": True, "changed": True, "provider": provider}] * vetoed_other
        out += [{"label": "S", "veto": False, "changed": False, "provider": provider}] * kept_s
        return out

    def test_the_rule_on_a_table_of_cases(self):
        cov = {"promotion": 0.95, "confirm": 0.92}
        r = self.J.readiness
        for name, rows, coverage, verdict in (
                ("29 vetoes, none wrong", self.rows(0, 29, 20), cov, "ready"),
                ("28 vetoes are not enough", self.rows(0, 28, 20), cov, "continue"),
                ("one wrong in 46", self.rows(1, 45, 30), cov, "ready"),
                ("one wrong in 45", self.rows(1, 44, 30), cov, "continue"),
                ("eight wrong in 30", self.rows(8, 22, 30), cov, "not_ready"),
                ("six wrong in 30: not proven either way", self.rows(6, 24, 30), cov, "continue"),
                ("coverage under 90 %", self.rows(0, 40, 20), {"promotion": 0.85, "confirm": 0.95}, "continue"),
                ("no coverage known", self.rows(0, 40, 20), {}, "continue"),
                ("losses unbounded", self.rows(0, 40, 5), cov, "continue"),
                ("a wrong veto that changed a decision", self.rows(1, 60, 40, changed_wrong=True), cov, "continue"),
                ("questions for Anton are left out", self.rows(0, 29, 20)
                 + [{"label": "?", "veto": True, "changed": True, "provider": "jev"}] * 5, cov, "ready")):
            with self.subTest(name=name):
                self.assertEqual(r(rows, coverage)["verdict"], verdict)

    def test_a_fallback_with_over_a_fifth_of_the_verdicts_is_judged_on_its_own(self):
        cov = {"promotion": 1.0}
        rows = self.rows(0, 40, 30) + self.rows(0, 20, 10, provider="openai")  # the fallback: 30 % of them
        report = self.J.readiness(rows, cov)
        self.assertEqual(set(report["by_provider"]), {"jev", "openai"})
        self.assertEqual(report["by_provider"]["jev"]["verdict"], "ready")
        self.assertEqual(report["by_provider"]["openai"]["verdict"], "continue")
        self.assertEqual(report["verdict"], "continue")
        self.assertNotIn("by_provider", self.J.readiness(self.rows(0, 40, 30) + self.rows(0, 2, 1,
                                                                                         provider="openai"), cov))

    def test_the_fallbacks_share_counts_its_verdicts_left_for_a_person_too(self):
        # Codex review of 3.3.0: the fallback gave 21 of 100 verdicts, 10 of them left for Anton ("?").
        cov = {"promotion": 1.0}
        rows = self.rows(0, 40, 39) + self.rows(0, 6, 5, provider="openai")
        rows += [{"label": "?", "veto": True, "changed": False, "provider": "openai"}] * 10
        self.assertEqual(self.J.readiness(rows[:79] + rows[79:90], cov)["verdict"], "ready")  # 11 of 90: no check
        report = self.J.readiness(rows, cov)
        self.assertEqual(set(report.get("by_provider") or ()), {"jev", "openai"})
        self.assertEqual(report["by_provider"]["openai"]["verdict"], "continue")
        self.assertEqual(report["verdict"], "continue")


if __name__ == "__main__":
    unittest.main()
