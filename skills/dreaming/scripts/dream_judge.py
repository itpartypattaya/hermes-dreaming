"""dream_judge.py — the optional cloud judge of the dream (3.3.0). Off by default; dream.py loads this file only
when `judge.mode` is `shadow` or `veto` (and for the review commands).

The rules count a (fact, message) pair as a mention when the message shares enough words with the fact. Many such
pairs are no confirmation at all ("Гоша тренирует юниоров" counted for "Гоша не пьёт кофе"). The judge asks a
decision model whether the message confirms the fact, contradicts it, or neither, and may only take a counted pair
OUT of the count — never add one. What it sends is decided in dream.py (which pairs, from which chats, which text);
this file only talks to the provider, checks the answer and keeps the night within its attempts, money and time.

Phase 0 of the plan measured the judge on exactly the question below (INSTRUCTIONS, LABELS) and set the threshold
on it: tau 0.50 holds for these words only — change them and it has to be measured again.

Providers: Jev (OpenRouter's decisions API, model by TypeSafe) and OpenAI's decisions API. HTTPS, no proxy, no
redirects; the endpoints are constants, not settings. The API key comes from the environment, else from its one
line in $HERMES_HOME/.env (Hermes strips provider keys from the environment of cron scripts); it goes in one
request header and nowhere else.

No file is written here: dream.py reads and writes the cache, the journal of sent pairs and the health record (with
its private writer, and its forget sweep cleans them while this file is not loaded); this file says what goes in
them. Standard library only.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from collections import Counter, namedtuple
from datetime import datetime

PROMPT_VERSION = 1  # part of every cache key: a verdict on other words is no verdict on these
PROVENANCE_VERSION = 1  # the same for what a message's origin means (dream.judge_permission)

INSTRUCTIONS = "Does the message confirm the fact, contradict it, or neither?"
LABELS = {"confirms": "the message confirms the fact: the same person or thing, and the fact still holds",
          "contradicts": "the message contradicts the fact: it says the fact is not true or no longer true",
          "unrelated": "the message is about another person or thing, or neither confirms nor denies the fact"}
JEV_KEYS = {"A": "confirms", "B": "contradicts", "C": "unrelated"}

PROVIDERS = {
    # OpenRouter reports the charge of a call in usage.cost (USD). The data reaches OpenRouter and TypeSafe.
    "jev": {"url": "https://openrouter.ai/api/alpha/decisions", "model": "typesafe/jev-1.14",
            "env": "OPENROUTER_API_KEY"},
    # OpenAI's price list of 22.10.2026: $0.10 per million input tokens, output free.
    "openai": {"url": "https://api.openai.com/v1/decisions", "model": "gpt-6-luna", "env": "OPENAI_API_KEY",
               "usd_per_input_token": 0.10 / 1e6},
}

RESERVE_USD = 0.0002  # what a call costs when its answer does not say (a cut-off answer, an error page)
JUDGE_BUDGET_SEC = 120  # the judge's share of a night
DEADLINE_RESERVE_SEC = 90  # left to the rest of the pass before the gate's deadline
PER_CALL_TIMEOUT = 20
MAX_BODY = 65536
RETRY_BACKOFF = (0.5, 1.0)  # before the second and the third attempt
TTL_DAYS = 90
RETRYABLE = {408, 429, 500, 502, 503, 504}

Item = namedtuple("Item", "pair paths fact words fact_ref")
Verdict = namedtuple("Verdict", "provider label confidence probs veto source")
Send = namedtuple("Send", "item provider outcome verdict")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise _Redirect(code)


class _Redirect(Exception):
    pass


class _TooBig(Exception):
    pass


class _OutOfTime(Exception):
    pass


OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# The request and the answer
# ---------------------------------------------------------------------------

def payload(provider, fact, words):
    """The request body: the fact and the person's own words — nothing else of the message or of the home."""
    text = f"Fact: {fact}\nMessage: {words}"
    if provider == "openai":
        return {"model": PROVIDERS[provider]["model"], "input": text,
                "questions": [{"type": "choice", "name": "rel", "instructions": INSTRUCTIONS,
                               "choices": [{"value": v, "description": d} for v, d in LABELS.items()]}]}
    return {"model": PROVIDERS[provider]["model"], "state": text,
            "questions": {"rel": {"type": "choice", "instructions": INSTRUCTIONS,
                                  "criteria": {k: LABELS[v] for k, v in JEV_KEYS.items()}}}}


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_answer(label, confidence, probs):
    """"ok", or why the answer cannot be used: one of the three labels, a probability for each of the three (finite,
    0..1, summing to 1 within 0.02), the label among the most probable, a confidence 0..1."""
    if label not in LABELS:
        return "label"
    if not isinstance(probs, dict) or set(probs) != set(LABELS):
        return "probs_keys"
    if not all(_number(v) and 0 <= v <= 1 for v in probs.values()):
        return "probs_value"
    if abs(sum(probs.values()) - 1) > 0.02:
        return "probs_sum"
    if probs[label] < max(probs.values()):
        return "probs_argmax"
    if not (_number(confidence) and 0 <= confidence <= 1):
        return "confidence"
    return "ok"


def validate(provider, data):
    """(label, confidence, probs, reason) of a provider's answer; label None when it cannot be used (`unknown`).
    The alpha API of Jev is read as strictly as OpenAI's: exactly the one question, nothing guessed."""
    if not isinstance(data, dict):
        return None, None, None, "shape"
    if provider == "openai":
        answers = data.get("answers")
        if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict) \
                or answers[0].get("name") != "rel":
            return None, None, None, "answers"
        a = answers[0]
        if a.get("type") == "refusal":
            return None, None, None, "refusal"
        label, probs = a.get("choice"), {}
        for p in a.get("probabilities") or []:
            if not isinstance(p, dict) or p.get("value") in probs:
                return None, None, None, "probs_shape"
            probs[p.get("value")] = p.get("probability")
    else:
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != {"rel"} or not isinstance(answers["rel"], dict):
            return None, None, None, "answers"
        a = answers["rel"]
        if a.get("type") == "refusal":
            return None, None, None, "refusal"
        label = JEV_KEYS.get(a.get("choice"))
        raw = a.get("probabilities")
        if isinstance(raw, list):
            raw = {p.get("value") or p.get("label"): p.get("probability") for p in raw if isinstance(p, dict)}
        if not isinstance(raw, dict):
            return None, None, None, "probs_shape"
        probs = {JEV_KEYS.get(k, k): v for k, v in raw.items()}
    confidence = a.get("confidence")
    reason = check_answer(label, confidence, probs)
    if reason != "ok":
        return None, None, None, reason
    return label, confidence, probs, "ok"


def is_veto(label, confidence, tau):
    """A usable answer takes the pair out of the count when it is not `confirms` and its confidence reaches tau."""
    return label is not None and label != "confirms" and confidence >= tau


def cost_of(provider, data):
    """What the call cost by the provider's own answer, or None when the answer does not say."""
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    if provider == "openai":
        tokens = usage.get("input_tokens")
        ok = _number(tokens) and tokens >= 0
        return tokens * PROVIDERS["openai"]["usd_per_input_token"] if ok else None
    cost = usage.get("cost")
    return float(cost) if _number(cost) and cost >= 0 else None


# ---------------------------------------------------------------------------
# One night
# ---------------------------------------------------------------------------

def api_key(name, home=None):
    """The key named *name*: the environment, else its line in <home>/.env (`NAME=value`, `export NAME=value`,
    quoted or not). Only that line is read; nothing of it is ever printed."""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    if not home:
        return None
    try:
        with open(os.path.join(home, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, value = line.partition("=")
                if not sep or key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1].strip()
                else:
                    value = value.split(" #", 1)[0].strip()
                return value or None
    except (OSError, UnicodeDecodeError):
        return None
    return None


class Budget:
    """Attempts, money and time of one night. The time is the judge's share: JUDGE_BUDGET_SEC, and never past the
    gate's deadline minus DEADLINE_RESERVE_SEC (the rest of the pass needs it). Money is counted by what the
    providers report, a call without a price at RESERVE_USD; a cap stops the next call, so the last one may pass it."""

    def __init__(self, max_attempts, max_cost_usd, deadline=None):
        self.max_attempts, self.max_cost = max_attempts, max_cost_usd
        self.attempts, self.cost = 0, 0.0
        share = JUDGE_BUDGET_SEC
        if deadline is not None:
            share = min(share, deadline - time.time() - DEADLINE_RESERVE_SEC)
        self.end = time.monotonic() + share

    def remaining(self):
        return self.end - time.monotonic()

    def stop(self):
        if self.remaining() <= 0:
            return "deadline"
        if self.attempts >= self.max_attempts:
            return "attempts"
        if self.cost >= self.max_cost:
            return "cost"
        return None


def _read_body(resp, budget):
    chunks, size = [], 0
    while True:
        if budget.remaining() <= 0:
            raise _OutOfTime()
        chunk = resp.read(16384)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_BODY:
            raise _TooBig()
        chunks.append(chunk)


def call(provider, key, body, budget, opener=None):
    """(outcome, answer) of one pair at one provider. Outcomes: `ok` (answer = (label, confidence, probs)),
    `unknown` (an answer that cannot be used: not JSON, too big, off the schema, a refusal), `fail_net` (no
    answer: the network, a timeout, a redirect, 408 / 429 / 5xx after two more attempts), `fail_auth` (401 / 403),
    `fail_request` (another 4xx: the API changed). Every attempt counts against the budget."""
    spec = PROVIDERS[provider]
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    outcome = "fail_net"
    for attempt in range(1 + len(RETRY_BACKOFF)):
        if attempt:
            time.sleep(max(0.0, min(RETRY_BACKOFF[attempt - 1], budget.remaining())))
        if budget.stop():
            return outcome, None
        req = urllib.request.Request(spec["url"], data=raw, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
        budget.attempts += 1
        data, code = None, None
        try:
            with (opener or OPENER).open(req, timeout=max(0.05, min(PER_CALL_TIMEOUT, budget.remaining()))) as resp:
                text = _read_body(resp, budget)
            try:
                data = json.loads(text)
            except ValueError:
                code = "bad_json"
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
        except _Redirect:
            code = "redirect"
        except _TooBig:
            code = "too_big"
        except (_OutOfTime, urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            code = "net"
        cost = cost_of(provider, data) if data is not None else None
        budget.cost += RESERVE_USD if cost is None else cost
        if data is not None:
            label, confidence, probs, _reason = validate(provider, data)
            return ("ok", (label, confidence, probs)) if label is not None else ("unknown", None)
        if code in ("bad_json", "too_big"):
            return "unknown", None
        if code in (401, 403):
            return "fail_auth", None
        if code == "redirect":
            return "fail_net", None
        if isinstance(code, int) and code not in RETRYABLE:
            return "fail_request", None
        outcome = "fail_net"
    return outcome, None


Night = namedtuple("Night", "verdicts sends attempts cost_usd cache_hits outcomes providers_off stop_reason "
                            "cache_updates")


def judge(items, providers, tau, max_attempts=250, max_cost_usd=0.05, deadline=None, cache=None, now=None,
          live=False, replay=None, home=None, opener=None):
    """The night's verdicts on *items* (one per pair, sorted). A verdict in *cache* (dream.py read it) is taken
    first; with *replay* ({(provider, fact, words): recorded answer}) the recorded answers are, and nothing goes
    out; otherwise a pair goes out only when *live*. A pair goes to the first provider that is still on; when that
    one gives no usable answer (`fail_net`, `unknown`), the pair goes once to the next. A provider without a key,
    or that refused it, or whose API changed, is off for the rest of the night."""
    verdicts, sends, cache_updates = {}, [], {}
    outcomes = {p: Counter() for p in providers}
    off, keys, cache_hits, stop_reason = set(), {}, 0, None
    budget = Budget(max_attempts, max_cost_usd, deadline)
    if live and replay is None:
        for p in providers:
            keys[p] = api_key(PROVIDERS[p]["env"], home)
            if not keys[p]:
                off.add(p)
                outcomes[p]["no_key"] += 1
    for it in items:
        key = cache_key(it.pair, it.fact, it.words)
        hit = cache_get(cache, key, providers, now) if replay is None else None
        if hit:
            verdicts[it.pair] = Verdict(hit["provider"], hit["label"], hit["confidence"], hit["probs"],
                                        is_veto(hit["label"], hit["confidence"], tau), "cache")
            cache_hits += 1
            continue
        if replay is not None:
            for p in providers:
                answer = replay_answer(replay.get((p, it.fact, it.words)), p)
                if answer:
                    verdicts[it.pair] = Verdict(p, *answer, is_veto(answer[0], answer[1], tau), "replay")
                    break
            continue
        if not live or stop_reason:
            continue
        for p in providers:
            if p in off:
                continue
            stop_reason = budget.stop()
            if stop_reason:
                break
            outcome, answer = call(p, keys[p], payload(p, it.fact, it.words), budget, opener)
            outcomes[p][outcome] += 1
            verdict = None
            if outcome == "ok":
                verdict = Verdict(p, *answer, is_veto(answer[0], answer[1], tau), "live")
                verdicts[it.pair] = verdict
                cache_updates[key] = cache_record(it.fact_ref, verdict, now)
            elif outcome in ("fail_auth", "fail_request"):
                off.add(p)
            sends.append(Send(it, p, outcome, verdict))
            if verdict:
                break
        if not stop_reason and set(providers) <= off:
            stop_reason = "providers_off"
    if not stop_reason and set(providers) <= off:
        stop_reason = "providers_off"
    return Night(verdicts, sends, budget.attempts, budget.cost, cache_hits,
                 {p: dict(sorted(c.items())) for p, c in outcomes.items()}, off, stop_reason, cache_updates)


def replay_answer(row, provider):
    """(label, confidence, probs) of a recorded answer: the provider's raw `response`, or `label` / `confidence` /
    `probs` as validated before — both checked the way a live answer is. None when it cannot be used."""
    if not isinstance(row, dict):
        return None
    if "response" in row:
        label, confidence, probs, _reason = validate(provider, row["response"])
    else:
        label, confidence, probs = row.get("label"), row.get("confidence"), row.get("probs")
        if check_answer(label, confidence, probs) != "ok":
            label = None
    return (label, confidence, probs) if label is not None else None


# ---------------------------------------------------------------------------
# The cache: {"version", "entries": {key: {fact_ref, provider, label, confidence, probs, at}}}
# ---------------------------------------------------------------------------

def _h(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


def cache_key(pair, fact, words):
    """The pair (and what its origin means) plus the very text sent: an edited fact or message is asked again."""
    return _h(pair.fact_id, pair.message_key, PROVENANCE_VERSION) + ":" + _h(PROMPT_VERSION, fact, words)


def _age_days(at, now):
    try:
        when = datetime.fromisoformat(str(at))
    except ValueError:
        return None
    if when.tzinfo is None and now is not None and now.tzinfo is not None:
        when = when.replace(tzinfo=now.tzinfo)
    return (now - when).total_seconds() / 86400 if now is not None else 0.0


def cache_get(cache, key, providers, now, ttl_days=TTL_DAYS):
    """The cached verdict under *key* when a provider configured now gave it within *ttl_days*, else None."""
    rec = ((cache or {}).get("entries") or {}).get(key)
    if not isinstance(rec, dict) or rec.get("provider") not in providers:
        return None
    age = _age_days(rec.get("at"), now)
    if age is None or age > ttl_days:
        return None
    if check_answer(rec.get("label"), rec.get("confidence"), rec.get("probs")) != "ok":
        return None
    return rec


def cache_record(fact_ref, verdict, now):
    return {"fact_ref": str(fact_ref), "provider": verdict.provider, "label": verdict.label,
            "confidence": verdict.confidence, "probs": verdict.probs,
            "at": now.isoformat(timespec="seconds") if now else None}


def cache_merge(cache, updates, now, forgotten=frozenset(), ttl_days=TTL_DAYS):
    """The cache to write: the old entries still within the TTL plus tonight's, none of a forgotten fact."""
    entries = {}
    for key, rec in list(((cache or {}).get("entries") or {}).items()) + list(updates.items()):
        if not isinstance(rec, dict) or str(rec.get("fact_ref")) in forgotten:
            continue
        age = _age_days(rec.get("at"), now)
        if age is not None and age <= ttl_days:
            entries[key] = rec
    return {"version": 1, "entries": dict(sorted(entries.items()))}


# ---------------------------------------------------------------------------
# Health: {"nights": [{"date", "degraded", "reason"}], "alerted_at"}
# ---------------------------------------------------------------------------

ALERT_NIGHTS = 3
ALERT_COOLDOWN_DAYS = 7
HEALTH_KEEP = 30


def health_record(health, day, degraded, reason):
    """The health file with tonight's night: one record a date (a second run that night replaces the first)."""
    nights = [n for n in (health or {}).get("nights") or [] if isinstance(n, dict) and n.get("date") != day]
    nights.append({"date": day, "degraded": bool(degraded), "reason": reason})
    nights.sort(key=lambda n: str(n.get("date")))
    return dict(health or {}, nights=nights[-HEALTH_KEEP:])


def degraded_streak(health):
    """Degraded nights back from the last one, until two healthy nights in a row."""
    count, healthy = 0, 0
    for n in reversed((health or {}).get("nights") or []):
        if n.get("degraded"):
            count, healthy = count + 1, 0
        else:
            healthy += 1
            if healthy >= 2:
                break
    return count


def health_alert(health, now):
    """An alert when ALERT_NIGHTS degraded nights came without two healthy ones between — at most once in
    ALERT_COOLDOWN_DAYS; it stamps the health record it is given."""
    streak = degraded_streak(health)
    if streak < ALERT_NIGHTS or not (health.get("nights") or [{}])[-1].get("degraded"):
        return None
    age = _age_days(health.get("alerted_at"), now) if health.get("alerted_at") else None
    if age is not None and age < ALERT_COOLDOWN_DAYS:
        return None
    health["alerted_at"] = now.isoformat(timespec="seconds")
    reason = health["nights"][-1].get("reason") or "no verdicts"
    return {"kind": "judge_degraded", "nights": streak,
            "message": (f"The cloud judge could not judge enough pairs for {streak} night(s) ({reason}) — the rules "
                        f"decided alone. Check the API keys, the network and the judge's limits "
                        f"(`stats.judge` of the report).")}


# ---------------------------------------------------------------------------
# Review: a sample for a person to label, and the rule that decides between shadow and veto
# ---------------------------------------------------------------------------

def export_sample(records, seed):
    """(rows, entries): one judged pair per fact, chosen with *seed*; a row holds only an id, the fact and the
    words as sent, sorted by id (the order tells nothing); an entry says what the judge did with it."""
    by_fact = {}
    for rec in records:
        if rec.get("kind") == "pair" and rec.get("outcome") == "ok":
            by_fact.setdefault(str((rec.get("fact_refs") or [""])[0]), []).append(rec)
    rng = random.Random(seed)
    rows, entries = [], {}
    for fact_ref in sorted(by_fact):
        options = sorted(by_fact[fact_ref], key=lambda r: (str(r.get("pair")), r.get("provider"), r.get("night")))
        rec = rng.choice(options)
        rid = _h(seed, fact_ref, json.dumps(rec.get("pair")), rec.get("provider"))[:16]
        rows.append({"id": rid, "fact": rec.get("fact"), "message": rec.get("words")})
        entries[rid] = {"fact_ref": fact_ref, "pair": rec.get("pair"), "paths": rec.get("paths"),
                        "provider": rec.get("provider"), "label": rec.get("label"),
                        "confidence": rec.get("confidence"), "veto": bool(rec.get("veto")),
                        "changed": bool(rec.get("fact_changed"))}
    rows.sort(key=lambda r: r["id"])
    return rows, entries


def coverage_of(records):
    """{path: judged / eligible} over the night records."""
    eligible, judged = Counter(), Counter()
    for rec in records:
        if rec.get("kind") == "night":
            for path, c in (rec.get("paths") or {}).items():
                eligible[path] += c.get("eligible") or 0
                judged[path] += c.get("judged") or 0
    return {p: round(judged[p] / eligible[p], 4) for p in sorted(eligible) if eligible[p]}


def _binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def _solve(f, lo=0.0, hi=1.0):
    """The p in [lo, hi] where the decreasing f crosses zero (bisection)."""
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_upper(k, n, alpha=0.05):
    """One-sided upper Clopper-Pearson bound of k successes in n."""
    if n <= 0 or k >= n:
        return 1.0
    return _solve(lambda p: _binom_cdf(k, n, p) - alpha)


def cp_lower(k, n, alpha=0.05):
    """One-sided lower Clopper-Pearson bound of k successes in n."""
    if n <= 0 or k <= 0:
        return 0.0
    # P(X >= k) grows with p: solved as its decreasing complement
    return _solve(lambda p: alpha - (1 - _binom_cdf(k - 1, n, p))) if k < n else alpha ** (1 / n)


MAX_WRONG, MAX_LOST, MIN_COVERAGE, MAX_FALLBACK_SHARE = 0.10, 0.20, 0.90, 0.20


def _rule(rows, coverage):
    labeled = [r for r in rows if r.get("label") in ("S", "C", "U")]
    vetoed = [r for r in labeled if r.get("veto")]
    n, k = len(vetoed), sum(r["label"] == "S" for r in vetoed)
    true = sum(r["label"] == "S" for r in labeled)
    wrong_changed = sum(1 for r in vetoed if r["label"] == "S" and r.get("changed"))
    ub, lb, lost_ub = cp_upper(k, n), cp_lower(k, n), cp_upper(k, true)
    covered = bool(coverage) and all(v >= MIN_COVERAGE for v in coverage.values())
    if lb > MAX_WRONG:
        verdict = "not_ready"
    elif ub <= MAX_WRONG and lost_ub <= MAX_LOST and covered and not wrong_changed:
        verdict = "ready"
    else:
        verdict = "continue"
    return {"verdict": verdict, "labeled": len(labeled), "vetoes": n, "wrong_vetoes": k,
            "wrong_ub": round(ub, 4), "wrong_lb": round(lb, 4), "true_confirmations": true,
            "lost_ub": round(lost_ub, 4), "wrong_on_changed": wrong_changed, "coverage": coverage}


def readiness(rows, coverage):
    """The plan's rule on labeled rows ({label S/C/U/?, veto, changed, provider}): `ready` for veto when the upper
    95 % bound of wrong vetoes is at most 10 %, that of lost confirmations at most 20 %, coverage at least 90 % on
    every path and no wrong veto changed a decision; `not_ready` when the lower bound of wrong vetoes is over 10 %;
    `continue` otherwise. "?" rows (for Anton) are left out. When the fallback gave over a fifth of the verdicts,
    each provider must pass on its own."""
    out = _rule(rows, coverage)
    out["for_a_person"] = sum(1 for r in rows if r.get("label") == "?")
    labeled = [r for r in rows if r.get("label") in ("S", "C", "U")]
    shares = Counter(r.get("provider") for r in labeled)
    if len(shares) > 1 and sorted(shares.values())[-2] / len(labeled) > MAX_FALLBACK_SHARE:
        out["by_provider"] = {p: _rule([r for r in rows if r.get("provider") == p], coverage) for p in sorted(shares)}
        verdicts = {v["verdict"] for v in out["by_provider"].values()} | {out["verdict"]}
        out["verdict"] = ("not_ready" if "not_ready" in verdicts else
                          "ready" if verdicts == {"ready"} else "continue")
    return out

