#!/usr/bin/env python3
"""dream-reject.py — close a question about a fact a human called outdated / wrong.

Why: the core `memory` tool edits MEMORY.md/USER.md only and cannot delete a
row from `memory_store.db`, and the only repeat filter of the dream is
"already in durable memory". A rejected fact is in neither, so it came back as
a candidate every night (live case: 12 identical questions about a dead
thread in 5 weeks).

The script writes only the reject list (`cache/dream-rejected.json`, 0600):
neither memory nor `memory_store.db` is touched — the fact stays in the DB but
the dream stops proposing / asking about it. Matching in `dream.py` is by text,
not `fact_id`, so a fact re-extracted under a new id is silenced too.

Examples:
  dream-reject.py 6 --reason "thread 9 is gone: the child chat has no topics"
  dream-reject.py --content "Thread 9 is the child's thread" --reason "outdated"
  dream-reject.py --list
  dream-reject.py --undo 6
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


_DREAM = None


def _dream():
    """Load dream.py next to this file (fingerprint, classifier, config)."""
    global _DREAM
    if _DREAM is None:
        spec = importlib.util.spec_from_file_location(
            "_dream_for_reject", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dream.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _DREAM = mod
    return _DREAM


def _default_state():
    p = os.environ.get("DREAM_REJECTED_STATE")
    if p:
        return p
    try:
        cfg = _dream().load_config()
        p = (cfg.get("state") or {}).get("rejected") or "cache/dream-rejected.json"
    except Exception:  # noqa: BLE001
        p = "cache/dream-rejected.json"
    return p if os.path.isabs(os.path.expanduser(p)) else os.path.join(HOME, p)


def _classify_unsafe(content):
    """Quarantine classifier from dream.py; unavailable — treat as ordinary text."""
    try:
        return _dream().classify_unsafe(content)
    except Exception:  # noqa: BLE001
        return None


def _fingerprint(content):
    try:
        return _dream()._fact_fingerprint(content)
    except Exception:  # noqa: BLE001 — same formula as dream.py, kept as a fallback
        import hashlib
        import re
        norm = re.sub(r"\s+", " ", (content or "").lower()).strip()
        return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path, data):
    """0600: the content is private."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def fetch_content(fact_id):
    """Fact text from memory_store.db (read-only). None — the fact is gone."""
    db = os.path.join(HOME, "memory_store.db")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("select content from facts where fact_id=?", (fact_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return row[0] if row else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reject list of the nightly dream")
    ap.add_argument("fact_id", nargs="?", help="fact id from the dream report")
    ap.add_argument("--content", help="fact text (when it is no longer in the DB)")
    ap.add_argument("--reason", default="", help="why rejected — for the record")
    ap.add_argument("--state", default=None)
    ap.add_argument("--list", action="store_true", help="show the reject list")
    ap.add_argument("--undo", help="lift a rejection by record key or fact_id")
    args = ap.parse_args(argv)
    state_path = args.state or _default_state()

    state = load_state(state_path)

    if args.list:
        if not state:
            print("reject list is empty")
            return 0
        # Rejections are indefinite and invisible, so age matters: a
        # half-year-old "outdated" may have become true again. Review the list
        # every few months and lift what no longer applies (--undo).
        now = datetime.now(timezone.utc)
        for key, rec in sorted(state.items(), key=lambda kv: kv[1].get("at") or ""):
            shown = ("[secret: text not stored]" if rec.get("redacted")
                     else (rec.get("content") or "")[:80])
            trace = f" (fact_id {rec['fact_id']})" if rec.get("fact_id") else ""
            try:
                age = f"{(now - datetime.fromisoformat(rec['at'])).days}d ago"
            except (TypeError, ValueError, KeyError):
                age = rec.get("at", "?")
            print(f"{key}{trace}: {age} — {shown}"
                  f"{' | ' + rec['reason'] if rec.get('reason') else ''}")
        return 0

    if args.undo:
        # The key is the fingerprint, but people pass the fact_id from the
        # report out of habit (and old records) — accept both.
        removed = [args.undo] if state.pop(args.undo, None) is not None else [
            key for key, rec in list(state.items())
            if isinstance(rec, dict) and str(rec.get("fact_id")) == str(args.undo)]
        for key in removed:
            state.pop(key, None)
        if not removed:
            print(f"no record {args.undo} in the reject list", file=sys.stderr)
            return 1
        save_state(state_path, state)
        print(f"rejection lifted: {', '.join(removed)}")
        return 0

    content = args.content
    if content is None:
        if not args.fact_id:
            ap.error("fact_id or --content is required")
        content = fetch_content(args.fact_id)
        if content is None:
            ap.error(f"fact {args.fact_id} not found in memory_store.db — pass --content")

    # The key is ALWAYS the text fingerprint. Keying by fact_id is unsafe:
    # sqlite reuses ids after the last row is deleted, and a rejection of a new
    # fact would silently overwrite the rejection of an old one. fact_id stays
    # a tracing field and works in `--undo`.
    key = _fingerprint(content)
    record = {
        "fact_id": args.fact_id,
        "content": content,
        "fingerprint": key,
        "reason": args.reason,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # A quarantined secret may be closed (otherwise it wakes the agent every
    # night), but its text must not be copied into a second file: the
    # fingerprint is enough to silence exactly this fact.
    redacted = _classify_unsafe(content) == "secret"
    if redacted:
        record["content"] = None
        record["redacted"] = "secret"
    state[key] = record
    save_state(state_path, state)
    print(f"rejected: {key} — "
          f"{'[secret: text not stored]' if redacted else content[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
