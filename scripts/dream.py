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
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

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
    # Where the fact store lives. Empty = ask Hermes: `plugins.hermes-memory-store.db_path`
    # from config.yaml (the holographic provider honours it), else
    # $HERMES_HOME/memory_store.db. Relative paths resolve against HERMES_HOME.
    "fact_store_path": "",
    # Word stems that hint a fact is about the user / household (profile
    # candidates for USER.md). Categories are matched exactly.
    "profile_hint_terms": ["prefer", "likes", "dislikes", "goal", "plans", "works", "lives",
                           "family", "spouse", "partner", "child", "daughter", "son",
                           "предпочита", "любит", "не любит", "цель", "планирует",
                           "работает", "живёт", "живет", "семь", "родител", "дочь", "сын", "мама", "папа"],
    "profile_categories": ["user", "user_pref", "preference", "profile", "personal", "family"],
    # Declarative near-duplicate rules (see `_semantic_memory_alias`).
    "alias_rules": [],
    "diary": {"heading": "## Dream", "keep_sections": 90},
    "windows": {"themes_days": 14, "corroboration_days": 60, "md_decay_days": 60},
    "gates": {"min_score": 0.55, "min_mentions": 3, "new_facts_cap": 30,
              "seen_cooldown_days": 14, "md_ask_cooldown_days": 14,
              "publish_cap": 15},
    "weights": {"relevance": 0.30, "frequency": 0.24, "query_diversity": 0.15,
                "recency": 0.15, "consolidation": 0.10, "conceptual_richness": 0.06},
    # Optional char limits of the durable files (as in Hermes `memory.*_char_limit`)
    # — reported as fill level so the agent knows when to merge before adding.
    "memory_char_limits": {},
    # Markers of pinned §-entries in MEMORY.md: never flagged as stale.
    "pinned_markers": ["📌", "[pinned]"],
    # Loss guard: the pass snapshots the durable files; if on the next pass more
    # than this fraction of yesterday's entries is gone, an alert is raised
    # (a bad LLM turn or a wrong `memory replace` — never silent).
    "memory_loss_alert_fraction": 0.25,
    "state": {"asked": "cache/dream-asked.json",
              "rejected": "cache/dream-rejected.json",
              "seen": "cache/dream-seen.json",
              "snapshot": "cache/dream-snapshot.json"},
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
    return _deep_merge(DEFAULT_CONFIG, data)


def _hermes_plugin_db_path(home=None):
    """`plugins.hermes-memory-store.db_path` from Hermes config.yaml, or None.
    Read with PyYAML when available, else a narrow line scanner — the pass must
    not grow a dependency for one key."""
    cfg = os.path.join(home or HOME, "config.yaml")
    try:
        with open(cfg, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text) or {}
        value = ((data.get("plugins") or {}).get("hermes-memory-store") or {}).get("db_path")
        return str(value) if value else None
    except Exception:  # noqa: BLE001 — no yaml, or a file it cannot parse
        pass
    section = re.search(r"^plugins:\n((?:[ \t]+.*\n|\n)*)", text, re.MULTILINE)
    if not section:
        return None
    store = re.search(r"^[ \t]+hermes-memory-store:[ \t]*\n((?:[ \t]{4,}.*\n|\n)*)",
                      section.group(1), re.MULTILINE)
    if not store:
        return None
    hit = re.search(r"^[ \t]+db_path:[ \t]*([^#\n]*)", store.group(1), re.MULTILINE)
    value = hit.group(1).strip().strip("\"'") if hit else ""
    return value or None


def fact_store_path(home=None):
    """Resolved path of the fact store: `$DREAM_FACT_STORE` > dreaming.json
    `fact_store_path` > Hermes `plugins.hermes-memory-store.db_path` >
    `$HERMES_HOME/memory_store.db`. `$HERMES_HOME`, `~` and relative paths are
    expanded the way the provider does it — an install that moved its store
    must not look empty to the dream."""
    home = home or HOME
    raw = (os.environ.get("DREAM_FACT_STORE") or CONFIG.get("fact_store_path")
           or _hermes_plugin_db_path(home) or "memory_store.db")
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
    global MD_ASK_COOLDOWN_DAYS, SEEN_COOLDOWN_DAYS, TRUSTED_CHAT_IDS, TRUST_PRIVATE
    global TRUST_NO_CHAT, STOPWORDS, TOKEN_MIN_LATIN, TOKEN_MIN_OTHER, ALIAS_RULES
    global PROFILE_HINT_RE, PROFILE_CATEGORIES, DIARY_HEADING, DIARY_SECTION_RE
    global DIARY_KEEP_SECTIONS, DURABLE_MEMORY_PATHS, MEMORY_CHAR_LIMITS
    global PINNED_MARKERS, MEMORY_LOSS_ALERT_FRACTION
    CONFIG = cfg
    LOCAL_TZ = _tz(_env("DREAM_TIMEZONE", cfg.get("timezone")))
    WEIGHTS = dict(DEFAULT_CONFIG["weights"], **(cfg.get("weights") or {}))
    gates = cfg.get("gates") or {}
    MIN_SCORE = _env("DREAM_MIN_SCORE", float(gates.get("min_score", 0.55)), float)
    MIN_MENTIONS = _env("DREAM_MIN_MENTIONS", int(gates.get("min_mentions", 3)), int)
    NEW_FACTS_CAP = _env("DREAM_NEW_FACTS_CAP", int(gates.get("new_facts_cap", 30)), int)
    PUBLISH_CAP = int(gates.get("publish_cap", 15))
    MD_ASK_COOLDOWN_DAYS = _env("DREAM_MD_ASK_COOLDOWN_DAYS",
                                int(gates.get("md_ask_cooldown_days", 14)), int)
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
    STOPWORDS = set(RU_STOP) | set(EN_STOP)
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
    DURABLE_MEMORY_PATHS = list(cfg.get("durable_memory_paths") or [])
    MEMORY_CHAR_LIMITS = dict(cfg.get("memory_char_limits") or {})
    PINNED_MARKERS = [str(m) for m in (cfg.get("pinned_markers") or []) if m]
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
привет коротко напомни запиши сделай расскажи покажи какие какой какая пожалуйста спасибо""".split())

# Latin stopwords (the tokenizer only keeps len>=4, shorter ones drop anyway).
# Platform words mirror RU_STOP (chat/thread/topic/message): otherwise any
# message with "telegram"+"thread" corroborates any routing fact (48 false
# mentions on live data). chat_id is a separate token and is not affected.
EN_STOP = set("""that this with from your have will just like what when then than they them
some more very much been does were about into over only also which would could should there
here please today okay yes
telegram thread threads topic topics chat chats group groups message messages channel
https http www""".split())


def _conn(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


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


def is_harness_envelope(text):
    """True for harness service inserts that are not human speech."""
    return bool(HARNESS_ENVELOPE_RE.match(text or ""))


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


def _keep_token(tok):
    if tok in STOPWORDS:
        return False
    if tok.isascii():
        return len(tok) >= TOKEN_MIN_LATIN
    return len(tok) >= TOKEN_MIN_OTHER


def sig_tokens(text):
    """Signature tokens: Unicode words without stopwords (Latin ≥4, other ≥5 chars).

    Reply quotes and voice transcripts are unwrapped BEFORE bracket blocks are
    cut: a family reply to a message about an entry is a mention of the entry
    (otherwise a short "yes, still Honda" gives 0 tokens and the dream asks the
    same thing every night). Other [..] blocks (sender tags, [id=N], image
    descriptions) are still cut as metadata."""
    unwrapped = REPLY_QUOTE_RE.sub(lambda m: " " + m.group(1) + " ", text or "")
    unwrapped = VOICE_QUOTE_RE.sub(lambda m: " " + m.group(1) + " ", unwrapped)
    unwrapped = strip_urls(unwrapped)
    low = re.sub(r"\[[^\]]*\]", " ", unwrapped).lower()
    return {w for w in TOKEN_RE.findall(low) if _keep_token(w)}


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


def load_facts(db):
    if _missing_db(db, "treating the fact store as empty"):
        return []
    rows = []
    c = _conn(db)
    try:
        for r in c.execute("select fact_id,content,category,tags,trust_score,retrieval_count,"
                           "helpful_count,created_at,updated_at from facts"):
            rows.append(dict(zip(
                ["fact_id", "content", "category", "tags", "trust_score", "retrieval_count",
                 "helpful_count", "created_at", "updated_at"], r)))
    finally:
        c.close()  # the sqlite3 context manager commits but does NOT close
    return rows


def _trusted_message(source, chat_type, chat_id):
    """Only humans corroborate memory: no cron prompts, no group chats outside
    the trusted list (fail-closed), private/legacy sessions per config."""
    if (source or "") == "cron":
        return False
    ctype = (chat_type or "")
    cid = str(chat_id or "")
    if cid and cid in TRUSTED_CHAT_IDS:
        return True
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
        try:
            rows = list(c.execute(
                "select m.id, m.role, m.content, m.timestamp, m.observed, "
                "s.source, s.chat_type, s.chat_id "
                "from messages m left join sessions s on m.session_id = s.id "
                "where m.timestamp>=? and m.content is not null", (cutoff,)))
        except sqlite3.OperationalError:
            # No usable `sessions` table: the source of a message is unknown.
            # Fail closed, not open — without it a cron job's own prompt rows
            # (role=user) corroborated facts. What is still known is the session
            # id: Hermes names cron sessions `cron_…`, so those are dropped
            # outright; the rest are "sessions without chat" and go through the
            # same config switch as any other session lacking chat metadata.
            print("[dream] warn: sessions table unavailable — cron sessions dropped by id, "
                  "the rest trusted only if trust_sessions_without_chat", file=sys.stderr)
            rows = [(mid, role, content, ts, observed,
                     "cron" if str(sid or "").startswith("cron_") else None, None, None)
                    for mid, sid, role, content, ts, observed in c.execute(
                        "select id,session_id,role,content,timestamp,observed from messages "
                        "where timestamp>=? and content is not null", (cutoff,))]
    finally:
        c.close()
    for mid, role, content, ts, observed, source, chat_type, chat_id in rows:
        if not content or not (role == "user" or observed == 1):
            continue
        if not _trusted_message(source, chat_type, chat_id):
            continue
        if is_harness_envelope(content):
            continue
        out.append({"id": mid, "content": content, "ts": ts, "day": _day_of(ts),
                    "tokens": sig_tokens(content)})
    return out


# ---------------------------------------------------------------------------
# Corroboration and scoring
# ---------------------------------------------------------------------------

EVIDENCE_MAX = 3


def corroborate(item_tokens, messages, evidence=False):
    """How many different days / messages mention the token set.

    Match: overlap ≥2 tokens, or ≥1 "distinctive" token (len ≥8).
    Returns (mentions, ref_days, last_ts) or, with evidence=True,
    (mentions, ref_days, last_ts, samples) where samples are up to
    EVIDENCE_MAX (day, snippet) pairs from different days — provenance for the
    reviewer ("why does the dream think this is confirmed?")."""
    if not item_tokens:
        return (0, 0, None, []) if evidence else (0, 0, None)
    distinctive = {t for t in item_tokens if len(t) >= 8}
    days, mentions, last_ts, samples = set(), 0, None, []
    for m in messages:
        inter = item_tokens & m["tokens"]
        if len(inter) >= 2 or (inter & distinctive):
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
            "evidence": evidence}
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


def load_durable_memory_text(mem_md):
    chunks = []
    for path in _memory_candidate_paths(mem_md):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    chunks.append(f.read())
            except OSError:
                pass
    return "\n\n".join(chunks)


def _memory_chunks(memory_text):
    """Split memory/profile text into comparison chunks for fuzzy dedupe."""
    chunks = []
    for part in re.split(r"\n\s*§\s*\n|\n{2,}", memory_text or ""):
        part = part.strip()
        if len(part) > 20:
            chunks.append(part)
    return chunks or [memory_text or ""]


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
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        out.extend((target, chunk) for chunk in _memory_chunks(text))
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


def _stems(tokens):
    """Crude stems (first 5 chars) for fuzzy comparison only.

    Inflection breaks exact comparison («транспортные»≠«транспорт») — a
    reworded duplicate scored containment 0.3–0.5 against the 0.62 threshold
    and resurfaced in promotions every night. corroborate() keeps exact tokens."""
    return {t[:5] for t in tokens}


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
        if _covers(item_stems, chunk_stems, token_threshold):
            return True
        if _same_subject(item_stems, chunk_stems, content, chunk):
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
NUMBER_RE = re.compile(r"(?<![\w.])\d[\d.,:/-]*\d(?![\w])|(?<!\w)\d(?!\w)")


# A fact that opens with its own date stamp ("2026-06-19 X said that …") is
# dated provenance, not content: the stamp must not count as a number when the
# fact is compared with the entry written from it, or every stamped fact would
# come back as "different numbers" (a candidate the night after it was saved,
# a conflict with the entry it produced).
LEADING_DATE_STAMP_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}(?:[ ,:—-]+|$)")


def _numbers(text):
    body = LEADING_DATE_STAMP_RE.sub("", text or "", count=1)
    return {n.strip(".,:") for n in NUMBER_RE.findall(_norm(body))}


def find_conflicts(content, memory_text):
    """Memory chunks that look like the same subject with different numbers/dates."""
    item_stems = _stems(sig_tokens(content))
    if len(item_stems) < CONFLICT_MIN_SHARED:
        return []
    item_nums = _numbers(content)
    if not item_nums:
        return []
    out = []
    for chunk in _memory_chunks(memory_text):
        chunk_stems = _stems(sig_tokens(chunk))
        if not chunk_stems:
            continue
        shared = item_stems & chunk_stems
        overlap = len(shared) / min(len(item_stems), len(chunk_stems))
        if len(shared) < CONFLICT_MIN_SHARED or overlap < CONFLICT_MIN_OVERLAP:
            continue
        chunk_nums = _numbers(chunk)
        if chunk_nums and chunk_nums != item_nums and not (item_nums <= chunk_nums):
            out.append({"entry": chunk[:160],
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

def _fact_fingerprint(content):
    return hashlib.md5(_norm(content).encode("utf-8")).hexdigest()[:16]


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
    if not rejected:
        return False
    fingerprint = _fact_fingerprint(content)
    item_stems = _stems(sig_tokens(content))
    for rec in rejected.values():
        if not isinstance(rec, dict):
            continue
        if rec.get("fingerprint") == fingerprint:
            return True
        text = rec.get("content") or ""
        if not text or len(item_stems) < 4:
            continue
        if _covers(item_stems, _stems(sig_tokens(text))):
            return True
    return False


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
EPHEMERAL_HINTS_RE = re.compile(
    r"\b(?:тест|контрольн\w*|экзамен\w*|экскурс\w*|дедлайн\w*|"
    r"test|exam\w*|deadline\w*|field\s+trip|bring|submit|prepare|"
    r"sight\s+words?|страниц\w*|лист\w*\s+для\s+чтени\w*|"
    r"завтра|сегодня|послезавтра|принести|сдать|подготовить)\b"
    r"|(?:สอบ|กำหนดส่ง|ทัศนศึกษา|นำมา|ส่ง)",
    re.IGNORECASE,
)
DATED_RE = re.compile(
    rf"\b\d{{1,2}}\s+(?:{MONTHS_RE})(?:\s+\d{{4}})?\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:,?\s+\d{4})?\b|"
    rf"\b\d{{1,2}}\s+(?:{THAI_MONTHS_RE})(?:\s+\d{{4}})?\b",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|на этой неделе|на следующей неделе|"
    r"today|tomorrow|this week|next week)\b"
    r"|(?:วันนี้|พรุ่งนี้|มะรืน)",
    re.IGNORECASE,
)


def is_ephemeral_fact(content):
    """Temporary dated school/calendar facts should not become durable memory."""
    text = content or ""
    has_time_anchor = bool(DATED_RE.search(text) or RELATIVE_DATE_RE.search(text))
    return bool(has_time_anchor and EPHEMERAL_HINTS_RE.search(text))


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
EN_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})"
    r"(?:,?\s+(\d{4}))?\b", re.IGNORECASE)
THAI_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({THAI_MONTHS_RE})(?:\s+(\d{{4}}))?")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")


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
    r"|\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"            # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b[0-9a-f]{40,}\b"                           # long hex (keys/sessions)
    r")")
PASSWORD_MARKER_RE = re.compile(
    r"(?:парол[ьяию]|password|passwd|passphrase|seed[- ]?фраза|seed[- ]?phrase|"
    r"секретн\w{0,4}\s+(?:ключ|код)|api[- ]?ключ|private\s+key|2fa[- ]?код)"
    r"\s*[:=—]\s*\S{4,}",
    re.IGNORECASE)
INJECTION_RE = re.compile(
    r"(?:"
    r"игнорируй\s+(?:все\s+)?(?:предыдущие|прошлые|прежние)\s+(?:инструкции|правила|указания)"
    r"|ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules)"
    r"|disregard\s+(?:all\s+)?(?:previous|prior|above)"
    r"|забудь\s+(?:все|всё|свои)\s+(?:инструкции|правила|ограничения)"
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


def classify_unsafe(content):
    """'secret' | 'injection' | None. Conservative quarantine: exclusion from
    auto-promotion with a note in the report, never deletion."""
    text = content or ""
    if SECRET_RE.search(text) or PASSWORD_MARKER_RE.search(text):
        return "secret"
    if INJECTION_RE.search(text):
        return "injection"
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


def extract_themes(messages, window_days):
    cutoff = _now().timestamp() - window_days * 86400
    doc_freq = defaultdict(int)
    samples = {}
    for m in messages:
        if m["ts"] < cutoff:
            continue
        for w in m["tokens"]:
            doc_freq[w] += 1
            samples.setdefault(w, m["content"][:120])
    themes = [{"theme": w, "mentions": n, "sample": samples[w]}
              for w, n in doc_freq.items() if n >= MIN_MENTIONS]
    themes.sort(key=lambda x: -x["mentions"])
    return themes


def parse_md_entries(mem_md):
    if not os.path.exists(mem_md):
        return []
    with open(mem_md, encoding="utf-8") as f:
        text = f.read()
    parts = [p.strip() for p in text.split("§")]
    return [p for p in parts if len(p) > 10]


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


def _replace_anchor(chunk, sources):
    """A verbatim head of `chunk` that occurs in exactly one durable entry."""
    head = " ".join((chunk or "").split())[:REPLACE_ANCHOR_CHARS].strip()
    if not head:
        return None
    hits = sum(1 for _, other in sources if head in " ".join(other.split()))
    return head if hits == 1 else None


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
    anchor = _replace_anchor(best, sources)
    if anchor:
        item["old_text"] = anchor
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


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_memory_loss(mem_md, snapshot_path):
    """Compare durable files with the previous snapshot; write a new one.
    Returns a list of alerts (possibly empty)."""
    if not snapshot_path:
        return []
    previous = load_snapshot(snapshot_path)
    current, alerts = {}, []
    for p in _memory_candidate_paths(mem_md):
        name = _rel_home(p)
        prev = set(previous.get(name) or [])
        if not os.path.exists(p):
            # A file that was there yesterday and is gone today is the loudest
            # loss there is — it must not slip through as "nothing to compare"
            # and then be overwritten by an empty snapshot. Alert once; the
            # snapshot forgets the file only after the alert was raised.
            if len(prev) >= 4:
                alerts.append({"kind": "memory_loss", "file": name,
                               "lost": len(prev), "had": len(prev),
                               "message": f"{name}: the file is missing — all {len(prev)} entries "
                                          f"present at the previous pass are gone. "
                                          f"Check whether that was intended."})
            continue
        keys = _entry_keys_of(p)
        current[name] = sorted(keys)
        if len(prev) >= 4 and keys is not None:
            lost = prev - keys
            frac = len(lost) / len(prev)
            if frac > MEMORY_LOSS_ALERT_FRACTION:
                alerts.append({"kind": "memory_loss", "file": name,
                               "lost": len(lost), "had": len(prev),
                               "message": f"{name}: {len(lost)} of {len(prev)} entries present at the "
                                          f"previous pass are gone ({round(100 * frac)}%). "
                                          f"Check whether that was intended."})
    try:
        _write_private(snapshot_path, json.dumps(
            {**current, "_at": _now().isoformat(timespec="seconds")}, ensure_ascii=False))
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
            sids = [r[0] for r in c.execute(
                "select distinct session_id from messages where role='user' and timestamp>=? "
                "and (instr(coalesce(content,''),?)>0 or instr(coalesce(content,''),?)>0)",
                (since, *needles))]
            if not sids:
                return False
            for sid in sids:
                answered = c.execute(
                    "select 1 from messages where session_id=? and role='assistant' "
                    "and trim(coalesce(content,''))!='' limit 1", (sid,)).fetchone()
                if answered:
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
            if bucket.pop(k, None) is not None:
                dropped += 1
    reask = set(pending.get("asked") or [])
    if dropped or reask:
        print(f"[dream] note: the pass of {pending['generated_at']} was not acknowledged by an "
              f"agent turn — {dropped} cooldown(s) and {len(reask)} md question(s) reopened",
              file=sys.stderr)
    return reask


def _load_asked_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def md_decays(mem_md, messages, decay_days, asked_state_path=None, cap=None, reask=()):
    """§-entries of MEMORY.md that have not surfaced in conversations for long
    → soft flag "still relevant?".

    Cooldown: an entry flagged within the last MD_ASK_COOLDOWN_DAYS is not
    raised again (state = {entry_key: iso_date}). Without it the dream asked
    the same thing every night. Keys of entries that disappeared from
    MEMORY.md are pruned. Only entries that make it into the published slice
    (`cap`) are marked as asked — an overflow entry never reached the agent.
    `reask` — keys whose previous showing was never acknowledged by an agent
    turn (see `_agent_acked`): their cooldown is dropped."""
    out = []
    now = _now()
    now_ts = now.timestamp()
    asked = _load_asked_state(asked_state_path) if asked_state_path else {}
    current_keys, changed = set(), False
    for key in reask:
        if key in asked:
            asked.pop(key, None)
            changed = True
    for i, entry in enumerate(parse_md_entries(mem_md)):
        key = _entry_key(entry)
        current_keys.add(key)
        if is_pinned(entry):
            continue
        toks = sig_tokens(entry)
        mentions, ref_days, last_ts = corroborate(toks, messages)
        days_since = (now_ts - last_ts) / 86400 if last_ts else None
        if not (mentions == 0 or (days_since is not None and days_since > decay_days)):
            continue
        last_asked = _parse_iso(asked.get(key))
        if last_asked and (now - last_asked) < timedelta(days=MD_ASK_COOLDOWN_DAYS):
            continue
        if cap is not None and len(out) >= cap:
            break
        asked[key] = now.isoformat(timespec="seconds")
        changed = True
        out.append({"index": i, "entry": entry[:160], "key": key,
                    "last_mention_days": round(days_since, 1) if days_since is not None else None,
                    "reason": "never surfaced within the window" if mentions == 0
                              else f"not surfaced for ~{round(days_since)} d"})
    if asked_state_path:
        pruned = {k: v for k, v in asked.items() if k in current_keys}
        if changed or pruned.keys() != asked.keys():
            try:
                _write_private(asked_state_path, json.dumps(pruned, ensure_ascii=False, indent=1))
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
            rec["percent"] = round(100.0 * n / int(limit))
        out[name] = rec
    return out


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def run(window_days, corr_days, md_decay_days, mem_md, asked_state_path=None,
        rejected_state_path=None, seen_state_path=None, snapshot_state_path=None):
    state_db = os.path.join(HOME, "state.db")
    facts = load_facts(fact_store_path())
    messages = load_messages(state_db, corr_days)
    memory_text = load_durable_memory_text(mem_md)
    rejected = load_rejected(rejected_state_path)
    now = _now()
    generated_at = now.isoformat(timespec="seconds")
    seen = load_seen(seen_state_path)
    reask_md = _revert_unacked(seen, state_db) if seen_state_path else set()
    seen_new_facts = seen.setdefault("new_facts", {})
    seen_fact_decays = seen.setdefault("fact_decays", {})
    seen_conflicts = seen.setdefault("conflicts", {})

    scored = []
    for f in facts:
        score, sig, meta = signals_for_fact(f, messages)
        scored.append({**f, "score": score, "signals": sig, "meta": meta,
                       "unsafe": classify_unsafe(f["content"]),
                       "in_memory": already_in_memory(f["content"], memory_text),
                       "rejected": is_rejected(f["content"], rejected)})

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

    # An event with a past date is shown by NO section: no work is possible.
    expired_ids = {s["fact_id"] for s in safe if is_expired_event(s["content"])}
    safe = [s for s in safe if s["fact_id"] not in expired_ids]

    # Every fact created or reinforced during the window is surfaced for a
    # separate profile review — not pre-filtered by score (a stable preference
    # matters before it accumulates retrievals), minus what is already in
    # durable memory, minus what was shown within the cooldown (before the cap,
    # so suppressed candidates do not take slots from unseen ones).
    new_facts_window = [s for s in safe if s["meta"]["days_old"] <= window_days]
    new_facts_pending = [s for s in new_facts_window if not s["in_memory"]]
    new_facts = unseen(new_facts_pending, seen_new_facts, now)
    new_facts_suppressed = len(new_facts_pending) - len(new_facts)
    new_facts.sort(key=lambda s: (s["meta"]["days_old"], -s["score"]))
    new_facts = new_facts[:NEW_FACTS_CAP]

    promotion_candidates = [
        s for s in safe
        if s["score"] >= MIN_SCORE and promotion_ready(s) and not s["in_memory"]
    ]
    ephemeral_events = [s for s in promotion_candidates if is_ephemeral_fact(s["content"])]
    promotions = [s for s in promotion_candidates if not is_ephemeral_fact(s["content"])]
    promotions.sort(key=lambda s: -s["score"])
    ephemeral_events.sort(key=lambda s: -s["score"])
    # A fact already offered as a promotion is not repeated in new_facts: the
    # agent reviews it once (live case: 8 extracted facts arrived in both
    # sections, doubling the prompt for no extra work).
    promoted_ids = {s["fact_id"] for s in promotions}
    new_facts = [s for s in new_facts if s["fact_id"] not in promoted_ids]

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

    md_dec = md_decays(mem_md, messages, md_decay_days, asked_state_path, cap=PUBLISH_CAP,
                       reask=reask_md)

    # Only published slices count as shown.
    published_decays = decays[:PUBLISH_CAP]
    published_conflicts = conflicts[:PUBLISH_CAP]
    if seen_state_path:
        mark_seen(new_facts, seen_new_facts, now)
        mark_seen(published_decays, seen_fact_decays, now)
        mark_seen(published_conflicts, seen_conflicts, now)
        for bucket in (seen_new_facts, seen_fact_decays, seen_conflicts):
            prune_seen(bucket, now)
        # What this pass is about to show, keyed for `_revert_unacked` tomorrow.
        fp = _fact_fingerprint
        seen["_pending"] = {
            "generated_at": generated_at,
            "seen": {"new_facts": [fp(s["content"]) for s in new_facts],
                     "fact_decays": [fp(s["content"]) for s in published_decays],
                     "conflicts": [fp(s["content"]) for s in published_conflicts]},
            "asked": [d["key"] for d in md_dec if d.get("key")],
        }
        try:
            _write_private(seen_state_path, json.dumps(seen, ensure_ascii=False, indent=1))
        except OSError as e:
            print(f"[dream] warn: seen-state not written: {e}", file=sys.stderr)

    usage = memory_usage(mem_md)
    alerts = check_memory_loss(mem_md, snapshot_state_path)
    diary = build_diary(window_days, len(facts), len(messages), promotions, decays, themes, md_dec,
                        ephemeral_events, quarantined, len(suppressed), conflicts=published_conflicts,
                        alerts=alerts)

    memory_sources = load_durable_memory_sources(mem_md)

    def _pub_near(s):
        item = _pub(s)
        near = nearest_entry(s["content"], sources=memory_sources)
        if near:
            item["nearest_entry"] = near
        return item

    for d in md_dec:
        d.pop("key", None)  # internal handle, not for the agent

    return {
        "generated_at": generated_at,
        "window_days": window_days, "corr_window_days": corr_days, "md_decay_days": md_decay_days,
        "weights": WEIGHTS,
        "gates": {"min_score": MIN_SCORE, "min_mentions": MIN_MENTIONS,
                  "promotion_corroboration": "2 ref_days OR min_mentions OR 3 retrievals + 1 helpful"},
        "stats": {"facts": len(facts), "new_facts_reviewed": len(new_facts),
                  "new_facts_window": len(new_facts_window),
                  "messages_window": len(messages),
                  "promotions": len(promotions), "ephemeral_events": len(ephemeral_events),
                  "fact_decays": len(decays), "md_decays": len(md_dec), "themes": len(themes),
                  "conflicts": len(conflicts),
                  "quarantined": len(quarantined),
                  # Informational: silenced by the reject list / cooldown / expiry.
                  # Not work — never in the precheck's actionable keys.
                  "rejected_suppressed": len(suppressed),
                  "new_facts_suppressed": new_facts_suppressed,
                  "fact_decays_suppressed": decays_suppressed,
                  "conflicts_suppressed": conflicts_suppressed,
                  "expired_events": len(expired_ids),
                  "alerts": len(alerts)},
        "memory_usage": usage,
        "alerts": alerts,
        "promotions": [_pub_near(s) for s in promotions[:PUBLISH_CAP]],
        "new_facts": [_pub_near(s) for s in new_facts],
        "ephemeral_events": [_pub(s) for s in ephemeral_events[:PUBLISH_CAP]],
        "fact_decays": [_pub(s) for s in decays[:PUBLISH_CAP]],
        "conflicts": [dict(_pub_near(s), conflicts=s["conflicts"]) for s in published_conflicts],
        "md_decays": md_dec,
        "emerging_themes": themes[:PUBLISH_CAP],
        "quarantined": [_pub_quarantined(s) for s in quarantined[:PUBLISH_CAP]],
        "diary": diary,
    }


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
    return {"fact_id": s["fact_id"], "content": s["content"], "category": s["category"],
            "tags": s["tags"], "score": s["score"], "signals": s["signals"],
            "days_old": s["meta"]["days_old"], "retrieval_count": s["meta"]["rc"],
            "mentions": s["meta"]["mentions"], "ref_days": s["meta"]["ref_days"],
            "evidence": s["meta"].get("evidence", []),
            "why": _why(s),
            "in_memory": s.get("in_memory", False),
            "ephemeral": is_ephemeral_fact(s["content"]),
            "user_profile_hint": user_profile_hint(s)}


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------

def build_diary(window, nfacts, nmsgs, promotions, decays, themes, md_dec, ephemeral_events=None,
                quarantined=None, suppressed=0, conflicts=None, alerts=None):
    ephemeral_events = ephemeral_events or []
    quarantined = quarantined or []
    conflicts = conflicts or []
    lines = [f"{DIARY_HEADING} {_now().astimezone(LOCAL_TZ).strftime('%Y-%m-%d')}",
             f"Theme window: {window} d · facts: {nfacts} · messages (corroboration): {nmsgs}.", ""]
    for a in alerts or []:
        lines.append(f"⚠️ {a.get('message')}")

    def _line(p):
        if "meta" in p:
            return (f"- ({p['score']}, mentions {p['meta']['mentions']}/{p['meta']['ref_days']}d) "
                    f"{p['content'][:85]}")
        return f"- {p['content'][:85]}"

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
                     + "; ".join(c["content"][:45] for c in conflicts[:3]))
    if themes:
        lines.append("\n**Emerging themes:** " + ", ".join(f"{t['theme']}×{t['mentions']}" for t in themes[:8]))
    if md_dec:
        lines.append(f"\n**MEMORY.md entries not surfaced for long ({len(md_dec)}):** "
                     + "; ".join(d["entry"][:45] for d in md_dec[:3]))
    if decays:
        lines.append(f"\n**Facts never used ({len(decays)}):** "
                     + "; ".join(d["content"][:45] for d in decays[:3]))
    if quarantined:
        reasons = ", ".join(sorted({q["unsafe"] for q in quarantined}))
        lines.append(f"\n**Quarantined (not promoted): {len(quarantined)}** — {reasons}.")
    if suppressed:
        lines.append(f"\n**Rejected earlier — not asking again: {suppressed}.**")
    lines.append("")
    return "\n".join(lines)


def _write_private(path, text, append=False):
    """Dream files contain private facts — create with mode 0600.

    `os.open(..., 0o600)` applies the mode only on CREATION, so a diary created
    earlier kept 0644 (live case). chmod is enforced explicitly."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a" if append else "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
# CLI
# ---------------------------------------------------------------------------

def explain(fact_id, mem_md, corr_days):
    """`--explain ID`: per-signal breakdown of one fact (OpenClaw promote-explain)."""
    facts = [f for f in load_facts(fact_store_path()) if f["fact_id"] == fact_id]
    if not facts:
        print(f"fact {fact_id} not found", file=sys.stderr)
        return 1
    f = facts[0]
    messages = load_messages(os.path.join(HOME, "state.db"), corr_days)
    memory_text = load_durable_memory_text(mem_md)
    score, sig, meta = signals_for_fact(f, messages)
    print(f"fact {fact_id}: {f['content'][:200]}")
    print(f"score {score} (gate {MIN_SCORE}); weights {WEIGHTS}")
    for k, v in sig.items():
        print(f"  {k:20s} {v:5.3f} × {WEIGHTS[k]:.2f} = {v * WEIGHTS[k]:.3f}")
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


def _state_default(env_name, cfg_key):
    p = os.environ.get(env_name)
    if p is not None:
        return p
    p = (CONFIG.get("state") or {}).get(cfg_key)
    if not p:
        return ""
    return p if os.path.isabs(os.path.expanduser(p)) else os.path.join(HOME, p)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only dreaming pass over agent memory")
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
    ap.add_argument("--explain", type=int, default=None, metavar="FACT_ID",
                    help="print the score breakdown of one fact and exit")
    args = ap.parse_args(argv)

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

    result = run(window, corr, md_days, mem_md,
                 asked_state_path=asked or None,
                 rejected_state_path=rejected or None,
                 seen_state_path=seen or None,
                 snapshot_state_path=snapshot or None)

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
