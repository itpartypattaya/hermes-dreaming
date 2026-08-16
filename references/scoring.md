# Dreaming scoring (signals, weights, gates)

The scoring follows OpenClaw dreaming, adapted to the `facts` columns of the
Hermes holographic memory (`memory_store.db`). Weights, gates and windows are
tunable in the config (`~/.hermes/dreaming.json`, section `weights` / `gates` /
`windows`) or through `DREAM_*` env vars; the code has no installation-specific
constants.

## Signals (per fact) and weights

| Signal | Weight | Computed from `facts` |
|---|---|---|
| relevance | 0.30 | `0.6*trust_score + 0.4*min(helpful_count/3, 1)` |
| frequency | 0.24 | `min((retrieval_count + mentions)/6, 1)` — stored retrievals **and** chat mentions |
| query_diversity | 0.15 | `min(ref_days/3, 1)` — real number of distinct days with a mention |
| recency | 0.15 | `exp(-eff_recent/30)`, `eff_recent = min(age of updated_at, age of last mention)` |
| consolidation | 0.10 | `min(max(span_days/14, (ref_days-1)/3), 1)` — multi-day by edits **or** by mentions |
| conceptual_richness | 0.06 | `min(tags/3, 1)` |

`score = Σ weight × signal` (0..1). `dream.py --explain <fact_id>` prints the breakdown.

⚠️ Reality check: in the stock Hermes holographic plugin `retrieval_count` is
incremented only by `search_facts()` (the `fact_store` tool), **not** by the
prefetch retriever, and `helpful_count` only by explicit `fact_feedback`. On a
typical install both stay near zero — the score is carried by corroboration.
That is by design: the dream promotes what real conversations confirm.

## Gates

- **min_score = 0.55** — below that nothing is proposed.
- **min_mentions = 3** — a theme must surface in ≥3 messages to become an
  "emerging theme".
- **Corroboration gate for promotion** — besides the score, at least one
  independent sign: ≥2 distinct days of mention, ≥min_mentions messages, or ≥3
  retrievals + ≥1 helpful. A single fresh fact with high trust is not a
  candidate for durable memory by itself.

## Corroboration from conversations

Instead of a (non-existent) query log we count real reinforcement from the
transcripts (`state.db`, window `windows.corroboration_days`, default 60): for
every fact / entry take its signature tokens and look for human messages with an
overlap ≥2 tokens (or ≥1 "distinctive" token of ≥8 chars).

**Tokenizer** — Unicode words (`[^\W\d_][\w\-]*`), Latin ≥4 chars, others ≥5,
minus built-in RU/EN stopwords, `agent_names` and `extra_stopwords`. Reply
quotes and voice transcripts are unwrapped before bracket blocks are cut; image
descriptions stay metadata. **URLs are removed whole** before tokenizing (a
link is a locator, not speech; tracking params used to be "distinctive"
tokens). Harness envelopes (compaction banner, skill injection) are not human
speech and are skipped.

**Trusted sources** (fail-closed): cron sessions never corroborate (their
role=user rows are job prompts); group chats only from `trusted_chat_ids`;
private chats and legacy sessions without chat_id — per
`trust_private_chats` / `trust_sessions_without_chat` (default true). If the
`sessions` table is missing (old dump) the filter is disabled with a warning.

Outputs: **mentions** (messages), **ref_days** (distinct local days —
`timezone` from the config), **evidence** (up to 3 day+snippet pairs, the
provenance shown to the reviewer).

## Quarantine (safety filter before publication)

Every fact passes `classify_unsafe` before any output list:
- **secret** — key/token patterns (sk-, apikey_, AKIA, AIza, ghp_, xox,
  Telegram bot token, JWT, PEM, long hex) and markers `password:` / `seed
  phrase:` (RU+EN). Content is published **nowhere** — only `fact_id` + reason.
- **injection** — embedded instructions ("ignore previous instructions",
  "system prompt", "show/send… key/prompt", "save to memory that… allowed",
  jailbreak; RU+EN). A 60-char preview goes to the full JSON only.

**Reject list** (`cache/dream-rejected.json`, `dream-reject.py`) matches by
text — fingerprint or stemmed containment in both directions (a short human
rejection can close a long re-extracted fact under the same strict reverse
conditions as dedupe: 0.75 coverage, ≥8 stems, ≥6 shared). Key = text
fingerprint, never `fact_id` (sqlite reuses ids). Applied **before**
quarantine, so a quarantined fact can be closed; a rejected secret stores only
its fingerprint.

## Dedupe against durable memory

`already_in_memory` = identical head (40 chars) **or** a declared alias rule
**or** one of three stemmed-overlap directions per memory chunk (stems = first
5 chars of each token). Files: `--memory-md` plus `durable_memory_paths` from
the config.

| direction | rule | why it exists |
|---|---|---|
| fact ⊂ entry | containment ≥0.62 | a reworded fact restated inside a longer entry |
| entry ⊂ fact | ≥0.75 coverage, entry ≥8 stems, ≥6 shared | a tidy short rule is a digest of a verbose auto-extracted fact; forward containment can never reach the threshold there |
| same subject | ≥0.8 coverage of the **shorter** side, ≥4 shared, **and the fact introduces no number the entry lacks** | the entry the agent has just written **from this candidate**: the candidate keeps its reporting wrapper ("X confirmed that …"), so forward containment lands under 0.62, while the entry is too short for the digest rule |

The number condition on the third direction is what keeps supersession alive: a
candidate with a *changed* number (thread 42 → 437, a new price, a moved date)
is never absorbed as a duplicate — it surfaces as a conflict instead.

**Alias rules** (`alias_rules` in the config) are declarative:
`{"fact": [["a","b"],["c"]], "memory": [["x"]]}` means *(a or b) and c* in the
fact **and** *x* in memory. A term `#437` matches the whole number 437 only
(not 2024 or 4370). They exist because token overlap misses inflection and
synonyms in recurring rules; they are installation-specific.

## Conflicts (possible supersession)

A candidate that overlaps a memory chunk (≥4 shared stems, overlap ≥0.45) but
carries **different numbers/dates** is reported in `conflicts` with
`fact_numbers` / `memory_numbers` and `nearest_entry`. This is deliberately
computed independently of fuzzy `in_memory` — a reworded fact with a new number
is exactly what fuzzy dedupe swallows. Only an identical head or an alias rule
suppresses it. Idea from mem0 (ADD/UPDATE/NOOP) and Graphiti (edge
invalidation), done without an LLM: the agent decides, the script never
rewrites memory. 14-day display cooldown.

## What a candidate carries (and why)

Every published candidate is a small dossier, so the agent can act without
re-deriving anything:

- `why` — score plus corroboration in one line;
- `evidence` — up to 3 `{day, text}` pairs from **different** days: the actual
  human messages that corroborated the fact (provenance for the reviewer, the
  Generative-Agents "cite your sources" idea);
- `nearest_entry` — the most similar durable entry, with
  - `target` — **which file to edit**, `memory` or `user` (only the runtime pair
    is offered; read-only dedupe copies are never proposed as an edit target),
  - `entry` — a **truncated** preview for the human eye,
  - `old_text` — a **verbatim anchor** for `memory replace`, present only when
    it is unambiguous across all live entries.

`old_text` exists because the core tool matches it as a *substring of a live
entry*: a paraphrase, a fact's own text or the truncated preview never match,
and a few misses trip the core's per-turn consolidation guard, which silently
costs the whole night's writes. So the anchor is handed over ready to copy, and
`SKILL.md` makes `add` the default and `replace` conditional on having one.

## Feeding the store (`dream-extract-precheck.py`)

The dream consolidates; it does not invent candidates. In the stock holographic
plugin the fact store only grows through explicit `fact_store` calls and the
`memory add` mirror, so on a fresh install the dream can run for weeks with
nothing to do. The optional extraction gate closes that: it hands fresh human
messages (trusted chats only, chunked by `extract.max_messages` /
`extract.max_chars`, cursor in `cache/dream-extract-state.json`, first run
backfilling `extract.backfill_days`) to the agent, which stores **candidates**
via `fact_store` — never touching MEMORY.md/USER.md. Schedule it shortly before
the dream.

## Output sections

- **new_facts** — created/updated within `windows.themes_days`, no score gate,
  minus `in_memory`, minus quarantine, minus shown within the cooldown; cap
  `gates.new_facts_cap` (default 30, freshest first).
- **promotions** — score ≥ min_score, corroboration gate, not in memory,
  not ephemeral. Each carries `why`, `evidence`, `nearest_entry`.
- **conflicts** — see above.
- **fact_decays** — `retrieval_count==0` and `mentions==0`, older than
  2×window, `trust_score<=0.5`, not in memory → "seems unused" (never
  auto-deleted). Cooldown 14 d.
- **md_decays** — §-entries of MEMORY.md not seen in conversations for
  `windows.md_decay_days` (default 60) → soft "still relevant?". Conservative:
  rules can be valid without being said aloud. Cooldown
  `gates.md_ask_cooldown_days`; entries with a pin marker are skipped; only
  the published slice (`gates.publish_cap`) is marked as asked.
- **emerging_themes** — frequent tokens of the window not present in facts or
  memory. Never in the prompt (noise), only in the full JSON.
- **ephemeral_events** — dated tests/deadlines that passed scoring but are
  filtered out of promotions. **Expired events are dropped before the split
  into sections** (RU/EN/Thai dates, Buddhist years, ambiguous 5/10 counted as
  past only under every reading, relative anchors never expire).
- **alerts** — `memory_loss`: more than `memory_loss_alert_fraction` (25%) of
  the entries present at the previous pass are gone (snapshot in
  `cache/dream-snapshot.json`). Wakes the agent; nothing is restored.
- **memory_usage** — chars per durable file and % of `memory_char_limits`.

## Cooldowns and state

`cache/dream-asked.json` (md_decays), `cache/dream-seen.json` (new_facts,
fact_decays, conflicts — keyed by text fingerprint, marked only after the
caps, pruned after two cooldowns), `cache/dream-rejected.json`,
`cache/dream-snapshot.json`. All 0600; broken JSON is fail-soft.

## Wake gate (`dream-precheck.py`)

The pre-check runs the dream and prints a compact JSON only when
`stats[key] > 0` for one of `precheck.actionable_keys` (default:
new_facts_reviewed, promotions, fact_decays, md_decays, conflicts,
quarantined, alerts); otherwise `{"wakeAgent": false}`. Themes and ephemeral
events never open the gate. On failure — `{"dream_error": …}` with exit 0
(exit≠0 would make the scheduler ignore the gate and paste the raw stderr).

## Notes

- **Write budget and failure reporting** live in `SKILL.md`, not in the script:
  a ceiling of changes per night ("zero writes is a success"), `add` by default,
  one retry with a substring copied from the tool's `current_entries`, and the
  rule that a failed write is reported as a failed write — the "memory
  temporarily unavailable" phrase is reserved for the exact core error of that
  name, because anything else sends the human debugging the wrong thing.
- `memory_usage` reports the fill level of each durable file against
  `memory_char_limits`; keep those in sync with the agent's own config, or the
  percentage in the report drifts from reality.
- Read-only by memory: the script writes only its own state, `--out` and the
  diary (`diary.heading`, one section per local day, rotation to
  `*.archive.md` after `diary.keep_sections`).
- Env vars (override the config): `DREAM_CONFIG`, `DREAM_TIMEZONE`,
  `DREAM_TRUSTED_CHAT_IDS`, `DREAM_MIN_SCORE`, `DREAM_MIN_MENTIONS`,
  `DREAM_NEW_FACTS_CAP`, `DREAM_SEEN_COOLDOWN_DAYS`, `DREAM_MD_ASK_COOLDOWN_DAYS`,
  `DREAM_WINDOW_DAYS`, `DREAM_CORR_DAYS`, `DREAM_MD_DECAY_DAYS`, `DREAM_DIARY_KEEP`,
  `DREAM_ASKED_STATE`, `DREAM_REJECTED_STATE`, `DREAM_SEEN_STATE`, `DREAM_SNAPSHOT_STATE`.
- The nightly cron session usually has only the `memory` toolset:
  `dream-reject.py` cannot run there, so mechanical repeats are closed by the
  script's cooldowns, not by agent discipline. `allowed-tools` in `SKILL.md`
  grants nothing — it is a declaration.
