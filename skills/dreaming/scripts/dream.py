#!/usr/bin/env python3
"""dream.py — read-only nightly "dream" (memory consolidation) for a Hermes agent.

Phases (in the spirit of OpenClaw dreaming):
  light — load facts (memory_store.db) and human messages (state.db) for the
          corroboration window
  rem   — recurring themes from conversations, decayed facts, stale entries of
          MEMORY.md, possible contradictions between candidates and memory
  deep  — score facts with corroboration from real conversations → candidates
          for durable memory (MEMORY.md / USER.md)

Corroboration: for every fact / memory entry we count in how many DIFFERENT
days human messages actually mention it (by signature tokens). That gives an
honest query_diversity, strengthens frequency/consolidation and "recency by
mention" — without patching the core and without a query log.

IMPORTANT: the script never writes to runtime MEMORY.md / USER.md. It emits JSON
and appends only the dream diary; justified promotions and profile facts are
applied by the agent through the regular `memory` tool.

Everything installation-specific (trusted chats, alias rules, agent names,
timezone, diary heading, …) lives in a JSON config — see `load_config()` and
`examples/dreaming.example.json`. Precedence: CLI flag > env var > config > default.
"""
from __future__ import annotations
import argparse
import contextlib
import functools
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALWAYS_UNTRUSTED_SOURCES = ("cron", "subagent", "tool")

DEFAULT_CONFIG = {
    # IANA timezone for the diary date and for bucketing messages into "days".
    # Without it a 03:00 local dream would be filed under yesterday's UTC date.
    "timezone": "UTC",
    # Only humans corroborate memory. Group chats must be listed here explicitly
    # (fail-closed: an empty list means NO group chat corroborates); private
    # chats and legacy sessions without chat_id are trusted by default; cron
    # sessions are never trusted (their role=user rows are job prompts).
    "trusted_chat_ids": [],
    "trust_private_chats": True,
    "trust_sessions_without_chat": True,
    # Session sources whose role=user rows are machine prompts, not speech.
    # `cron` is always here (a job's own prompt corroborated facts before this
    # existed). Add `cli` when scripts drive the agent non-interactively — a
    # nightly "summarize this git diff" prompt is otherwise read as family talk,
    # and the diff's vocabulary becomes the top "emerging theme".
    # Machine-paced sources never count as human speech. Hermes itself groups
    # cron, subagent and tool runs that way (MACHINE_PACED_SOURCES), and a
    # subagent brief looks exactly like a person talking — same wording, same
    # role=user rows (external review, 2026-10-07).
    "untrusted_sources": ["cron", "subagent", "tool"],  # always included, see configure()
    # Regexes; a message whose text matches any of them never corroborates.
    # For machine text that carries no source of its own — bridge briefs
    # ("via to-mia"), bot digests, forwarded reports — which land in whatever
    # thread their author used, so `excluded_threads` cannot catch them.
    "exclude_patterns": [],
    # Threads (forum topics) of a TRUSTED chat that must NOT corroborate:
    # {"<chat_id>": ["<thread_id>", …]}. A trusted chat is trusted as a whole,
    # but an agent's own alerts, machine-generated cards and assistant bridges
    # usually live in their own threads there; their vocabulary drowns real human
    # talk in emerging themes and wakes the extraction gate for nothing. Absent
    # key = previous behaviour; a thread that is not listed stays trusted.
    "excluded_threads": {},
    # Names the agent is addressed by — pure noise for corroboration.
    "agent_names": [],
    # Extra stopwords on top of the built-in RU/EN lists.
    "extra_stopwords": [],
    # Minimal token length: ASCII/Latin tokens vs everything else (Cyrillic
    # inflection makes short tokens noisy).
    "token_min_len": {"latin": 4, "other": 5},
    # Files that already are durable memory (dedupe target). Relative paths are
    # resolved against HERMES_HOME. `--memory-md` is always included.
    "durable_memory_paths": ["memories/MEMORY.md", "memories/USER.md"],
    # Where candidate facts come from: "holographic" (the Hermes holographic
    # memory provider), "sqlite" / "jsonl" (your own export, mapped by
    # `fact_table` / `fact_columns`) or "none" (memory files only).
    "fact_source": "holographic",
    # Where the fact store lives. Empty = (holographic) ask Hermes:
    # `plugins.hermes-memory-store.db_path` from config.yaml, else
    # $HERMES_HOME/memory_store.db; generic sources must set it. Relative paths
    # resolve against HERMES_HOME.
    "fact_store_path": "",
    # Generic sources only: the table and the names of your columns / keys.
    # Required: id, content. Optional: created_at, updated_at (ISO or epoch),
    # category, tags (string or list), trust (0..1).
    "fact_table": "facts",
    "fact_columns": {},
    # Word stems that hint a fact is about the user / household (profile
    # candidates for USER.md). Categories are matched exactly.
    "profile_hint_terms": ["prefer", "likes", "dislikes", "goal", "plans", "works", "lives",
                           "family", "spouse", "partner", "child", "daughter", "son",
                           "предпочита", "любит", "не любит", "цель", "планирует",
                           "работает", "живёт", "живет", "семь", "родител", "дочь", "сын", "мама", "папа"],
    "profile_categories": ["user", "user_pref", "preference", "profile", "personal", "family"],
    # Declarative near-duplicate rules (see `_semantic_memory_alias`).
    "alias_rules": [],
    # Where the diary lives (relative to HERMES_HOME). It was hard-wired in the
    # gate, so an install that keeps its memories elsewhere got a second
    # DREAMS.md next to the first one (external review, 2026-10-07).
    "diary": {"heading": "## Dream", "keep_sections": 90, "path": "memories/DREAMS.md"},
    "windows": {"themes_days": 14, "corroboration_days": 60, "md_decay_days": 60},
    # Promotions used to repeat every night by design ("until written"). With
    # an approval gate in the way, or a budget that cut them off, that meant the
    # same wake-up forever — hence a short rest, much shorter than the 14 days
    # of the other sections.
    # A fact a human rejected keeps its trust in the store, so the provider's
    # prefetch goes on injecting it into ordinary turns — the rejection taught
    # the dream, not the retrieval. `trust_feedback` offers the agent the one
    # call that fixes that (`fact_feedback unhelpful`, -0.10 trust; below
    # `feedback_trust_floor` the fact leaves search and prefetch).
    # The floor is the PROVIDER's, not ours: holographic recalls nothing below
    # its `min_trust_threshold` (its own default 0.3, and its meaning search
    # applies the same floor). Set here only to override it — unset, the pass
    # reads the provider's value, so a store tuned to 0.5 is not offered facts
    # retrieval already ignores, and the nightly report names the real number.
    "gates": {"min_score": 0.55, "min_mentions": 3, "new_facts_cap": 30,
              "promotion_cooldown_days": 3,
              # None = ask the provider (its `min_trust_threshold`), else 0.3.
              "feedback_trust_floor": None, "feedback_cap": 5,
              "seen_cooldown_days": 14, "md_ask_cooldown_days": 14,
              # A §-entry that survived a "still relevant?" question — kept as is
              # past md_ask_cooldown_days, or rewritten in place — counts as
              # confirmed and is not asked about again for this long.
              "md_confirmed_cooldown_days": 90,
              "publish_cap": 15},
    "weights": {"relevance": 0.30, "frequency": 0.24, "query_diversity": 0.15,
                "recency": 0.15, "consolidation": 0.10, "conceptual_richness": 0.06},
    # Optional char limits of the durable files (as in Hermes `memory.*_char_limit`)
    # — reported as fill level so the agent knows when to merge before adding.
    "memory_char_limits": {},
    # Markers of pinned §-entries in MEMORY.md: never flagged as stale.
    "pinned_markers": ["📌", "[pinned]"],
    # Probes: phrases that must stay in the durable memory files ("allergic to peanuts"). The loss guard below
    # catches a mass loss; one entry merged away while memory was being tidied at its limit is under its threshold.
    # A probe that disappears raises one alert (and one more if it comes back and goes again).
    "probes": [],
    # A rejected fact (dream-reject.py) is asked about once more when the rejection is older than this many days
    # and the fact was said again after it (a new store row and a human mention since); 0 = never.
    "reject_reask_days": 180,
    # Loss guard: the pass snapshots the durable files; if on the next pass more
    # than this fraction of yesterday's entries is gone, an alert is raised
    # (a bad LLM turn or a wrong `memory replace` — never silent).
    "memory_loss_alert_fraction": 0.25,
    # Writes waiting for a human (`memory.write_approval: true` in Hermes).
    # A staged write answers "success, staged" to the agent but does not change
    # MEMORY.md, so without this the same candidate woke the agent every night
    # and piled up new pending files (external review, 2026-10-07).
    "pending": {"dir": "pending/memory", "alert_min": 5, "alert_days": 7},
    # Keywords for the fact store (holographic >= 0.6.0): words a person might
    # ask with that a fact does not contain — synonyms, other roots,
    # translations. The provider keeps them in `tags` after a "keywords: " line
    # and searches them like text. With `enabled` the pass lists facts that have
    # none (`keywords_needed`, at most `cap` a night) and the extraction run is
    # asked to give them at `add`. Off by default: an older provider ignores the
    # parameter, and the same facts would come back forever. Turn it on with
    # holographic >= 0.6.1 — 0.6.0 stamped a fact "changed now" when it got
    # keywords, and old facts came back as new ones.
    "keywords": {"enabled": False, "cap": 10},
    # holographic `scope: user / chat / thread` keeps facts in one database per user / chat / topic, which the
    # dream does not read by default: one chat's facts would reach memory every chat sees. `read` lists every
    # such database in the report (counts only — no text, no ids); `promote_from` names the chats whose facts
    # join the pass like the shared store's — "<platform>:<chat id>", "<platform>:<chat id>:<thread id>" with
    # scope thread, "<platform>:<user id>" with scope user, or the database's file name. Their facts are
    # corroborated only by messages of their own chat (or user), and their messages corroborate nothing else.
    "chat_stores": {"read": False, "promote_from": []},
    "state": {"asked": "cache/dream-asked.json",
              "rejected": "cache/dream-rejected.json",
              "seen": "cache/dream-seen.json",
              "snapshot": "cache/dream-snapshot.json",
              # holographic >= 0.8.0 `forget`: which tombstones were swept, and
              # hashes of the diary previews of every fact (old lines carry no id).
              "forget_sweep": "cache/dream-forget-sweep.json",
              "diary_index": "cache/dream-diary-index.json"},
    "precheck": {"max_content": 300,
                 "actionable_keys": ["new_facts_reviewed", "promotions", "fact_decays",
                                     "md_decays", "conflicts", "quarantined", "alerts"]},
}


def config_path():
    return os.environ.get("DREAM_CONFIG") or os.path.join(HOME, "dreaming.json")


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    """Read the JSON config; missing file → defaults, broken JSON → warning + defaults."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root must be an object")
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        print(f"[dream] warn: config {path} ignored: {e}", file=sys.stderr)
        data = {}
    merged = _deep_merge(DEFAULT_CONFIG, data)
    # Keys the file does not mention are taken from Hermes itself, not guessed:
    # the example ships the CORE defaults (2200/1375), and on an install with
    # raised limits that produced "memory at 285%" every night. An explicit
    # value — even one equal to the default — stays untouched; only absence
    # delegates (external review, 2026-10-07).
    if "timezone" not in data:
        hermes_tz = (_hermes_config() or {}).get("timezone")
        if hermes_tz:
            merged["timezone"] = str(hermes_tz)
    if "memory_char_limits" not in data:
        if "memory" in hermes_config_unread():
            print("[dream] note: memory: of config.yaml cannot be read for sure — char limits not taken from it",
                  file=sys.stderr)
        hermes_mem = hermes_memory_settings()
        limits = {name: int(value) for name, value in
                  (("memories/MEMORY.md", hermes_mem.get("memory_char_limit")),
                   ("memories/USER.md", hermes_mem.get("user_char_limit")))
                  if _a_char_limit(value)}
        if limits:
            merged["memory_char_limits"] = limits
            print(f"[dream] note: char limits taken from config.yaml: {limits}", file=sys.stderr)
    return merged


def _a_char_limit(value):
    """A memory char limit worth taking from config.yaml: a positive finite number. Not a bool — `true` is an int
    to Python and was a limit of one character, "memory at 100000%" (third review of 2.9.6, Codex) — and not
    `.inf` / `.nan`, which PyYAML gives as floats and `int()` refuses (review of the 2.9.7 branch, Codex: a huge int
    made `math.isfinite()` overflow), and not a fraction below one, which `int()` makes 0 — no limit at all (third
    review of the branch, Codex)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0  # not through float(): a 400-digit int is finite, and math.isfinite() overflowed on it
    return isinstance(value, float) and math.isfinite(value) and int(value) > 0


def _hermes_config_text(home=None):
    """Hermes config.yaml as text; "" when there is none, None when it is there but cannot be read (not UTF-8, no
    permission). The two differ: no file is a fresh install with the defaults, an unreadable one is a config this
    pass cannot see, and its settings count as unread (third review of the 2.9.7 branch, Codex: a stray non-UTF-8
    byte in a comment pointed the pass at the default database)."""
    path = os.path.join(home or HOME, "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError):
        return None if os.path.exists(path) else ""


def _hermes_config_parsed(home=None):
    """(Hermes config.yaml as a dict, parsed) — `parsed` is True when PyYAML read the file.

    The flag is what the plugin-setting reader needs: once YAML has read the file, its answer
    is the truth, and "the key is not there" must stay "not there" instead of falling through
    to a line scanner that cannot tell `store.key` from `store.something.key`."""
    text = _hermes_config_text(home)
    if not text:
        return {}, False
    try:
        import yaml  # type: ignore
    except Exception:  # noqa: BLE001 — no PyYAML: the scanner below is the only reader
        return {}, False
    try:
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — a file YAML cannot parse: let the scanner try
        return {}, False
    return (data if isinstance(data, dict) else {}), True


def _hermes_config(home=None):
    """Hermes config.yaml as a dict: PyYAML's, or without it (or on a parse error) the top-level scalars the
    scanner reads (`_scan_block`) — a nested block is None there. Callers must degrade gracefully: the pass never
    requires a dependency. Before 2.9.7 there was no fallback, so without PyYAML `timezone` was never read and
    the pass ran on UTC."""
    data, parsed = _hermes_config_parsed(home)
    return data if parsed else (_scan_block(_hermes_config_text(home) or "", ()) or {})


_MEMORY_KEYS = ("memory_char_limit", "user_char_limit", "provider", "write_approval")


def hermes_enabled(value):
    """A Hermes on/off setting as Hermes reads it (`tools/write_approval._normalize_enabled`): a YAML bool as it
    is, a string among on / true / yes / 1 / approve / enabled, anything else — a number, null — off."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"on", "true", "yes", "1", "approve", "enabled"}
    return False


def hermes_memory_settings(home=None):
    """`memory:` of Hermes config.yaml — the limits and the provider that decide
    what the dream can see and what counts as "memory is filling up".

    The defaults shipped in the example config are the CORE defaults (2200 and
    1375), so an install that raised its limits got "memory at 285%" and a
    nightly order to merge and drop entries (external review, 2026-10-07). The
    numbers live in one place; read them from there.

    Once PyYAML has read the file its answer is final, an empty one too: a `memory:`
    without these keys, or not a mapping, is "not set" and does not fall through to
    the scanner (review of 2.9.6, Codex: a second `memory:` block, which YAML keeps,
    had the scanner read the first). `write_approval` comes back as a bool. A block the scanner cannot read for
    sure gives {} — no provider, so the extraction gate stays shut; `hermes_config_unread` says why."""
    data, parsed = _hermes_config_parsed(home)
    if parsed:
        mem = data.get("memory")
    else:
        text = _hermes_config_text(home)
        mem = None if text is None else _scan_block(text, ("memory",))
    out = {key: mem[key] for key in _MEMORY_KEYS if key in mem} if isinstance(mem, dict) else {}
    if "write_approval" in out:
        out["write_approval"] = hermes_enabled(out["write_approval"])
    return out


def hermes_config_unread(home=None):
    """The blocks of Hermes config.yaml the dream relies on — "memory", "plugins.hermes-memory-store" — that the
    scanner cannot read for sure (`_scan_block` gives None), or all of them when the file is there but cannot be
    read; () when PyYAML read the file or there is none. Such a block is not taken as empty: the extraction gate
    stays shut, the pass reads no facts unless `fact_store_path` names the store, `install.py --check` says so."""
    text = _hermes_config_text(home)
    if text is None:
        return ("memory", ".".join(_STORE))
    if not text or _hermes_config_parsed(home)[1]:
        return ()
    return tuple(".".join(p) for p in (("memory",), _STORE) if _scan_block(text, p) is None)


# A key at the start of a line (after its indent), plain or quoted, and what follows the colon.
_KEY_RE = re.compile(r"""(["']?)(\w[\w.-]*)\1[ \t]*:(?:[ \t]+(.*)|[ \t]*$)""")
_UNREAD = object()
_STORE = ("plugins", "hermes-memory-store")


class _Unsure(Exception):
    """The scanner cannot tell what YAML reads here. The block it was reading counts as unread, not as empty: an
    empty store block means the defaults — `memory_store.db`, `scope: profile`, cron writes on — and the second
    review of the 2.9.7 branch (Codex) found four configs where a half-read block let those defaults open the
    extraction gate or point the pass at another database. Unsure is closed."""


# A plain scalar of this many characters or more that looks like a number is not typed: YAML's base-60 and
# arbitrary-size ints cost time that grows with the square of the length (second review of the 2.9.7 branch).
_NUMBER_LEN = 100
# Plain scalars as PyYAML (YAML 1.1) types them: "write_approval: 1" is a number, which Hermes reads as off, and
# "05000" is octal. PyYAML's own lists: "y" and "n" stay strings (third review of 2.9.6, Codex); binary, base-60
# ("1:30" is 90), ".inf" and ".nan" are numbers (review of the 2.9.7 branch, Codex).
_YAML_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_YAML_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_YAML_NULL = {"", "~", "null", "Null", "NULL"}
_YAML_INT = re.compile(r"[-+]?(?:0|[1-9][0-9_]*)$")
_YAML_OCT = re.compile(r"[-+]?0[0-7_]+$")
_YAML_HEX = re.compile(r"[-+]?0x[0-9a-fA-F_]+$")
_YAML_BIN = re.compile(r"[-+]?0b[0-1_]+$")
_YAML_INT60 = re.compile(r"[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+$")
# PyYAML's float: a digit before the point, or an unsigned ".5" ("-.5" stays a string)
_YAML_FLOAT = re.compile(r"(?:[-+]?[0-9][0-9_]*\.[0-9_]*|\.[0-9_]+)(?:[eE][-+][0-9]+)?$")
_YAML_FLOAT60 = re.compile(r"[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*$")
_YAML_INF = re.compile(r"[-+]?\.(?:inf|Inf|INF)$")
_YAML_NAN = re.compile(r"\.(?:nan|NaN|NAN)$")
_YAML_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}"
                             r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)?$")
# YAML's line breaks: str.splitlines() also breaks at \v, \f and \x1c-\x1e, which YAML does not
_YAML_LINES = re.compile("\r\n|[\r\n\x85\u2028\u2029]")
# YAML's double-quoted escapes (JSON knows only some of them)
_YAML_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
                 "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
                 "L": " ", "P": " "}
_YAML_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
# How deep a one-line flow (`{a: [b, {c: d}]}`) is followed before it counts as unread: past it, recursion would
# reach Python's limit (review of the 2.9.7 branch, Codex: a 1,100-deep list crashed the config load).
_FLOW_DEPTH = 64


def _quoted(text, start):
    """(value, index after the closing quote) of the quoted scalar at `text[start]`, or None when it does not
    close on this line. Single quotes double to escape (`''`); double quotes take YAML's escapes (`\\"`, `\\\\`,
    `\\n`, `\\x41`, `\\u00e9`…). An escape YAML does not know gives _UNREAD as the value, with the end still found,
    so it leaves out its own key and nothing else (review of the 2.9.7 branch, Codex)."""
    quote, i = text[start], start + 1
    if quote == "'":
        out = []
        while i < len(text):
            if text[i] == "'":
                if text[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), i + 1
            out.append(text[i])
            i += 1
        return None
    out, bad = [], False
    while i < len(text):
        c = text[i]
        if c == '"':
            return (_UNREAD if bad else "".join(out)), i + 1
        if c != "\\":
            out.append(c)
            i += 1
            continue
        esc = text[i + 1:i + 2]
        if esc in _YAML_ESCAPES:
            out.append(_YAML_ESCAPES[esc])
            i += 2
        elif esc in _YAML_HEX_ESCAPES:
            width = _YAML_HEX_ESCAPES[esc]
            digits = text[i + 2:i + 2 + width]
            if len(digits) == width and all(d in "0123456789abcdefABCDEF" for d in digits):
                out.append(chr(int(digits, 16)) if int(digits, 16) <= 0x10FFFF else "")
                bad = bad or int(digits, 16) > 0x10FFFF
                i += 2 + width
            else:
                bad, i = True, i + 2
        else:
            bad, i = True, i + 2
    return None


def _strip_comment(text, plain=False):
    """`text` without its comment: `#` at the start or after a space, outside quotes (`"a #b"` keeps it). A quote
    opens a quoted scalar only where a scalar starts — at the start of `text`, or in a flow after `[`, `{`, `,` or
    `:` — and is a character inside a plain one, as in YAML: `/data/family 'archive #1'.db` ends at the `#` (third
    review of the 2.9.7 branch, Codex). `plain`: the text continues a plain scalar (a continuation line)."""
    flow = not plain and text[:1] in ("{", "[")
    prev, i = "", 0  # prev: the last character before `i` that is not a space, outside quotes
    while i < len(text):
        c = text[i]
        if c in ("'", '"') and not plain and (i == 0 or (flow and prev in ("[", "{", ",", ":"))):
            hit = _quoted(text, i)
            if hit is None:
                return text
            prev, i = c, hit[1]
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip(" \t")
        if c not in " \t":
            prev = c
        i += 1
    return text.rstrip(" \t")


def _base60(text):
    sign = -1 if text.startswith("-") else 1
    total = 0.0 if "." in text else 0
    for part in text.lstrip("+-").replace("_", "").split(":"):
        total = total * 60 + (float(part) if "." in part else int(part))
    return sign * total


def _plain(text):
    """A plain scalar typed as PyYAML types it, or _UNREAD for what this reader does not take: a block scalar
    (`|`, `>`), an anchor, an alias, a tag, a directive, a number of _NUMBER_LEN characters or more, a timestamp
    (PyYAML makes it a date, whose string differs — third review of the 2.9.7 branch, Codex)."""
    text = text.strip(" \t")
    if text[:1] in ("|", ">", "&", "*", "!", "%", "@", "`") or _YAML_TIMESTAMP.match(text):
        return _UNREAD
    if text in _YAML_NULL:
        return None
    if text in _YAML_TRUE or text in _YAML_FALSE:
        return text in _YAML_TRUE
    digits = text.replace("_", "")
    if len(text) >= _NUMBER_LEN and any(r.match(text) for r in (_YAML_INT, _YAML_OCT, _YAML_HEX, _YAML_BIN,
                                                                  _YAML_INT60, _YAML_FLOAT60, _YAML_FLOAT)):
        return _UNREAD
    try:
        if _YAML_INT.match(text):
            return int(digits)
        if _YAML_OCT.match(text):
            return int(digits, 8)
        if _YAML_HEX.match(text):
            return int(digits, 16)
        if _YAML_BIN.match(text):
            return int(digits, 2)
        if _YAML_INT60.match(text) or _YAML_FLOAT60.match(text):
            return _base60(text)
        if _YAML_INF.match(text):
            return float("-inf") if text.startswith("-") else float("inf")
        if _YAML_NAN.match(text):
            return float("nan")
        if _YAML_FLOAT.match(text) and any(c.isdigit() for c in text):
            return float(digits)
    except (ValueError, OverflowError):
        return _UNREAD
    return text


def _flow(text, i=0, depth=0):
    """(value, index after it) of the flow collection or scalar at `text[i]` — `{…}` a dict, `[…]` a list, nested
    up to _FLOW_DEPTH — or None on what this reader cannot follow on the line. A value it does not read (an
    anchor, a tag, an escape YAML does not know, a flow deeper than _FLOW_DEPTH) raises _Unsure: leaving the pair
    out kept an earlier value of a repeated key, and leaving the mapping out let the defaults in (second review
    of the 2.9.7 branch, Codex)."""
    if depth > _FLOW_DEPTH:
        raise _Unsure
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text):
        return None
    c = text[i]
    if c in ("'", '"'):
        return _quoted(text, i)
    if c in "{[":
        close, out, i = ("}" if c == "{" else "]"), ({} if c == "{" else []), i + 1
        while True:
            while i < len(text) and text[i] in " \t":
                i += 1
            if i >= len(text):
                return None
            if text[i] == close:
                return out, i + 1
            if isinstance(out, dict):
                if text[i] in ("'", '"'):
                    hit = _quoted(text, i)
                    if hit is None:
                        return None
                    key, i = hit
                else:
                    j = i
                    while j < len(text) and text[j] not in ":,{}[]":
                        j += 1
                    if j + 1 < len(text) and text[j] == ":" and text[j + 1] not in " \t,{}[]":
                        # `{provider:holographic}` is one key in YAML, `provider:holographic` with no value (third
                        # review of the 2.9.7 branch, Codex)
                        raise _Unsure
                    key, i = text[i:j].strip(" \t"), j
                    if not key:
                        raise _Unsure  # `{: none}`: a key YAML refuses (third review of the 2.9.7 branch, Codex)
                while i < len(text) and text[i] in " \t":
                    i += 1
                if i >= len(text) or text[i] != ":":
                    return None
                hit = _flow(text, i + 1, depth + 1)
                if hit is None:
                    return None
                value, i = hit
                if value is _UNREAD or key is _UNREAD:
                    raise _Unsure
                out[str(key)] = value
            else:
                hit = _flow(text, i, depth + 1)
                if hit is None:
                    return None
                value, i = hit
                if value is _UNREAD:
                    raise _Unsure
                out.append(value)
            while i < len(text) and text[i] in " \t":
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
            elif i >= len(text) or text[i] != close:
                return None
    j = i
    while j < len(text) and text[j] not in ",{}[]":
        j += 1
    return _plain(text[i:j]), j


def _scalar(rest):
    """The value after `key:` on its line, typed as PyYAML types it for the forms Hermes writes: a plain scalar
    (`_plain`), a quoted string (escapes decoded), a one-line flow collection (`_flow`) — or _UNREAD for what
    this reader does not take (a block scalar, an anchor, an alias, a tag, a flow that does not close on the line);
    inside a flow, such a value raises _Unsure, which `_scan_block` turns into None. `#` starts a comment only at
    the start or after a space and outside quotes, as in YAML: `holo#graphic` and `"a #b"` are values."""
    rest = _strip_comment(rest.strip(" \t"))
    if rest[:1] in ("'", '"', "{", "["):
        hit = _flow(rest)
        if hit is None or rest[hit[1]:].strip(" \t"):
            return _UNREAD
        return hit[0]
    return _plain(rest)


def _a_plain_value(value_text):
    """Is this value on the key's line a plain scalar, which YAML continues on deeper lines?"""
    return bool(value_text) and value_text[:1] not in ("'", '"', "{", "[", "|", ">", "&", "*", "!")


def _scan_block(text, path):
    """{key: value} of the direct children of the mapping at `path` in config.yaml (`()` is the top level,
    `("memory",)`, `("plugins", "hermes-memory-store")`), or None when the scanner cannot tell what YAML reads
    there — the reader behind the config helpers when PyYAML is missing or cannot parse the file. Not a YAML
    parser: block mappings with plain or quoted keys, and values typed as PyYAML types them (`_scalar`):
    scalars, a plain scalar continued on deeper lines, one-line flow collections. A flow mapping stands for the
    block at `path` or at any key on the way to it (`plugins: {hermes-memory-store: {…}}`), or for the whole
    document. A tab indent is taken although YAML refuses it.

    None — unsure — is not {}: an empty store block means the defaults, and a half-read one let them open the
    extraction gate or point the pass at another database (second review of the 2.9.7 branch, Codex). It comes
    on a value of `path` or of a key on the way to it that this reader does not take (a tag, an anchor, a block
    scalar, an escape YAML does not know, a flow deeper than _FLOW_DEPTH or not closed on its line, a number of
    _NUMBER_LEN characters or more), a continued plain value with a blank or comment line inside (YAML keeps
    those as line breaks), a line of that mapping that is not a key or sits at another indent, an indented line
    outside any mapping, and on a file of several YAML documents or with content on a `---` line.

    A comment line belongs to no block, at any indent: YAML skips it, so a top-level "# Section heading" between
    `memory:` and its `provider:` does not end the mapping (issue #2: the old block regex stopped there and
    reported no provider while Hermes ran holographic; the same regex read `plugins:` until 2.9.7). Each mapping's
    first content line fixes the indent of its children, whatever it holds (review of 2.9.6, Codex: a quoted key
    the old extractor skipped let a nested key set it); deeper lines belong to a nested value. A key with a value
    on its line holds no children: the lines under `memory: |` are text, not settings (review of 2.9.7, Codex),
    and the lines under a plain value continue it, as YAML folds them — `provider: holographic` over
    `    disabled` is "holographic disabled", not holographic (review of the 2.9.7 branch, Codex). Every definition
    of `path`, or of a key on the way to it, replaces what an earlier one gave, as in YAML: a second `plugins:`
    without the store leaves nothing (review of 2.9.7). A key may carry a comment, the file a BOM, and the last
    line may lack a newline. A document marker is one only in column 0: `---` inside a block scalar is text."""
    path = tuple(path)
    try:
        return _scan_block_or_raise(text, path)
    except _Unsure:
        return None


def _scan_block_or_raise(text, path):
    result, found = {}, not path
    content = started = ended = False  # a content line seen; a `---` seen; a `...` seen
    gap = False  # a blank or comment line since the last content line: it ends a continued plain value
    # [indent, key, indent of its children, opaque: a value on the key's line, continuation lines of a plain
    # value of a direct child of `path` (or None), the value's first line, a list: its children are `- ` items]
    stack = []

    def on_path():
        """Is the mapping at the top of the stack `path` or a mapping on the way to it?"""
        keys = tuple(entry[1] for entry in stack)
        return path[:len(keys)] == keys

    def close(entry):
        if entry[4]:
            value = _plain(" ".join([entry[5]] + entry[4]))
            if value is _UNREAD:
                raise _Unsure
            result[entry[1]] = value

    for line in _YAML_LINES.split(text.lstrip("﻿")):
        # YAML's whitespace is space and tab: NBSP and the other Unicode spaces are text to it, and stripping them
        # moved keys and changed values (third review of the 2.9.7 branch, Codex)
        body = line.strip(" \t")
        if not body:
            gap = True
            continue
        if body[0].isspace():
            raise _Unsure  # a line that starts with a space YAML does not take for one
        if line.startswith(("---", "...")) and (len(line) == 3 or line[3] in " \t"):
            if _strip_comment(line[3:].strip(" \t")):
                raise _Unsure  # `--- {memory: …}`, `--- !tag`: a document on the marker's line
            if line.startswith("---"):
                if content or started or ended:
                    raise _Unsure  # a second document, empty ones too (`---` / `...` / `---`)
                started = True
            else:
                ended = True
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if body.startswith("#") and not (stack and stack[-1][3] and indent > stack[-1][0]):
            gap = True
            continue
        if ended:
            raise _Unsure  # content after `...` is another document
        content = True
        after_gap, gap = gap, False
        if (stack and indent == stack[-1][0] and not stack[-1][3] and stack[-1][2] in (None, indent)
                and (body == "-" or body.startswith(("- ", "-\t")))):
            # a list at the indent of its key, as PyYAML (and Hermes, saving its config) writes it: `toolsets:`
            # over `- hermes-cli`. Nothing on `path` lies inside a list: PyYAML reads no store under `plugins:` there
            stack[-1][2], stack[-1][6] = indent, True
            continue
        while stack and indent <= stack[-1][0]:
            close(stack.pop())
        if stack:
            top = stack[-1]
            if top[3]:
                if top[4] is not None:
                    piece = _strip_comment(body, plain=True)
                    if not piece:
                        gap = True
                        continue
                    if after_gap or _KEY_RE.match(line, indent):
                        raise _Unsure
                    top[4].append(piece)
                    gap = piece != body
                continue
            first = top[2] is None
            if first:
                top[2] = indent
            if indent != top[2]:
                if on_path() and not top[6]:
                    raise _Unsure
                continue
            if body == "-" or body.startswith(("- ", "-\t")):
                if first or top[6]:
                    top[6] = True  # a list, as above: `plugins:` over `  - holographic`
                elif on_path():
                    raise _Unsure
                continue
            if top[6]:
                if on_path():
                    raise _Unsure  # a key after list items: not YAML
                continue
        elif indent:
            raise _Unsure
        hit = _KEY_RE.match(line, indent)
        if not hit:
            if not stack and body.startswith("{"):
                # the whole document as one flow mapping: `{timezone: UTC, memory: {provider: x}}`
                value = _scalar(body)
                if value is _UNREAD:
                    raise _Unsure
                for name in path:
                    value = value.get(name) if isinstance(value, dict) else None
                found, result = True, (dict(value) if isinstance(value, dict) else {})
                stack.append([indent, "", None, True, None, "", False])
            elif on_path():
                raise _Unsure
            continue
        key, rest = hit.group(2), hit.group(3) or ""
        here = tuple(entry[1] for entry in stack) + (key,)
        value_text = _strip_comment(rest.strip(" \t"))
        continued = None
        if path[:len(here)] == here:
            # `path` or a key on the way to it is defined again: what came before is gone
            found, result = True, {}
            if value_text:
                value = _scalar(value_text)
                if value is _UNREAD:
                    raise _Unsure
                for name in path[len(here):]:
                    value = value.get(name) if isinstance(value, dict) else None
                result = dict(value) if isinstance(value, dict) else {}
        elif found and here[:-1] == path:
            value = _scalar(rest)
            if value is _UNREAD:
                raise _Unsure
            result[key] = value
            if _a_plain_value(value_text):
                continued = []
                gap = value_text != rest.strip(" \t")  # a comment on the key's line ends the value there
        stack.append([indent, key, None, bool(value_text), continued, value_text, False])
    while stack:
        close(stack.pop())
    return result


def _hermes_plugin_setting(key, home=None):
    """`plugins.hermes-memory-store.<key>` from Hermes config.yaml as a string, or None.
    Read with PyYAML when available, else a narrow line scanner — the pass must
    not grow a dependency for a few keys."""
    text = _hermes_config_text(home)
    if not text:
        return None
    data, parsed = _hermes_config_parsed(home)
    if parsed:
        # YAML read the file, so its answer is final: a key that is absent, nested deeper
        # (`store.diagnostics.key`) or not a scalar is "unset" — it must not fall through to the
        # scanner, which would find the nested one (Codex review of 2.7.1). A mapping is the
        # plugin's shape; anything else (a list, a bare string after a wrong indent) is "unset",
        # not a crash of the whole night (review 2026-10-13).
        plugins = data.get("plugins")
        store = plugins.get("hermes-memory-store") if isinstance(plugins, dict) else None
        value = store.get(key) if isinstance(store, dict) else None
        if value is None or value == "" or isinstance(value, (dict, list)):
            return None
        return str(value)
    return _scan_plugin_setting(text, key)


def _scan_plugin_setting(text, key):
    """`plugins.hermes-memory-store.<key>` by the scanner (`_scan_block`) when PyYAML is missing or failed, as a
    string like the YAML path gives, or None. Only a direct child of `hermes-memory-store` counts, a one-line flow
    mapping (`hermes-memory-store: {scope: chat}`) is read, and a comment anywhere no longer ends `plugins:` or
    the store's block — it did until 2.9.7, and `db_path`, `scope`, `cron_writes` and `min_trust_threshold`
    after one fell back to their defaults: the pass read another database and said nothing of a split scope."""
    value = (_scan_block(text, _STORE) or {}).get(key)
    if value is None or value == "" or isinstance(value, (dict, list)):
        return None
    return str(value)


def _hermes_plugin_db_path(home=None):
    """`plugins.hermes-memory-store.db_path` from Hermes config.yaml, or None."""
    return _hermes_plugin_setting("db_path", home)


# holographic `scope` values that keep facts in separate files (holographic-users/, holographic-chats/) next to
# `db_path`. With them the shared store the dream reads gets nothing: chats write to their own files, and a cron
# job — no chat, no user — reads and writes nothing at all, so the extraction job stores nothing either.
SPLIT_SCOPES = {"user": "user", "chat": "chat", "thread": "forum topic"}


def fact_store_note(home=None):
    """Why the holographic fact store the dream reads cannot fill up, in one sentence, or None.

    Said in the report, the diary and `install.py --check`, and the extraction gate stops on it: the pass itself
    still works (the memory-file review needs no facts), but promotions stay empty for a reason the user should
    know — not because nothing was said (external review, 2026-10-13)."""
    if fact_source() != "holographic":
        return None
    if ".".join(_STORE) in hermes_config_unread(home):
        why = ("plugins.hermes-memory-store of config.yaml cannot be read for sure (the file is not readable "
               "UTF-8, or PyYAML is missing or refuses it and the block has a form the fallback reader does not take)")
        fix = ("run with Hermes' own Python (it has PyYAML), or write the block in plain `key: value` lines")
        if os.environ.get("DREAM_FACT_STORE") or CONFIG.get("fact_store_path"):
            # the store named in dreaming.json is read (third review of the 2.9.7 branch, Codex: the note said
            # otherwise); scope and cron_writes stay unknown
            return (f"{why} — the dream reads the store fact_store_path names, but cannot tell its scope and "
                    f"cron_writes, so the extraction job stays asleep; {fix}")
        return (f"{why} — the dream reads no facts and the extraction job stays asleep rather than guess the "
                f"database, scope and cron_writes; {fix}")
    scope = holographic_scope(home)
    if scope in SPLIT_SCOPES:
        read, keys = chat_stores_settings()
        if read or keys:
            return (f"holographic runs with scope: {scope} — the dream reads the databases per {SPLIT_SCOPES[scope]} "
                    f"through chat_stores ({'an overview of all, ' if read else ''}facts of {len(keys)} listed "
                    "promoted); a cron job writes nothing, so the extraction job stays asleep")
        return (f"holographic runs with scope: {scope} — facts go to a separate database per {SPLIT_SCOPES[scope]}, "
                "which the dream does not read (it would carry one chat's facts into memory every chat sees), and a "
                "cron job writes nothing, so the shared store stays as it is; the memory-file review still works. "
                "Set fact_source to 'none' to make this explicit")
    cron_writes = (_hermes_plugin_setting("cron_writes", home) or "").strip().lower()
    if cron_writes in ("false", "no", "off", "0"):
        return ("holographic cron_writes is false — the extraction job cannot store candidate facts, so only facts "
                "the agent saves in ordinary turns reach the dream")
    return None


# ---------------------------------------------------------------------------
# Per-chat / per-user fact stores (holographic scope: user / chat / thread)
# ---------------------------------------------------------------------------
# The databases sit next to `db_path`: holographic-users/<key>.db (scope user) or holographic-chats/<key>.db
# (chat, thread). Read-only, like the shared store. Only `promote_from` stores give facts to the pass; their ids
# become "<key>#<id>" — never mistaken for a shared-store id, and a fact_store call with one fails instead of
# touching another fact (a cron session cannot reach a chat's database anyway).
CHAT_STORE_DIRS = {"user": "holographic-users", "chat": "holographic-chats", "thread": "holographic-chats"}
FACT_ID_RE = re.compile(r"(?:[A-Za-z0-9_.-]+#)?\d+")


def holographic_min_trust(home=None):
    """`plugins.hermes-memory-store.min_trust_threshold` as a float, or None.

    The trust below which holographic stops recalling a fact at all — words,
    structure and meaning alike. Unset, unreadable or not a number → None, and
    the caller keeps its own default: a config we cannot parse must not silently
    turn `trust_feedback` into "offer everything"."""
    raw = _hermes_plugin_setting("min_trust_threshold", home)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def holographic_scope(home=None):
    """`plugins.hermes-memory-store.scope` of Hermes config.yaml, lower case; "profile" when unset."""
    return (_hermes_plugin_setting("scope", home) or "profile").strip().lower()


def store_key(platform, ident, thread=None):
    """The file stem holographic gives the store of a chat / thread / user — its `_safe_part`, verbatim."""
    value = f"{platform or 'unknown'}-{ident}" + (f"-t{thread}" if thread else "")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:60]
    return safe if safe == value else f"{safe}-{hashlib.sha256(value.encode()).hexdigest()[:10]}"


def _promote_key(raw):
    """"telegram:-100123" / "telegram:-100123:42" → the store key; a file name or key passes as it is."""
    raw = str(raw or "").strip()
    if raw.endswith(".db"):
        raw = raw[:-3]
    parts = raw.split(":")
    if len(parts) in (2, 3) and parts[0] and parts[1]:
        return store_key(parts[0], parts[1], parts[2] if len(parts) == 3 and parts[2] else None)
    return raw


def chat_stores_settings():
    """(read, {promoted store keys}) of the `chat_stores` config."""
    cfg = CONFIG.get("chat_stores") or {}
    if not isinstance(cfg, dict):
        return False, set()
    keys = {_promote_key(r) for r in (cfg.get("promote_from") or []) if str(r or "").strip()}
    return cfg.get("read") is True, keys


def chat_store_dir(home=None):
    """(folder of the per-chat / per-user databases, scope), or (None, scope) when there is none to read."""
    scope = holographic_scope(home)
    if fact_source() != "holographic" or scope not in CHAT_STORE_DIRS:
        return None, scope
    base = fact_store_path(home)
    return (os.path.join(os.path.dirname(base), CHAT_STORE_DIRS[scope]) if base else None), scope


def list_chat_stores(home=None):
    """{store key: path} of the per-chat / per-user databases, sorted by key."""
    folder, _scope = chat_store_dir(home)
    if not folder or not os.path.isdir(folder):
        return {}
    return {n[:-3]: os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".db")}


def load_chat_facts(home=None):
    """Facts of the `promote_from` stores: ids "<key>#<id>", each with `store`. A store that cannot be read is
    skipped with a warning — the rest of the pass goes on."""
    _read, keys = chat_stores_settings()
    if not keys:
        return []
    stores = list_chat_stores(home)
    out = []
    for key in sorted(keys & set(stores)):
        try:
            rows = _load_holographic(stores[key])
        except sqlite3.Error as exc:
            print(f"[dream] warn: chat store {key} unreadable: {exc}", file=sys.stderr)
            continue
        for row in rows:
            row["fact_id"] = f"{key}#{row['fact_id']}"
            row["store"] = key
            out.append(row)
    return out


def load_all_facts(home=None):
    """The shared store's facts and those of the promoted chat stores."""
    return load_facts() + load_chat_facts(home)


def chat_stores_overview(window_days, home=None):
    """What every per-chat / per-user database holds — counts only, never text or ids: `read` shows all of them,
    otherwise only the promoted ones. None when `chat_stores` is off."""
    read, keys = chat_stores_settings()
    if not read and not keys:
        return None
    folder, scope = chat_store_dir(home)
    if not folder:
        return {"scope": scope, "stores": [], "missing": sorted(keys),
                "note": "holographic scope is not user / chat / thread — there are no per-chat databases"}
    stores = list_chat_stores(home)
    # Counted by SQL: a farm may hold thousands of databases, and the overview must not read their texts.
    cutoff = (_now() - timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for key, path in stores.items():
        if not read and key not in keys:
            continue
        try:
            c = _conn(path)
            try:
                total, new = c.execute("select count(*), coalesce(sum(created_at > ?), 0) from facts",
                                       (cutoff,)).fetchone()
            finally:
                c.close()
        except sqlite3.Error:
            rows.append({"store": key, "error": "unreadable", "promote": key in keys})
            continue
        rows.append({"store": key, "facts": total, "new": new, "promote": key in keys})
    return {"scope": scope, "stores": rows, "missing": sorted(keys - set(stores))}


def _message_store(source, chat_id, thread_id, user_id, scope):
    """The store key the turn of a message wrote to, under *scope*; None without a chat (or user)."""
    # The plugin strips the ids before they become a file name (`_user_key`, `_chat_key`); so must this.
    if scope == "user":
        user = str(user_id or "").strip()
        return store_key(source, user) if user else None
    chat = str(chat_id or "").strip()
    if not chat:
        return None
    thread = str(thread_id or "").strip() if scope == "thread" else ""
    return store_key(source, chat, thread or None)


def _own_store_message(source, chat_id, thread_id):
    """A message of a promoted chat that the trust rules leave out still corroborates its own chat's facts —
    unless it is machine speech or from an excluded thread."""
    if (source or "") in UNTRUSTED_SOURCES:
        return False
    tid, cid = str(thread_id or ""), str(chat_id or "")
    return not (tid and tid in EXCLUDED_THREADS.get(cid, ()))


def messages_for(fact, messages, by_store=None):
    """The messages that may corroborate *fact*: its own store's for a chat-store fact, the trusted ones else."""
    store = fact.get("store")
    if by_store is not None:
        return by_store.get(store, []) if store else by_store.get(None, [])
    if store:
        return [m for m in messages if m.get("store") == store]
    return [m for m in messages if not m.get("own_only")]


def messages_by_store(messages):
    """{store key: its messages, None: the trusted messages} for messages_for()."""
    out = {None: [m for m in messages if not m.get("own_only")]}
    for m in messages:
        if m.get("store"):
            out.setdefault(m["store"], []).append(m)
    return out


FACT_SOURCES = ("holographic", "none", "sqlite", "jsonl")


def fact_source():
    """Where candidate facts come from: `$DREAM_FACT_SOURCE` > dreaming.json
    `fact_source` > `holographic`. An unknown value is an error, not a silent
    "none": a typo must not quietly turn the dream into a memory-file checker."""
    raw = os.environ.get("DREAM_FACT_SOURCE") or CONFIG.get("fact_source") or "holographic"
    src = str(raw).strip().lower()
    if src not in FACT_SOURCES:
        raise ValueError(f"fact_source {raw!r} is not one of {', '.join(FACT_SOURCES)}")
    return src


def fact_store_path(home=None):
    """Resolved path of the fact store: `$DREAM_FACT_STORE` > dreaming.json
    `fact_store_path` > (holographic only) Hermes `plugins.hermes-memory-store.db_path`
    > `$HERMES_HOME/memory_store.db`. `$HERMES_HOME`, `~` and relative paths are
    expanded the way the provider does it — an install that moved its store
    must not look empty to the dream. A generic source (`sqlite`, `jsonl`) has
    no default: None until `fact_store_path` is set. None too for holographic when
    the store's block cannot be read for sure (`hermes_config_unread`): the default
    database is a guess, maybe another one."""
    home = home or HOME
    raw = os.environ.get("DREAM_FACT_STORE") or CONFIG.get("fact_store_path")
    if not raw:
        if fact_source() != "holographic" or ".".join(_STORE) in hermes_config_unread(home):
            return None
        raw = _hermes_plugin_db_path(home) or "memory_store.db"
    raw = str(raw).replace("${HERMES_HOME}", home).replace("$HERMES_HOME", home)
    raw = os.path.expanduser(raw)
    return raw if os.path.isabs(raw) else os.path.join(home, raw)


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def _parse_excluded_threads(env_value, cfg_value):
    """``{chat_id: {thread_id, …}}`` from ``DREAM_EXCLUDED_THREADS``
    ("chat:thread,chat:thread") or from the config mapping. A malformed entry is
    dropped, never raised: a typo here must not take the nightly pass down."""
    out = {}
    if env_value is not None:
        for pair in env_value.split(","):
            chat, _, thread = pair.partition(":")
            chat, thread = chat.strip(), thread.strip()
            if chat and thread:
                out.setdefault(chat, set()).add(thread)
        return out
    if not isinstance(cfg_value, dict):
        return out
    for chat, threads in cfg_value.items():
        chat = str(chat).strip()
        if not chat or not isinstance(threads, (list, tuple, set)):
            continue
        ids = {str(t).strip() for t in threads if str(t).strip()}
        if ids:
            out[chat] = ids
    return out


def _tz(name):
    if ZoneInfo is not None and name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — unknown zone → UTC with a warning
            print(f"[dream] warn: unknown timezone {name!r}, using UTC", file=sys.stderr)
    return timezone.utc


# Effective settings — module globals so that tests (and embedding code) can
# override them; `configure()` (re)populates them from a config dict.
CONFIG = dict(DEFAULT_CONFIG)


def configure(cfg):
    global CONFIG, LOCAL_TZ, WEIGHTS, MIN_SCORE, MIN_MENTIONS, NEW_FACTS_CAP, PUBLISH_CAP
    global MD_ASK_COOLDOWN_DAYS, MD_CONFIRMED_COOLDOWN_DAYS, SEEN_COOLDOWN_DAYS
    global TRUSTED_CHAT_IDS, TRUST_PRIVATE
    global TRUST_NO_CHAT, EXCLUDED_THREADS, UNTRUSTED_SOURCES, EXCLUDE_PATTERNS
    global STOPWORDS, TOKEN_MIN_LATIN, TOKEN_MIN_OTHER, ALIAS_RULES
    global PROFILE_HINT_RE, PROFILE_CATEGORIES, DIARY_HEADING, DIARY_SECTION_RE
    global DIARY_KEEP_SECTIONS, DURABLE_MEMORY_PATHS, MEMORY_CHAR_LIMITS
    global PINNED_MARKERS, PROBES, REJECT_REASK_DAYS, MEMORY_LOSS_ALERT_FRACTION, DIARY_PATH
    global PROMOTION_COOLDOWN_DAYS, PENDING_DIR, PENDING_ALERT_MIN, PENDING_ALERT_DAYS
    global FEEDBACK_TRUST_FLOOR, FEEDBACK_CAP, KEYWORDS_ENABLED, KEYWORDS_CAP
    global FEEDBACK_FLOOR_EXPLICIT
    CONFIG = cfg
    # A timezone the config does not mention is filled in by load_config()
    # from Hermes' own config.yaml, so here it is simply used.
    LOCAL_TZ = _tz(_env("DREAM_TIMEZONE", cfg.get("timezone")))
    WEIGHTS = dict(DEFAULT_CONFIG["weights"], **(cfg.get("weights") or {}))
    gates = cfg.get("gates") or {}
    MIN_SCORE = _env("DREAM_MIN_SCORE", float(gates.get("min_score", 0.55)), float)
    MIN_MENTIONS = _env("DREAM_MIN_MENTIONS", int(gates.get("min_mentions", 3)), int)
    NEW_FACTS_CAP = _env("DREAM_NEW_FACTS_CAP", int(gates.get("new_facts_cap", 30)), int)
    PUBLISH_CAP = int(gates.get("publish_cap", 15))
    MD_ASK_COOLDOWN_DAYS = _env("DREAM_MD_ASK_COOLDOWN_DAYS",
                                int(gates.get("md_ask_cooldown_days", 14)), int)
    MD_CONFIRMED_COOLDOWN_DAYS = _env("DREAM_MD_CONFIRMED_COOLDOWN_DAYS",
                                      int(gates.get("md_confirmed_cooldown_days", 90)), int)
    PROMOTION_COOLDOWN_DAYS = _env("DREAM_PROMOTION_COOLDOWN_DAYS",
                                   int(gates.get("promotion_cooldown_days", 3)), int)
    # Two settings, two parses: a bad cap used to reset the floor too, and an explicit
    # override silently became the provider's value (Codex review of 2.7.1).
    raw_floor = gates.get("feedback_trust_floor")
    try:
        FEEDBACK_TRUST_FLOOR = 0.3 if raw_floor is None else float(raw_floor)
        if not 0.0 <= FEEDBACK_TRUST_FLOOR <= 1.0:
            raise ValueError(raw_floor)
    except (TypeError, ValueError):
        FEEDBACK_TRUST_FLOOR, raw_floor = 0.3, None  # unusable override = no override
    try:
        FEEDBACK_CAP = int(gates.get("feedback_cap", 5))
    except (TypeError, ValueError):
        FEEDBACK_CAP = 5
    # Whether the floor above is a deliberate override. Unset, `feedback_floor()`
    # asks the provider instead of guessing (resolved lazily: one config read per
    # run, and only for the holographic source).
    FEEDBACK_FLOOR_EXPLICIT = raw_floor is not None
    kw = cfg.get("keywords") if isinstance(cfg.get("keywords"), dict) else {}
    KEYWORDS_ENABLED = kw.get("enabled") is True
    try:
        KEYWORDS_CAP = max(0, int(kw.get("cap", 10)))
    except (TypeError, ValueError):
        KEYWORDS_CAP = 10
    pend = cfg.get("pending") or {}
    PENDING_DIR = str(pend.get("dir") or "pending/memory")
    try:
        PENDING_ALERT_MIN = int(pend.get("alert_min", 5))
        PENDING_ALERT_DAYS = int(pend.get("alert_days", 7))
    except (TypeError, ValueError):
        PENDING_ALERT_MIN, PENDING_ALERT_DAYS = 5, 7
    SEEN_COOLDOWN_DAYS = _env("DREAM_SEEN_COOLDOWN_DAYS",
                              int(gates.get("seen_cooldown_days", 14)), int)
    env_chats = os.environ.get("DREAM_TRUSTED_CHAT_IDS")
    if env_chats is None:  # legacy name kept for existing installs
        env_chats = os.environ.get("DREAM_FAMILY_CHAT_IDS")
    if env_chats is not None:
        TRUSTED_CHAT_IDS = {c.strip() for c in env_chats.split(",") if c.strip()}
    else:
        TRUSTED_CHAT_IDS = {str(c).strip() for c in (cfg.get("trusted_chat_ids") or []) if str(c).strip()}
    TRUST_PRIVATE = bool(cfg.get("trust_private_chats", True))
    TRUST_NO_CHAT = bool(cfg.get("trust_sessions_without_chat", True))
    EXCLUDED_THREADS = _parse_excluded_threads(
        os.environ.get("DREAM_EXCLUDED_THREADS"), cfg.get("excluded_threads"))
    env_sources = os.environ.get("DREAM_UNTRUSTED_SOURCES")
    raw_sources = (env_sources.split(",") if env_sources is not None
                   else (cfg.get("untrusted_sources") or []))
    # Machine-paced sources are not configurable away: a job prompt, a subagent
    # brief and a tool-driven turn are never speech. A list in dreaming.json
    # replaces the default instead of extending it, and the shipped example once
    # listed only `cron` (Codex review of 2.7.2, 2026-10-14).
    UNTRUSTED_SOURCES = set(ALWAYS_UNTRUSTED_SOURCES) | {str(s).strip() for s in raw_sources if str(s).strip()}
    EXCLUDE_PATTERNS = []
    for pat in (cfg.get("exclude_patterns") or []):
        try:
            EXCLUDE_PATTERNS.append(re.compile(str(pat), re.IGNORECASE))
        except re.error as exc:
            print(f"[dream] warn: exclude_patterns {pat!r} ignored: {exc}", file=sys.stderr)
    STOPWORDS = set(RU_STOP) | set(EN_STOP) | OTHER_STOP | OTHER_TEMPORAL_STOP | OTHER_POLARITY_STOP
    STOPWORDS |= {_norm(w) for w in (cfg.get("agent_names") or []) if w}
    STOPWORDS |= {_norm(w) for w in (cfg.get("extra_stopwords") or []) if w}
    tml = cfg.get("token_min_len") or {}
    TOKEN_MIN_LATIN = int(tml.get("latin", 4))
    TOKEN_MIN_OTHER = int(tml.get("other", 5))
    ALIAS_RULES = [r for r in (cfg.get("alias_rules") or []) if isinstance(r, dict)]
    terms = [t for t in (cfg.get("profile_hint_terms") or []) if t]
    PROFILE_HINT_RE = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(_norm(t)) for t in terms) + r")",
        re.IGNORECASE) if terms else None
    PROFILE_CATEGORIES = {_norm(c) for c in (cfg.get("profile_categories") or [])}
    diary = cfg.get("diary") or {}
    DIARY_HEADING = str(diary.get("heading") or "## Dream").strip()
    DIARY_SECTION_RE = re.compile(rf"^{re.escape(DIARY_HEADING)} \d{{4}}-\d{{2}}-\d{{2}}",
                                  re.MULTILINE)
    DIARY_KEEP_SECTIONS = _env("DREAM_DIARY_KEEP", int(diary.get("keep_sections", 90)), int)
    DIARY_PATH = str(diary.get("path") or "memories/DREAMS.md")
    DURABLE_MEMORY_PATHS = list(cfg.get("durable_memory_paths") or [])
    MEMORY_CHAR_LIMITS = dict(cfg.get("memory_char_limits") or {})
    PINNED_MARKERS = [str(m) for m in (cfg.get("pinned_markers") or []) if m]
    PROBES = [str(m).strip() for m in (cfg.get("probes") or []) if str(m or "").strip()]
    try:
        REJECT_REASK_DAYS = max(0, int(cfg.get("reject_reask_days", 180)))
    except (TypeError, ValueError):
        REJECT_REASK_DAYS = 180
    try:
        MEMORY_LOSS_ALERT_FRACTION = float(cfg.get("memory_loss_alert_fraction", 0.25))
    except (TypeError, ValueError):
        MEMORY_LOSS_ALERT_FRACTION = 0.25


# Threshold of stemmed containment above which a rewording counts as the same
# fact (shared by memory dedupe and the reject list).
REJECT_MATCH_THRESHOLD = 0.62

RU_STOP = set("""и в во не на я с со что а то как это он она они мы вы ты по из за к у о об от
для же бы ли так уже или но да нет там тут вот про при над под без через между есть быть был
была было были будет если когда чтобы тоже чтоб этот эта эти того этом всё все ещё уже него неё
их его её им ей мне меня тебя нас вам нам свой своя свои очень более менее как-то кто-то что-то
надо нужно можно сейчас потом тогда чтобы который которая которые этого этой этим была будет
сообщение сообщения ветка ветке чат чате топик тред тебе меня себе свои
привет коротко напомни запиши сделай расскажи покажи какие какой какая пожалуйста спасибо
твоего твоей твоих твоим твоими моего моей моих моими нашего нашей наших вашего вашей ваших
поэтому потому почему зачем одним одной словом может могут можешь пришли ответь посмотри
проверь напиши давай давайте
только просто конечно наверное например обязательно получается кажется значит вообще
совсем именно понятно видимо похоже ладно хорошо отлично понял поняла помню помнишь
думаю считаю знаю хочу хотел хотела будем сегодня завтра вчера утром вечером""".split())
# Dates in words: never a topic, and two of them ("12 сентября … завтра") were
# enough for two unrelated texts to "corroborate" each other. Kept apart from
# RU_STOP because the store-duplicate check must NOT ignore them — "12 сентября"
# and "12 октября" are different facts.
RU_TEMPORAL_STOP = set("""сегодня завтра вчера
января февраля марта апреля августа сентября октября ноября декабря""".split())
# Negations and comparatives are stopwords for corroboration (they are not a
# topic) but MUST stay significant for the store-duplicate check: «<person> ест
# острое» and «<person> не ест острое» are opposite facts, and ignoring «не»
# collapsed them into one (external review, 2026-10-07).
RU_POLARITY_STOP = set("""не нет без ни никогда нельзя
более менее больше меньше""".split())
RU_STOP |= RU_TEMPORAL_STOP
# The additions of 2026-10-02 (possessives, connectives, imperatives addressed to
# the agent, dates in words) come from the live top of "emerging themes": none
# of them is a topic.

# Latin stopwords (the tokenizer only keeps len>=4, shorter ones drop anyway).
# Platform words mirror RU_STOP (chat/thread/topic/message): otherwise any
# message with "telegram"+"thread" corroborates any routing fact (48 false
# mentions on live data). chat_id is a separate token and is not affected.
EN_STOP = set("""that this with from your have will just like what when then than they them
some more very much been does were about into over only also which would could should there
here please today okay yes
telegram thread threads topic topics chat chats group groups message messages channel
https http www""".split())

# The other languages the holographic store reads (German, Spanish, French, Portuguese, Italian, Dutch, Turkish,
# Polish, Indonesian): function words of four letters and more — shorter ones never become tokens. Without them two
# unrelated Polish lines met on «dobra» + «wieczorem», two Dutch ones on «gaat» + «naar» (2026-10-13). Words that
# are also English content words («door», «data», «solo», «fare») are left out.
OTHER_STOP = set("""aber alle allem allen aller alles also andere anderen auch beim bereits bitte bist bisschen dabei
dafür daher damit dann darauf darum dass dein deine deinem deinen deiner denn dessen dich dies diese diesem diesen
dieser dieses doch dort durch eine einem einen einer eines etwas euch euer eure für gegen gibt habe haben hast hatte
hätte hier hinter ihm ihn ihnen ihre ihrem ihren ihrer immer jede jedem jeden jeder jedes jetzt kann kannst können
könnte machen macht mein meine meinem meinen meiner mich muss müssen nach noch oder schon sehr sein seine seinem
seinen seiner seit sich sind soll sollen sollte über unser unsere unter viel vielleicht warum weil welche welchen
welcher wenn werden wieder wird wirklich wurde würde zwischen danke genau gerade
algo algunos ahora antes aquí aquel así bien cada casi como contra cual cuál cuando cuándo cuánto desde después donde
dónde durante ella ellas ellos entonces entre eran eres esta está están estar estas este esto estos fuera gracias
hace hacer hasta hola mismo mucho nosotros nuestra nuestro otra otro para pero poco porque puede pueden quien quién
sido sobre sólo somos también tanto tener tengo tiene tienen todo todos usted vamos vale será siempre esos esas
bueno
alors après aussi autre avant avec avoir beaucoup bonjour cela celle celui cette chez comme comment dans depuis donc
elle elles encore être fait faire faut leur leurs mais merci même notre nous parce pendant peut pour pourquoi quand
quel quelle quelque sera serait sont sous suis tout toute toutes tous très vous votre vers voilà ceci ceux
agora ainda algum alguma aqui assim até então essa esse está estão estou isso isto mesmo muito nossa nosso obrigado
obrigada onde outra outro pela pelo pode porquê qual quem também têm tenho tudo você vocês sempre
anche ancora avere bene cosa così dalla dalle dallo degli della delle dello dove dopo ecco essere grazie hanno
loro mentre molto nella nelle nello nostro nostra ogni allora perché però poco quale quanto quello quella questo
questa sono stato stata tanto tutti tutto vostro ciao fino
alle alles altijd ander andere omdat beetje bijna daar daarom deze doen dus eens hebben heeft iets jouw kunnen maar
moet moeten naar onze waar wanneer waarom welke willen worden wordt zijn zich zou zullen alsjeblieft bedankt gaan gaat
ancak artık bana beni benim bile biraz bize bizim bunu bunun böyle diye için kadar nasıl neden nerede niye olan
olarak oldu olur onun senin sizin şimdi sonra önce lütfen teşekkürler teşekkür tamam evet gibi
albo bardzo będzie było była były cały coś czemu dlaczego dlatego gdzie jaki jaka jakie jego jest jestem jeszcze
każdy kiedy która które który mamy może można mnie nasz nasza nawet przez również siebie sobie także tego tutaj
tylko wszystko wtedy więc zawsze żeby dziękuję proszę dobra dobrze teraz
adalah akan anda apakah atau bagaimana bahwa banyak bisa boleh dalam dari dengan jadi juga kalau kami kamu karena
kenapa kita mana masih mereka nanti oleh pada para saja sama sangat saya sedang sekali sudah tapi untuk terima kasih
""".split())
# Months and today / tomorrow / yesterday in those languages and in English: not a topic for corroboration (two
# texts dated "November" are not about one thing — a German November dentist visit corroborated a remark about the
# November rain), but significant for the store-duplicate check, like RU_TEMPORAL_STOP.
OTHER_TEMPORAL_STOP = set("""january february march april june july august september october november december
januar februar märz juni juli oktober dezember heute morgen gestern
enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre hoy mañana ayer
janvier février mars avril juin juillet août septembre octobre novembre décembre aujourd demain hier
janeiro fevereiro março maio junho julho setembro outubro novembro dezembro hoje amanhã ontem
gennaio febbraio aprile maggio giugno luglio settembre ottobre dicembre oggi domani ieri
januari februari maart augustus vandaag gisteren
ocak şubat mart nisan mayıs haziran temmuz ağustos eylül ekim kasım aralık bugün yarın
styczeń stycznia luty lutego marzec marca kwiecień kwietnia maja czerwiec czerwca lipiec lipca sierpień sierpnia
wrzesień września październik października listopad listopada grudzień grudnia dzisiaj jutro wczoraj
maret juni agustus desember besok kemarin""".split())
# Negations and comparatives: stopwords for corroboration, significant for the store-duplicate check (RU_POLARITY_STOP).
OTHER_POLARITY_STOP = set("""nicht kein keine keinen keiner niemals ohne mehr weniger
nunca nada nadie ningún ninguna jamás menos más
jamais rien personne sans moins plus
nenhum nenhuma mais
niente nessuno senza meno più
niet geen nooit niets zonder meer minder
değil asla
nigdy żaden więcej mniej
tidak bukan belum tanpa lebih kurang""".split())
TEMPORAL_STOP = RU_TEMPORAL_STOP | OTHER_TEMPORAL_STOP
POLARITY_STOP = RU_POLARITY_STOP | OTHER_POLARITY_STOP


def _conn(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fold(s):
    """Unicode NFC plus ё→е.

    The same word arrives in different shapes: "ёлка"/"елка" are two strings,
    and a decomposed "й" (и + U+0306) is a third one after a copy-paste out of
    a browser. Both broke comparison silently — the tokens simply did not meet
    (external review, 2026-10-07)."""
    return unicodedata.normalize("NFC", s or "").replace("ё", "е").replace("Ё", "Е")


def _norm(s):
    return re.sub(r"\s+", " ", _fold(s).lower()).strip()


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# Reply quote: [Replying to: "…"] — the quoted text is unwrapped, not cut.
REPLY_QUOTE_RE = re.compile(r'\[replying\s+to:?\s*[«"“]?(.*?)[»"”]?\s*\]',
                            re.IGNORECASE | re.DOTALL)
# Voice message: [The user sent a voice message~ Here's what they said: "…"].
# The transcript is human speech and must be unwrapped like a reply quote:
# wrapped in brackets it yielded 0 tokens, i.e. voice messages (78 of 1103 in
# 60 days on live data) corroborated nothing. Image descriptions (`sent an
# image`) deliberately stay metadata: that is the model's retelling, not words
# of a human.
VOICE_QUOTE_RE = re.compile(
    r'\[the user sent a voice message[^:\]]*:\s*[«"“]?(.*?)[»"”]?\s*\]',
    re.IGNORECASE | re.DOTALL)

# Harness envelopes: context-compaction banner and skill-body injection. They
# are role=user in history but contain no human text — big English blocks that
# any English fact latches onto through generic words ("context"+"unless").
# Live case: a dead fact survived 34 nights on 30 "mentions in 12 days" — all
# 30 were compaction banners.
HARNESS_ENVELOPE_RE = re.compile(
    r"^\s*\[(?:context\s+compaction|important:\s*the\s+user\s+has\s+invoked)",
    re.IGNORECASE)
# Service text the harness persists as an ordinary message. Matched ANYWHERE in
# the text, not just at the start: the core glues the todo snapshot into a real
# reply, and a failure notice can be quoted (exact strings from Hermes 0.21.5 —
# tools/todo_tool.py:21, agent/turn_failure_copy.py:39-45,208).
HARNESS_INSERTS = (
    "[Your active task list was preserved across context compression]",
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects",
    "Operation interrupted: waiting for model response (",
    "Operation interrupted.",
)


def is_harness_envelope(text):
    """True for harness service inserts that are not human speech."""
    raw = text or ""
    if HARNESS_ENVELOPE_RE.match(raw):
        return True
    return any(ins in raw for ins in HARNESS_INSERTS)


# A URL is a locator, not speech, and has no place in corroboration. It broke
# it from both sides: tracking params (`fbclid`, `tn__`, `cft__`) longer than 8
# chars became "distinctive" tokens where ONE match suffices; and host+path do
# not help either — two DIFFERENT links share `facebook`+`posts`, i.e. an
# overlap ≥2 and a false mention. A bare URL now yields 0 tokens (a link alone
# is not a durable fact), words around it are untouched.
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def strip_urls(text):
    return URL_RE.sub(" ", text or "")


TOKEN_RE = re.compile(r"[^\W\d_][\w\-]*", re.UNICODE)

# Scripts written without spaces between words. A Chinese, Japanese or Thai sentence is one regex "word", so two
# texts about the same thing shared no token and nothing in these languages was ever corroborated (0 of 30
# questions found their fact, 2026-10-13). Such runs are compared by character n-grams instead, as the holographic
# store compares them: pairs for Chinese/Japanese and Korean, triples for Thai, Lao, Khmer and Myanmar, whose
# characters are letters. Korean is written with spaces, but its particles stick to the word (레나는 = Lena + topic).
_HAN_KANA = "々぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ"
_HANGUL = "가-힯"
_SEA = "฀-໿က-႟ក-៿"
UNSEGMENTED_RE = re.compile(f"[{_HAN_KANA}]+|[{_HANGUL}]+|[{_SEA}]+")
_SEA_RE = re.compile(f"[{_SEA}]")
_HIRAGANA_RE = re.compile("[぀-ゟ]+")
_HAN_RE = re.compile("[々㐀-䶿一-鿿豈-﫿]")
# Chinese function characters (particles, pronouns, question words, auxiliaries) and Japanese particles: a pair
# made only of them (什么, 我们, には) carries no topic and is dropped; a pair with one of them counts half.
CJK_FUNCTION = frozenset("的是了吗呢吧啊呀哦嗯么怎什哪谁几多少在有和与及或就都也还要会能可以把被给让对为从到这那你我他她它们个一不没很太"
                         "該應些嘛們這個嗎麼還讓從對為會說說来來裡里"
                         "はがをにのでともへやかねよなだ")
# Thai function words: a triple inside one of them carries no topic.
TH_FUNCTION = sorted(set("""ของ ที่ และ ใน กับ ไม่ ได้ จะ แล้ว ไหม อะไร ยังไง อย่างไร คือ เป็น มี ให้ ครับ ค่ะ คะ นะ เลย ด้วย
    แบบ ไหน เท่าไหร่ กี่ ว่า ก็ หรือ แต่ จาก ถึง เรา ฉัน ผม คุณ เขา นี้ นั้น หน่อย ควร วันนี้ พรุ่งนี้ เมื่อวาน""".split()),
                     key=len, reverse=True)
# Whole runs that are chat fillers ("ok", "thanks", "yes"): no n-grams at all.
FILLER_RUNS = frozenset("""はい うん ええ いいえ 了解 了解です りょうかい オーケー ありがとう ありがとうございます お願い
    お願いします よろしく そう そうです わかった わかりました 好的 好 谢谢 謝謝 没问题 對 对 是的 嗯嗯 哈哈 哈哈哈
    네 예 응 아니 아니요 좋아 좋아요 그래 그래요 알겠어 알겠어요 알겠습니다 오케이 고마워 고마워요 감사 감사합니다
    ครับ ค่ะ คะ นะ จ้ะ จ้า โอเค ได้ ได้เลย ใช่ ขอบคุณ ขอบคุณครับ ขอบคุณค่ะ ขอบคุณมาก""".split())


def is_gram(token):
    """True for an n-gram of unsegmented script (see UNSEGMENTED_RE), False for a word."""
    return bool(token) and UNSEGMENTED_RE.match(token[0]) is not None


def gram_weight(gram):
    """How much one shared n-gram is worth: 1, or ½ with a function character / all kana, 0 for no topic."""
    if _SEA_RE.match(gram):
        return 0.0 if any(gram in w for w in TH_FUNCTION) else 1.0
    hits = sum(c in CJK_FUNCTION for c in gram)
    if hits == len(gram):  # before the kana rule: には, ので are particles, not half a word (review 2026-10-13)
        return 0.0
    return 0.5 if hits or _HIRAGANA_RE.fullmatch(gram) else 1.0


def run_grams(run):
    """Content n-grams of one unsegmented run; a run of one character (a lone 猫 between Latin words) gives none."""
    if run in FILLER_RUNS:
        return set()
    n = 3 if _SEA_RE.match(run) else 2
    if len(run) < n:
        return set()
    return {g for g in (run[i:i + n] for i in range(len(run) - n + 1)) if gram_weight(g) > 0}


def _keep_token(tok):
    if tok in STOPWORDS:
        return False
    if tok.isascii():
        return len(tok) >= TOKEN_MIN_LATIN
    return len(tok) >= TOKEN_MIN_OTHER


def _raw_tokens(text):
    """Significant tokens of an already unwrapped string (brackets are metadata): words, and the n-grams of the
    runs of unsegmented script (cut out first, so "ACME的老板" gives the word "acme" and the pair "老板")."""
    low = re.sub(r"\[[^\]]*\]", " ", strip_urls(_fold(text))).lower()
    grams = set()
    for run in UNSEGMENTED_RE.findall(low):
        grams |= run_grams(run)
    words = {w for w in TOKEN_RE.findall(UNSEGMENTED_RE.sub(" ", low)) if _keep_token(w)}
    return words | (grams - STOPWORDS)


def sig_tokens(text):
    """Signature tokens: Unicode words without stopwords (Latin ≥4, other ≥5 chars).

    Reply quotes and voice transcripts are unwrapped BEFORE bracket blocks are
    cut: a family reply to a message about an entry is a mention of the entry
    (otherwise a short "yes, still Vespa" gives 0 tokens and the dream asks the
    same thing every night). Other [..] blocks (sender tags, [id=N], image
    descriptions) are still cut as metadata."""
    raw = text or ""
    # A reply quote counts only when the person added words of their own. A bare
    # "ок" under a quoted cron report used to corroborate everything the report
    # mentioned — including the entries it proposed to drop (external review,
    # 2026-10-07). The live case that put the unwrapping here ("Да, всё ещё
    # Vespa") keeps working: its tail has its own tokens.
    tail = VOICE_QUOTE_RE.sub(" ", REPLY_QUOTE_RE.sub(" ", raw))
    tail_tokens = _raw_tokens(tail)
    quoted = " ".join(m.group(1) for m in REPLY_QUOTE_RE.finditer(raw))
    if not tail_tokens and quoted:
        return set()
    if quoted and MACHINE_QUOTE_RE.search(quoted):
        # Quoting a machine (a cron report, a script block) is not speech about
        # the subject, however much the person adds around it.
        return tail_tokens
    unwrapped = REPLY_QUOTE_RE.sub(lambda m: " " + m.group(1) + " ", raw)
    unwrapped = VOICE_QUOTE_RE.sub(lambda m: " " + m.group(1) + " ", unwrapped)
    return _raw_tokens(unwrapped)


MACHINE_QUOTE_RE = re.compile(
    r"cronjob\s+response|##\s*script\s+output|\[dream[\]-]|wakeagent", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _missing_db(db, what):
    """A database the core has not created yet is 'nothing to work with', not a
    crash. On a fresh install `memory_store.db` appears with the first stored
    fact and `state.db` with the first session; without this the nightly job
    reported a traceback-flavoured `dream_error` every night until then."""
    if os.path.exists(db):
        return False
    print(f"[dream] note: {db} does not exist yet — {what}", file=sys.stderr)
    return True


FACT_FIELDS = ["fact_id", "content", "category", "tags", "trust_score", "retrieval_count",
               "helpful_count", "created_at", "updated_at"]
# Generic sources (`sqlite`, `jsonl`): column / key names of the user's own
# store. Only `id` and `content` are required; a missing optional field gets the
# neutral value the holographic store would have (trust 0.5, no retrievals) —
# the score is then carried by corroboration, as it mostly is anyway.
DEFAULT_FACT_COLUMNS = {"id": "id", "content": "content", "created_at": "created_at",
                        "updated_at": "updated_at", "category": "category", "tags": "tags",
                        "trust": "trust"}


def load_facts(db=None):
    """Facts of the configured source (`fact_source`) as dicts with FACT_FIELDS.

    `holographic` — the Hermes holographic provider's SQLite `facts` table;
    `sqlite` — any SQLite table, columns mapped by `fact_columns`;
    `jsonl` — one JSON object per line, keys mapped the same way;
    `none` — no fact store: the dream works on the memory files alone.
    Every store is opened read-only. `db` forces the holographic schema at that
    path (tests and old callers)."""
    src = "holographic" if db else fact_source()
    if src == "none":
        return []
    path = db or fact_store_path()
    if not path:
        why = ("plugins.hermes-memory-store of config.yaml cannot be read for sure" if src == "holographic"
               else "fact_store_path is not set")
        print(f"[dream] note: fact_source={src} but {why} — no facts", file=sys.stderr)
        return []
    if _missing_db(path, "treating the fact store as empty"):
        return []
    if src == "holographic":
        return _load_holographic(path)
    cols = dict(DEFAULT_FACT_COLUMNS, **(CONFIG.get("fact_columns") or {}))
    raw = _load_generic_sqlite(path, cols) if src == "sqlite" else _load_jsonl(path, cols)
    return [f for f in (_normalize_fact(r, cols) for r in raw) if f]


def _load_holographic(db):
    rows = []
    c = _conn(db)
    try:
        origins = _fact_origins(c)
        for r in c.execute("select " + ",".join(FACT_FIELDS) + " from facts"):
            row = dict(zip(FACT_FIELDS, r))
            row["tags"], row["keywords"] = split_keywords(row.get("tags"))
            if origins.get(row["fact_id"]):
                row["origin"] = origins[row["fact_id"]]
            rows.append(row)
    finally:
        c.close()  # the sqlite3 context manager commits but does NOT close
    return rows


ORIGINS = ("said", "confirmed", "inferred")


def _fact_origins(conn):
    """{fact_id: origin} (holographic >= 0.15.0): who stands behind a fact's current text — `said` (a person said
    it), `confirmed`, `inferred` (an agent's conclusion). An inference or an unknown origin a person confirmed since
    reads as `confirmed`, as the provider shows it. Unknown (stored before 0.15.0, or an older schema: the column
    and the table appear on a writer's open) is left out. Read-only; never part of the `facts` query, which the
    provider keeps stable for this reader."""
    if "src_origin" not in _table_columns(conn, "fact_versions"):
        return {}
    try:
        out = {fid: o for fid, o in conn.execute(
            "SELECT fact_id, src_origin FROM fact_versions WHERE status = 'current'") if o in ORIGINS}
        if _table_columns(conn, "fact_confirmations"):
            for (fid,) in conn.execute("SELECT DISTINCT fact_id FROM fact_confirmations"):
                if out.get(fid) in (None, "inferred"):
                    out[fid] = "confirmed"
        return out
    except sqlite3.Error:
        return {}


KEYWORDS_MARK = "keywords: "


def split_keywords(tags):
    """(the human's tags, the keywords) of a holographic `tags` value.

    holographic >= 0.6.0 appends the agent's keywords after a "keywords: "
    line. They are search aids, not tags: left in, a fact with eight synonyms
    scored as "conceptually rich" and climbed towards promotion."""
    text = tags or ""
    if text.startswith(KEYWORDS_MARK):
        head, tail = "", text[len(KEYWORDS_MARK):]
    else:
        head, mark, tail = text.rpartition("\n" + KEYWORDS_MARK)
        if not mark:
            return text, []
    return head, [w.strip() for w in tail.split(",") if w.strip()]


_SQL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_generic_sqlite(db, cols):
    table = str(CONFIG.get("fact_table") or "facts")
    if not _SQL_NAME_RE.match(table):
        raise ValueError(f"fact_table {table!r} is not a plain SQL name")
    c = _conn(db)
    try:
        have = {r[1] for r in c.execute(f'pragma table_info("{table}")')}
        if not have:
            raise ValueError(f"table {table!r} not found in {db}")
        for need in ("id", "content"):
            if cols[need] not in have:
                raise ValueError(f"column {cols[need]!r} ({need}) not in table {table!r}")
        names = [n for n in dict.fromkeys(cols.values()) if n in have]
        cur = c.execute("select " + ",".join(f'"{n}"' for n in names) + f' from "{table}"')
        return [dict(zip(names, r)) for r in cur]
    finally:
        c.close()


def _load_jsonl(path, cols):
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                print(f"[dream] warn: {path}:{n} is not JSON — skipped", file=sys.stderr)
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _ts_text(v):
    """A timestamp in the holographic text form ('YYYY-MM-DD HH:MM:SS', UTC)
    from epoch seconds / milliseconds or an ISO string with any offset."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        sec = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s  # leave it to _parse_ts; an unparsable date only weakens recency
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_fact(r, cols):
    content = r.get(cols["content"])
    fid = r.get(cols["id"])
    if not isinstance(content, str) or not content.strip() or fid in (None, ""):
        return None
    tags = r.get(cols["tags"])
    if isinstance(tags, (list, tuple)):
        tags = ",".join(str(t) for t in tags)
    trust = r.get(cols["trust"])
    try:
        trust = float(trust) if trust is not None else 0.5
    except (TypeError, ValueError):
        trust = 0.5
    created = _ts_text(r.get(cols["created_at"]))
    return {"fact_id": fid, "content": content, "category": r.get(cols["category"]),
            "tags": tags or "", "trust_score": trust, "retrieval_count": 0, "helpful_count": 0,
            "created_at": created, "updated_at": _ts_text(r.get(cols["updated_at"])) or created}


def _table_columns(conn, table):
    """Column names of `table` ([] when it does not exist). The query is built
    from this instead of catching OperationalError three times over."""
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _trusted_message(source, chat_type, chat_id, thread_id=None):
    """Only humans corroborate memory: no machine-driven sources, no group chats
    outside the trusted list (fail-closed), no excluded threads of a trusted
    chat, private/legacy sessions per config.

    ``thread_id`` is optional on purpose: callers on a schema without threads
    (and older embedders) keep working, and an unknown thread is treated as
    "not excluded" — the chat-level decision stands."""
    if (source or "") in UNTRUSTED_SOURCES:
        return False
    ctype = (chat_type or "")
    cid = str(chat_id or "")
    if cid and cid in TRUSTED_CHAT_IDS:
        tid = str(thread_id or "")
        return not (tid and tid in EXCLUDED_THREADS.get(cid, ()))
    if ctype in ("group", "supergroup", "channel", "forum"):
        return False
    if ctype in ("dm", "private"):
        return TRUST_PRIVATE
    if not ctype and not cid:
        return TRUST_NO_CHAT
    # Unknown chat type with an id that is not trusted — be conservative.
    return TRUST_PRIVATE if not cid else False


def _day_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def load_messages(db, days):
    """Human messages (user + observed) for `days`, with tokens and local day."""
    if _missing_db(db, "no conversations to corroborate with"):
        return []
    cutoff = _now().timestamp() - days * 86400
    out = []
    c = _conn(db)
    try:
        msg_cols = _table_columns(c, "messages")
        sess_cols = _table_columns(c, "sessions")
        if not msg_cols:
            print("[dream] warn: no messages table — nothing corroborates", file=sys.stderr)
            return []
        # Role and visibility go into the WHERE, not into Python: a 60-day
        # window on a busy install is tens of thousands of rows, 90% of them
        # tool output (53k rows / ~200M chars and ~0.5 GB RSS on the install the
        # external review measured). The flags are the core's own visibility
        # model: active=1 is a live row, compacted=1 is folded by compression
        # but still visible, 0/0 is rewound — and a rewound row is not speech.
        where = ["m.timestamp>=?", "m.content is not null", "m.content != ''"]
        if "observed" in msg_cols:
            where.append("(m.role='user' or m.observed=1)")
        else:
            where.append("m.role='user'")
        if "active" in msg_cols and "compacted" in msg_cols:
            where.append("(coalesce(m.active,1)=1 or coalesce(m.compacted,0)=1)")
        cols = ["m.id", "m.role", "m.content", "m.timestamp",
                "m.observed" if "observed" in msg_cols else "0",
                "m.session_id"]
        if sess_cols:
            cols += ["s.source", "s.chat_type", "s.chat_id",
                     "s.thread_id" if "thread_id" in sess_cols else "null",
                     "s.user_id" if "user_id" in sess_cols else "null"]
            join = " left join sessions s on m.session_id = s.id"
        else:
            # No usable `sessions` table: the source of a message is unknown.
            # Fail closed, not open — without it a cron job's own prompt rows
            # (role=user) corroborated facts. What is still known is the session
            # id: Hermes names cron sessions `cron_…`, so those are dropped
            # outright; the rest are "sessions without chat" and go through the
            # same config switch as any other session lacking chat metadata.
            print("[dream] warn: sessions table unavailable — cron sessions dropped by id, "
                  "the rest trusted only if trust_sessions_without_chat", file=sys.stderr)
            cols += ["null", "null", "null", "null", "null"]
            join = ""
        sql = (f"select {', '.join(cols)} from messages m{join} "
               f"where {' and '.join(where)}")
        try:
            rows = list(c.execute(sql, (cutoff,)))
        except sqlite3.OperationalError as exc:
            print(f"[dream] warn: message query failed ({exc}) — corroboration skipped",
                  file=sys.stderr)
            return []
    finally:
        c.close()
    seen_rows = set()
    # Promoted chat stores: each message learns which store its turn wrote to, so a chat-store fact is
    # corroborated by its own chat only; a promoted chat's message outside the trusted list counts for nothing else.
    _read, promote = chat_stores_settings() if fact_source() == "holographic" else (False, set())
    scope = holographic_scope() if promote else "profile"
    for row in rows:
        mid, role, content, ts, observed, sid = row[:6]
        source, chat_type, chat_id, thread_id, user_id = row[6:11]
        if not sess_cols and str(sid or "").startswith("cron_"):
            source = "cron"
        if not content or not (role == "user" or observed == 1):
            continue
        store = _message_store(source, chat_id, thread_id, user_id, scope) if promote else None
        own_only = False
        if not _trusted_message(source, chat_type, chat_id, thread_id):
            if store in promote and _own_store_message(source, chat_id, thread_id):
                own_only = True
            else:
                continue
        if is_harness_envelope(content):
            continue
        if any(p.search(content) for p in EXCLUDE_PATTERNS):
            continue
        # Compression copies the row it folds, and both copies are visible. The
        # same sentence must not corroborate twice (it was worth +0.04 of score
        # per copy, enough to cross the 0.55 gate). Same key as the core's own
        # _dedupe_display_generations: session, text, timestamp.
        key = (sid, content, ts)
        if key in seen_rows:
            continue
        seen_rows.add(key)
        out.append({"id": mid, "content": content, "ts": ts, "day": _day_of(ts),
                    "tokens": sig_tokens(content), "store": store if store in promote else None,
                    "own_only": own_only, "chat_type": chat_type, "user_id": user_id})
    return out


# ---------------------------------------------------------------------------
# Corroboration and scoring
# ---------------------------------------------------------------------------

EVIDENCE_MAX = 3
# One shared word is evidence only when the word is rare. Latin keeps the
# historical 8 characters; Cyrillic needs more — see corroborate().
DISTINCTIVE_LATIN = 8
DISTINCTIVE_OTHER = 10


# Shared n-grams count by the stretches of text they cover, not one by one: the three pairs of ソフィア are one
# name, as "Sofia" is one word. Pairs that chain (ソフ → フィ → ィア) are one shared stretch; a mention needs two
# stretches, or a stretch and a word — as it needs two shared words in spaced scripts. A stretch of a name alone
# would otherwise corroborate every fact about that person. A stretch with only half-weight n-grams (a particle
# glued to a name, ンは) counts half; a long stretch is rare enough to count alone, as a long word does.
DISTINCTIVE_GRAMS = {"cjk": 5, "hangul": 4, "sea": 6}


def _gram_script(gram):
    return "sea" if _SEA_RE.match(gram) else "hangul" if "가" <= gram[0] <= "힯" else "cjk"


def gram_stretches(grams):
    """Groups of n-grams that chain into one stretch of text (each overlaps the next by all but one character)."""
    # Linear, not pairwise: every n-gram ending in k chains with every n-gram starting with k, so all of them are one
    # stretch — joined through k once instead of edge by edge (a character shared by hundreds of pairs, 一中 … 中一,
    # made the pairwise version quadratic; review of 2.5.1). A key nobody continues joins nothing.
    grams = set(grams)
    heads = {g[:-1] for g in grams}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g in grams:  # ends in a key that some n-gram starts with: join the key
        find(g)
        if g[1:] in heads:
            parent[find(g)] = find(("key", g[1:]))
    for g in grams:  # starts with a key that some n-gram ends in: join the same key
        key = ("key", g[:-1])
        if key in parent:
            parent[find(g)] = find(key)
    groups = defaultdict(set)
    for g in grams:
        groups[find(g)].add(g)
    return sorted(groups.values(), key=min)


def _mentions(shared):
    """Whether a shared set of stems is a mention: two words or stretches of n-grams, or one long stretch (a rare
    shared word is checked by the caller, see DISTINCTIVE_PREFIX_LATIN)."""
    score = sum(1 for s in shared if not is_gram(s))
    if score >= 2:
        return True
    for group in gram_stretches(s for s in shared if is_gram(s)):
        if len(group) >= DISTINCTIVE_GRAMS[_gram_script(next(iter(group)))]:
            return True
        weight = max(gram_weight(g) for g in group)
        # Chinese writes several words without a break (搬到上海 = "move to Shanghai"): a stretch of Han
        # characters weighs one word per two characters; kana, Hangul and Thai stretches are mostly one word.
        if any(_HAN_RE.search(g) for g in group):
            weight *= max(1.0, (len(group) + 1) / 2)
        elif len(group) == 1 and _SEA_RE.match(next(iter(group))):
            # Three Thai letters are a piece of a word as often as a word (น้ำ "water" sits in ห้องน้ำ
            # "bathroom" and in ว่ายน้ำ "swim"): alone they weigh half (blind Thai set, 4.4 % false, 2026-10-13).
            weight *= 0.5
        score += weight
    return score >= 2


# One shared rare word is a mention only when the two words share more than the 5-letter stem: «commercial» is
# rare, but its stem «comme» is also the stem of «comment» and «commence», «Schauinsland» shares «schau» with
# «schaut», «gidiyoruz» shares «gidiy» with every "we go" (French 3,3 % false mentions, 2026-10-13). Six letters
# keep «Lissabon»/«Lissabone», «transcription»/«transcribe». Cyrillic keeps its stem (endings are cut, not a prefix).
DISTINCTIVE_PREFIX_LATIN = 6


# Days of the week are long words in most languages (Dienstag, понедельник, niedziela) but never rare: a weekday
# still counts as one shared word, never as a rare one that is a mention alone.
WEEKDAY_PREFIXES = tuple("""понедельник вторник среда среду среды четверг пятниц суббот воскресен
monday tuesday wednesday thursday friday saturday sunday
montag dienstag mittwoch donnerstag freitag samstag sonnabend sonntag
lunes martes miércoles jueves viernes sábado domingo lundi mardi mercredi jeudi vendredi samedi dimanche
segunda-feira terça-feira quarta-feira quinta-feira sexta-feira lunedì martedì mercoledì giovedì venerdì sabato domenica
maandag dinsdag woensdag donderdag vrijdag zaterdag zondag pazartesi çarşamba perşembe cumartesi
poniedział wtorek wtorki środa środy środę czwart piąt sobot niedziel senin selasa kamis jumat sabtu""".split())


def _distinctive_stem(token):
    """(script, stem) — tagged, so a Latin prefix never meets the stem of a mixed token: «telegram_react» and
    «telegram-юзернейм» share six letters, not a word (live data, 2026-10-13)."""
    latin = latin_fold(token)
    return ("latin", latin[:DISTINCTIVE_PREFIX_LATIN]) if latin.isascii() else ("other", _stem(token))


def corroborate(item_tokens, messages, evidence=False):
    """How many different days / messages mention the token set.

    Match: overlap ≥2 STEMS, or ≥1 "distinctive" stem. Stems, because Russian
    inflects: «Мы окончательно переехали, в Лиссабоне…» did not corroborate
    «Илья переехал в Лиссабон» while «Мой брат переехал в Москву» did (external
    review, 2026-10-07). "Distinctive" means rare enough that one hit is
    evidence — and 8 characters is not rare in Russian (41-45% of words reach
    it: «например», «обязательно»), hence a higher bar for non-Latin.
    Returns (mentions, ref_days, last_ts) or, with evidence=True,
    (mentions, ref_days, last_ts, samples) where samples are up to
    EVIDENCE_MAX (day, snippet) pairs from different days — provenance for the
    reviewer ("why does the dream think this is confirmed?")."""
    if not item_tokens:
        return (0, 0, None, []) if evidence else (0, 0, None)
    item_stems = _stems(item_tokens)
    distinctive = {_distinctive_stem(t) for t in item_tokens if not is_gram(t)
                   and len(t) >= (DISTINCTIVE_LATIN if latin_fold(t).isascii() else DISTINCTIVE_OTHER)
                   and not t.startswith(WEEKDAY_PREFIXES)}
    days, mentions, last_ts, samples = set(), 0, None, []
    for m in messages:
        inter = item_stems & _stems(m["tokens"])
        rare = bool(distinctive) and not distinctive.isdisjoint(
            _distinctive_stem(t) for t in m["tokens"] if not is_gram(t))
        if rare or _mentions(inter):
            mentions += 1
            # A message still counts as a mention, but its text travels to the
            # agent only when it is clean: the fact was screened by
            # classify_unsafe, the corroborating message was not — a
            # `password: hunter2` said in chat leaked through `evidence`.
            if (evidence and m["day"] not in days and len(samples) < EVIDENCE_MAX
                    and not classify_unsafe(m["content"])):
                samples.append({"day": m["day"], "text": _snippet(m["content"])})
            days.add(m["day"])
            last_ts = max(last_ts or 0, m["ts"])
    return (mentions, len(days), last_ts, samples) if evidence else (mentions, len(days), last_ts)


def _snippet(text, n=100):
    text = re.sub(r"\s+", " ", strip_urls(text or "")).strip()
    return text[:n] + ("…" if len(text) > n else "")


def signals_for_fact(f, messages):
    now = _now()
    rc = f.get("retrieval_count") or 0
    hc = f.get("helpful_count") or 0
    trust = f.get("trust_score")
    trust = 0.5 if trust is None else float(trust)
    created = _parse_ts(f.get("created_at"))
    updated = _parse_ts(f.get("updated_at")) or created
    tags = [t for t in re.split(r"[,\s]+", f.get("tags") or "") if t]

    toks = sig_tokens(f.get("content"))
    mentions, ref_days, last_ts, evidence = corroborate(toks, messages, evidence=True)

    days_updated = (now - updated).total_seconds() / 86400 if updated else 999
    days_last_ref = (now.timestamp() - last_ts) / 86400 if last_ts else 999
    eff_recent = min(days_updated, days_last_ref)
    span_days = ((updated - created).total_seconds() / 86400) if (created and updated) else 0

    relevance = min(1.0, 0.6 * trust + 0.4 * min(hc / 3.0, 1.0))
    frequency = min(1.0, (rc + mentions) / 6.0)          # stored retrievals + chat mentions
    query_diversity = min(1.0, ref_days / 3.0)           # real number of distinct days
    recency = math.exp(-max(eff_recent, 0) / 30.0)
    consolidation = min(1.0, max(span_days / 14.0, (ref_days - 1) / 3.0 if ref_days > 1 else 0.0))
    conceptual_richness = min(1.0, len(tags) / 3.0)

    sig = {"relevance": relevance, "frequency": frequency, "query_diversity": query_diversity,
           "recency": recency, "consolidation": consolidation, "conceptual_richness": conceptual_richness}
    score = round(sum(WEIGHTS[k] * v for k, v in sig.items()), 3)
    meta = {"days_old": round(days_updated, 1), "rc": rc, "mentions": mentions, "ref_days": ref_days,
            "evidence": evidence, "last_ts": last_ts or 0}
    return score, {k: round(v, 3) for k, v in sig.items()}, meta


# ---------------------------------------------------------------------------
# Durable memory: dedupe
# ---------------------------------------------------------------------------

def _memory_candidate_paths(mem_md):
    """Durable memory/profile files already injected into future turns.

    Facts may already be represented in USER.md or in repo-side copies, so
    promotions must dedupe against all of them — otherwise the dream keeps
    proposing the same profile facts forever."""
    paths = [mem_md]
    for p in DURABLE_MEMORY_PATHS:
        p = os.path.expanduser(str(p))
        paths.append(p if os.path.isabs(p) else os.path.join(HOME, p))
    seen, out = set(), []
    for p in paths:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            out.append(ap)
    return out


# The unit of durable memory is the §-ENTRY, not the paragraph. The core
# splits on the FULL delimiter "\n§\n" (so a bare § inside a line survives),
# strips each entry and drops empty ones — and `memory replace` locates an
# entry by `old_text` and then rewrites THAT WHOLE ENTRY
# (tools/memory_tool_store.py:23, 298-302, 510-512).
#
# This script used to split on blank lines as well. Two consequences, both
# reported from a live install (external review of v2.1.1, 2026-10-07): an
# anchor taken from the second paragraph of a multi-paragraph entry made the
# agent's replace wipe the entry whole (6 261 → 996 chars on a real
# MEMORY.md), and an anchor with collapsed whitespace was not found at all
# because the core matches `old_text` as a RAW substring.
MEMORY_ENTRY_DELIMITER = "\n§\n"


def _read_memory_file(path):
    """Memory file as the core reads it: utf-8-sig, so a BOM never becomes part
    of the first entry (and of an anchor built from it)."""
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


LEADING_DELIMITER_RE = re.compile("^[" + chr(92) + "s" + chr(0xFEFF) + "]*§[ " + chr(92) + "t]*\r?\n")


def _memory_entries(text):
    """§-entries exactly as the core parses them.

    One documented tolerance: a delimiter at the very START of the file. The
    core never writes one (it joins entries with the delimiter), but a hand
    edit often does, and then the core carries a stray "§" into entry #1 —
    which would show up in our preview, our anchor and our loss-guard key."""
    raw = LEADING_DELIMITER_RE.sub("", text or "", count=1)
    return [e for e in (p.strip() for p in raw.split(MEMORY_ENTRY_DELIMITER)) if e]


def load_durable_memory_text(mem_md):
    chunks = []
    for path in _memory_candidate_paths(mem_md):
        if os.path.exists(path):
            try:
                text = _read_memory_file(path)
            except OSError:
                continue
            # CRLF не особый случай: ядро читает память через read_text, то есть
            # с универсальными переводами строк — \r\n приходит как \n (проверено
            # на 0.21.5). Наш open() в текстовом режиме делает то же.
            chunks.append(text)
    # Joined with the delimiter, not a blank line: otherwise the last entry of
    # one file and the first of the next merge into one comparison unit.
    return MEMORY_ENTRY_DELIMITER.join(chunks)


def _memory_chunks(memory_text):
    """Comparison units for fuzzy dedupe — the core's §-entries."""
    return _memory_entries(memory_text) or [(memory_text or "").strip()]


def load_durable_memory_sources(mem_md):
    """[(target, chunk)] for the files the agent can actually EDIT.

    Only the runtime pair — `--memory-md` (target `memory`) and USER.md next to
    it (target `user`) — is included. The other `durable_memory_paths` are
    read-only copies used for dedupe; pointing the agent at those would send it
    to edit a file its `memory` tool does not own."""
    out = []
    for target, path in (("memory", mem_md),
                         ("user", os.path.join(os.path.dirname(mem_md), "USER.md"))):
        if not path or not os.path.exists(path):
            continue
        try:
            text = _read_memory_file(path)
        except OSError:
            continue
        out.extend((target, entry) for entry in _memory_entries(text))
    return out


NUM_TERM_RE = re.compile(r"^#(\d+)$")


def _term_hit(term, text):
    """`#437` → whole number 437 (not 2024 / 4370); anything else → substring."""
    m = NUM_TERM_RE.match(term)
    if m:
        return re.search(rf"(?<!\d){re.escape(m.group(1))}(?!\d)", text) is not None
    return term in text


def _groups_hit(groups, text):
    """AND of OR-groups: [["a","b"],["c"]] ⇒ (a or b) and c."""
    if not groups:
        return False
    for group in groups:
        if isinstance(group, str):
            group = [group]
        if not any(_term_hit(_norm(t), text) for t in group if t):
            return False
    return True


def _semantic_memory_alias(content, memory_text, rules=None):
    """Declarative aliases for recurring facts that get reworded.

    Token overlap alone misses inflection/synonyms. Each rule is
    {"fact": [[…],[…]], "memory": [[…]]}: when the fact matches its groups AND
    durable memory matches its groups, the fact counts as already known — no
    LLM needed in the nightly cron. Rules are installation-specific and live in
    the config (`alias_rules`)."""
    rules = ALIAS_RULES if rules is None else rules
    if not rules:
        return False
    c = _norm(content)
    m = _norm(memory_text)
    return any(_groups_hit(r.get("fact"), c) and _groups_hit(r.get("memory"), m) for r in rules)


# Inflectional endings, longest first. Not a morphological analyser — just the
# endings that make one word look like two: «переехали»/«переехал»,
# «Лиссабоне»/«Лиссабон», «транспортные»/«транспорт». A bare prefix cut was too
# crude the other way: at 5 characters «контент» and «контейнер» collapsed into
# one stem (external review, 2026-10-07).
RU_SUFFIXES = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ость", "ться", "лась", "лись", "ются", "ешься", "ишься",
    "ах", "ях", "ов", "ев", "ей", "ой", "ый", "ий", "ом", "ем", "ую", "юю",
    "ые", "ие", "ая", "яя", "ее", "ое", "ам", "ям", "ют", "ут", "ат", "ят",
    "ет", "ит", "ла", "ло", "ли", "ся", "сь", "шь",
    "а", "е", "и", "о", "у", "ы", "я", "ю", "й", "ь",
)
STEM_CHARS_LATIN = 5
STEM_CHARS_OTHER = 6
STEM_MIN_ROOT = 4


# Latin letters that do not decompose into a base letter and a diacritic.
_LATIN_FOLD = str.maketrans({"ł": "l", "ı": "i", "ß": "ss", "ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ħ": "h",
                             "þ": "th", "ð": "d"})


def latin_fold(token):
    """The token without diacritics when it is written in Latin letters (szkoła → szkola, öğretmen → ogretmen,
    Größe → grosse); any other token unchanged. One word must not get two stems because one of its forms happens
    to be plain ASCII and another is not: «szkoła»/«szkole» were "szkoła"/"szkol" — never the same word."""
    if token.isascii():
        return token
    plain = "".join(c for c in unicodedata.normalize("NFKD", token.translate(_LATIN_FOLD))
                    if not unicodedata.combining(c))
    return plain if plain.isascii() else token


def _stem(token):
    """One crude stem for fuzzy comparison. Latin (diacritics folded) keeps the old 5-char prefix; an n-gram of
    unsegmented script is its own stem."""
    if is_gram(token):
        return token
    latin = latin_fold(token)
    if latin.isascii():
        return latin[:STEM_CHARS_LATIN]
    t = token
    for suf in RU_SUFFIXES:
        if len(t) - len(suf) >= STEM_MIN_ROOT and t.endswith(suf):
            t = t[: -len(suf)]
            break
    return t[:STEM_CHARS_OTHER]


def _stems(tokens):
    """Stems for fuzzy comparison.

    Inflection breaks exact comparison («транспортные»≠«транспорт») — a
    reworded duplicate scored containment 0.3–0.5 against the 0.62 threshold
    and resurfaced in promotions every night."""
    return {_stem(t) for t in tokens}


# Reverse dedupe direction: a short memory entry as a digest of a long fact.
# Direct containment is |fact ∩ entry| / |fact|, so the tidier the rule, the
# worse it dedupes: an auto-extracted fact has 46 stems, a normal entry 19 —
# ceiling 0.41 against 0.62, unreachable. Live case: a rule sat in MEMORY.md
# while the agent answered "already there verbatim" six nights in a row and
# the dream kept proposing it (containment 0.35).
# The reverse check is stricter on purpose: the entry must lie almost entirely
# inside the fact, be substantial by itself, and really overlap. Otherwise a
# short entry about a person would silence any long fact mentioning them.
SUMMARY_CONTAINMENT = 0.75
SUMMARY_MIN_CHUNK_STEMS = 8
SUMMARY_MIN_SHARED_STEMS = 6


# Third dedupe direction: the entry the agent has just written from this very
# candidate. It is neither a containment (the candidate keeps its own reporting
# wrapper — "<person> confirmed that …" — so forward coverage lands just under
# 0.62) nor a digest (the entry is too short for the ≥8-stem reverse rule).
# Live case 2026-08-16: three facts were promoted, and one of them returned as
# a candidate in the very next pass at coverage 0.57.
# The guard against false positives is NUMBERS: identity is accepted only when
# the candidate introduces no number the entry lacks — so "thread 42 → 437",
# a new price or a moved date stays a conflict (an update to review), never a
# silent duplicate.
SAME_SUBJECT_RATIO = 0.8
SAME_SUBJECT_MIN_SHARED = 4
# A short entry that sits ENTIRELY inside the candidate ("X likes jasmine tea"
# vs "2026-06-19 Y said that X likes jasmine tea"): every stem of the entry is
# in the candidate, the numbers agree — the wrapper is the only difference.
SAME_SUBJECT_FULL_MIN_STEMS = 3


def _same_subject(item_stems, chunk_stems, content, chunk):
    shared = item_stems & chunk_stems
    if len(shared) < SAME_SUBJECT_MIN_SHARED:
        if not (len(chunk_stems) >= SAME_SUBJECT_FULL_MIN_STEMS and shared == chunk_stems):
            return False
    elif len(shared) / min(len(item_stems), len(chunk_stems)) < SAME_SUBJECT_RATIO:
        return False
    return _numbers(content) <= _numbers(chunk)


def _covers(item_stems, chunk_stems, threshold=REJECT_MATCH_THRESHOLD):
    if not chunk_stems:
        return False
    shared = item_stems & chunk_stems
    if len(shared) / len(item_stems) >= threshold:
        return True
    return (len(chunk_stems) >= SUMMARY_MIN_CHUNK_STEMS
            and len(chunk_stems) < len(item_stems)
            and len(shared) >= SUMMARY_MIN_SHARED_STEMS
            and len(shared) / len(chunk_stems) >= SUMMARY_CONTAINMENT)


# A negation flips a fact without changing its words: «<person> ест арахис» and
# «<person> не ест арахис» share every significant stem (negations are
# stopwords for corroboration), so fuzzy dedupe called the second "already in
# memory" — the changed fact was dropped silently, and an old rejection closed
# the opposite one (Codex review of 2.7.2, 2026-10-14). What is compared is
# what each side NEGATES — the word right after a negation ("не ест",
# "does not tolerate"; past a modal, the verb it carries: "не должна
# использоваться") — and only words both texts contain: a word negated on one
# side and plain on the other is a flip; a "не" about something the other text
# does not mention is not. A long entry is narrowed first to its sentences that
# say nearly the same as the candidate (stem Jaccard >= 0.5): a profile has a
# "не" in some sentence about something else. A flipped candidate is no
# duplicate of that entry (nor of a rejection): it stays a candidate and goes to
# `conflicts` as a possible update. Only negations count — comparatives
# ("больше", "more") are significant for the store-duplicate check but negate
# nothing ("drinks more coffee" is no flip of "drinks coffee"; review of 2.7.4).
# When a text says the same word both ways ("любит арахис, но не любит
# миндаль"), the word alone cannot tell the sides apart: such a word is
# compared together with the word after it, the thing it is said of (Codex
# review of 2.7.5).
NEGATION_WORDS = {w.replace("ё", "е") for w in """не нет без ни никогда нельзя
not no never nor neither none nothing nobody without cannot
doesn don didn isn aren wasn weren hasn haven hadn shouldn wouldn couldn
nicht kein keine keinen keiner niemals ohne nunca nada nadie ningún ninguna jamás
jamais rien personne sans pas non nenhum nenhuma niente nessuno senza
niet geen nooit niets zonder değil asla nie nigdy żaden tidak bukan belum tanpa""".split()}
# A modal carries the negated verb: the word after it is negated too.
_MODALS = set("""должен должна должно должны может могут мог могла могли нужно надо будет будут стал
стала стали хочет хотят умеет can could should would will must may might need needs have has had do does
did be been is are was were want wants going""".split())
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]+")
POLARITY_MIN_JACCARD = 0.5


def _plain_words(text):
    return _WORD_RE.findall(_norm(text).replace("ё", "е"))


def _real_word(words, start):
    """Index of the first real word from *start* (negations and 1–2 letter words skipped), or None."""
    for j in range(start, len(words)):
        if words[j] not in NEGATION_WORDS and len(words[j]) >= 3:
            return j
    return None


def _negations(words):
    """[(index, stem, "не ест")] — the first real word after each negation, and the next one as well when the
    first is a modal."""
    out = []
    for i, w in enumerate(words):
        if w not in NEGATION_WORDS:
            continue
        j = _real_word(words, i + 1)
        while j is not None:
            out.append((j, _stem(words[j]), f"{w} {words[j]}"))
            if words[j] not in _MODALS:
                break
            j = _real_word(words, j + 1)
    return out


def _two_way(words, negs):
    """Stems this text says both negated and plain."""
    at = {j for j, _, _ in negs}
    return {s for _, s, _ in negs} & {_stem(w) for k, w in enumerate(words) if k not in at and len(w) >= 3}


def _negated(words, both, two_way):
    """{key: "не ест"} for the negated words both texts contain; a word said both ways is keyed together with
    the word after it."""
    out = {}
    for j, s, phrase in _negations(words):
        if s not in both:
            continue
        if s in two_way:
            k = _real_word(words, j + 1)
            obj = words[k] if k is not None else ""
            out.setdefault(f"{s} {_stem(obj) if obj else ''}", f"{phrase} {obj}".strip())
        else:
            out.setdefault(s, phrase)
    return out


def polarity_flip(content, chunk):
    """(what the fact negates, what the entry negates) when they disagree about a word both contain, or None."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(chunk or "") if s.strip()]
    if len(sentences) > 1:
        item = _stems(sig_tokens(content))
        sentences = [s for s in sentences
                     if (st := _stems(sig_tokens(s))) and item and len(item & st) / len(item | st) >= POLARITY_MIN_JACCARD]
        if not sentences:
            return None
    mine, theirs = _plain_words(content), _plain_words(". ".join(sentences))
    both = {_stem(w) for w in mine} & {_stem(w) for w in theirs}
    two_way = _two_way(mine, _negations(mine)) | _two_way(theirs, _negations(theirs))
    a, b = _negated(mine, both, two_way), _negated(theirs, both, two_way)
    if set(a) == set(b):
        return None
    return sorted(a.values()), sorted(b.values())


def polarity_differs(content, chunk):
    """True when `content` negates (or un-negates) what `chunk` says about the same word."""
    return polarity_flip(content, chunk) is not None


HEAD_CHARS = 40
HEAD_MATCH_MIN_STEM_RATIO = 0.6


def exact_or_alias_in_memory(content, memory_text):
    """The strong part of dedupe: identical head (40 chars) or a declared alias.
    Fuzzy containment is deliberately NOT here — a fuzzy match with different
    numbers is a conflict candidate, not a duplicate.

    The head alone is not enough: two facts that open with the same template
    ("Notifications for the team go to thread 42…" / "…thread 437, from Monday")
    share 40 characters and differ in everything that matters. So a head hit
    must land in an entry that carries every number of the candidate and most
    of its stems — otherwise it is left to the conflict detector."""
    norm = _norm(content)
    head = norm[:HEAD_CHARS]
    if head:
        item_stems = _stems(sig_tokens(content))
        for chunk in _memory_chunks(memory_text):
            chunk_norm = _norm(chunk)
            if head not in chunk_norm:
                continue
            if polarity_differs(content, chunk):
                continue  # "use X" inside "do not use X" is a flip, not a copy (Codex review of 2.7.5)
            if norm in chunk_norm:
                return True
            if not (_numbers(content) <= _numbers(chunk)):
                continue
            chunk_stems = _stems(sig_tokens(chunk))
            if (not item_stems
                    or len(item_stems & chunk_stems) / len(item_stems) >= HEAD_MATCH_MIN_STEM_RATIO):
                return True
    return _semantic_memory_alias(content, memory_text)


def already_in_memory(content, memory_text, token_threshold=0.62):
    """True when `content` is already represented in durable memory.

    Exact first-40-char match catches identical facts; alias rules cover
    reworded recurring facts; stemmed containment (both directions, per memory
    chunk rather than the union) catches the remaining near-duplicates."""
    if exact_or_alias_in_memory(content, memory_text):
        return True
    item_tokens = sig_tokens(content)
    if len(item_tokens) < 4:
        return False
    item_stems = _stems(item_tokens)
    for chunk in _memory_chunks(memory_text):
        chunk_stems = _stems(sig_tokens(chunk))
        if ((_covers(item_stems, chunk_stems, token_threshold)
             or _same_subject(item_stems, chunk_stems, content, chunk))
                and not polarity_differs(content, chunk)):
            return True
    return False


# ---------------------------------------------------------------------------
# Possible contradictions / supersession
# ---------------------------------------------------------------------------
# A candidate that overlaps a memory entry strongly but disagrees on numbers or
# dates is most likely an UPDATE of that entry (thread 42 → 437, a new price, a
# moved deadline), not a new fact. mem0/Zep resolve this with an LLM
# (ADD/UPDATE/DELETE, edge invalidation); here it is a deterministic hint: the
# agent decides, the entry is never rewritten by the script.
CONFLICT_MIN_OVERLAP = 0.45
CONFLICT_MIN_SHARED = 4
# A number's edge is a letter of a spaced alphabet: Chinese, Japanese, Korean and Thai write it right against
# the words (每月500美元, ราคา800บาท), and with a bare \w edge those texts had no numbers at all — so no conflicts.
_NUM_EDGE = f"(?![{_HAN_KANA}{_HANGUL}{_SEA}])"
NUMBER_RE = re.compile(rf"(?<!{_NUM_EDGE}[\w.])\d[\d.,:/-]*\d(?!{_NUM_EDGE}\w)|(?<!{_NUM_EDGE}\w)\d(?!{_NUM_EDGE}\w)")


# A fact that opens with its own date stamp ("2026-06-19 X said that …") is
# dated provenance, not content: the stamp must not count as a number when the
# fact is compared with the entry written from it, or every stamped fact would
# come back as "different numbers" (a candidate the night after it was saved,
# a conflict with the entry it produced).
LEADING_DATE_STAMP_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}(?:[ ,:—-]+|$)")


# Thousands separators: "25 000", "25\u00a0000", "25,000" and "25000" are one
# number, and seeing them as four made every retelling of a price a conflict.
THOUSANDS_RE = re.compile(r"(?<=\d)[\s\u00a0\u202f,](?=\d{3}(?!\d))")
# A month said in words is as much a "number" as 15.04 — otherwise moving
# «15 марта» to «15 апреля» changed nothing comparable, and the fuzzy dedupe
# swallowed it as "already in memory" (external review, 2026-10-07).
# Month names of the other languages the holographic store reads, with the forms a date takes («14 listopada»,
# «14 Kasım'da», «14 de noviembre»); English is here too for the day-first order («14 November»).
OTHER_MONTHS = {}
for _num, _names in enumerate((
        "january jan januar enero janvier janeiro gennaio januari ocak styczeń stycznia",
        "february feb februar febrero février fevereiro febbraio februari şubat luty lutego",
        "march mar märz marzo mars março maart mart marzec marca maret",
        "april apr abril avril aprile nisan kwiecień kwietnia",
        "may mai mayo maio maggio mei mayıs maj maja",
        "june jun juni junio juin junho giugno haziran czerwiec czerwca",
        "july jul juli julio juillet julho luglio temmuz lipiec lipca",
        "august aug agosto août augustus ağustos sierpień sierpnia agustus",
        "september sep sept septiembre setiembre septembre setembro settembre eylül wrzesień września",
        "october oct oktober octubre octobre outubro ottobre ekim październik października",
        "november nov noviembre novembre novembro kasım listopad listopada",
        "december dec dezember diciembre décembre dezembro dicembre aralık grudzień grudnia desember"), 1):
    for _name in _names.split():
        OTHER_MONTHS[_name] = _num
OTHER_MONTHS_RE = "|".join(sorted(map(re.escape, OTHER_MONTHS), key=len, reverse=True))
# A day before the month: «14 November», «14. November», «14th of November», «14 de noviembre (de 2026)».
DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th|er|º|\.)?\s+(?:de\s+|of\s+)?({OTHER_MONTHS_RE})\b(?:\.?,?\s+(?:de\s+)?(\d{{4}}))?",
    re.IGNORECASE)
# Chinese / Japanese 11月14日 (2026年11月14日), Korean 11월 14일: no \b — the digits touch the characters.
CJK_DATE_RE = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号號]")
KO_DATE_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# Month names as markers `month:MM`, so "the 12th of May" and "the 12th of June" differ. Whole words only: the
# old prefixes made «мама», «машина», «Марина», "maybe" and "market" months too. Words that are also ordinary
# ones ("may", Spanish "mar", the names Maja and Mei) count only next to a day number, i.e. inside a date.
RU_MONTH_WORD_RE = re.compile(
    r"(?<!\w)(январ|феврал|апрел|июн|июл|сентябр|октябр|ноябр|декабр)[ьяею]?(?!\w)"
    r"|(?<!\w)(март|август)[ае]?(?!\w)|(?<!\w)(ма)[йяе](?!\w)", re.IGNORECASE)
RU_MONTH_NUM = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
                "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
AMBIGUOUS_MONTHS = frozenset("may mar mai maj maja mart mei jan sep dec".split())
# Month names are looked up without case and diacritics: IGNORECASE lets "KASIM" match «kasım», but "KASIM".lower()
# is "kasim", which was not a key — the pass died on a KeyError (review 2026-10-13).
_OTHER_MONTHS_FOLDED = {latin_fold(name): num for name, num in OTHER_MONTHS.items()}


def other_month(word):
    """Month number of a month name in one of the OTHER_MONTHS languages, any case; None when it is not one."""
    return _OTHER_MONTHS_FOLDED.get(latin_fold((word or "").lower()))


OTHER_MONTH_WORD_RE = re.compile(rf"(?<!\w)({OTHER_MONTHS_RE})(?!\w)", re.IGNORECASE)


def _month_markers(text):
    """`month:MM` for every month named in words (and for 11月 / 11월 in a date)."""
    text = text or ""
    nums = set()
    for m in RU_MONTH_WORD_RE.finditer(text):
        nums.add(RU_MONTH_NUM[next(g for g in m.groups() if g).lower()])
    for m in OTHER_MONTH_WORD_RE.finditer(text):
        month = other_month(m.group(1))
        if month and latin_fold(m.group(1).lower()) not in AMBIGUOUS_MONTHS:
            nums.add(month)
    for m in DAY_MONTH_RE.finditer(text):
        month = other_month(m.group(2))
        if month:
            nums.add(month)
    for m in EN_DATE_RE.finditer(text):
        nums.add(EN_MONTHS[m.group(1).lower()[:3]])
    for regex in (CJK_DATE_RE, KO_DATE_RE):
        for m in regex.finditer(text):
            if 1 <= int(m.group(2)) <= 12:
                nums.add(int(m.group(2)))
    return {f"month:{n:02d}" for n in nums}


# A day of the week is as much a "number" as a month: "ballet moved to Tuesdays" against the entry "ballet on Mondays"
# is an update, and without a marker the two shared every word, so the fuzzy dedupe swallowed the move as "already in
# memory" (audit 2026-10-13). Whole-word prefixes, longest first ("cumartesi" before "cuma"); words that are also
# ordinary ones are left out (Turkish "pazar" is a market, Indonesian "minggu" a week).
_WEEKDAY_NAMES = (  # a cell of five letters and more is a prefix, a shorter one or one ending in "=" a whole word
    # Russian by its forms: "среда" is also an environment ("рабочая среда", "в среде"), "суббот…" a субботник,
    # "воскресен…" a resurrection, "Пятницкая" a street — only the accusative and plural forms of среда count
    "понедельник вторник среду=|средам=|средами= четверг пятница=|пятницу=|пятницы=|пятнице=|пятницам=|пятницами= "
    "суббота=|субботу=|субботы=|субботе=|субботам=|субботами= воскресенье=|воскресенья=|воскресенью=|воскресеньям=|"
    "воскресеньями=",
    "monday tuesday wednesday thursday friday saturday sunday",
    "montag dienstag mittwoch donnerstag freitag samstag|sonnabend sonntag",
    "lunes martes miercoles jueves viernes sabado domingo",
    "lundi mardi mercredi jeudi vendredi samedi dimanche",
    "segunda-feira terca-feira quarta-feira quinta-feira sexta-feira sabado domingo",
    "lunedi martedi mercoledi giovedi venerdi sabato domenica",
    "maandag dinsdag woensdag donderdag vrijdag zaterdag zondag",
    "pazartesi sali|salilari carsamba persembe cuma|cumalari cumartesi -",
    "poniedzial wtorek|wtork sroda=|srode=|srody=|srodach|srodami|srodzie czwart piatek|piatk sobot niedziel",  # srodek: middle
    "senin selasa rabu kamis jumat sabtu -",
)
_WEEKDAY_PREFIXES_NUM = sorted({(p, n) for row in _WEEKDAY_NAMES for n, cell in enumerate(row.split(), 1)
                                for p in cell.split("|") if p != "-"}, key=lambda pn: -len(pn[0]))
_WEEKDAY_WORD_RE = re.compile(r"(?<![\w-])[^\W\d_][\w-]*", re.UNICODE)
_CJK_WEEKDAY_RE = re.compile(r"(?:星期|礼拜|禮拜|周|週)([一二三四五六日天])|([月火水木金土日])曜|([월화수목금토일])요일")
_CJK_WEEKDAY_NUM = {**dict(zip("一二三四五六", range(1, 7))), "日": 7, "天": 7,
                    **dict(zip("月火水木金土", range(1, 7))), **dict(zip("월화수목금토일", range(1, 8)))}


def _weekday_markers(text):
    """`weekday:N` (1 = Monday) for every day of the week named in the text."""
    days = set()
    for m in _WEEKDAY_WORD_RE.finditer(text or ""):
        word = latin_fold(m.group(0).lower())
        for prefix, n in _WEEKDAY_PREFIXES_NUM:
            if word == prefix.rstrip("=") or (len(prefix) >= 5 and not prefix.endswith("=") and word.startswith(prefix)):
                days.add(n)
                break
    for m in _CJK_WEEKDAY_RE.finditer(text or ""):
        ch = next(g for g in m.groups() if g)
        days.add(7 if m.group(2) == "日" else _CJK_WEEKDAY_NUM[ch])
    return {f"weekday:{n}" for n in days}


def _numbers(text):
    body = LEADING_DATE_STAMP_RE.sub("", text or "", count=1)
    normalized = THOUSANDS_RE.sub("", _norm(body))
    nums = {n.strip(".,:") for n in NUMBER_RE.findall(normalized)}
    return {n for n in nums if n} | _month_markers(normalized) | _weekday_markers(normalized)


def find_conflicts(content, memory_text):
    """Memory chunks that look like the same subject with different numbers/dates,
    or with the opposite negation (see polarity_differs)."""
    item_stems = _stems(sig_tokens(content))
    if len(item_stems) < CONFLICT_MIN_SHARED:
        return []
    item_nums = _numbers(content)
    out = []
    for chunk in _memory_chunks(memory_text):
        chunk_stems = _stems(sig_tokens(chunk))
        if not chunk_stems:
            continue
        shared = item_stems & chunk_stems
        overlap = len(shared) / min(len(item_stems), len(chunk_stems))
        if len(shared) < CONFLICT_MIN_SHARED or overlap < CONFLICT_MIN_OVERLAP:
            continue
        # An entry holding a secret or an instruction shows nothing of itself: its numbers and negated words
        # are its text too (a PIN went out as memory_numbers; Codex review of 2.7.5).
        hidden = {"entry": ENTRY_REDACTED, "replace_unsafe": True} if classify_unsafe(chunk) else None
        flip = polarity_flip(content, chunk)
        if flip:
            out.append(dict(hidden, fact_negations=flip[0]) if hidden else
                       {"entry": _safe_preview(chunk, 160, ENTRY_REDACTED),
                        "fact_negations": flip[0], "memory_negations": flip[1]})
            continue
        if not item_nums:
            continue
        chunk_nums = _numbers(chunk)
        if chunk_nums and chunk_nums != item_nums and not (item_nums <= chunk_nums):
            out.append(dict(hidden, fact_numbers=sorted(item_nums - chunk_nums)) if hidden else
                       {"entry": _safe_preview(chunk, 160, ENTRY_REDACTED),
                        "fact_numbers": sorted(item_nums - chunk_nums),
                        "memory_numbers": sorted(chunk_nums - item_nums)})
    return out


# ---------------------------------------------------------------------------
# Reject list: facts a human already called outdated / wrong
# ---------------------------------------------------------------------------
# Without it the dream has no way to close a question: the core `memory` tool
# edits MEMORY.md/USER.md only and cannot delete a row from memory_store.db,
# and the only repeat filter is "already in durable memory". A rejected fact is
# in neither, so it came back every night (live case: 12 identical questions
# about a dead thread in 5 weeks). Matching is by TEXT, not fact_id: sqlite
# reuses ids after the last row is deleted, and the core may re-extract the
# same fact under a new id. fact_id is kept for tracing only.

# ---------------------------------------------------------------------------
# Near-duplicates inside the fact store
# ---------------------------------------------------------------------------
# The store has no dedupe of its own beyond an exact UNIQUE(content): the same
# statement extracted twice half an hour apart ("X said that …" / "X said: …")
# is two rows, and both arrived as two candidates for one memory entry. The
# dream never deletes from the store; it shows one representative per group
# and lists the others in `duplicates`.
#
# Deliberately stricter than memory dedupe: compared are ALL words, not the
# signature tokens — the tokenizer drops short words, and "profile de" vs
# "profile kz" would otherwise be one fact. Every word one side has and the
# other lacks must be a stopword ("said that X" / "said: X") or an inflection
# of a word the other side has (same 5-char stem); numbers and short codes never
# are, so two prices or two dates stay apart. A leading date stamp is provenance
# and is ignored.
STORE_DUP_MIN_SHARED = 4
_WORD_RE = re.compile(r"\w[\w\-]*")


def _store_words(text):
    body = _norm(LEADING_DATE_STAMP_RE.sub("", text or "", count=1)).replace("ё", "е")
    return set(_WORD_RE.findall(body))


def _store_duplicates(a, b):
    wa, wb = _store_words(a), _store_words(b)
    if len(wa & wb) < STORE_DUP_MIN_SHARED:
        return False
    stop = {s.replace("ё", "е")
            for s in STOPWORDS - TEMPORAL_STOP - POLARITY_STOP}
    for mine, other in ((wa - wb, wb), (wb - wa, wa)):
        other_stems = {_stem(w) for w in other if len(w) >= 5}
        for w in mine:
            # A word with a digit is never an inflection: "2026-10-20" and "2026-09-15" share the stem "2026-", and two
            # dates of one recurring event were folded into one fact (audit 2026-10-13).
            if w in stop or (len(w) >= 5 and not any(c.isdigit() for c in w) and _stem(w) in other_stems):
                continue
            return False
    return True


def collapse_store_duplicates(items):
    """Keep the first fact of every near-duplicate group (callers sort
    best-first). Returns (kept, hidden); kept items are copies carrying
    `duplicates` (fact ids) — the input dicts are shared between sections."""
    kept, hidden = [], []
    for s in items:
        for k in kept:
            if _store_duplicates(k["content"], s["content"]):
                k.setdefault("duplicates", []).append(s["fact_id"])
                hidden.append(s)
                break
        else:
            kept.append(dict(s))
    return kept, hidden


def _fact_fingerprint(content):
    return hashlib.md5(_norm(content).encode("utf-8")).hexdigest()[:16]


def feedback_floor(fact_source_name=None, home=None):
    """The trust below which the provider stops recalling a fact.

    Explicit `gates.feedback_trust_floor` wins; otherwise the holographic
    provider's own `min_trust_threshold`; otherwise 0.3 (its default, and the
    value every earlier version of this pass assumed). Not cached: 2.7.1 kept
    the answer in a module global shared by every home and every run, so a
    second run in the same process — or a run for another home — got the first
    one's floor (Codex review). A config read costs nothing next to the pass."""
    if FEEDBACK_FLOOR_EXPLICIT:
        return FEEDBACK_TRUST_FLOOR
    source = fact_source_name if fact_source_name is not None else fact_source()
    if source != "holographic":
        return FEEDBACK_TRUST_FLOOR
    provider = holographic_min_trust(home)
    return FEEDBACK_TRUST_FLOOR if provider is None else provider


def trust_feedback_items(suppressed, rejected, fact_source_name):
    """Rejected facts that the store still treats as trustworthy.

    `dream-reject.py` closes a question for the DREAM; the store knows nothing
    about it, so with the holographic provider the same fact keeps arriving in
    ordinary turns through prefetch (it only drops out below the store's own
    floor — `feedback_floor()`, the provider's `min_trust_threshold`). The one
    call that teaches retrieval is `fact_feedback(action="unhelpful")` — the
    agent has it in the nightly session; the pass itself never writes the store.
    Only for the holographic source: nobody else has that tool."""
    if fact_source_name != "holographic":
        return []
    floor = feedback_floor(fact_source_name)
    out = []
    for s in suppressed:
        trust = s.get("trust_score")
        trust = 0.5 if trust is None else float(trust)
        if trust < floor:
            continue                      # уже ниже порога — prefetch его не поднимет
        reason, when = "", ""
        fingerprint = _fact_fingerprint(s["content"])
        rec = rejected.get(fingerprint)
        if isinstance(rec, dict):
            reason = str(rec.get("reason") or "")
            when = str(rec.get("at") or rec.get("rejected_at") or "")[:10]
        # Rejected facts leave before quarantine, so a secret the human closed with
        # dream-reject.py came back here in clear text (Codex review of 2.7.2): the call
        # needs only the id.
        unsafe = s.get("unsafe") or classify_unsafe(s["content"])
        if unsafe or classify_unsafe(reason):
            reason = ""
        out.append({
            "fact_id": s["fact_id"],
            "content": f"[hidden: {unsafe}]" if unsafe else s["content"][:200],
            "trust": round(trust, 2),
            # The store's own floor, in the item itself: the precheck hands the agent the items,
            # not `gates`, so a floor kept only there never reached the nightly prompt.
            "floor": round(floor, 2),
            "fingerprint": fingerprint,
            "rejected_at": when,
            "rejected_reason": reason[:160],
            "why": (f"rejected by a human{' on ' + when if when else ''}, but the store still "
                    f"trusts it at {round(trust, 2)} — prefetch keeps offering it"),
            "call": {"tool": "fact_feedback", "action": "unhelpful", "fact_id": s["fact_id"]},
        })
    out.sort(key=lambda i: -i["trust"])
    return out


# Confirmations (holographic >= 0.15.0): facts people keep saying, on CONFIRM_MIN_DAYS different days at least, by
# messages that do not negate them — offered to `fact_feedback(action="confirmed")`, at most CONFIRM_CAP a night. One
# mention is a topic, not an agreement; "Илья не переехал" mentions the fact it denies, so a message whose polarity
# differs from the fact's never counts. A fact the store already counts a confirmation of within CONFIRM_REST_DAYS
# rests.
CONFIRM_MIN_DAYS = 2
CONFIRM_CAP = 10
CONFIRM_REST_DAYS = 30
# Said again, not merely talked about: the message holds at least this share of the fact's own word stems. The
# corroboration rule (two shared stems, or one rare one) is right for "is this fact alive?" and too loose here — a
# family chat names a child and her school every day.
CONFIRM_MIN_COVERAGE = 0.6


def recent_confirmations(db, rest_days=CONFIRM_REST_DAYS):
    """Fact ids the store counts a confirmation of within *rest_days*; None when the store has no
    `fact_confirmations` table (holographic older than 0.15.0: no `confirmed` call to offer). Read-only."""
    if not db or not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fact_confirmations'"
                                ).fetchone():
                return None
            return {r[0] for r in conn.execute(
                "SELECT DISTINCT fact_id FROM fact_confirmations WHERE julianday('now') - julianday(confirmed_at) < ?",
                (rest_days,))}
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def confirm_items(safe, messages, by_store, fact_source_name, db=None):
    """Facts of the main store that people said again on different days (see CONFIRM_MIN_DAYS). Only for the
    holographic source with the confirmations table; chat-store facts get no call (a cron session cannot reach a
    chat's database). The pass itself never writes the store."""
    if fact_source_name != "holographic":
        return []
    rested = recent_confirmations(db or fact_store_path())
    if rested is None:
        return []
    out = []
    for s in safe:
        if s.get("store") or s.get("unsafe") or s["fact_id"] in rested \
                or (s.get("meta") or {}).get("ref_days", 0) < CONFIRM_MIN_DAYS:
            continue
        tokens = sig_tokens(s["content"])
        stems = _stems(tokens)
        if not stems:
            continue
        days = {m["day"] for m in messages_for(s, messages, by_store)
                if corroborate(tokens, [m])[0]
                and len(stems & _stems(m["tokens"])) / len(stems) >= CONFIRM_MIN_COVERAGE
                and not polarity_differs(s["content"], m["content"])}
        if len(days) >= CONFIRM_MIN_DAYS:
            out.append({"fact_id": s["fact_id"], "content": s["content"][:200], "days": len(days),
                        **({"origin": s["origin"]} if s.get("origin") else {}),
                        "fingerprint": _fact_fingerprint(s["content"]),
                        "call": {"tool": "fact_feedback", "action": "confirmed", "fact_id": s["fact_id"]}})
    out.sort(key=lambda i: (-i["days"], i["fact_id"]))
    return out


def keywords_needed_items(safe, fact_source_name):
    """Facts the store keeps without keywords, the most recalled first.

    Only for the holographic source with `keywords.enabled`: the call writes
    the provider's own column, and a provider older than 0.6.0 accepts the
    parameter and drops it. The pass itself never writes the store."""
    if not KEYWORDS_ENABLED or fact_source_name != "holographic":
        return []
    out = []
    for s in safe:
        if s.get("keywords"):
            continue
        out.append({
            "fact_id": s["fact_id"],
            "content": s["content"][:300],
            # The cooldown and the forget sweep key on the full text; a key of the cut text never matched a
            # fact longer than 300 characters (review of 2.7.4).
            "fingerprint": _fact_fingerprint(s["content"]),
            "retrieval_count": s["meta"]["rc"],
            "call": {"tool": "fact_store", "action": "update", "fact_id": s["fact_id"],
                     "keywords": ["3-8 words a person might ask with that the fact lacks"]},
        })
    out.sort(key=lambda i: (-i["retrieval_count"], str(i["fact_id"])))
    return out


def load_rejected(path):
    """Read the reject list. Missing / broken JSON — fail-soft: run without it."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_rejected(content, rejected):
    """Match by text fingerprint or by rewording of the same fact (both
    directions — a short human rejection must be able to close a long
    re-extracted fact, with the same strict reverse conditions as dedupe)."""
    return rejection_of(content, rejected) is not None


def rejection_of(content, rejected):
    """The latest reject record that matches `content` (see is_rejected), or None."""
    if not rejected:
        return None
    fingerprint = _fact_fingerprint(content)
    item_stems = _stems(sig_tokens(content))
    found = []
    for rec in rejected.values():
        if not isinstance(rec, dict):
            continue
        if rec.get("fingerprint") == fingerprint:
            found.append(rec)
            continue
        text = rec.get("content") or ""
        if not text or len(item_stems) < 4:
            continue
        if _covers(item_stems, _stems(sig_tokens(text))) and not polarity_differs(content, text):
            found.append(rec)
    epoch = datetime.min.replace(tzinfo=timezone.utc)  # an unreadable date loses to any readable one
    return max(found, key=lambda r: _parse_iso(str(r.get("at") or r.get("rejected_at") or "")) or epoch) if found         else None


def said_again(fact, meta, rec, now=None):
    """True when a rejection is old enough to ask about once more and the fact was said again after it: its store
    row was created after the rejection AND a human mentioned it after it. A rejection is not forever — "no longer
    does ballet" was right in May and wrong once she went back in September (mem0#7612 is the same trap with
    expiry). But "said again" alone cannot tell that from the core re-extracting the same stale fact while people
    still talk about it — the very case the list was made for (twelve identical questions in five weeks) — so only a
    rejection older than `reject_reask_days` (180; 0 = never) is asked about again, and a second "no" starts the
    count over. The same old row stays silenced."""
    if not REJECT_REASK_DAYS:
        return False
    at = _parse_iso(str(rec.get("at") or rec.get("rejected_at") or ""))
    created = _parse_ts(fact.get("created_at"))
    if at is None or created is None or (now or _now()) - at < timedelta(days=REJECT_REASK_DAYS):
        return False
    return created > at and (meta.get("last_ts") or 0) > at.timestamp()


# ---------------------------------------------------------------------------
# "Already shown": cooldown for new_facts / fact_decays / conflicts
# ---------------------------------------------------------------------------
# The reject list closes a question by a HUMAN decision, but for these
# sections the usual outcome is the agent's own "looked, nothing to do". That
# had nowhere to be stored, so the section re-opened the wake gate every night
# — new_facts up to 14 nights per fact, fact_decays indefinitely. Same trick as
# md_decays: not "silence forever" but "not more often than once per
# SEEN_COOLDOWN_DAYS". Key — text fingerprint (a reworded / re-extracted fact
# is new work). Only candidates that were actually published (after the caps)
# are marked — an overflow candidate never reached the agent.

def _parse_iso(s):
    try:
        dt = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def unseen(items, bucket, now, cooldown_days=None, key=None):
    """Candidates not shown for longer than the cooldown. Does not mutate state."""
    cooldown = timedelta(days=SEEN_COOLDOWN_DAYS if cooldown_days is None else cooldown_days)
    key = key or (lambda item: _fact_fingerprint(item["content"]))
    fresh = []
    for item in items:
        last = _parse_iso((bucket.get(key(item)) or {}).get("at"))
        if last and (now - last) < cooldown:
            continue
        fresh.append(item)
    return fresh


def _feedback_key(item):
    return item.get("fingerprint") or _fact_fingerprint(item["content"])


def mark_seen(items, bucket, now, key=None):
    """Mark as shown — call only for actually published items."""
    stamp = now.isoformat(timespec="seconds")
    key = key or (lambda item: _fact_fingerprint(item["content"]))
    for item in items:
        bucket[key(item)] = {"at": stamp, "fact_id": item.get("fact_id")}


def prune_seen(bucket, now, cooldown_days=None):
    """Drop entries older than two cooldowns so the state does not grow forever."""
    horizon = timedelta(days=2 * (SEEN_COOLDOWN_DAYS if cooldown_days is None else cooldown_days))
    for key in [k for k, v in bucket.items()
                if not isinstance(v, dict) or not _parse_iso(v.get("at"))
                or (now - _parse_iso(v.get("at"))) >= horizon]:
        bucket.pop(key, None)


def load_seen(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# Ephemeral (dated) events and expiry
# ---------------------------------------------------------------------------

MONTHS_RE = r"январ[яе]|феврал[яе]|март[ае]?|апрел[яе]|ма[йяе]|июн[яе]|июл[яе]|август[ае]?|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе]"
THAI_MONTHS_RE = (r"มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|"
                  r"กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|"
                  r"พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.")
# Thai alternatives are deliberately outside `\b`: Thai has no spaces between
# words and `\b` needs a \w/non-\w boundary, so `\bสอบ\b` never matched «สอบ»
# inside «การสอบ» and Thai school messages were never seen as temporary events.
# Words that make a dated fact a one-off event (an appointment, an exam, a meeting, a flight, a deadline, a thing to
# bring). Latin and Cyrillic stems take any ending; Chinese, Japanese, Korean and Thai words are matched anywhere —
# those scripts have no spaces (or glue particles to the word), so a \b never falls where it should.
EPHEMERAL_HINTS_RE = re.compile(
    r"\b(?:тест\w*|контрольн\w*|экзамен\w*|экскурс\w*|дедлайн\w*|"
    r"встреч\w*|созвон\w*|звонок|совещани\w*|собеседовани\w*|"
    r"вылет\w*|рейс\w*|прилёт\w*|прилет\w*|поезд\w*|"
    r"приём\w*|прием\w*|запись\s+к\s+врачу|визит\w*|"
    r"meeting|call|flight|appointment|interview|"
    r"test|exam\w*|deadline\w*|field\s+trip|bring|submit|prepare|"
    r"sight\s+words?|страниц\w*|лист\w*\s+для\s+чтени\w*|"
    r"завтра|сегодня|послезавтра|принести|сдать|подготовить|"
    r"termin|termine|terminen|arzttermin\w*|prüfung\w*|klassenarbeit\w*|klausur\w*|besprechung\w*|abflug|flug|frist|"
    r"abgabe\w*|mitbringen|abgeben|elternabend\w*|"
    r"cita|citas|examen\w*|prueba\w*|reunión|reuniones|vuelo\w*|plazo\w*|entrega\w*|traer|entregar|excursión|"
    r"rendez-vous|contrôle\w*|réunion\w*|vol|échéance\w*|apporter|rendre|sortie\s+scolaire|"
    r"consulta\w*|prova\w*|exame\w*|reunião|reuniões|voo\w*|prazo\w*|levar|trazer|excursão|"
    r"appuntament\w*|esam\w*|verifica\w*|riunion\w*|volo|voli|scadenz\w*|portare|consegna\w*|gita|"
    r"afspra\w*|toets\w*|vergadering\w*|vlucht\w*|meenemen|inleveren|schoolreis\w*|"
    r"randevu\w*|sınav\w*|toplantı\w*|uçuş\w*|teslim\w*|getir\w*|götür\w*|gezi\w*|"
    r"wizyt\w*|sprawdzian\w*|egzamin\w*|kartkówk\w*|spotkani\w*|przynieś\w*|oddać|wycieczk\w*|"
    r"janji|ujian|ulangan|rapat|pertemuan|penerbangan|tenggat|bawa|kumpulkan|karyawisata|"
    r"dentist|(?:to|at|see|visit)\s+(?:the|a)\s+doctor|doctor'?s?\s+(?:appointment|visit)|trip|flies|fly|due|"
    r"стоматолог\w*|к\s+врачу|у\s+врача|врач\s+прид[её]т|вызвать\s+врача|вызов\s+врача|при[её]м\s+врача|утренник\w*|zahnarzt\w*|ausflug\w*|wandertag\w*|"
    r"llevar|dentista|médico|último\s+dia|colloqui\w*|uçağ\w*|uçak|son\s+ödeme|son\s+gün|dentyst\w*|"
    r"wylot\w*|lot\s+do|zebrani\w*|\w*toets\w*|ouderavond\w*|tandarts\w*|jadwal|batas|dikumpulkan)\b"
    r"|(?:สอบ|กำหนดส่ง|ทัศนศึกษา|นำมา|ส่ง|นัด|ประชุม|เที่ยวบิน|"
    r"予約|テスト|試験|会議|打ち合わせ|フライト|締め切り|締切|持って|提出|遠足|"
    r"预约|預約|约了|約了|考试|考試|测验|測驗|会议|會議|航班|截止|带上|帶上|提交|春游|秋游|"
    r"예약|시험|회의|비행|마감|가져|제출|소풍|고사|상담|까지|"
    r"参観|健康診断|検診|出張|の便|歯医者|までに|复查|復查|复诊|復診|体检|體檢|飞|飛|家长会|家長會|之前|以前|"
    r"วัคซีน|บิน|ภายใน)",
    re.IGNORECASE,
)
# 14.11, 14/11, 14-11-2026, Korean 12. 31.; the digits may touch Chinese, Japanese or Thai (奶奶11/23上午).
NUMERIC_DATE_RE = re.compile(
    rf"(?<!{_NUM_EDGE}[\w.])(?<!-)(\d{{1,2}})(?:[./-]|\.\s)(\d{{1,2}})(?:[./-](\d{{2,4}}))?(?!{_NUM_EDGE}\w)(?!-\d)")
DATED_RE = re.compile(
    rf"\b\d{{1,2}}\s+(?:{MONTHS_RE})(?:\s+\d{{4}})?\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    rf"{NUMERIC_DATE_RE.pattern}|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b|"
    rf"\b\d{{1,2}}\s+(?:{THAI_MONTHS_RE})|"
    rf"{DAY_MONTH_RE.pattern}|{CJK_DATE_RE.pattern}|{KO_DATE_RE.pattern}",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|на этой неделе|на следующей неделе|"
    r"today|tomorrow|this week|next week|"
    r"heute|morgen|übermorgen|nächste\s+woche|hoy|mañana|pasado\s+mañana|la\s+semana\s+que\s+viene|"
    r"aujourd'hui|demain|après-demain|la\s+semaine\s+prochaine|hoje|amanhã|depois\s+de\s+amanhã|"
    r"oggi|domani|dopodomani|la\s+settimana\s+prossima|vandaag|overmorgen|volgende\s+week|"
    r"bugün|yarın|öbür\s+gün|gelecek\s+hafta|dzisiaj|dziś|jutro|pojutrze|w\s+przyszłym\s+tygodniu|"
    r"hari\s+ini|besok|lusa|minggu\s+depan)\b"
    r"|(?:วันนี้|พรุ่งนี้|มะรืน|今天|明天|后天|後天|今日|明日|明後日|来週|下周|下週|오늘|내일|모레|다음\s*주)",
    re.IGNORECASE,
)


# A whole date: a day and a month name («1 декабря», "30. Nov", «4 grudnia»), or a day and a month in digits
# (30/11, 30.11, 14-11-2026 — a real month, so a time «15.00» is not a date; review of 2.5.1).
_DATE_AHEAD = (rf"(?<!\d)\d{{1,2}}(?:\.|st|nd|rd|th|er)?\s+(?:de\s+|of\s+)?(?:{OTHER_MONTHS_RE}|{MONTHS_RE})\b"
               r"|(?<![\d-])(?:[12]\d|3[01]|0?[1-9])[./-](?:1[0-2]|0?[1-9])(?!\d)")
# A deadline: "until / by / before" followed by a whole date («до 1 декабря», «bis 30. Nov», «do 4 grudnia»), or a
# Turkish date with «kadar» after it («30/11'e kadar»). A bare number is not a date: "can do 20 pushups" is not a
# Polish «do 20.» (review 2026-10-13), and the old open-ended `\S*` before «kadar» was quadratic on long numbers.
DEADLINE_RE = re.compile(
    r"\b(?:до|until|by|before|till|bis|bis\s+zum|antes\s+del?|hasta\s+el|avant\s+le|até|entro\s+il|entro|tot|"
    rf"uiterlijk|do|sebelum)\s+(?:the\s+|dem\s+|el\s+|le\s+|il\s+)?(?:{_DATE_AHEAD})"
    rf"|(?:{_DATE_AHEAD})(?:\s+\d{{4}})?'?\w{{0,4}}\s+kadar\b",
    re.IGNORECASE)
# "Since <date>" opens a state that lasts — a job, a move, a rule («Илья врач с 14 ноября 2020 года», "Tom can
# do 20 pushups since 14 November 2020"). Only that phrase is taken out before the event check, not the whole
# fact: "Call on 14 November; Tom has worked here since 2020" is still a call (review of 2.5.1).
SINCE_DATE_RE = re.compile(
    r"\b(?:since|seit|desde|depuis|dal|dalla|sinds|vanaf|od|sejak|с|со)\s+(?:the\s+|dem\s+|el\s+|le\s+|il\s+)?"
    rf"(?:{_DATE_AHEAD})(?:\s+\d{{4}})?|(?:{_DATE_AHEAD})(?:\s+\d{{4}})?'?\w{{0,4}}\s+(?:beri|itibaren|bu\s+yana)\b",
    re.IGNORECASE)
# …unless the date opens a range: «с 15.11 по 17.11», «od 14 listopada do 16 listopada», "from … to …".
RANGE_END_RE = re.compile(
    r"\s*(?:г\.|года?)?\s*(?:,\s*)?(?:по|до|to|until|till|through|bis|al|au|a|do|tot|t/m|hingga|sampai|[-–—])\s*\d",
    re.IGNORECASE)


def _without_lasting(text):
    """The text with every "since <date>" phrase cut out (a range «с … по …» stays: it is an event's span)."""
    out, last = [], 0
    for m in SINCE_DATE_RE.finditer(text):
        if RANGE_END_RE.match(text, m.end()):
            continue
        out.append(text[last:m.start()])
        last = m.end()
    out.append(text[last:])
    return " ".join(out)


def is_ephemeral_fact(content):
    """Temporary dated school/calendar facts should not become durable memory."""
    text = _without_lasting(content or "")
    has_time_anchor = bool(DATED_RE.search(text) or RELATIVE_DATE_RE.search(text))
    return bool(has_time_anchor and (EPHEMERAL_HINTS_RE.search(text) or DEADLINE_RE.search(text)))


# A dated event whose date has passed is garbage, not context: it must not be
# promoted, and strong corroboration keeps it from decaying by itself. Live
# case: "test on July 3" stayed in the output until the end of July and
# opened the wake gate every night although no work was possible.
RU_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
             "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
EN_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
THAI_MONTHS = {"มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
               "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
               "พฤศจิกายน": 11, "ธันวาคม": 12,
               "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
               "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}

ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RU_DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+({MONTHS_RE})(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
# Month names whole ("Dec", "December"), not any word that starts like one: "mardi 10", "Marina 5", "Mark 3",
# "Jane 14" and "Mary 5" used to be dates (review 2026-10-13). A day may carry its suffix ("Dec 17th").
EN_DATE_RE = re.compile(
    r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|"
    r"october|oct|november|nov|december|dec)\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.IGNORECASE)
THAI_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({THAI_MONTHS_RE})(?:\s+(\d{{4}}))?")


def _resolve_year(year, month, day, today):
    """No year given → the one that puts the date closest to today (±180 d).
    A year ≥ 2400 is the Thai Buddhist calendar."""
    if year:
        year = int(year)
        if year >= 2400:
            year -= 543
        return year
    for candidate in (today.year, today.year - 1, today.year + 1):
        try:
            probe = datetime(candidate, month, day).date()
        except ValueError:
            continue
        if -180 <= (probe - today).days <= 180:
            return candidate
    return today.year


def _safe_date(year, month, day, today):
    try:
        return datetime(_resolve_year(year, month, day, today), month, day).date()
    except (ValueError, TypeError):
        return None


def extract_event_dates(text, today):
    """(certain dates, ambiguous pairs). Both empty — nothing parsed."""
    text = text or ""
    certain, ambiguous = [], []
    for y, m, d in ISO_DATE_RE.findall(text):
        got = _safe_date(y, int(m), int(d), today)
        if got:
            certain.append(got)
    for day, month_word, year in RU_DATE_RE.findall(text):
        month = next((n for stem, n in RU_MONTHS.items()
                      if month_word.lower().startswith(stem)), None)
        got = _safe_date(year, month, int(day), today) if month else None
        if got:
            certain.append(got)
    for month_word, day, year in EN_DATE_RE.findall(text):
        month = EN_MONTHS.get(month_word.lower()[:3])
        got = _safe_date(year, month, int(day), today) if month else None
        if got:
            certain.append(got)
    for day, month_word, year in DAY_MONTH_RE.findall(text):
        month = other_month(month_word)
        got = _safe_date(year, month, int(day), today) if month else None
        if got:
            certain.append(got)
    for regex in (CJK_DATE_RE, KO_DATE_RE):
        for year, month, day in regex.findall(text):
            got = _safe_date(year, int(month), int(day), today)
            if got:
                certain.append(got)
    for day, month_word, year in THAI_DATE_RE.findall(text):
        month = THAI_MONTHS.get(month_word)
        got = _safe_date(year, month, int(day), today) if month else None
        if got:
            certain.append(got)
    # 5/10 is both Oct 5 and May 10: keep both readings, decide by the worse one.
    for a, b, year in NUMERIC_DATE_RE.findall(text):
        pair = [d for d in (_safe_date(year, int(b), int(a), today),
                            _safe_date(year, int(a), int(b), today)) if d]
        if pair:
            ambiguous.append(pair)
    return certain, ambiguous


def is_expired_event(content, today=None):
    """True — a temporary event whose date has passed. Conservative: a relative
    anchor ("tomorrow") is not resolved; an ambiguous numeric date counts as
    past only if it is past under EVERY reading."""
    text = content or ""
    if not is_ephemeral_fact(text):
        return False
    text = _without_lasting(text)  # the date a state began is not the event's date
    if RELATIVE_DATE_RE.search(text):
        return False
    today = today or _now().astimezone(LOCAL_TZ).date()
    certain, ambiguous = extract_event_dates(text, today)
    if not certain and not ambiguous:
        return False
    return (all(d < today for d in certain)
            and all(max(pair) < today for pair in ambiguous))


# ---------------------------------------------------------------------------
# Quarantine of unsafe content
# ---------------------------------------------------------------------------
# Facts are extracted by the core from live conversations (including observed
# chats), so they may contain secrets or embedded instructions (indirect
# prompt injection). Such content must reach neither promotions, nor
# new_facts, nor the agent's prompt.
SECRET_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]{16,}"                        # OpenAI/Anthropic-style keys
    r"|apikey_[A-Za-z0-9]{8,}"                      # Anthropic console key ids
    r"|AKIA[0-9A-Z]{16}"                            # AWS access key
    r"|AIza[0-9A-Za-z_\-]{30,}"                     # Google API key
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"               # Slack
    r"|gsk_[A-Za-z0-9]{20,}"                        # Groq
    r"|ntn_[A-Za-z0-9]{20,}|secret_[A-Za-z0-9]{32,}"  # Notion (new and legacy)
    r"|sk_[a-f0-9]{32,}"                            # ElevenLabs and friends
    r"|gh[osu]_[A-Za-z0-9]{20,}"                    # GitHub OAuth/server/user tokens
    r"|\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"            # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b[0-9a-f]{40,}\b"                           # long hex (keys/sessions)
    r")")
# A credential marker is rarely glued to the colon: "Пароль от wifi в квартире:
# X" and "Password for the router is X" both carry one. Up to four words are
# allowed between the marker and the separator — but they must not contain a
# verb of change, so "сменила пароль от Wi-Fi" (an event, not a secret) stays
# out (external review, 2026-10-07).
PASSWORD_MARKER_RE = re.compile(
    r"(?:парол[ьяию]|password|passwd|passphrase|seed[- ]?фраза|seed[- ]?phrase|"
    r"секретн\w{0,4}\s+(?:ключ|код)|api[- ]?ключ|private\s+key|2fa[- ]?код|"
    r"пин[- ]?код|\bпин\b|\bpin\b|код\s+от)"
    r"(?:[^\n:=—–]{0,40}?)(?P<sep>[:=—–]|\s-\s)\s*(?P<value>\S{4,})",
    re.IGNORECASE)
# …and these say "we changed it", not "here it is". A colon or "=" hands the
# value over whatever comes first ("Changed password: hunter2", "поменяла
# пароль: розовыйслон"). After a dash a verb of change in the marker's own
# sentence makes it an event — "сменила пароль от Wi-Fi — новый знает только
# она", "сбросил пароль от Zoom — 40-минутный лимит остался" — unless the value
# looks like a credential: six or more characters and a digit or a symbol
# ("— Secret123!"); with Cyrillic in it, a digit glued to a letter
# ("— Кот2024!"), because "40-минутный" and "кв.123" are words, not
# passwords. Codex review of 2.7.2, review of 2.7.4, Codex review of 2.7.5.
PASSWORD_EVENT_RE = re.compile(
    r"(?:смен\w+|помен\w+|обнов\w+|сброс\w+|забыл\w*|changed|rotated|reset|forgot)",
    re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?\n]")
_CREDENTIAL_VALUE_RE = re.compile(r"[\d!@#$%^&*_+=~|\\/<>{}\[\]]")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_GLUED_DIGIT_RE = re.compile(r"[^\W\d_]\d|\d[^\W\d_]")


def _looks_like_credential(value):
    value = value.rstrip(".,;")
    if len(value) < 6 or not _CREDENTIAL_VALUE_RE.search(value):
        return False
    return not _CYRILLIC_RE.search(value) or bool(_GLUED_DIGIT_RE.search(value))


def _password_marker_unsafe(text):
    for marker in PASSWORD_MARKER_RE.finditer(text):
        if marker.group("sep").strip() in (":", "="):
            return True
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(text, 0, marker.start())]
        if not PASSWORD_EVENT_RE.search(text[(ends[-1] if ends else 0):marker.end()]):
            return True
        if _looks_like_credential(marker.group("value")):
            return True
    return False
INJECTION_RE = re.compile(
    r"(?:"
    # Stems, not exact forms: «Игнорируйте», «игнорировать», «забудьте» are the
    # same instruction (external review, 2026-10-07). Up to two words may stand
    # between the verb and its object.
    r"игнорир\w*\s+(?:\w+\s+){0,2}?(?:инструкци\w*|правил\w*|указани\w*|предыдущ\w*|прошл\w*)"
    r"|ignore\s+(?:\w+\s+){0,3}?(?:previous|prior|above|earlier|all)\s+(?:\w+\s+){0,2}?instructions?"
    r"|disregard\s+(?:all\s+|any\s+|your\s+)?(?:previous|prior|above|instructions|rules|guidelines)"
    r"|do\s+not\s+tell\s+the\s+user|system\s+prompt\s+override"
    r"|forget\s+(?:all\s+|your\s+|the\s+)?(?:previous\s+|prior\s+)?(?:instructions?|rules?)"
    r"|забуд\w*\s+(?:\w+\s+){0,2}?(?:инструкци\w*|правил\w*|ограничени\w*)"
    r"|запомни\s*[:,]?\s*(?:всегда|никогда|что\s+тебе\s+разреш)"
    r"|системн\w+\s+промпт|system\s+prompt"
    r"|(?:developer|god|dan)\s+mode|режим\s+разработчика"
    r"|new\s+instructions?\s*:|новые\s+инструкции\s*:"
    r"|(?:покажи|раскрой|выведи|отправь|перешли|скинь)[^.\n]{0,40}"
    r"(?:промпт|инструкци|ключ[иа]?\b|парол|токен|секрет)"
    r"|(?:запиши|сохрани|добавь)\s+в\s+память[^.\n]{0,60}"
    r"(?:игнориру|разреша|всегда\s+выполня|парол|ключ)"
    r"|(?:save|store|add|write)\s+(?:to|in|into)\s+memory[^.\n]{0,60}"
    r"(?:ignore|allow|always|password|key|secret)"
    r"|jailbreak"
    r")",
    re.IGNORECASE)


# The holographic provider's own screen (scripts/holo_screen.py, a verbatim copy made by tools/vendor_screen.py):
# credentials in its 18 languages, token shapes (a bot token inside a Bot API address, A[KS]IA…, an address with a
# password), environment assignments, encoded blobs, instructions and requests to send secrets in every language,
# chat-role markup, hidden characters, images. The provider refuses these when storing and recalling, but never
# cleans what it already holds, nor people's messages — and the dream carries both into durable memory and its
# report (2.9.0). Two things it counts the dream does not:
# - a change of a password told after a dash, where what follows is a word of the sentence, not a value ("сбросил
#   пароль от Zoom — 40-минутный лимит остался", "забыл пароль от домофона — кв.123 не открывает"): the dream's
#   rule since 2.7.5, narrowed in 2.9.0 to a word in a non-Latin script with the sentence going on after it — a
#   short or Latin value ("— abc1", "— 1234", "— step1 …") is taken for a password (Codex review of 2.9.0);
# - a zero-width joiner inside an emoji (👩‍💻), the tags of a subdivision flag (🏴 + tags) and the zero-width space
#   that separates words in Thai, Lao, Khmer and Burmese: how these are written, not hidden text.
# A secret wins over an instruction: an injection carries a preview into the full JSON (Codex review of 2.9.0).
# A door key is not a credential: "перешли Даше ключ от дачи", "покажи ключ от квартиры соседке" read as a request
# to send a secret to both screens ("перешли … ключ"). The word before «от» + something with a lock is replaced
# before the instruction checks, not the secret ones (2.9.1). Only things with a lock: «ключ от почты», «от личного
# кабинета», «от API», a bare «ключ» stay suspicious.
def _load_holo_screen(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "holo_screen.py")
    try:
        spec = importlib.util.spec_from_file_location("_dream_holo_screen", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        missing = [n for n in ("_recall_problem", "_secret_in", "_RE_CREDENTIALS") if not hasattr(mod, n)]
        if missing:
            raise ImportError(f"no {', '.join(missing)}")
        return mod
    except Exception as exc:  # noqa: BLE001 — a broken copy must not take the pass down (review of 2.9.1)
        print(f"[dream] warn: {path} not loaded ({exc!r}) — secrets and instructions screened by the dream's own "
              f"rules only", file=sys.stderr)
        return None


HOLO_SCREEN = _load_holo_screen()
_EMOJI_JOINER_RE = re.compile(r"(?<=[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F])\u200D(?=[\U0001F000-\U0001FAFF\u2600-\u27BF])")
_FLAG_TAGS_RE = re.compile(r"(?<=\U0001F3F4)[\U000E0030-\U000E0039\U000E0061-\U000E007A]{2,6}\U000E007F")
_NO_SPACE_SCRIPTS = "\u0E00-\u0EFF\u1000-\u109F\u1780-\u17FF"  # Thai, Lao, Burmese, Khmer
_NO_SPACE_LETTER_RE = re.compile(f"[{_NO_SPACE_SCRIPTS}]")


def _strip_word_separators(text):
    """The zero-width space these scripts separate words with, also next to a digit, a space or a mark ("ลูกสาว
    7\\u200b ขวบ", "ไปตลาด\\u200b, ซื้อปลา"; review of 2.9.1): dropped when a letter of them stands within four
    characters. Elsewhere it stays hidden text."""
    if "\u200b" not in text:
        return text
    return "".join(c for i, c in enumerate(text)
                   if c != "\u200b" or not _NO_SPACE_LETTER_RE.search(text, max(0, i - 4), i + 5))


_LATIN_RE = re.compile(r"[A-Za-z]")
# Between «от» and the thing only words that describe it — "от нашей виллы", "от почтового ящика", "от Дашиной
# машины" — never another noun: "ключ от почты в офисе", "ключ от API на даче" name a digital key and a place
# (review of 2.9.1).
_DESCRIBING = r"(?:[а-яё-]*(?:ого|его|ой|ей|ых|их|ую|юю|ая|яя|ое|ее|ые|ие|ий|ый|ом|ем|им|ым|ина|иной|иного|ова|овой|ового)|его|её|ее|их)"
_DOOR_KEY_RE = re.compile(
    r"\bключ(?:и|а|у|ом|ей|ами|ах)?(?=\s+от\s+(?:" + _DESCRIBING + r"\s+){0,2}?"
    r"(?:дач|квартир|дом(?:а|у|е|ом)?\b|домофон|машин|авто(?:мобил)?|гараж|двер|подъезд|калитк|ворот|сара[яеюй]"
    r"|подвал|кладов|офис|комнат|шкаф|ящик|сейф|зам(?:ок|к)|велосипед|байк|мотоцикл|скутер|номер|вилл|кондо"
    r"|бассейн))", re.IGNORECASE)
_LETTER_RE = re.compile(r"[^\W\d_]")


def _told_change(text, match):
    """The dream's rule: a credential word, a dash, then a word of the sentence, not a value — in a non-Latin
    script, nothing of a credential's shape, the sentence going on after it — in a sentence about changing it."""
    if "sep" not in match.re.groupindex or match.group("sep").strip(" \t\"'«“„‚`「『") not in ("—", "–", "-"):
        return False
    value = match.group("value").rstrip(".,;:!?")
    if not _LETTER_RE.search(value) or _LATIN_RE.search(value) or _looks_like_credential(value):
        return False
    rest = _SENTENCE_END_RE.split(text[match.end("value"):], 1)[0]
    if not _LETTER_RE.search(rest):
        return False
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text, 0, match.start())]
    return bool(PASSWORD_EVENT_RE.search(text[(ends[-1] if ends else 0):match.end()]))


def _screened_text(text):
    """The text the provider's screen judges: emoji joiners, flag tags and word separators out, and the value of a
    told change (_told_change) blanked."""
    text = _strip_word_separators(_FLAG_TAGS_RE.sub("", _EMOJI_JOINER_RE.sub("", text)))
    told = sorted({m.span("value") for rx in HOLO_SCREEN._RE_CREDENTIALS for m in rx.finditer(text)
                   if _told_change(text, m)}, reverse=True)
    for start, end in told:
        text = text[:start] + "…" + text[end:]
    return text


@functools.lru_cache(maxsize=8192)
def classify_unsafe(content):
    """'secret' | 'injection' | None. Conservative quarantine: exclusion from
    auto-promotion with a note in the report, never deletion. The dream's own
    rules and the holographic provider's screen (see _load_holo_screen); a
    secret by either wins over an instruction."""
    text = content or ""
    if SECRET_RE.search(text) or _password_marker_unsafe(text):
        return "secret"
    screened = _screened_text(text) if HOLO_SCREEN is not None else None
    if screened is not None and HOLO_SCREEN._secret_in(screened):
        return "secret"
    if INJECTION_RE.search(_DOOR_KEY_RE.sub("вещь", text)):
        return "injection"
    if screened is not None and HOLO_SCREEN._recall_problem(_DOOR_KEY_RE.sub("вещь", screened)):
        return "injection"  # not a credential: _secret_in covers those
    return None


# ---------------------------------------------------------------------------
# Candidate gates and helpers
# ---------------------------------------------------------------------------

def promotion_ready(item):
    """Require corroboration, not only a high internal provider score."""
    meta = item["meta"]
    helpful = item.get("helpful_count") or 0
    return (
        meta["ref_days"] >= 2
        or meta["mentions"] >= MIN_MENTIONS
        or (meta["rc"] >= 3 and helpful >= 1)
    )


def user_profile_hint(fact):
    """Hint only: the agent still judges stability, usefulness and sensitivity."""
    category = _norm(fact.get("category"))
    if category in PROFILE_CATEGORIES:
        return True
    return bool(PROFILE_HINT_RE and PROFILE_HINT_RE.search(_norm(fact.get("content"))))


def _theme_gram(gram):
    """An n-gram that can stand for a theme: a pair of Han characters with no function character (学校, 牙医) —
    usually a word. Kana, Hangul and Thai n-grams are pieces of words (アン, 나는, รีย) and are left out."""
    return all(_HAN_RE.match(c) for c in gram) and gram_weight(gram) == 1.0


def extract_themes(messages, window_days):
    cutoff = _now().timestamp() - window_days * 86400
    doc_freq = defaultdict(int)
    samples = {}
    for m in messages:
        if m["ts"] < cutoff:
            continue
        for w in m["tokens"]:
            if is_gram(w) and not _theme_gram(w):
                continue
            doc_freq[w] += 1
            samples.setdefault(w, m["content"][:120])
    themes = [{"theme": w, "mentions": n, "sample": samples[w]}
              for w, n in doc_freq.items() if n >= MIN_MENTIONS]
    themes.sort(key=lambda x: -x["mentions"])
    return themes


def parse_md_entries(mem_md):
    """§-entries of a memory file, parsed exactly as the core does."""
    if not os.path.exists(mem_md):
        return []
    try:
        text = _read_memory_file(mem_md)
    except OSError:
        return []
    return [e for e in _memory_entries(text) if len(e) > 10]


def _entry_key(entry):
    """Stable key of a §-entry for the cooldown (normalized head of the text)."""
    return hashlib.md5(_norm(entry)[:200].encode("utf-8")).hexdigest()[:16]


def is_pinned(entry):
    """A human-pinned entry (marker from `pinned_markers`) is never asked about."""
    return any(m in (entry or "") for m in PINNED_MARKERS)


# `old_text` of the core `memory replace` is matched as a SUBSTRING of an
# existing entry, so a verbatim head of the entry is always a valid anchor —
# while a paraphrase or a fact's own text never is. Live failure 2026-08-16:
# the agent was told to "prefer replace" and passed fact texts as `old_text`;
# four zero-match errors tripped the core's per-turn consolidation guard and
# nothing was written that night. Hence: hand the anchor over, ready to copy.
REPLACE_ANCHOR_CHARS = 60
ANCHOR_MIN_CHARS = 12
# Above this the whole entry is not handed over verbatim (prompt budget); the
# agent is told the entry is bigger than the preview instead.
FULL_ENTRY_MAX_CHARS = 1200


def _replace_anchor(entry, sources):
    """A VERBATIM substring of `entry` that occurs in exactly one entry.

    Raw, with the original whitespace: the core looks for `old_text` as a
    literal substring, so collapsing spaces (as this did until 2026-10-08)
    made 5 of 56 anchors unfindable on a live memory file.

    Preference order is "what a human would copy": a whole single line, from
    the top down, trimmed to REPLACE_ANCHOR_CHARS and then at full length. A
    heading-only line like "## Marina" is too short and too common to locate
    anything, hence ANCHOR_MIN_CHARS. The last resort is a raw prefix of the
    entry, newline and all — it still matches, it is just less pleasant to
    copy. The anchor only LOCATES the entry; `replace` then rewrites the whole
    of it, which is why `nearest_entry` ships the full text next to it."""
    raw = entry or ""
    if not raw.strip():
        return None
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    candidates = []
    for line in lines:
        candidates.append(line[:REPLACE_ANCHOR_CHARS].rstrip())
        if len(line) > REPLACE_ANCHOR_CHARS:
            candidates.append(line)
    candidates.append(raw[:REPLACE_ANCHOR_CHARS].rstrip())
    seen = set()
    for cand in candidates:
        if len(cand) < ANCHOR_MIN_CHARS or cand in seen:
            continue
        seen.add(cand)
        if sum(1 for _, other in sources if cand in (other or "")) == 1:
            return cand
    return None


def nearest_entry(content, memory_text=None, sources=None):
    """The durable memory chunk most similar to `content`, or None.

    Shown next to a candidate so the agent UPDATES the existing entry instead
    of appending a near-duplicate (mem0's ADD/UPDATE/NOOP idea, without an LLM
    in the cron). Carries `source` (which file to edit) and, when unambiguous,
    `old_text` — a verbatim anchor for `memory replace`."""
    if sources is None:
        sources = [("", chunk) for chunk in _memory_chunks(memory_text)]
    item_stems = _stems(sig_tokens(content))
    if len(item_stems) < 3:
        return None
    best, best_src, best_score = None, "", 0.0
    for src, chunk in sources:
        chunk_stems = _stems(sig_tokens(chunk))
        if not chunk_stems:
            continue
        shared = len(item_stems & chunk_stems)
        if shared < 3:
            continue
        score = shared / min(len(item_stems), len(chunk_stems))
        if score > best_score:
            best, best_src, best_score = chunk, src, score
    if best is None or best_score < 0.3:
        return None
    item = {"entry": best[:160], "overlap": round(best_score, 2)}
    if best_src:
        item["target"] = best_src
    # An entry a human wrote with a credential (or an instruction) in it: the
    # candidate still learns that it has a neighbour, but not its text — the
    # full entry and the replace anchor carried the secret into the prompt, the
    # cache and the cron log (Codex review of 2.7.2, 2026-10-14).
    if classify_unsafe(best):
        item.update(entry=ENTRY_REDACTED, replace_unsafe=True,
                    note="this entry holds a secret or an instruction and is not shown: "
                         "do not replace it — add a separate entry or ask")
        return item
    # `replace` rewrites the WHOLE entry, so the agent must know what it is
    # about to overwrite: how big the entry is, whether it has more than one
    # line/section, and — while it fits the prompt budget — its full text, so
    # the edit can keep what is already there (and pin `matched_entry`).
    if len(best) > 160:
        item["entry_chars"] = len(best)
    sections = [ln for ln in best.split("\n") if ln.strip()]
    if len(sections) > 1:
        item["multi_section"] = True
        item["note"] = ("replace rewrites this ENTIRE entry — carry over everything "
                        "worth keeping into new_content")
    anchor = _replace_anchor(best, sources)
    if len(best) <= FULL_ENTRY_MAX_CHARS:
        item["full_entry"] = best
        if anchor:
            item["old_text"] = anchor
    elif anchor:
        # Too big to show, so an update cannot be composed safely here.
        item["replace_unsafe"] = True
        item["note"] = ("this entry is longer than the preview and is not shown in full: "
                        "do not replace it blind — add a separate entry or ask")
    return item


# ---------------------------------------------------------------------------
# Loss guard: snapshot of durable entries between passes
# ---------------------------------------------------------------------------
# The script never writes memory, but the agent does after every dream. A bad
# turn ("memory replace" that swallowed half of MEMORY.md) would otherwise stay
# silent until somebody notices. The pass keeps the §-entry keys of the durable
# files; if on the next pass more than `memory_loss_alert_fraction` of them are
# gone, an alert is raised for the human (nothing is restored automatically —
# the human may have pruned on purpose).

def _entry_keys_of(path):
    return {_entry_key(e) for e in parse_md_entries(path)}


ENTRY_HEAD_CHARS = 60
# Below this an entry is too short for "it shrank" to mean anything.
SHRINK_MIN_CHARS = 160
# A file with this much text is worth guarding even when it holds few entries
# (a 4-entry MEMORY.md is normal; losing 90% of its characters is not).
LOSS_MIN_CHARS = 400


def _entry_head_key(entry):
    """Key of an entry's BEGINNING — survives a truncation that `_entry_key`
    (first 200 normalized chars) would see as a different entry."""
    return hashlib.md5(_norm(entry)[:ENTRY_HEAD_CHARS].encode("utf-8")).hexdigest()[:16]


def _entry_sizes_of(path):
    """Snapshot of one file: sizes by entry key and by entry head.

    Sizes make the guard notice that one BIG entry vanished among small ones;
    head keys make it notice an entry that kept its beginning and lost its
    body — the exact shape of a replace that swallowed a multi-section entry."""
    entries = parse_md_entries(path)
    return {"entries": {_entry_key(e): len(e) for e in entries},
            "heads": {_entry_head_key(e): len(e) for e in entries}}


def _ints(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in value.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _snapshot_sizes(value):
    """Previous snapshot for one file → ({entry_key: chars}, {head_key: chars}).
    Tolerates both older formats: a bare list of keys and a flat {key: chars}."""
    if isinstance(value, list):
        return {str(k): 0 for k in value}, {}
    if isinstance(value, dict):
        if "entries" in value or "heads" in value:
            return _ints(value.get("entries")), _ints(value.get("heads"))
        return _ints(value), {}
    return {}, {}


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_PROBE_SENTENCE_RE = re.compile(r"[.;!?。；！？\n]+")


def probe_found(probe, memory_text):
    """A probe is in memory when its text is (case, ё and spacing aside), or every word of it is in one entry
    (any form of it: "allergic to peanuts" / "peanut allergy" is not that, "аллергия на арахис" / "аллергии на
    арахис" is)."""
    if _norm(probe) in _norm(memory_text):
        return True
    stems = _stems(sig_tokens(probe))
    # in one sentence: "allergic to pollen; Bob likes peanuts" holds both words and not the fact (review 2026-10-13)
    return bool(stems) and any(stems <= _stems(sig_tokens(sentence)) for chunk in _memory_chunks(memory_text)
                               for sentence in _PROBE_SENTENCE_RE.split(chunk))


def check_probes(memory_text, previous_missing):
    """(alerts, missing): one alert per probe that went missing since the previous pass (or was never found);
    a probe still missing stays quiet — the alert said it, the agent cannot restore it on its own."""
    missing = [p for p in PROBES if not probe_found(p, memory_text)]
    before = set(previous_missing or [])
    alerts = [{"kind": "probe_missing", "probe": p[:120],
               "message": f"memory no longer holds “{p[:120]}” (a probe in dreaming.json) — check whether an "
                          f"entry was merged or removed by mistake."} for p in missing if p not in before]
    return alerts, missing


def check_memory_loss(mem_md, snapshot_path, memory_text=None):
    """Compare durable files with the previous snapshot; write a new one.
    Returns a list of alerts (possibly empty)."""
    if not snapshot_path:
        if PROBES:  # without the snapshot a missing probe would alert every night
            print("[dream] warn: probes are checked only with a snapshot state (--snapshot-state)", file=sys.stderr)
        return []
    previous = load_snapshot(snapshot_path)
    current, alerts = {}, []
    probes_missing = []
    if PROBES and memory_text is not None:
        probe_alerts, probes_missing = check_probes(memory_text, previous.get("_probes_missing"))
        alerts.extend(probe_alerts)
    for p in _memory_candidate_paths(mem_md):
        name = _rel_home(p)
        prev_sizes, prev_heads = _snapshot_sizes(previous.get(name))
        prev = set(prev_sizes)
        if not os.path.exists(p):
            # A file that was there yesterday and is gone today is the loudest
            # loss there is — it must not slip through as "nothing to compare"
            # and then be overwritten by an empty snapshot. Alert once; the
            # snapshot forgets the file only after the alert was raised.
            # The same floor as for a file that is still there: one big entry is worth an alert too
            # (Codex review of 2.7.2 — a lone 2000-character entry vanished with its file, silently).
            if len(prev) >= 4 or sum(prev_sizes.values()) >= LOSS_MIN_CHARS:
                alerts.append({"kind": "memory_loss", "file": name,
                               "lost": len(prev), "had": len(prev),
                               "message": f"{name}: the file is missing — all {len(prev)} entries "
                                          f"present at the previous pass are gone. "
                                          f"Check whether that was intended."})
            continue
        snap = _entry_sizes_of(p)
        current[name] = snap
        sizes, heads = snap["entries"], snap["heads"]
        keys = set(sizes)
        prev_total = sum(prev_sizes.values())
        if len(prev) >= 4 or prev_total >= LOSS_MIN_CHARS:
            lost = prev - keys
            frac = len(lost) / len(prev)
            prev_chars = prev_total
            lost_chars = sum(prev_sizes.get(k, 0) for k in lost)
            char_frac = (lost_chars / prev_chars) if prev_chars else 0.0
            if frac > MEMORY_LOSS_ALERT_FRACTION or char_frac > MEMORY_LOSS_ALERT_FRACTION:
                # Entry count alone misses "one big entry vanished"; the share of
                # characters alone misses "many small entries gone". Both, then.
                alerts.append({"kind": "memory_loss", "file": name,
                               "lost": len(lost), "had": len(prev),
                               "lost_chars": lost_chars, "had_chars": prev_chars,
                               "message": f"{name}: {len(lost)} of {len(prev)} entries present at the "
                                          f"previous pass are gone "
                                          f"({round(100 * frac)}% of entries, "
                                          f"{round(100 * char_frac)}% of characters). "
                                          f"Check whether that was intended."})
            # An entry that kept its head but lost most of its body: a replace
            # that swallowed the rest of a multi-section entry.
            for key, was in prev_heads.items():
                now_len = heads.get(key)
                if not was or now_len is None or was < SHRINK_MIN_CHARS:
                    continue
                if now_len < was * (1 - MEMORY_LOSS_ALERT_FRACTION * 2):
                    alerts.append({"kind": "entry_truncated", "file": name,
                                   "had_chars": was, "chars": now_len,
                                   "message": f"{name}: an entry shrank from {was} to {now_len} "
                                              f"characters while keeping its beginning — check that "
                                              f"a replace did not drop the rest of it."})
    try:
        _write_private(snapshot_path, json.dumps(
            {**current, "_at": _now().isoformat(timespec="seconds"),
             **({"_probes_missing": probes_missing} if probes_missing else {})}, ensure_ascii=False))
    except OSError as e:
        print(f"[dream] warn: snapshot not written: {e}", file=sys.stderr)
    return alerts


def _agent_acked(state_db, generated_at):
    """Did the agent turn that received the payload stamped `generated_at`
    actually answer? Cooldowns used to start the moment the pre-check printed
    an item — if the model or the memory tool then failed, the item vanished
    for 14 days without any outcome. There is no channel for the agent to
    acknowledge at night (a cron session has memory and nothing else), but the
    session itself is on record: Hermes stores cron sessions in state.db, the
    job prompt (role=user) carries the payload verbatim, and a completed turn
    leaves a non-empty assistant message. So the next pass looks back: no
    session with that stamp, or a session without an answer → not acked → the
    marks are dropped and the items come back tonight.

    Fail-soft: no state.db / no messages table → assume acked (old behaviour),
    otherwise a host that does not persist sessions would re-show every night."""
    if not generated_at:
        return True
    if not os.path.exists(state_db):
        return True
    when = _parse_iso(generated_at)
    since = when.timestamp() - 3600 if when else 0
    needles = (f'"generated_at":"{generated_at}"', f'"generated_at": "{generated_at}"')
    c = _conn(state_db)
    try:
        try:
            msg_cols = _table_columns(c, "messages")
            sids = [r[0] for r in c.execute(
                "select distinct session_id from messages where role='user' and timestamp>=? "
                "and (instr(coalesce(content,''),?)>0 or instr(coalesce(content,''),?)>0)",
                (since, *needles))]
            if not sids:
                return False
            # An answer is an answer: not a harness notice, not a row the core
            # typed as a failed turn, not the text that preceded a tool call in
            # a turn that then died. "Operation interrupted." used to count
            # (external review, 2026-10-07).
            where = ["session_id=?", "role='assistant'", "trim(coalesce(content,''))!=''"]
            if "display_kind" in msg_cols:
                where.append("coalesce(display_kind,'')!='failed_turn'")
            if "tool_calls" in msg_cols:
                where.append("coalesce(tool_calls,'')=''")
            sql = f"select content from messages where {' and '.join(where)} limit 20"
            for sid in sids:
                for (content,) in c.execute(sql, (sid,)):
                    if not is_harness_envelope(content):
                        return True
            return False
        except sqlite3.OperationalError:
            return True
    finally:
        c.close()


def _revert_unacked(seen, state_db):
    """Pop the previous pass's `_pending` record from the seen state; if that
    showing was never acknowledged, drop its marks. Returns the md_decays keys
    to re-ask (the asked-state lives in its own file)."""
    pending = seen.pop("_pending", None)
    if not isinstance(pending, dict) or not pending.get("generated_at"):
        return set()
    if _agent_acked(state_db, pending.get("generated_at")):
        return set()
    dropped = 0
    for bucket_name, keys in (pending.get("seen") or {}).items():
        bucket = seen.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for k in keys or []:
            # Ride-along sections are reopened silently: on a night the gate kept shut nobody was meant to answer.
            if bucket.pop(k, None) is not None and bucket_name not in ("trust_feedback", "keywords_needed", "confirm"):
                dropped += 1
    reask = set(pending.get("asked") or [])
    if dropped or reask:
        print(f"[dream] note: the pass of {pending['generated_at']} was not acknowledged by an "
              f"agent turn — {dropped} cooldown(s) and {len(reask)} md question(s) reopened",
              file=sys.stderr)
    return reask


def load_pending_writes(home=None):
    """Memory writes staged for human approval, newest first.

    With `memory.write_approval: true` a cron write answers `success: true,
    staged: true` and MEMORY.md stays as it was. The dream has to read the
    queue to tell "saved" from "waiting", or it offers the same candidate every
    night and the queue grows (87 unattended items on the install the external
    review measured). Fail-soft: no directory, unreadable or foreign JSON — an
    empty list, never an exception."""
    base = os.path.join(home or HOME, PENDING_DIR)
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        record_id = str(data.get("id") or name[:-5])
        created = data.get("created_at") if isinstance(data.get("created_at"), (int, float)) else None
        for pos, op in enumerate(_pending_ops(payload)):
            content = op.get("content") or op.get("new_content") or ""
            out.append({
                "id": record_id if len(_pending_ops(payload)) == 1 else "%s#%d" % (record_id, pos),
                "record_id": record_id,
                "action": str(op.get("action") or data.get("action") or ""),
                "target": str(op.get("target") or payload.get("target") or ""),
                "content": content if isinstance(content, str) else "",
                "old_text": op.get("old_text") if isinstance(op.get("old_text"), str) else "",
                "created_at": created,
            })
    out.sort(key=lambda p: p.get("created_at") or 0, reverse=True)
    return out


def _pending_ops(payload):
    """The writes inside one queued record: a single write, or every op of a
    batch. The core stages an `operations[]` call as ONE record whose payload
    carries `action: "batch"` and the ops themselves (`memory_tool.py`:
    "batch if operations is not None"), and the skill is the side that asks for
    batches — so reading only the top level made every staged batch look like an
    empty write: invisible to `_staged_text`, and the pass would offer the same
    candidates again the next night, which is the one thing this queue reader
    exists to prevent. Malformed input degrades to "one write", never raises."""
    ops = payload.get("operations")
    if isinstance(ops, (list, tuple)):
        inner = [op for op in ops if isinstance(op, dict)]
        if inner:
            return inner
    if str(payload.get("action") or "") == "batch":
        # A batch whose ops are unreadable: keep whatever text the record carries
        # and drop the "batch" label, so the queue still counts as staged instead
        # of silently reading as "nothing is waiting".
        return [dict(payload, action="")]
    return [payload]


def _staged_text(pending):
    """One blob of everything the queue would write — `already_in_memory` then
    answers "is this candidate already waiting?" with the same machinery that
    answers "is it already in memory?"."""
    return MEMORY_ENTRY_DELIMITER.join(
        p["content"] for p in pending
        if p.get("content") and p.get("action") in ("add", "replace", ""))


def _staged_removals(pending):
    """Texts the queue would REMOVE (or replace away): an entry whose removal is
    waiting must not be asked about, nor counted as confirmed by its cooldown."""
    return [p.get("old_text") or p.get("content") or ""
            for p in pending if p.get("action") in ("remove", "replace")]


def pending_alert(pending, seen, now):
    """An alert when the queue is both big and old — once per cooldown.

    The queue is the human's job, not the agent's, so this is a reminder, not
    work: it fires only when `alert_min` items have been waiting longer than
    `alert_days`, and then rests for the usual cooldown."""
    if not pending or len(pending) < PENDING_ALERT_MIN:
        return None
    oldest = min((p["created_at"] for p in pending if p.get("created_at")), default=None)
    if not oldest:
        return None
    age_days = (now.timestamp() - oldest) / 86400
    if age_days < PENDING_ALERT_DAYS:
        return None
    last = _parse_iso((seen.get("_pending_alert") or {}).get("at"))
    if last and (now - last) < timedelta(days=SEEN_COOLDOWN_DAYS):
        return None
    seen["_pending_alert"] = {"at": now.isoformat(timespec="seconds"), "count": len(pending)}
    # Writes and queued records differ when a record holds an `operations[]` batch:
    # the human reviewing the queue sees records, so name both rather than let the
    # numbers disagree with what `/memory pending` shows.
    records = len({p.get("record_id") or p.get("id") for p in pending})
    scope = (f"{len(pending)} memory write(s)" if records == len(pending)
             else f"{len(pending)} memory write(s) in {records} queued record(s)")
    return {"kind": "pending_writes", "count": len(pending), "records": records,
            "oldest_days": round(age_days),
            "message": (f"{scope} are waiting for approval, the oldest for "
                        f"{round(age_days)} days — nothing from them is in memory yet. "
                        f"Review them (`/memory` in Hermes) or turn the gate off.")}


def _load_asked_state(path):
    """{entry_key: {"at", "confirmed"?, "stems"?}}. A legacy value (a bare ISO
    date — the format before confirmations existed) reads as {"at": value}."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = {"at": v}
        elif isinstance(v, dict) and v.get("at"):
            out[k] = dict(v)
    return out


# A §-entry rewritten after a "still relevant?" question is the SAME entry with
# a new key: the key hashes the text, and the usual answer to the question is a
# fresh date stamp ("As of 2026-09-23, …"). Live case 18–19.09 and 24–30.09:
# three entries rewritten that way were asked again the very next night, and
# one six days later — the question came back precisely BECAUSE it had been
# answered. So an entry without a record of its own inherits the record of a
# vanished one on the same subject (≥4 shared stems, ≥0.7 of the shorter side),
# and the inheritance itself is the confirmation. Numbers are deliberately
# ignored: a new date is the very thing a confirming rewrite changes. The bar is
# lower than dedupe's because the pool is tiny — only entries that vanished
# after a question — and a false match costs little: a freshly written entry
# skips one "still relevant?" round. Rewrites inflect words («полке» → «полка»),
# and crude 5-char stems lose those, so 0.8 missed a plain rephrasing.
MD_SUCCESSOR_MIN_SHARED = 4
MD_SUCCESSOR_RATIO = 0.7


def _md_successor(stems, orphans):
    """Key of the vanished asked entry `stems` most likely rewrites, or None."""
    best, best_ratio = None, 0.0
    for key, rec in orphans.items():
        other = set(rec.get("stems") or ())
        shared = stems & other
        if len(shared) < MD_SUCCESSOR_MIN_SHARED:
            continue
        ratio = len(shared) / min(len(stems), len(other))
        if ratio >= MD_SUCCESSOR_RATIO and ratio > best_ratio:
            best, best_ratio = key, ratio
    return best


def _md_cooldown_end(rec):
    """When the running cooldown of an asked-record ends (None — no cooldown)."""
    confirmed = _parse_iso(rec.get("confirmed"))
    if confirmed:
        return confirmed + timedelta(days=MD_CONFIRMED_COOLDOWN_DAYS)
    at = _parse_iso(rec.get("at"))
    return at + timedelta(days=MD_ASK_COOLDOWN_DAYS) if at else None


def md_decays(mem_md, messages, decay_days, asked_state_path=None, cap=None, reask=(),
              staged_removals=()):
    """§-entries of MEMORY.md that have not surfaced in conversations for long
    → soft flag "still relevant?".

    Cooldowns. An entry asked within MD_ASK_COOLDOWN_DAYS is not raised again
    (without it the dream asked the same thing every night). An entry that
    SURVIVED a question counts as confirmed and rests for
    MD_CONFIRMED_COOLDOWN_DAYS: either it is still there unchanged when the
    short cooldown runs out (the agreed way to say "not relevant" is to remove
    or rewrite the entry, so keeping it is the answer), or it was rewritten in
    place — see `_md_successor`. Records outlive their entry until their own
    cooldown ends: a rewrite must still find the record of the text it replaced.

    Only entries that make it into the published slice (`cap`) are marked as
    asked — an overflow entry never reached the agent. `reask` — keys whose
    previous showing was never acknowledged by an agent turn (see
    `_agent_acked`): their records are dropped, the question is asked anew."""
    out = []
    now = _now()
    now_ts = now.timestamp()
    stamp = now.isoformat(timespec="seconds")
    asked = _load_asked_state(asked_state_path) if asked_state_path else {}
    changed = False
    for key in reask:
        if key in asked:
            asked.pop(key, None)
            changed = True
    entries = [(i, entry, _entry_key(entry)) for i, entry in enumerate(parse_md_entries(mem_md))]
    current_keys = {key for _, _, key in entries}
    orphans = {k: r for k, r in asked.items() if k not in current_keys and r.get("stems")}
    for i, entry, key in entries:
        stems = _stems(sig_tokens(entry))
        rec = asked.get(key)
        if rec is None and orphans:
            prev = _md_successor(stems, orphans)
            if prev:
                rec = orphans.pop(prev)
                asked.pop(prev, None)
                rec["confirmed"] = stamp
                asked[key] = rec
                changed = True
                print(f"[dream] note: MEMORY entry #{i} rewrites an entry asked on "
                      f"{rec.get('at', '?')[:10]} — counted as confirmed", file=sys.stderr)
        if rec is not None and rec.get("stems") != sorted(stems):
            rec["stems"] = sorted(stems)  # also upgrades a legacy record
            changed = True
        if is_pinned(entry):
            continue
        if any(t and t in entry for t in staged_removals):
            # Its removal (or rewrite) is already waiting for approval: asking
            # again is noise, and "still here, so it is confirmed" would be a
            # lie — the answer was given, it is the queue that is stuck.
            continue
        toks = sig_tokens(entry)
        mentions, ref_days, last_ts = corroborate(toks, messages)
        days_since = (now_ts - last_ts) / 86400 if last_ts else None
        if not (mentions == 0 or (days_since is not None and days_since > decay_days)):
            continue
        if rec is not None:
            end = _md_cooldown_end(rec)
            if end and now < end:
                continue
            if not rec.get("confirmed"):
                # Asked, short cooldown over, entry still here unchanged: kept.
                rec["confirmed"] = stamp
                changed = True
                continue
        if cap is not None and len(out) >= cap:
            break
        asked[key] = {"at": stamp, "stems": sorted(stems)}
        changed = True
        out.append({"index": i, "entry": _safe_preview(entry, 160), "key": key,
                    "last_mention_days": round(days_since, 1) if days_since is not None else None,
                    "reason": "never surfaced within the window" if mentions == 0
                              else f"not surfaced for ~{round(days_since)} d"})
    if asked_state_path:
        # A record of a vanished entry is kept only while its cooldown runs.
        kept = {k: v for k, v in asked.items()
                if k in current_keys or ((_md_cooldown_end(v) or now) > now)}
        if changed or kept.keys() != asked.keys():
            try:
                _write_private(asked_state_path, json.dumps(kept, ensure_ascii=False, indent=1))
            except OSError as e:
                print(f"[dream] warn: asked-state not written: {e}", file=sys.stderr)
    return out


def _rel_home(path):
    """Path relative to HERMES_HOME when inside it (keys of memory_char_limits)."""
    try:
        rel = os.path.relpath(path, HOME)
    except ValueError:  # different drive on Windows
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def memory_usage(mem_md):
    """Fill level of the durable files (chars, and % when a limit is configured).
    Keys are paths relative to HERMES_HOME (e.g. `memories/MEMORY.md`), and so
    are the keys of `memory_char_limits` in the config."""
    out = {}
    for path in _memory_candidate_paths(mem_md):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                n = len(f.read())
        except OSError:
            continue
        name = _rel_home(path)
        limit = MEMORY_CHAR_LIMITS.get(name)
        rec = {"chars": n}
        if limit:
            rec["limit"] = int(limit)
            rec["percent"] = round(Fraction(100 * n, int(limit)))  # exact: a 400-digit limit overflowed a float
        out[name] = rec
    return out


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def run(window_days, corr_days, md_decay_days, mem_md, asked_state_path=None,
        rejected_state_path=None, seen_state_path=None, snapshot_state_path=None):
    state_db = os.path.join(HOME, "state.db")
    facts = load_all_facts()
    all_messages = load_messages(state_db, corr_days)
    by_store = messages_by_store(all_messages)
    messages = by_store[None]  # the trusted ones: shared facts, memory entries, themes
    memory_text = load_durable_memory_text(mem_md)
    rejected = load_rejected(rejected_state_path)
    now = _now()
    generated_at = now.isoformat(timespec="seconds")
    seen = load_seen(seen_state_path)
    reask_md = _revert_unacked(seen, state_db) if seen_state_path else set()
    seen_new_facts = seen.setdefault("new_facts", {})
    seen_fact_decays = seen.setdefault("fact_decays", {})
    seen_conflicts = seen.setdefault("conflicts", {})
    seen_promotions = seen.setdefault("promotions", {})
    seen_feedback = seen.setdefault("trust_feedback", {})
    seen_keywords = seen.setdefault("keywords_needed", {})
    seen_confirm = seen.setdefault("confirm", {})
    # A write waiting for approval is not in memory yet, but it IS the answer to
    # the candidate that produced it: offering it again every night only grows
    # the queue. The staged texts are treated as a second memory file — the same
    # dedupe decides "already covered".
    pending_writes = load_pending_writes()
    staged_text = _staged_text(pending_writes)
    staged_removals = _staged_removals(pending_writes)

    scored = []
    for f in facts:
        score, sig, meta = signals_for_fact(f, messages_for(f, all_messages, by_store))
        rejection = rejection_of(f["content"], rejected)
        again = rejection is not None and said_again(f, meta, rejection)
        scored.append({**f, "score": score, "signals": sig, "meta": meta,
                       "unsafe": classify_unsafe(f["content"]),
                       "in_memory": already_in_memory(f["content"], memory_text),
                       "staged": bool(staged_text)
                       and already_in_memory(f["content"], staged_text),
                       "rejected": rejection is not None and not again,
                       # said again after a human rejected it: a candidate once more, but one to ask about
                       **({"previously_rejected": {"at": str(rejection.get("at") or rejection.get("rejected_at")
                                                             or "")[:10],
                                                   "reason": str(rejection.get("reason") or "")[:160]}}
                          if again else {})})

    # Rejected by a human leaves through no section — not as a candidate, not
    # as a "new fact", not as a relevance question. Removed BEFORE quarantine:
    # otherwise a quarantined fact could never be closed (the reject list did
    # not reach it, deleting from the DB needs an explicit human request, and
    # `quarantined` is actionable) and woke the agent every night forever.
    suppressed = [s for s in scored if s["rejected"]]
    active = [s for s in scored if not s["rejected"]]

    # Quarantine: secrets and facts with embedded instructions never leave.
    # Secrets — no content at all; injection — a short preview in the full JSON only.
    quarantined = [s for s in active if s["unsafe"]]
    safe = [s for s in active if not s["unsafe"]]

    # A dated event whose date has passed cannot be worked on — but dropping it
    # without a trace meant a durable fact that merely CONTAINED a trigger word
    # («страница», «тест», "example") vanished from every section silently
    # (external review, 2026-10-07). It is now listed in its own read-only
    # section and in the diary; it still wakes nobody.
    expired = [s for s in safe if is_expired_event(s["content"])]
    expired_ids = {s["fact_id"] for s in expired}
    safe = [s for s in safe if s["fact_id"] not in expired_ids]

    # Every fact created or reinforced during the window is surfaced for a
    # separate profile review — not pre-filtered by score (a stable preference
    # matters before it accumulates retrievals), minus what is already in
    # durable memory, minus what was shown within the cooldown (before the cap,
    # so suppressed candidates do not take slots from unseen ones).
    new_facts_window = [s for s in safe if s["meta"]["days_old"] <= window_days]
    new_facts_pending = [s for s in new_facts_window
                         if not s["in_memory"] and not s["staged"]]
    new_facts = unseen(new_facts_pending, seen_new_facts, now)
    new_facts_suppressed = len(new_facts_pending) - len(new_facts)
    new_facts.sort(key=lambda s: (s["meta"]["days_old"], -s["score"]))

    promotion_candidates = [
        s for s in safe
        if s["score"] >= MIN_SCORE and promotion_ready(s)
        and not s["in_memory"] and not s["staged"]
    ]
    # Short rest after a promotion was actually shown (and the night acked): a
    # candidate the agent could not write — approval gate, budget, a refusal —
    # used to come back every single night.
    promotions_before_cooldown = len(promotion_candidates)
    promotion_candidates = unseen(promotion_candidates, seen_promotions, now,
                                  cooldown_days=PROMOTION_COOLDOWN_DAYS)
    promotions_suppressed = promotions_before_cooldown - len(promotion_candidates)
    staged_suppressed = sum(1 for s in safe if s.get("staged"))
    ephemeral_events = [s for s in promotion_candidates if is_ephemeral_fact(s["content"])]
    promotions = [s for s in promotion_candidates if not is_ephemeral_fact(s["content"])]
    promotions.sort(key=lambda s: -s["score"])
    ephemeral_events.sort(key=lambda s: -s["score"])
    promotions, promotions_dups = collapse_store_duplicates(promotions)
    # A fact already offered as a promotion is not repeated in new_facts: the
    # agent reviews it once (live case: 8 extracted facts arrived in both
    # sections, doubling the prompt for no extra work). A duplicate hidden
    # behind a promotion counts as offered too.
    promoted_ids = {s["fact_id"] for s in promotions} | {s["fact_id"] for s in promotions_dups}
    new_facts = [s for s in new_facts if s["fact_id"] not in promoted_ids]
    # Collapsed before the cap, so a hidden twin does not take a slot. Twins of
    # a published representative are marked shown together with it (below);
    # twins of one cut by the cap wait for it.
    new_facts, new_facts_dups = collapse_store_duplicates(new_facts)
    new_facts = new_facts[:NEW_FACTS_CAP]
    shown_twins = {i for s in new_facts for i in s.get("duplicates", ())}
    new_facts_dups = [s for s in new_facts_dups if s["fact_id"] in shown_twins]

    # Possible updates of existing entries: same subject, different numbers.
    # Deliberately NOT gated by fuzzy `in_memory`: a reworded fact with a new
    # number is exactly what fuzzy dedupe swallows as "already known". Only an
    # identical head or a human-declared alias rule closes the question.
    # A fact already offered as a promotion is not repeated here either: it
    # arrives with its own `nearest_entry`, so the agent sees the possible
    # update once, in one section (the head-match fix surfaced eight sibling
    # facts at once, each doubled as a "conflict" with an unrelated sibling).
    conflicts_pending = []
    for s in safe:
        if s["fact_id"] in promoted_ids:
            continue
        if is_ephemeral_fact(s["content"]) or exact_or_alias_in_memory(s["content"], memory_text):
            continue
        hits = find_conflicts(s["content"], memory_text)
        if hits:
            conflicts_pending.append({**s, "conflicts": hits})
    conflicts = unseen(conflicts_pending, seen_conflicts, now)
    conflicts_suppressed = len(conflicts_pending) - len(conflicts)
    conflicts.sort(key=lambda s: -s["score"])

    decays_pending = [s for s in safe
                      if (s["meta"]["rc"] == 0 and s["meta"]["mentions"] == 0
                          and s["meta"]["days_old"] > window_days * 2
                          and (s["trust_score"] if s["trust_score"] is not None else 0.5) <= 0.5
                          and not s["in_memory"])]
    decays = unseen(decays_pending, seen_fact_decays, now)
    decays_suppressed = len(decays_pending) - len(decays)
    decays.sort(key=lambda s: -s["meta"]["days_old"])

    facts_text = " ".join(_norm(f["content"]) for f in facts)
    themes = [t for t in extract_themes(messages, window_days)
              if t["theme"] not in facts_text and t["theme"] not in _norm(memory_text)]
    for t in themes:
        if classify_unsafe(t.get("sample")):
            t["sample"] = "[hidden: suspicious content]"

    # Rejected facts the store still trusts: offer the one call that teaches
    # retrieval. Shown at most once per cooldown, and never on a source without
    # the tool.
    # Chat-store facts get neither call: a cron session cannot reach a chat's database.
    feedback_pending = trust_feedback_items([s for s in suppressed if not s.get("store")], rejected, fact_source())
    feedback_fresh = unseen(feedback_pending, seen_feedback, now, key=_feedback_key)
    # Only the shown slice is marked, so what the cap cuts off is not suppressed
    # — it simply comes tomorrow. The counter means "resting", nothing else.
    feedback = feedback_fresh[:FEEDBACK_CAP]
    feedback_suppressed = len(feedback_pending) - len(feedback_fresh)

    # Facts without keywords (holographic >= 0.6.0 searches them like text):
    # rides along when the agent is awake anyway. Quarantined, rejected and
    # expired facts are not offered — `safe` already left them out. A fact the
    # agent found nothing for rests like any shown item instead of coming back
    # every night; one that got keywords simply leaves the list.
    keywords_pending = keywords_needed_items([s for s in safe if not s.get("store")], fact_source())
    keywords_fresh = unseen(keywords_pending, seen_keywords, now, key=_feedback_key)
    keywords_needed = keywords_fresh[:KEYWORDS_CAP]

    # Facts people said again on different days (holographic >= 0.15.0): a confirmation is worth giving while the
    # agent is awake, never a reason to wake it.
    confirm_pending = confirm_items(safe, all_messages, by_store, fact_source())
    confirm = unseen(confirm_pending, seen_confirm, now, key=_feedback_key)[:CONFIRM_CAP]

    md_dec = md_decays(mem_md, messages, md_decay_days, asked_state_path, cap=PUBLISH_CAP,
                       staged_removals=staged_removals,
                       reask=reask_md)

    # Only published slices count as shown.
    published_promotions = promotions[:PUBLISH_CAP]
    published_decays = decays[:PUBLISH_CAP]
    published_conflicts = conflicts[:PUBLISH_CAP]
    if seen_state_path:
        mark_seen(new_facts + new_facts_dups, seen_new_facts, now)
        mark_seen(published_promotions, seen_promotions, now)
        mark_seen(feedback, seen_feedback, now, key=_feedback_key)
        mark_seen(keywords_needed, seen_keywords, now, key=_feedback_key)
        mark_seen(confirm, seen_confirm, now, key=_feedback_key)
        mark_seen(published_decays, seen_fact_decays, now)
        mark_seen(published_conflicts, seen_conflicts, now)
        for bucket in (seen_new_facts, seen_fact_decays, seen_conflicts):
            prune_seen(bucket, now)
        prune_seen(seen_promotions, now, cooldown_days=PROMOTION_COOLDOWN_DAYS)
        prune_seen(seen_feedback, now)
        prune_seen(seen_keywords, now)
    # The queue alert stamps its own cooldown into `seen`: computed before the state is written, or the stamp
    # never reached the file and the same alert woke the agent every night (Codex review of 2.7.2).
    queue_alert = pending_alert(pending_writes, seen, now) if seen_state_path else None
    if seen_state_path:
        # What this pass is about to show, keyed for `_revert_unacked` tomorrow.
        fp = _fact_fingerprint
        seen["_pending"] = {
            "generated_at": generated_at,
            "seen": {"new_facts": [fp(s["content"]) for s in new_facts + new_facts_dups],
                     "fact_decays": [fp(s["content"]) for s in published_decays],
                     "conflicts": [fp(s["content"]) for s in published_conflicts],
                     "promotions": [fp(s["content"]) for s in published_promotions],
                     # Ride-along sections are marked even when nothing else woke the agent; without an entry
                     # here a night the gate kept shut still rested them for the whole cooldown (Codex review
                     # of 2.7.2).
                     "trust_feedback": [_feedback_key(i) for i in feedback],
                     "keywords_needed": [_feedback_key(i) for i in keywords_needed],
                     "confirm": [_feedback_key(i) for i in confirm]},
            "asked": [d["key"] for d in md_dec if d.get("key")],
        }
        try:
            _write_private(seen_state_path, json.dumps(seen, ensure_ascii=False, indent=1))
        except OSError as e:
            print(f"[dream] warn: seen-state not written: {e}", file=sys.stderr)

    usage = memory_usage(mem_md)
    alerts = check_memory_loss(mem_md, snapshot_state_path, memory_text)
    if queue_alert:
        alerts.append(queue_alert)
    store_note = fact_store_note()
    chat_stores = chat_stores_overview(window_days)
    diary = build_diary(window_days, len(facts), len(messages), promotions, decays, themes, md_dec,
                        ephemeral_events, quarantined, len(suppressed), conflicts=published_conflicts,
                        alerts=alerts, note=store_note, chat_stores=chat_stores)

    memory_sources = load_durable_memory_sources(mem_md)

    def _pub_near(s):
        item = _pub(s)
        near = nearest_entry(s["content"], sources=memory_sources)
        if near:
            item["nearest_entry"] = near
        return item

    for d in md_dec:
        d.pop("key", None)  # internal handle, not for the agent

    result = {
        "generated_at": generated_at,
        "window_days": window_days, "corr_window_days": corr_days, "md_decay_days": md_decay_days,
        "weights": WEIGHTS,
        "gates": {"min_score": MIN_SCORE, "min_mentions": MIN_MENTIONS,
                  "promotion_corroboration": "2 ref_days OR min_mentions OR 3 retrievals + 1 helpful",
                  # The store's own recall floor (provider `min_trust_threshold` unless
                  # overridden here): what `trust_feedback` has to push a fact below.
                  "feedback_trust_floor": feedback_floor()},
        "stats": {"facts": len(facts), "new_facts_reviewed": len(new_facts),
                  # holographic >= 0.8.0: the store takes change / end / correct / forget (SKILL.md, step 7).
                  "fact_history": fact_history_supported(),
                  "new_facts_window": len(new_facts_window),
                  "messages_window": len(messages),
                  "promotions": len(promotions), "ephemeral_events": len(ephemeral_events),
                  "fact_decays": len(decays), "md_decays": len(md_dec), "themes": len(themes),
                  "conflicts": len(conflicts),
                  "quarantined": len(quarantined),
                  # Informational: silenced by the reject list / cooldown / expiry,
                  # or hidden behind a near-duplicate twin in the store.
                  # Not work — never in the precheck's actionable keys.
                  "rejected_suppressed": len(suppressed),
                  "new_facts_suppressed": new_facts_suppressed,
                  "promotions_suppressed": promotions_suppressed,
                  # Informational, never actionable: a staged write is the
                  # human's queue, not the agent's work.
                  "staged_suppressed": staged_suppressed,
                  "pending_writes": len(pending_writes),
                  "trust_feedback": len(feedback),
                  "trust_feedback_suppressed": feedback_suppressed,
                  # Tonight's slice and the whole backlog of facts without keywords.
                  "keywords_needed": len(keywords_needed),
                  "keywords_missing": len(keywords_pending),
                  "confirm": len(confirm),
                  "fact_decays_suppressed": decays_suppressed,
                  "conflicts_suppressed": conflicts_suppressed,
                  "expired_events": len(expired_ids),
                  "store_duplicates": len(promotions_dups) + len(new_facts_dups),
                  "alerts": len(alerts)},
        "memory_usage": usage,
        # Why the fact store cannot fill up (holographic scope / cron_writes), or None. Not work: never opens the gate.
        "alerts": alerts,
        "promotions": [_pub_near(s) for s in published_promotions],
        "new_facts": [_pub_near(s) for s in new_facts],
        "ephemeral_events": [_pub(s) for s in ephemeral_events[:PUBLISH_CAP]],
        # Read-only: never in `precheck.actionable_keys`, so it opens no gate.
        "expired_events": [_pub(s) for s in expired[:PUBLISH_CAP]],
        # Not in `precheck.actionable_keys` by default: a trust nudge is worth
        # doing when the agent is already awake, not worth waking it for.
        "trust_feedback": feedback,
        # Not actionable either: keywords are worth adding while awake.
        "keywords_needed": keywords_needed,
        # Not actionable either: people said these again on different days.
        "confirm": confirm,
        "fact_decays": [_pub(s) for s in decays[:PUBLISH_CAP]],
        "conflicts": [dict(_pub_near(s), conflicts=s["conflicts"]) for s in published_conflicts],
        "md_decays": md_dec,
        "emerging_themes": themes[:PUBLISH_CAP],
        "quarantined": [_pub_quarantined(s) for s in quarantined[:PUBLISH_CAP]],
        "diary": diary,
    }
    # Present only when there is something to say: a single shared store keeps the output it always had.
    if store_note:
        result["fact_store_note"] = store_note  # why the fact store cannot fill up; not work
    if chat_stores:
        result["chat_stores"] = chat_stores  # per-chat databases: counts, which are promoted, which are missing
    return result


def _pub_quarantined(s):
    """Secrets — fact_id + reason only (content is published nowhere);
    injection — a short preview for human review in the full JSON."""
    item = {"fact_id": s["fact_id"], "reason": s["unsafe"]}
    if s["unsafe"] == "injection":
        item["preview"] = (s["content"] or "")[:60]
    return item


def _why(s):
    """Short explanation why the fact surfaced (report and debugging)."""
    m = s["meta"]
    bits = [f"score {s['score']}"]
    if m["ref_days"]:
        bits.append(f"{m['mentions']} mention(s) in {m['ref_days']} day(s)")
    if m["rc"]:
        bits.append(f"{m['rc']} retrieval(s)")
    return "; ".join(bits)


def _pub(s):
    out = _pub_fields(s)
    if s.get("duplicates"):
        out["duplicates"] = list(s["duplicates"])
    return out


def _pub_fields(s):
    out = _pub_base(s)
    if s.get("store"):
        out["store"] = s["store"]  # a chat's own database: memory tool only, never fact_store / fact_feedback
    if s.get("previously_rejected"):
        out["previously_rejected"] = s["previously_rejected"]  # ask the human again; never write it straight away
    return out


def _pub_base(s):
    return {"fact_id": s["fact_id"], "content": s["content"], "category": s["category"],
            "tags": s["tags"], "score": s["score"], "signals": s["signals"],
            "days_old": s["meta"]["days_old"], "retrieval_count": s["meta"]["rc"],
            "mentions": s["meta"]["mentions"], "ref_days": s["meta"]["ref_days"],
            "evidence": s["meta"].get("evidence", []),
            "why": _why(s),
            "in_memory": s.get("in_memory", False),
            "ephemeral": is_ephemeral_fact(s["content"]),
            "user_profile_hint": user_profile_hint(s),
            **({"origin": s["origin"]} if s.get("origin") else {})}


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------

REDACTED = "[redacted: the entry holds a secret — look it up in the file by index]"
ENTRY_REDACTED = "[redacted: the entry holds a secret or an instruction]"


def _safe_preview(text, n, redacted=REDACTED):
    """Preview of durable text for the prompt and the diary.

    A MEMORY.md entry can itself hold a credential (a human wrote it there).
    The pass screens facts and evidence, but "still relevant?" questions and the
    diary carried the entry verbatim — the one place where a secret from memory
    reached the prompt and then a file on disk (external review, 2026-10-07)."""
    if classify_unsafe(text):
        return redacted
    return (text or "")[:n]


def chat_stores_line(cs):
    """One line on the per-chat databases: how many, how many facts, which are promoted, which are missing."""
    rows = cs.get("stores") or []
    total = sum(r.get("facts", 0) for r in rows)
    new = sum(r.get("new", 0) for r in rows)
    promoted = [r["store"] for r in rows if r.get("promote")]
    line = (f"Chat stores ({cs.get('scope')}): {len(rows)} database(s), {total} fact(s), {new} new in the window"
            + (f"; promoted: {', '.join(promoted)}" if promoted else "; none promoted"))
    if cs.get("missing"):
        line += f"; listed but not found: {', '.join(cs['missing'])}"
    if cs.get("note"):
        line += f" — {cs['note']}"
    return line + "."


def build_diary(window, nfacts, nmsgs, promotions, decays, themes, md_dec, ephemeral_events=None,
                quarantined=None, suppressed=0, conflicts=None, alerts=None, note=None, chat_stores=None):
    ephemeral_events = ephemeral_events or []
    quarantined = quarantined or []
    conflicts = conflicts or []
    lines = [f"{DIARY_HEADING} {_now().astimezone(LOCAL_TZ).strftime('%Y-%m-%d')}",
             f"Theme window: {window} d · facts: {nfacts} · messages (corroboration): {nmsgs}.", ""]
    for a in alerts or []:
        lines.append(f"⚠️ {a.get('message')}")
    if note:
        lines.append(f"ℹ️ {note}.")
    if chat_stores and chat_stores.get("stores") is not None:
        lines.append(chat_stores_line(chat_stores))

    def _line(p):
        if "meta" in p:
            return (f"- ({p['score']}, mentions {p['meta']['mentions']}/{p['meta']['ref_days']}d) "
                    f"{_diary_text(p['content'], 85)}{_diary_tag(p)}")
        return f"- {_diary_text(p['content'], 85)}{_diary_tag(p)}"

    if promotions:
        lines.append("**Strong signals (candidates to promote):**")
        lines.extend(_line(p) for p in promotions[:5])
    else:
        lines.append("No strong promotion candidates yet.")
    if ephemeral_events:
        lines.append("\n**Temporary events (do not promote):**")
        lines.extend(_line(p) for p in ephemeral_events[:5])
    if conflicts:
        lines.append(f"\n**Possible updates of existing entries ({len(conflicts)}):** "
                     + "; ".join(_diary_text(c["content"], 45) + _diary_tag(c) for c in conflicts[:3]))
    if themes:
        lines.append("\n**Emerging themes:** " + ", ".join(f"{t['theme']}×{t['mentions']}" for t in themes[:8]))
    if md_dec:
        lines.append(f"\n**MEMORY.md entries not surfaced for long ({len(md_dec)}):** "
                     + "; ".join(d["entry"][:45] for d in md_dec[:3]))
    if decays:
        lines.append(f"\n**Facts never used ({len(decays)}):** "
                     + "; ".join(_diary_text(d["content"], 45) + _diary_tag(d) for d in decays[:3]))
    if quarantined:
        reasons = ", ".join(sorted({q["unsafe"] for q in quarantined}))
        lines.append(f"\n**Quarantined (not promoted): {len(quarantined)}** — {reasons}.")
    if suppressed:
        lines.append(f"\n**Rejected earlier — not asking again: {suppressed}.**")
    lines.append("")
    return "\n".join(lines)


def _owner_of(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_uid, st.st_gid)


def _preserve_owner(path, owner):
    """Keep a state file owned by whoever owned it (or owns its directory).

    A Docker install runs the gateway under its own uid, while a hand run comes
    through `docker exec` as root: the file is then root-owned 0600 and the
    gateway's own pass reads it as missing — silently (the same trap the core
    guards against in cron/jobs.py:_preserve_file_ownership). POSIX only, and
    only when we really are root."""
    if os.name != "posix" or owner is None:
        return
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0 or owner == (0, 0):
        return
    try:
        os.chown(path, owner[0], owner[1])
    except OSError as exc:
        print(f"[dream] warn: owner of {path} not restored: {exc}", file=sys.stderr)


def _write_private(path, text, append=False):
    """Dream files contain private facts — create with mode 0600.

    `os.open(..., 0o600)` applies the mode only on CREATION, so a diary created
    earlier kept 0644 (live case). chmod is enforced explicitly."""
    owner = _owner_of(path) or _owner_of(os.path.dirname(path) or ".")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a" if append else "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _preserve_owner(path, owner)


def rotate_diary(path, keep=None):
    """Keep the last `keep` sections in the diary, move the rest to *.archive.md.
    Nothing is deleted; the diary is not loaded into the prompt, so this is
    hygiene, not token economy."""
    keep = DIARY_KEEP_SECTIONS if keep is None else keep
    if keep <= 0:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    starts = [m.start() for m in DIARY_SECTION_RE.finditer(text)]
    if len(starts) <= keep:
        return False
    cut = starts[len(starts) - keep]
    _write_private(path + ".archive.md", text[:cut], append=True)
    _write_private(path, text[cut:])
    print(f"[dream] diary: {len(starts) - keep} old section(s) moved to the archive",
          file=sys.stderr)
    return True


def _ensure_private(path):
    """Enforce 0600 on an already existing file (see _write_private)."""
    try:
        if os.path.exists(path) and (os.stat(path).st_mode & 0o777) != 0o600:
            os.chmod(path, 0o600)
    except OSError:
        pass


def append_diary(path, diary):
    """Idempotent: the section «<heading> YYYY-MM-DD» is appended once per day."""
    header = (diary.splitlines() or [""])[0].strip()
    _ensure_private(path)
    try:
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = ""
    if header and header in existing:
        print(f"[dream] diary: section «{header}» already present — skipped", file=sys.stderr)
        return False
    _write_private(path, diary + "\n", append=True)
    rotate_diary(path)
    return True


# ---------------------------------------------------------------------------
# Forgotten facts (holographic >= 0.8.0)
# ---------------------------------------------------------------------------
# `fact_store forget` deletes a fact from the store for good and leaves a
# tombstone in its `forgotten` table: the id, never the text. The copies this
# skill made are this skill's to clean, by that id: the last payload
# (cache/dream.json), the reject list, the "already shown" state, the diary and
# its archive, and the logs of the dream cron jobs (they hold the payload).
# Diary lines written since 2.4.0 end with "[#<fact_id>]"; older ones carry no
# id and are found through the diary index — hashes of the previews the diary
# writes, kept for every fact while it still exists (the text is gone after the
# forget, so the index is built ahead of time, on every pass and every sweep).

DIARY_TAG_RE = re.compile(r"\s*\[#((?:[A-Za-z0-9_.-]+#)?\d+)\]")
DREAM_JOB_SCRIPTS = ("dream-precheck.py", "dream-extract-precheck.py")


def _diary_tag(item):
    fact_id = item.get("fact_id")
    return f" [#{fact_id}]" if isinstance(fact_id, int) or FACT_ID_RE.fullmatch(str(fact_id or "")) else ""


def _diary_text(text, n):
    """What the diary writes for a fact since 2.4.0: one line, and "; " only ever between list items (a fact's own
    "; " would make the item boundary ambiguous for the sweep)."""
    return " ".join((text or "")[:n].split()).replace("; ", ", ")


def _preview_hash(text):
    return hashlib.sha256(_fold(text or "").strip().encode("utf-8")).hexdigest()[:20]


def _preview_hashes(content):
    """Hashes of every form the diary has written a fact in: an 85-character line, a 45-character list item, as
    is (before 2.4.0, where a newline in the text broke the line: also its first line) and as `_diary_text`."""
    forms = set()
    for n in (85, 45):
        raw = (content or "")[:n]
        forms |= {raw, raw.split("\n", 1)[0], _diary_text(content, n)}
    return sorted({_preview_hash(f) for f in forms if f.strip()})


def _json_file(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def load_forgotten(db=None, errors=None):
    """{fact_id: forgotten_at} from the store's tombstones; {} when the source is not holographic, the store
    is missing, or the provider is older than 0.8.0 (no `forgotten` table)."""
    if fact_source() != "holographic" and not db:
        return {}
    path = db or fact_store_path()
    if not path:  # the store's settings cannot be read for sure: not "nothing was forgotten" either
        msg = "plugins.hermes-memory-store of config.yaml cannot be read for sure — tombstones not read"
        if errors is not None:
            errors.append(msg)
        else:
            print(f"[dream] warn: {msg}", file=sys.stderr)
    try:
        out = _tombstones(path)
    except sqlite3.Error as exc:
        # Unreadable is not "nothing was forgotten": the gate used to exit clean and silent while the copies
        # stayed (Codex review of 2.7.2). Reported like a chat store's error; the next run tries again.
        out = {}
        if errors is not None:
            errors.append(f"{path}: {exc}")
        else:
            print(f"[dream] warn: tombstones of the fact store unreadable: {exc}", file=sys.stderr)
    if not db:  # and those of the chat stores promoted now or before, as "<key>#<id>"
        stores = list_chat_stores()
        for key in sorted(sweep_stores() & set(stores)):
            try:
                out.update({f"{key}#{i}": at for i, at in _tombstones(stores[key]).items()})
            except sqlite3.Error as exc:  # one chat's database must not stop the others' sweep
                if errors is not None:
                    errors.append(f"{stores[key]}: {exc}")
                else:
                    print(f"[dream] warn: tombstones of chat store {key} unreadable: {exc}", file=sys.stderr)
    return out


def sweep_stores():
    """Chat stores whose tombstones are read: the ones promoted now and every one promoted before — a fact copied
    while its chat was listed must still be swept after the chat leaves the list (review 2026-10-13)."""
    keys = set(chat_stores_settings()[1])
    path = _state_default("DREAM_FORGET_STATE", "forget_sweep")
    if path:
        keys |= set(_json_file(path, {}).get("stores") or [])
    return keys


def _tombstones(path):
    """{fact_id: forgotten_at} of one holographic database ({} without one or without the table). An unreadable
    database raises sqlite3.Error — the caller decides; a chat store's error is not "nothing was forgotten"."""
    if not path or not os.path.exists(path):
        return {}
    c = _conn(path)
    try:
        if not c.execute("select 1 from sqlite_master where type='table' and name='forgotten'").fetchone():
            return {}
        return {int(r[0]): str(r[1]) for r in c.execute("select fact_id, forgotten_at from forgotten")}
    finally:
        c.close()


def fact_history_supported(db=None):
    """True when the store keeps fact history (holographic >= 0.8.0: change / end / correct / forget)."""
    path = db or (fact_store_path() if fact_source() == "holographic" else None)
    if not path or not os.path.exists(path):
        return False
    c = _conn(path)
    try:
        if not c.execute("select 1 from sqlite_master where type='table' and name='meta'").fetchone():
            return False
        # The marker is written in the same transaction that versions every fact; the tables exist a moment earlier.
        return bool(c.execute("select 1 from meta where key = 'history_schema'").fetchone())
    except sqlite3.Error:
        return False
    finally:
        c.close()


def update_diary_index(path, facts):
    """Add the preview hashes of every current fact (and the fingerprint the reject list and the cooldowns
    key on). Hashes accumulate per id: a fact that changed keeps the hashes of its older texts, whose lines
    are still in the diary. Returns True when the file changed."""
    if not path:
        return False
    index = _json_file(path, {})
    changed = False
    for f in facts:
        fid = str(f.get("fact_id"))
        if not FACT_ID_RE.fullmatch(fid) or not f.get("content"):
            continue
        entry = index.setdefault(fid, {"previews": [], "fingerprints": []})
        previews = sorted(set(entry.get("previews") or []) | set(_preview_hashes(f["content"])))
        fingerprints = sorted(set(entry.get("fingerprints") or []) | {_fact_fingerprint(f["content"])})
        if previews != entry.get("previews") or fingerprints != entry.get("fingerprints"):
            entry["previews"], entry["fingerprints"] = previews, fingerprints
            changed = True
    if changed:
        _write_private(path, json.dumps(index, ensure_ascii=False))
    return changed


def _strip_tags(text):
    return DIARY_TAG_RE.sub("", text).strip()


_DIARY_ITEM_RE = re.compile(r"^- (?:\([^)]*mentions [^)]*\) )?(.*)$")
# A line the diary writes itself; anything else right after a removed item is the rest of a fact text that
# contained a newline (before 2.4.0) and goes with it.
_DIARY_OWN_LINE_RE = re.compile(r"^(?:- |\*\*|#|⚠️|Theme window|No strong|\s*$)")


def scrub_diary_text(text, fact_ids, index):
    """The diary without the lines (or list items) of *fact_ids*: by the "[#id]" tag, or by the preview
    hashes of the index for lines written before the tags. Returns (text, removed)."""
    ids = {str(i) for i in fact_ids}
    hashes = {h for i in ids for h in (index.get(i) or {}).get("previews", [])}

    def gone(item):
        tags = set(DIARY_TAG_RE.findall(item))
        return bool(tags & ids) or (not tags and _preview_hash(_strip_tags(item)) in hashes)

    out, removed, continuation = [], 0, False
    for line in text.split("\n"):
        if continuation and not _DIARY_OWN_LINE_RE.match(line):
            continue
        continuation = False
        item = _DIARY_ITEM_RE.match(line)
        if item and gone(item.group(1)):
            removed += 1
            continuation = not DIARY_TAG_RE.search(line)  # tagged lines (2.4.0+) are always one line
            continue
        if line.startswith("**") and ":** " in line:
            head, rest = line.split(":** ", 1)
            parts = rest.split("; ")
            drop = {i for i, p in enumerate(parts) if gone(p)}
            for i in range(len(parts)):  # untagged legacy items whose own text contained "; "
                for j in range(i + 2, len(parts) + 1):
                    joined = "; ".join(parts[i:j])
                    if not DIARY_TAG_RE.search(joined) and _preview_hash(joined) in hashes:
                        drop.update(range(i, j))
            if drop:
                removed += len(drop)
                kept = [p for i, p in enumerate(parts) if i not in drop]
                if not kept:
                    continue
                line = f"{head}:** " + "; ".join(kept)
        out.append(line)
    return "\n".join(out), removed


def _drop_fact_items(node, ids):
    """Remove every dict carrying one of *ids* as `fact_id` from the lists of a JSON payload."""
    removed = 0
    if isinstance(node, dict):
        for value in node.values():
            removed += _drop_fact_items(value, ids)
    elif isinstance(node, list):
        keep = []
        for item in node:
            if isinstance(item, dict) and str(item.get("fact_id")) in ids:
                removed += 1
                continue
            if isinstance(item, dict) and isinstance(item.get("duplicates"), list):
                twins = [d for d in item["duplicates"] if str(d) not in ids]
                removed += len(item["duplicates"]) - len(twins)
                item["duplicates"] = twins
            removed += _drop_fact_items(item, ids)
            keep.append(item)
        node[:] = keep
    return removed


def _scrub_strings(node, ids, index):
    """The diary text a payload carries (`diary`): the same line rules as the diary file. Other strings are not
    diary text (a kept fact's own words may look like a diary line) and are left alone; facts leave a payload
    with their item (`_drop_fact_items`)."""
    if not isinstance(node, dict) or not isinstance(node.get("diary"), str):
        return 0
    cleaned, removed = scrub_diary_text(node["diary"], ids, index)
    if removed:
        node["diary"] = cleaned
    return removed


FORGOTTEN_COPY = "[removed: a forgotten fact]"
_COPY_PIECE_RE = re.compile(r"\n|(?<=[.!?;])\s+")


def _carries_forgotten(text, hashes):
    """True when *text* holds a forgotten fact verbatim — as a whole, as a line or as a sentence. Only the
    preview hashes are left of its text, so a reworded copy is not found; a memory entry written from the fact
    and an `existing_facts` line of the extraction pass are verbatim."""
    if not hashes or not isinstance(text, str) or not text.strip():
        return False
    for piece in {text, *_COPY_PIECE_RE.split(text)}:
        piece = piece.strip().lstrip("§").strip()
        if piece and hashes & {_preview_hash(piece[:85]), _preview_hash(_diary_text(piece, 85))}:
            return True
    return False


def _is_forgotten_item(item, hashes, fingerprints):
    """A payload item (it has `content`) that IS a forgotten fact. Its full fingerprint decides: two facts may
    share their first 85 characters — all the preview hashes keep — and differ in the end ("…to Telegram" /
    "…by email"; Codex review of 2.7.5). A text no longer than a preview is matched by its hashes, and so is
    every item while no fingerprint was recorded (tombstones swept before 2.4)."""
    content = item.get("content")
    if not isinstance(content, str):
        return False
    if (item.get("fingerprint") or _fact_fingerprint(content)) in fingerprints:
        return True
    return (len(content.strip()) <= 85 or not fingerprints) and _carries_forgotten(content, hashes)


def _scrub_copies(node, hashes, fingerprints=frozenset(), _item=False):
    """Copies of forgotten facts that ride WITHOUT their fact_id — a memory entry shown as another candidate's
    `nearest_entry`, a "still relevant?" entry, a conflict's entry, a bare string in a list. `_drop_fact_items`
    only knew items by id, and these stayed in the payload (Codex review of 2.7.2). A `nearest_entry` goes as a
    whole: its anchor without the text is a blind replace. A kept item's own `content` is its fact, already
    judged by `_is_forgotten_item`."""
    removed = 0
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "diary" or (_item and key == "content"):
                continue  # line rules of its own (_scrub_strings); a kept fact's own text
            if isinstance(value, str):
                if value != FORGOTTEN_COPY and _carries_forgotten(value, hashes):
                    node[key] = FORGOTTEN_COPY
                    removed += 1
            elif key == "nearest_entry" and isinstance(value, dict) and any(
                    _carries_forgotten(v, hashes) for v in value.values()):
                node[key] = {"entry": FORGOTTEN_COPY, "replace_unsafe": True}
                removed += 1
            else:
                removed += _scrub_copies(value, hashes, fingerprints)
    elif isinstance(node, list):
        keep = []
        for value in node:
            if isinstance(value, str):
                if value != FORGOTTEN_COPY and _carries_forgotten(value, hashes):
                    value = FORGOTTEN_COPY
                    removed += 1
            elif isinstance(value, dict) and _is_forgotten_item(value, hashes, fingerprints):
                # An item whose own text is the forgotten fact goes with it: a placeholder left in
                # `promotions` kept the gate open and offered "[removed: …]" for memory (review of 2.7.4).
                removed += 1
                continue
            else:
                removed += _scrub_copies(value, hashes, fingerprints,
                                         isinstance(value, dict) and isinstance(value.get("content"), str))
            keep.append(value)
        node[:] = keep
    return removed


def _preview_set(index):
    return {h for rec in index.values() for h in (rec or {}).get("previews", [])}


def _fingerprint_set(index):
    return {fp for rec in index.values() for fp in (rec or {}).get("fingerprints", [])}


def scrub_result(result):
    """A pass's result without facts forgotten while it ran (it loaded them before the forget): before it is
    published, not after."""
    ids = {str(i) for i in load_forgotten()}
    if not ids:
        return 0
    index = forgotten_diary_index(ids)
    return (_drop_fact_items(result, ids) + _scrub_strings(result, ids, index)
            + _scrub_copies(result, _preview_set(index), _fingerprint_set(index)))


def _dream_job_dirs(home):
    """Output folders of the cron jobs that run the dream gates (their logs hold the payload)."""
    data = _json_file(os.path.join(home, "cron", "jobs.json"), {})
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out = []
    for job in jobs or []:
        script = os.path.basename(str((job or {}).get("script") or ""))
        if script in DREAM_JOB_SCRIPTS and job.get("id"):
            out.append(os.path.join(home, "cron", "output", str(job["id"])))
    return out


_JSON_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){20,})"')


def _json_string(literal):
    try:
        return json.loads(f'"{literal}"')
    except ValueError:
        return literal


@contextlib.contextmanager
def _sweep_lock(home, wait=True):
    """One writer of the dream's state at a time: the nightly pass and the forget gate both rewrite it. POSIX
    advisory lock on cache/dream-state.lock; a no-op where fcntl does not exist. Yields whether the lock is held:
    with `wait=False` a held lock is not waited for (the gate runs under a timeout, and the pass that holds the
    lock sweeps before it starts and scrubs its output after — nothing is left for the gate)."""
    try:
        import fcntl
    except ImportError:
        yield True
        return
    path = os.path.join(home, "cache", "dream-state.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except (BlockingIOError, PermissionError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def forget_sweep(db=None, home=None):
    """Clean this skill's copies of facts the store has forgotten (see the section comment). Every run looks for
    every tombstone again, so a copy written back by a pass that loaded the fact before it was forgotten, or a
    file that could not be removed last time, is caught on the next run; with nothing left the run changes
    nothing. Also refreshes the diary index from the current facts, so a fact forgotten later can still be found
    in old lines. `errors` lists what could not be cleaned (the gate then fails and is retried)."""
    home = home or HOME
    index_path = _state_default("DREAM_DIARY_INDEX", "diary_index")
    state_path = _state_default("DREAM_FORGET_STATE", "forget_sweep")
    state = _json_file(state_path, {}) if state_path else {}
    # Chat stores promoted now join the remembered ones before anything else, and the record is saved at once:
    # their facts may already be in the payload, and the run may end early below.
    promoted = sorted(set(state.get("stores") or []) | chat_stores_settings()[1])
    if state_path and promoted != sorted(state.get("stores") or []):
        state["stores"] = promoted
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        _write_private(state_path, json.dumps(state, ensure_ascii=False))
    state_before = json.dumps(state, sort_keys=True)
    store_errors = []
    tombstones = load_forgotten(db, errors=store_errors)
    facts = []
    store = db or (fact_store_path() if fact_source() == "holographic" else None)
    if store and os.path.exists(store):
        try:
            facts += _load_holographic(store)
        except (sqlite3.Error, OSError) as exc:
            print(f"[dream] warn: diary index not updated for the shared store: {exc}", file=sys.stderr)
    if not db:
        facts += load_chat_facts(home)  # indexed even without a shared store (scope chat may have none)
    if facts:
        try:
            update_diary_index(index_path, facts)
        except OSError as exc:
            print(f"[dream] warn: diary index not updated: {exc}", file=sys.stderr)
    done = state.setdefault("swept", {})
    new = sorted(str(i) for i in tombstones if str(i) not in done)
    summary = {"tombstones": len(tombstones), "swept": len(new), "payload": 0, "rejected": 0, "seen": 0,
               "diary_lines": 0, "cron_logs": 0, "errors": store_errors}
    if not tombstones:
        return summary
    ids = {str(i) for i in tombstones}
    live = _json_file(index_path, {}) if index_path else {}
    index = {i: {"previews": sorted(set((done.get(i) or {}).get("previews") or [])
                                    | set((live.get(i) or {}).get("previews") or []))} for i in ids}
    fingerprints = {fp for i in ids for fp in (live.get(i) or {}).get("fingerprints", [])} \
        | {fp for i in ids for fp in (done.get(i) or {}).get("fingerprints", [])}

    payload_path = os.path.join(home, "cache", "dream.json")
    payload = _json_file(payload_path, {})
    if payload:
        summary["payload"] = (_drop_fact_items(payload, ids) + _scrub_strings(payload, ids, index)
                              + _scrub_copies(payload, _preview_set(index), fingerprints))
        if summary["payload"]:
            _write_private(payload_path, json.dumps(payload, ensure_ascii=False, indent=2))

    rejected_path = _state_default("DREAM_REJECTED_STATE", "rejected")
    rejected = _json_file(rejected_path, {}) if rejected_path else {}
    gone = [k for k, rec in rejected.items()
            if k in fingerprints or (isinstance(rec, dict) and str(rec.get("fact_id")) in ids)]
    for key in gone:
        rejected.pop(key)
    if gone:
        summary["rejected"] = len(gone)
        _write_private(rejected_path, json.dumps(rejected, ensure_ascii=False, indent=1) + "\n")

    seen_path = _state_default("DREAM_SEEN_STATE", "seen")
    seen = _json_file(seen_path, {}) if seen_path else {}
    for name, bucket in seen.items():
        if name == "_pending" and isinstance(bucket, dict):
            for key, values in (bucket.get("seen") or {}).items():
                if isinstance(values, list):
                    kept = [v for v in values if v not in fingerprints]
                    summary["seen"] += len(values) - len(kept)
                    bucket["seen"][key] = kept
        elif isinstance(bucket, dict):
            for key in [k for k, v in bucket.items()
                        if k in fingerprints or (isinstance(v, dict) and str(v.get("fact_id")) in ids)]:
                bucket.pop(key)
                summary["seen"] += 1
    if summary["seen"]:
        _write_private(seen_path, json.dumps(seen, ensure_ascii=False, indent=1))

    diary = os.path.join(home, DIARY_PATH)
    for path in (diary, diary + ".archive.md"):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            continue
        except OSError as exc:
            summary["errors"].append(f"{path}: {exc}")
            continue
        cleaned, removed = scrub_diary_text(text, ids, index)
        if removed:
            summary["diary_lines"] += removed
            _write_private(path, cleaned)

    pattern = re.compile(r'"fact_id"\s*:\s*"?((?:[A-Za-z0-9_.-]+#)?\d+)\b')
    for folder in _dream_job_dirs(home):
        try:
            names = os.listdir(folder)
        except FileNotFoundError:
            continue
        except OSError as exc:
            summary["errors"].append(f"{folder}: {exc}")
            continue
        hashes = _preview_set(index)
        for name in names:
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                # By id, and by a verbatim copy in any JSON string of the log: logs of the extraction pass before
                # 2.7.3 carried `existing_facts` as bare strings, and a memory entry rides without an id.
                if set(pattern.findall(text)) & ids or any(
                        _carries_forgotten(_json_string(lit), hashes) for lit in _JSON_STRING_RE.findall(text)):
                    os.remove(path)
                    summary["cron_logs"] += 1
            except OSError as exc:
                summary["errors"].append(f"{path}: {exc}")

    # The hashes move from the index to the sweep state: they keep finding copies written back later, and the
    # owner of another copy of the diary (a git backup) cleans it by them (forgotten_diary_index); not text.
    stamp = _now().isoformat(timespec="seconds")
    for i in ids:
        record = done.setdefault(i, {"at": stamp})
        record["previews"] = index[i]["previews"]
        record["fingerprints"] = sorted(set(record.get("fingerprints") or [])
                                        | set((live.get(i) or {}).get("fingerprints") or []))
    if index_path and any(i in live for i in ids):
        for i in ids:
            live.pop(i, None)
        _write_private(index_path, json.dumps(live, ensure_ascii=False))
    if state_path and json.dumps(state, sort_keys=True) != state_before:
        _write_private(state_path, json.dumps(state, ensure_ascii=False))
    return summary


def forgotten_diary_index(ids):
    """{fact_id: {"previews": [...], "fingerprints": [...]}} for *ids*: from the sweep state once swept, else from
    the diary index — what scrub_diary_text needs to clean another copy of the diary (a backup) of those facts."""
    index_path = _state_default("DREAM_DIARY_INDEX", "diary_index")
    state_path = _state_default("DREAM_FORGET_STATE", "forget_sweep")
    index = _json_file(index_path, {}) if index_path else {}
    swept = (_json_file(state_path, {}) if state_path else {}).get("swept") or {}
    out = {}
    for i in (str(x) for x in ids):
        done = (swept.get(i) if isinstance(swept.get(i), dict) else None) or {}
        previews = set(done.get("previews") or []) | set((index.get(i) or {}).get("previews") or [])
        fingerprints = set(done.get("fingerprints") or []) | set((index.get(i) or {}).get("fingerprints") or [])
        out[i] = {"previews": sorted(previews), "fingerprints": sorted(fingerprints)}
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def explain(fact_id, mem_md, corr_days):
    """`--explain ID`: per-signal breakdown of one fact (OpenClaw promote-explain)."""
    facts = [f for f in load_all_facts() if str(f["fact_id"]) == str(fact_id)]
    if not facts:
        print(f"fact {fact_id} not found", file=sys.stderr)
        return 1
    f = facts[0]
    messages = messages_for(f, load_messages(os.path.join(HOME, "state.db"), corr_days))
    memory_text = load_durable_memory_text(mem_md)
    score, sig, meta = signals_for_fact(f, messages)
    print(f"fact {fact_id}: {f['content'][:200]}")
    print(f"score {score} (gate {MIN_SCORE}); weights {WEIGHTS}")
    for k, v in sig.items():
        print(f"  {k:20s} {v:5.3f} * {WEIGHTS[k]:.2f} = {v * WEIGHTS[k]:.3f}")
    print(f"  mentions {meta['mentions']} in {meta['ref_days']} day(s); retrievals {meta['rc']}; "
          f"days_old {meta['days_old']}")
    for e in meta.get("evidence") or []:
        print(f"    {e['day']}: {e['text']}")
    scored = {"meta": meta, "helpful_count": f.get("helpful_count")}
    print(f"promotion_ready {promotion_ready(scored)}; in_memory {already_in_memory(f['content'], memory_text)}; "
          f"unsafe {classify_unsafe(f['content'])}; ephemeral {is_ephemeral_fact(f['content'])}; "
          f"expired {is_expired_event(f['content'])}")
    near = nearest_entry(f["content"], sources=load_durable_memory_sources(mem_md))
    if near:
        print(f"nearest entry in {near.get('target', '?')} ({near['overlap']}): {near['entry']}")
        if near.get("old_text"):
            print(f"  replace anchor: {near['old_text']!r}")
    for c in find_conflicts(f["content"], memory_text):
        print(f"possible conflict: {c}")
    return 0


def dry_run_summary(result, source):
    """Plain-text report of a `--dry-run` pass: what the dream would put in
    front of the agent tonight. Facts carry their ids so a human can follow up
    with `--explain`; contents are trimmed, secrets never shown."""
    st = result["stats"]
    lines = ["Dry run — nothing was written: state files were copied to a temporary",
             "directory and discarded; no diary, no memory change.",
             f"fact source: {source} · facts: {st['facts']} · human messages in window: "
             f"{st['messages_window']}"]
    usage = ", ".join(f"{name} {rec['percent']}% ({rec['chars']}/{rec['limit']})"
                      if rec.get("percent") is not None else f"{name} {rec['chars']} chars"
                      for name, rec in (result.get("memory_usage") or {}).items())
    if usage:
        lines.append(f"memory: {usage}")
    if result.get("fact_store_note"):
        lines.append(f"note: {result['fact_store_note']}")
    if result.get("chat_stores"):
        lines.append(chat_stores_line(result["chat_stores"]))

    def facts(title, items, why=False):
        lines.append(f"{title}: {len(items)}")
        for item in items[:8]:
            text = re.sub(r"\s+", " ", item.get("content") or "")[:110]
            tail = f"  ({item.get('why')})" if why and item.get("why") else ""
            lines.append(f"  - [{item.get('fact_id')}] {text}{tail}")
        if len(items) > 8:
            lines.append(f"  … and {len(items) - 8} more")

    facts("promotions (candidates for durable memory)", result.get("promotions") or [], why=True)
    facts("new facts to review", result.get("new_facts") or [])
    facts("conflicts (same subject, different numbers or negation)", result.get("conflicts") or [])
    facts("facts that look unused", result.get("fact_decays") or [])
    lines.append(f"facts people said again on different days (to confirm): {len(result.get('confirm') or [])}")
    md = result.get("md_decays") or []
    lines.append('memory entries to ask "still relevant?": ' + str(len(md)))
    for d in md[:8]:
        lines.append("  - " + re.sub(r"\s+", " ", d.get("entry") or "")[:110])
    lines.append(f"quarantined (secrets / injection, content hidden): {st.get('quarantined', 0)}")
    for a in result.get("alerts") or []:
        lines.append(f"ALERT: {a.get('message') or a}")
    work = sum(st.get(k, 0) for k in ("promotions", "new_facts_reviewed", "conflicts",
                                       "fact_decays", "md_decays", "quarantined", "alerts"))
    lines.append("verdict: the nightly job would wake the agent" if work
                 else "verdict: nothing to do — the nightly job would stay asleep "
                      "(unless a memory file is near its limit)")
    return "\n".join(lines)


def _state_default(env_name, cfg_key):
    p = os.environ.get(env_name)
    if p is not None:
        return p
    p = (CONFIG.get("state") or {}).get(cfg_key)
    if not p:
        return ""
    return p if os.path.isabs(os.path.expanduser(p)) else os.path.join(HOME, p)


def main(argv=None):
    global HOME
    ap = argparse.ArgumentParser(description="Read-only dreaming pass over agent memory")
    ap.add_argument("--hermes-home", default=None,
                    help="Hermes home (default: $HERMES_HOME or ~/.hermes); the cron gates pass "
                         "the home they were installed into")
    ap.add_argument("--config", default=None, help="JSON config (default: $DREAM_CONFIG or "
                                                   "$HERMES_HOME/dreaming.json)")
    ap.add_argument("--window", type=int, default=None, help="window for emerging themes / new facts")
    ap.add_argument("--corr-window", type=int, default=None,
                    help="window for corroboration from conversations")
    ap.add_argument("--md-decay-days", type=int, default=None,
                    help="'not surfaced for long' threshold for MEMORY.md entries")
    ap.add_argument("--memory-md", default=None)
    ap.add_argument("--out", default="-")
    ap.add_argument("--diary", default="")
    ap.add_argument("--asked-state", default=None,
                    help="cooldown state of md_decay questions; empty string — off")
    ap.add_argument("--rejected-state", default=None,
                    help="reject list (dream-reject.py); empty string — off")
    ap.add_argument("--seen-state", default=None,
                    help="cooldown of new_facts/fact_decays/conflicts; empty string — off")
    ap.add_argument("--snapshot-state", default=None,
                    help="snapshot for the memory-loss guard; empty string — off")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the pass on throw-away copies of the state files and print a "
                         "short plain-text report; writes nothing (no state, no diary)")
    ap.add_argument("--explain", default=None, metavar="FACT_ID",
                    help="print the score breakdown of one fact and exit")
    ap.add_argument("--forget-sweep", action="store_true",
                    help="only clean the copies of facts the store has forgotten (holographic >= 0.8.0) "
                         "and print what was removed; every pass does this first")
    args = ap.parse_args(argv)

    if args.hermes_home:
        HOME = args.hermes_home
    configure(load_config(args.config))
    if args.explain is not None:
        return explain(args.explain, args.memory_md or os.path.join(HOME, "memories", "MEMORY.md"),
                       args.corr_window or int((CONFIG.get("windows") or {}).get("corroboration_days", 60)))
    windows = CONFIG.get("windows") or {}
    window = args.window if args.window is not None else _env("DREAM_WINDOW_DAYS", int(windows.get("themes_days", 14)), int)
    corr = args.corr_window if args.corr_window is not None else _env("DREAM_CORR_DAYS", int(windows.get("corroboration_days", 60)), int)
    md_days = args.md_decay_days if args.md_decay_days is not None else _env("DREAM_MD_DECAY_DAYS", int(windows.get("md_decay_days", 60)), int)
    mem_md = args.memory_md or os.path.join(HOME, "memories", "MEMORY.md")
    asked = args.asked_state if args.asked_state is not None else _state_default("DREAM_ASKED_STATE", "asked")
    rejected = args.rejected_state if args.rejected_state is not None else _state_default("DREAM_REJECTED_STATE", "rejected")
    seen = args.seen_state if args.seen_state is not None else _state_default("DREAM_SEEN_STATE", "seen")
    snapshot = args.snapshot_state if args.snapshot_state is not None else _state_default("DREAM_SNAPSHOT_STATE", "snapshot")

    if args.dry_run:
        # The pass itself writes cooldowns and the loss-guard snapshot. A dry run
        # gets copies of them — real input, nothing written back.
        sandbox = tempfile.mkdtemp(prefix="dream-dry-")
        try:
            paths = []
            for i, real in enumerate((asked, rejected, seen, snapshot)):
                if not real:
                    paths.append(None)
                    continue
                copy = os.path.join(sandbox, f"state{i}.json")
                if os.path.exists(real):
                    shutil.copyfile(real, copy)
                paths.append(copy)
            result = run(window, corr, md_days, mem_md, asked_state_path=paths[0],
                         rejected_state_path=paths[1], seen_state_path=paths[2],
                         snapshot_state_path=paths[3])
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
        print(dry_run_summary(result, fact_source()))
        return 0

    # Forgotten facts first: the pass must not re-read a cooldown or a reject record of a fact that is gone,
    # and the diary index must hold every fact before today's lines are written.
    def sweep(when):
        try:
            swept = forget_sweep()
        except Exception as e:  # noqa: BLE001 — a sweep hiccup must not cost the night's pass
            swept = {"errors": [f"{type(e).__name__}: {e}"]}
        if not args.forget_sweep and (swept.get("errors") or any(
                swept.get(k) for k in ("payload", "rejected", "seen", "diary_lines", "cron_logs"))):
            print(f"[dream] forget sweep ({when}): {json.dumps(swept, ensure_ascii=False)}", file=sys.stderr)
        return swept

    with _sweep_lock(HOME, wait=not args.forget_sweep) as held:
        if not held:  # the pass holds the state and sweeps itself; the gate's next tick looks again
            print(json.dumps({"skipped": "the nightly pass holds the state"}, ensure_ascii=False))
            return 0
        swept = sweep("before the pass")
        if args.forget_sweep:
            print(json.dumps(swept, ensure_ascii=False))
            return 1 if swept.get("errors") else 0

        result = run(window, corr, md_days, mem_md,
                     asked_state_path=asked or None,
                     rejected_state_path=rejected or None,
                     seen_state_path=seen or None,
                     snapshot_state_path=snapshot or None)

        scrub_result(result)  # a fact forgotten while the pass ran never reaches its output
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out in ("-", "", None):
            print(text)
        else:
            _write_private(args.out, text)
        if args.diary:
            try:
                append_diary(args.diary, result["diary"])
            except OSError as e:
                # The JSON is already out; a diary hiccup must not turn into dream_error.
                print(f"[dream] warn: diary not written: {e}", file=sys.stderr)
        # A fact forgotten in the instant between the check above and the writes: the next sweep (this one,
        # or the gate's) finds it.
        sweep("after the pass")

    st = result["stats"]
    print(f"[dream] facts:{st['facts']} messages:{st['messages_window']} promote:{st['promotions']} "
          f"ephemeral:{st.get('ephemeral_events', 0)} fact-decay:{st['fact_decays']} "
          f"md-decay:{st['md_decays']} conflicts:{st.get('conflicts', 0)} themes:{st['themes']} "
          f"quarantine:{st.get('quarantined', 0)} "
          f"rejected-earlier:{st.get('rejected_suppressed', 0)} "
          f"shown-earlier:{st.get('new_facts_suppressed', 0)}+{st.get('fact_decays_suppressed', 0)}"
          f"+{st.get('conflicts_suppressed', 0)}",
          file=sys.stderr)


# Populate the effective settings at import: config from $DREAM_CONFIG /
# $HERMES_HOME/dreaming.json (defaults when absent). `main()` re-applies with
# `--config` when given.
configure(load_config())

if __name__ == "__main__":
    raise SystemExit(main() or 0)
