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
  dream-reject.py --from-report --reason "duplicates the system prompt"
      # after a manual memory cleanup: reject every promotion the last pass
      # proposed, so the dropped entries are not offered right back
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
    """Load dream.py next to this file (fingerprint, classifier, config), configured for HOME."""
    global _DREAM
    if _DREAM is None:
        spec = importlib.util.spec_from_file_location(
            "_dream_for_reject", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dream.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # registered, as an import is: a setting written on it then raises (3.0.1)
        spec.loader.exec_module(mod)
        mod.load_for_home(HOME)
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
    """Quarantine classifier from dream.py. Unavailable — treat the text as a secret: the record then keeps the
    fingerprint only, never the text. "Ordinary text" copied a password into the reject list whenever dream.py
    could not be loaded (review of 2.9.8)."""
    try:
        return _dream().classify_unsafe(content)
    except Exception as exc:  # noqa: BLE001 — a broken dream.py must not stop a rejection
        print(f"[dream-reject] warn: the screen is unavailable ({type(exc).__name__}: {exc}) — the text is not "
              "stored, only its fingerprint", file=sys.stderr)
        return "secret"


def _fingerprint(content):
    # No fallback formula: the one kept here was not dream.py's (no NFC, no ё → е), so its keys never matched a
    # fact the nightly pass reads (review of 3.3.1). dream.py is loaded before this anyway (the lock, the state).
    return _dream()._fact_fingerprint(content)


def load_state(path):
    """The reject list through dream.py's reader: a broken file is moved aside with a warning. Read as {} it was
    overwritten by the next rejection, and every earlier one was gone (review of 2.9.8)."""
    return _dream()._load_state_file(path)


def peek_state(path):
    """The reject list for `--list`, which runs without the state lock: a broken file is reported and left where it
    is. Moving it aside here raced a locked writer — the new, good list could be the one moved away (Codex review
    of the 2.9.9 changes); the next locked run sets it aside."""
    data, why = _dream()._read_state_file(path)
    if why:
        print(f"the reject list {path} is {why} — shown as empty and left as it is; the next rejection or nightly "
              "pass moves it aside", file=sys.stderr)
    return data


def save_state(path, data):
    """0600: the content is private. Written beside and renamed over: truncating in place let a reader in
    between see an empty list, and every rejection came back for a night (Codex review of 2.7.2)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_content(fact_id):
    """Fact text from the configured fact source (read-only, the same loader the
    dream uses). None — no such fact. A main fact store that cannot be read raises (OSError, ValueError,
    sqlite3.Error): "not found" sent the human to `--content` for a fact that was there (review of 3.3.1). A chat
    store's loader reports its own failures and gives no facts, so there an unreadable store is still "not found"."""
    d = _dream()
    # holographic: resolve the store against this script's HERMES_HOME.
    if "#" in str(fact_id):  # "<chat store>#<id>" of a promoted chat store (chat_stores, 2.6.0)
        facts = d.load_chat_facts(HOME)
    else:
        facts = (d.load_facts(d.fact_store_path(HOME)) if d.fact_source() == "holographic"
                 else d.load_facts())
    for f in facts:
        if str(f.get("fact_id")) == str(fact_id):
            return f.get("content")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reject list of the nightly dream")
    ap.add_argument("fact_id", nargs="?", help="fact id from the dream report")
    ap.add_argument("--content", help="fact text (when it is no longer in the DB)")
    ap.add_argument("--reason", default="", help="why rejected — for the record")
    ap.add_argument("--state", default=None)
    ap.add_argument("--hermes-home", default=None,
                    help="the Hermes home the dream ran with (default $HERMES_HOME or ~/.hermes): its "
                         "config, fact store, report and state lock")
    ap.add_argument("--list", action="store_true", help="show the reject list")
    ap.add_argument("--undo", help="lift a rejection by record key or fact_id")
    ap.add_argument("--from-report", nargs="?", const="", metavar="JSON",
                    help="reject every promotion in the last dream report (default "
                         "cache/dream.json, or the path given). Use right after a manual "
                         "memory cleanup: what you dropped comes back as a promotion")
    args = ap.parse_args(argv)
    if args.hermes_home:
        # The lock, the config and the fact store must be the dream's: a reject run for another home took
        # ~/.hermes's lock while the dream held its own (Codex review of 2.7.5).
        global HOME
        HOME = os.path.abspath(os.path.expanduser(args.hermes_home))
        _dream().load_for_home(HOME)  # its config too: the fact store `fetch_content` reads is this home's
    if args.list:
        return _run(ap, args)
    # Read, change, write under the lock the nightly pass and the forget gate hold while they rewrite the
    # same file: a rejection added between their read and their write was lost (Codex review of 2.7.2).
    with _dream()._sweep_lock(HOME) as held:
        if not held:
            print("the dream's state lock could not be taken — nothing written, try again", file=sys.stderr)
            return 1
        return _run(ap, args)


def _run(ap, args):
    state_path = args.state or _default_state()

    # Only a holder of the state lock may move a broken file aside; `--list` reads without it.
    try:
        state = peek_state(state_path) if args.list else load_state(state_path)
    except OSError as exc:  # there but unreadable: a rejection written now would replace every earlier one
        print(f"{exc}", file=sys.stderr)
        return 1

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

    if args.from_report is not None:
        report = args.from_report or os.path.join(HOME, "cache", "dream.json")
        try:
            with open(report, encoding="utf-8") as f:
                promos = (json.load(f) or {}).get("promotions") or []
        except (OSError, ValueError) as exc:
            print(f"cannot read report {report}: {exc}", file=sys.stderr)
            return 1
        if not promos:
            print("report has no promotions — nothing to reject")
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        added = skipped = 0
        for item in promos:
            text = item.get("content") or ""
            if not text:
                continue
            key = _fingerprint(text)
            if key in state:
                skipped += 1
                continue
            rec = {"fact_id": item.get("fact_id"), "content": text, "fingerprint": key,
                   "reason": args.reason, "at": now}
            if _classify_unsafe(text) == "secret":
                rec["content"] = None
                rec["redacted"] = "secret"
            state[key] = rec
            added += 1
            print(f"rejected: {key} — {text[:90] if rec.get('content') else '[secret]'}")
        if added:
            save_state(state_path, state)
        print(f"{added} rejected, {skipped} already on the list, {len(state)} total")
        return 0

    content = args.content
    if content is None:
        if not args.fact_id:
            ap.error("fact_id or --content is required")
        try:
            content = fetch_content(args.fact_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"cannot read the fact store: {exc} — nothing written", file=sys.stderr)
            return 1
        if content is None:
            ap.error(f"fact {args.fact_id} not found in the fact store — pass --content")

    # The key is ALWAYS the text fingerprint. Keying by fact_id is unsafe: the
    # core may re-extract the same fact under a new id, and a generic `sqlite`
    # source without AUTOINCREMENT reuses the id of a deleted last row (a
    # rejection of a new fact would overwrite the rejection of an old one;
    # holographic never reuses an id). fact_id stays a tracing field and works
    # in `--undo`.
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
