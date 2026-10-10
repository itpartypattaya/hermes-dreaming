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
| conceptual_richness | 0.06 | `min(tags/3, 1)` — the human's tags only; holographic keywords (after the `keywords: ` line) are not counted |

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
overlap of **≥2 stems**, or ≥1 "distinctive" stem (a token of ≥8 chars for
Latin, ≥10 for anything else — in Russian 8 characters is not rare, and one
shared «обязательно» was enough for a false mention).

**Stems, not word forms.** Russian inflects, so exact comparison missed the
same statement said differently («переехали» / «переехал», «Лиссабоне» /
«Лиссабон»). `_stem()` strips the frequent endings and then cuts to 6 chars
(5 for Latin) — a plain prefix cut collapsed «контент» and «контейнер».

**Tokenizer** — Unicode words (`[^\W\d_][\w\-]*`) over NFC-normalized text with
ё→е, Latin ≥4 chars, others ≥5, minus built-in RU/EN stopwords, `agent_names`
and `extra_stopwords`. Short words (`Илья`, `сын`, `дом`) do drop out — raise
`token_min_len` if that matters more than the noise it brings back. Reply quotes
and voice transcripts are unwrapped before bracket blocks are cut, **but a quote
counts only when the person added words of their own** (a bare "ок" under a
quoted cron report used to corroborate everything the report mentioned), and a
quoted machine report never counts. Image descriptions stay metadata. **URLs are
removed whole** before tokenizing (a link is a locator, not speech; tracking
params used to be "distinctive" tokens). Harness inserts are not human speech
and are skipped: the compaction banner, the skill injection, the preserved todo
snapshot and both turn-failure notices — matched anywhere in the text, because
the core glues the todo snapshot into a real reply.

**Role and visibility** are filtered in SQL, not in Python: `role='user' OR
observed=1` and `active=1 OR compacted=1` (a rewound row is not speech). Rows
are then deduplicated by `(session_id, content, timestamp)` — compression keeps
a copy of the row it folds, and both copies are visible, so one compaction used
to add +0.04 to a fact's score.

**Trusted sources** (fail-closed): machine-paced sessions never corroborate —
`untrusted_sources` defaults to `cron`, `subagent` and `tool` (a subagent brief
reads exactly like a person talking, and the core groups them the same way);
`exclude_patterns` drops machine text that has no source of its own (bridge
briefs, forwarded digests) by regex on the content; group chats only from
`trusted_chat_ids`;
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
- **injection** — embedded instructions: telling the reader to disregard
  its earlier rules, mentions of the system prompt, requests to show or send a
  key or the prompt, "save to memory that … is allowed", jailbreak phrasing;
  RU+EN. A 60-char preview goes to the full JSON only.

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
  Generative-Agents "cite your sources" idea). A corroborating message that
  itself trips the secret / injection classifier still counts as a mention but
  never travels as evidence — the fact was screened, its witnesses must be too;
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
- **near-duplicates in the store** — the store only has `UNIQUE(content)`, so
  one statement extracted twice ("X said that …" / "X said: …") is two rows.
  In `promotions` and `new_facts` one representative per group is shown, the
  rest go to its `duplicates` (ids) and `stats.store_duplicates`; hidden twins
  are marked shown with it. Stricter than memory dedupe: every word one side
  lacks must be a stopword or an inflection of the other's word, so short
  codes ("profile de" / "kz"), numbers and dates keep facts apart. Nothing is
  deleted from the store.
- **conflicts** — see above.
- **fact_decays** — `retrieval_count==0` and `mentions==0`, older than
  2×window, `trust_score<=0.5`, not in memory → "seems unused" (never
  auto-deleted). Cooldown 14 d.
- **expired_events** — dated events whose date has passed. Read-only (never in
  `precheck.actionable_keys`, so they open no gate): they used to disappear from
  every section without a trace, which also swallowed durable facts that merely
  contained a trigger word.
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
- **memory_pressure** — present only when a file is at or above
  `precheck.memory_full_pct`: `[{file, percent, chars, limit}]`, worst first. The
  prompt keys its consolidation instruction off this field, not off raw percentages.

## The unit of memory is the §-entry

The core splits a memory file on the FULL delimiter `\n§\n`, strips each entry
and drops empty ones (`tools/memory_tool_store.py:_parse_entries`), reads it as
`utf-8-sig`, and `memory replace` **replaces the whole entry it finds** —
`old_text` only locates it. This script mirrors that exactly (plus one
tolerance: a delimiter at the very start of a file, which a hand edit often
leaves).

That is why `nearest_entry` ships:

| field | why |
|---|---|
| `entry` | preview, 160 chars — never usable as `old_text` |
| `entry_chars` | the entry is bigger than the preview |
| `multi_section` | more than one line: `new_content` must carry all of it |
| `full_entry` | the exact text about to be overwritten (≤1200 chars), also the value for `matched_entry` |
| `old_text` | a verbatim, raw, single-line anchor unique among entries |
| `replace_unsafe` | too long to show in full → do not replace blind |

Splitting on blank lines (as versions up to 2.1.1 did) produced anchors from
the middle of an entry and anchors with collapsed whitespace: on a live
MEMORY.md one "correct" replace cut the file from 6 261 to 996 characters, and
5 of 56 anchors matched nothing at all.

## Writes waiting for approval

With `memory.write_approval: true` a cron write answers `success: true,
staged: true` and the file does not change. The pass reads
`pending/memory/*.json` (`pending.dir`), treats those texts as a second memory
file for dedupe, and therefore:

- a candidate already queued is not offered again (`stats.staged_suppressed`);
- an entry whose removal is queued is not asked about, and does not become
  "confirmed" by waiting;
- promotions get a short rest after being shown (`gates.promotion_cooldown_days`,
  3 days) — they used to repeat every night "until written", which under an
  approval gate meant forever;
- a queue of ≥`pending.alert_min` items older than `pending.alert_days` raises
  one `pending_writes` alert, then rests for the usual cooldown. It is the
  human's job, not the agent's.

## Cooldowns and state

`cache/dream-asked.json` (md_decays) holds `{at, confirmed?, stems}` per entry
key (a legacy bare date reads as `{at}`). An entry still present unchanged when
`md_ask_cooldown_days` runs out is **confirmed** — "not relevant" is answered by
removing or rewriting the entry, so keeping it is an answer too. So is a
**rewrite**: the key hashes the text, and the usual reply to "still relevant?"
is a fresh "as of <date>" stamp — before 2026-10-02 that new text was a new
question the very next night. An entry without a record of its own inherits the
record of a vanished asked entry on the same subject (≥4 shared stems, ≥0.7 of
the shorter side; numbers ignored) and is confirmed by that. A confirmed entry
rests `md_confirmed_cooldown_days` (90). Records of vanished entries live until
their own cooldown ends — long enough for a rewrite to find them.

`cache/dream-seen.json` (new_facts,
fact_decays, conflicts — keyed by text fingerprint, marked only after the
caps, pruned after two cooldowns), `cache/dream-rejected.json`,
`cache/dream-snapshot.json`. All 0600; broken JSON is fail-soft.

**Acknowledgement.** A mark is provisional until an agent turn confirms it.
`dream-seen.json` carries a `_pending` record of the last pass (`generated_at`
plus the keys it showed); on the next pass `_agent_acked()` looks for the cron
session whose prompt embeds that `generated_at` and checks it ended with a
non-empty assistant message. No such session (manual run, gate closed by an
outside cause) or a session without an answer (model / memory-tool failure) →
the marks are dropped and the `md_decays` questions reopened, and the items
return that night. No `state.db` or no `messages` table → treated as
acknowledged (old behaviour) so a host that does not persist sessions does not
re-show forever.

**Fact store location.** `fact_store_path()` resolves, in order (env beats
config, as everywhere): `$DREAM_FACT_STORE` → `fact_store_path` in `dreaming.json` → Hermes
`plugins.hermes-memory-store.db_path` from `config.yaml` (PyYAML if present,
else a narrow scanner) → `$HERMES_HOME/memory_store.db`. `$HERMES_HOME`, `~`
and relative paths expand the way the provider expands them. The pass, the
extraction gate and `dream-reject.py` all go through it.

**Extraction cursor** (`cache/dream-extract-state.json`) is `(since_ts,
since_id)`: rows can share a timestamp, and a chunk cut between two of them
must not lose the remainder. A legacy state without `since_id` is read as
"before every id" — at worst a few rows are handed over twice, deduped by
`existing_facts`.

The cursor is committed by the agent's answer, the same way as the display
cooldowns: the payload carries `generated_at`, the chunk's end waits in
`pending`, and the next run commits it only if `_agent_acked()` finds the cron
session with that stamp ending in an assistant message (`[SILENT]` counts).
No answer → the same chunk again; after `extract.max_retries` (3) unanswered
hand-overs the cursor moves on with a note on stderr, so a chunk that kills the
turn every time cannot stall extraction. Before this a failed turn silently
skipped its chunk.

## Wake gate (`dream-precheck.py`)

The pre-check runs the dream and prints a compact JSON only when
`stats[key] > 0` for one of `precheck.actionable_keys` (default:
new_facts_reviewed, promotions, fact_decays, md_decays, conflicts,
quarantined, alerts), **or** when a durable file sits at or above
`precheck.memory_full_pct` (`memory_pressure`); otherwise `{"wakeAgent": false}`.
Themes and ephemeral events never open the gate.

The fill-level gate exists because the core's char limit guards **writes only**:
at the ceiling `add`/`replace` are refused and the agent silently stops
recording — no exception, nothing logged, and the prompt still loads fine. On a
quiet night the promotions gate stays shut, so without this clause nobody is
awake to see 95 % coming. It is self-limiting: once consolidated below the
threshold it closes again. On failure — `{"dream_error": …}` with exit 0
(exit≠0 would make the scheduler ignore the gate and paste the raw stderr).
A `dream.py` that does not return within `precheck.timeout_sec` (600) is such
a failure too — a locked `state.db` must not hold the job until the
scheduler's own script timeout (an hour) and its raw alert.

## Notes

- **Write budget and failure reporting** live in `SKILL.md`, not in the script:
  a ceiling of changes per night ("zero writes is a success"), `add` by default,
  one retry with a substring copied from the tool's `current_entries`, and the
  rule that a failed write is reported as a failed write — the "memory
  temporarily unavailable" phrase is reserved for the exact core error of that
  name, because anything else sends the human debugging the wrong thing.
- `memory_usage` reports the fill level of each durable file against
  `memory_char_limits`; keep those in sync with the agent's own config, or the
  percentage in the report drifts from reality. Measure with the core's counter
  (entries joined by the delimiter) — `wc -c` counts bytes, and non-Latin text
  takes two per character, so it overstates the fill by up to 2×.
- Removing an entry from durable memory does not remove the fact from the
  store: it becomes `in_memory: false` and is proposed again on the next pass.
  Put deliberately dropped facts on the reject list (`dream-reject.py`), or a
  manual cleanup undoes itself overnight (live case: 30→13 entries, and the very
  next pass offered 29 promotions — all of them the deleted ones).
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
