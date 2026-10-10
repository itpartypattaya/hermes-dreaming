# Changelog

All notable changes to hermes-dreaming. Dates are the release day; the version
is the one in `plugin.json`.

## 2.3.0 — 2026-10-11

### Added

- **Keywords for the fact store** (`keywords.enabled`, off by default). holographic ≥ 0.6.0 finds a
  fact by words it does not contain — synonyms, other roots, translations — only through keywords
  kept with the fact. Facts that have none are now listed in `keywords_needed`, the most recalled
  first, at most `keywords.cap` (10) a night, each with a ready `fact_store update` call; one the
  agent found nothing for rests like any shown item. The section rides along when the agent is
  awake and never wakes it. With the flag on, the extraction payload carries `keywords`, and new
  facts get theirs at `add`. Turn it on with holographic ≥ 0.6.1: an older provider accepts the
  parameter and drops it, and 0.6.0 stamped a fact "changed now" when it got keywords, which would
  have brought old facts back as new ones. Checked end to end on a copy of a live store with the
  real provider: the fact got its keywords, search found it by them, it left the list and its
  `updated_at` stayed.

### Fixed

- **Keywords no longer score as tags.** holographic keeps keywords in the `tags` column after a
  `keywords: ` line, and the pass split tags on commas: a fact with eight synonyms counted as
  "conceptually rich" and climbed towards promotion. The keywords are split off when the store is
  read; the score and the published `tags` carry the human's tags only.

## 2.2.1 — 2026-10-08

Checked against Hermes **v0.21.6** (tag `818c13be`), released the same day. The
skill needed no adaptation — the cron wake gate, `skip_memory` for cron jobs,
the pending-record shape and the memory limit keys are unchanged, and the
`messages` table only gained a column (the loader reads columns through PRAGMA
anyway). The audit did surface two gaps of our own, both of which also exist
against 0.21.5:

### Fixed

- **A staged `operations[]` batch was invisible to the pass.** The core stages a
  batch as ONE pending record whose payload carries `action: "batch"` and the
  ops inside, and `load_pending_writes` read only the top level: the record
  looked like an empty write, `_staged_text` never saw its content, and the pass
  would offer the same candidates again the next night — the exact failure this
  reader exists to prevent, and batches are what step 4a asks the agent for.
  Ops are now expanded, each carries the `record_id` of its queue record, and
  the queue alert names both ("6 memory write(s) in 1 queued record(s)") so the
  numbers agree with what a human sees in `/memory pending`. A batch whose ops
  cannot be read still contributes its text instead of silently reading as
  "nothing is waiting".
- **"The error carries `current_entries`" is not true for a batch.** A failed
  batch answers without the inventory on purpose (echoing it grew the context
  the consolidation was called to shrink); 0.21.6 adds up to three
  `closest_entries` instead. `SKILL.md` and the installer's cron prompt now say
  so and tell the agent to re-issue the ops one at a time rather than guess a
  new anchor.

### Tests

- `core_replace` in the suite now matches the real core: an exact whole-entry
  match wins absolutely, identical duplicate entries are not ambiguity, and
  0.21.6's folded-typography fallback sits behind a `fold` flag so both editions
  are covered.
- A contract test asserts that every anchor `_replace_anchor` produces addresses
  exactly one entry **under the core's rules**. Pinned along the way: the core's
  folding does **not** cover the Russian guillemets `«»`, so an anchor still has
  to be verbatim — the 0.21.6 tolerance barely helps non-Latin memory.

**Tests:** 321 (was 309).

## 2.2.0 — 2026-10-08

An external review of 2.1.1 (another agent read the code against the Hermes
0.21.5 source, ran the suite and a pass on synthetic Russian data, and had every
finding re-checked by separate sceptics) produced 17 fixes. All of them are in,
plus three ideas taken from the same review. Backwards compatible: no config key
changed meaning, and old state files are still read.

### Fixed — the memory model

- **The unit of memory is the §-entry, not the paragraph.** The pass used to
  split memory files on blank lines as well, so an anchor could come from the
  middle of a multi-paragraph entry — and `memory replace` rewrites the whole
  entry it finds. On a live `MEMORY.md` one "correct" replace cut the file from
  6 261 to 996 characters. Parsing now mirrors the core exactly (`\n§\n`,
  `utf-8-sig`), with one tolerance: a delimiter at the very start of a file.
- **Anchors are raw.** They used to be handed over with collapsed whitespace
  while the core matches `old_text` as a literal substring: 5 of 56 anchors on a
  live file matched nothing, and every miss counts toward the core's per-turn
  failure guard. An anchor is now a verbatim single line, unique among entries.
- **`nearest_entry` says what is at stake:** `full_entry` (the exact text about
  to be overwritten, also the value for `matched_entry`), `entry_chars`,
  `multi_section`, `replace_unsafe` for an entry too long to show in full.
- **The loss guard counts characters too**, not just entries, and notices an
  entry that kept its beginning and lost its body. It now also works on files
  with only a few entries.

### Fixed — what counts as a human saying something

- Role and visibility are filtered **in SQL** (`role='user' OR observed=1`,
  `active=1 OR compacted=1`). On a busy install that is tens of thousands of
  rows and ~0.5 GB of RSS saved; on a small one the pass still got 3× faster.
- Compression copies the row it folds and both copies are visible: rows are
  deduplicated by `(session_id, content, timestamp)`. One compaction used to add
  +0.04 to a fact's score.
- `untrusted_sources` defaults to `cron`, `subagent` and `tool` — the core
  groups them the same way, and a subagent brief reads exactly like a person
  talking. New `exclude_patterns` drops machine text that has no source of its
  own (bridge briefs, forwarded digests) by regex.
- Harness inserts are recognised by their exact strings from Hermes 0.21.5 (the
  preserved todo snapshot, both turn-failure notices, `Operation interrupted`)
  and matched anywhere in the text, because the core glues the todo snapshot
  into a real reply.
- A reply quote counts only when the person added words of their own, and a
  quoted machine report never counts: a bare "ок" under a quoted cron report
  used to corroborate everything the report mentioned, including the entries it
  proposed to delete.

### Fixed — Russian

- Corroboration compares **stems**: «Мы окончательно переехали, в Лиссабоне…»
  now corroborates «переехал в Лиссабон», while «Мой брат переехал в Москву»
  does not. Both were wrong before, in opposite directions.
- `_stem()` strips frequent endings instead of cutting to 5 characters, so
  «контент» and «контейнер» are no longer one stem.
- Text is NFC-normalized with ё→е before tokenizing.
- A single shared word is "distinctive" at ≥8 characters for Latin but ≥10 for
  anything else — in Russian 8 is not rare, and one shared «обязательно» was
  enough for a false mention. Frequent Russian words of 5+ letters joined the
  stopword list.
- Negations and comparatives (`не`, `нет`, `без`, `более`…) stay significant for
  the store-duplicate check: «ест острое» and «не ест острое» are opposite facts
  that used to collapse into one.

### Fixed — numbers and dates

- `25 000`, `25\u00a0000`, `25,000` and `25000` are one number: every retelling
  of a price used to look like a conflict.
- A month said in words is compared like a number, so moving «15 марта» to
  «15 апреля» becomes a possible update instead of being swallowed by the fuzzy
  dedupe as "already in memory".

### Fixed — screening

- More key shapes: `gsk_` (Groq), `ntn_`/`secret_` (Notion), `sk_`+hex,
  `gho_`/`ghs_`/`ghu_` (GitHub).
- A credential marker no longer has to touch the colon: "Пароль от wifi в
  квартире: X" and "Password for the router is: X" are caught, while "сменила
  пароль от Wi-Fi" stays an event, not a secret.
- Injection patterns match word stems and include the four the core's own cron
  scanner uses, so quarantine happens before Hermes blocks the whole prompt.
- The extraction job screens the messages it hands over (that was the one door
  into the prompt nobody watched), and a "still relevant?" preview of an entry
  that itself holds a credential is redacted.

### Fixed — operations

- **Writes waiting for approval are read.** With `memory.write_approval: true` a
  cron write answers `staged: true` and the file does not change: the queue
  (`pending/memory`) is now treated as a second memory file, so a queued
  candidate is not offered again, an entry whose removal is queued is not asked
  about, and a queue of ≥5 items older than 7 days raises one alert.
- Promotions get a short rest (`gates.promotion_cooldown_days`, 3 days). They
  used to repeat every night "until written", which under an approval gate meant
  forever.
- Char limits and the timezone come from Hermes' `config.yaml` when the skill
  does not set them. The example's numbers are the core defaults, and an install
  with raised limits was told "memory at 285%" every night.
- Acknowledgement counts only a real answer: no `tool_calls`, not
  `display_kind=failed_turn`, not a harness notice. "Operation interrupted."
  used to pass for one.
- The extraction job checks `memory.provider`, not just `fact_source`: without
  the provider there is no `fact_store` tool, and up to 200 messages were handed
  to a model that could not store them while the cursor moved past them.
- Expired dated events are listed in their own read-only section instead of
  vanishing, and the hint list covers meetings, calls, flights and appointments
  — not only school words.
- The wake gate survives a malformed `dreaming.json` (it used to exit 1 with a
  traceback, so Hermes woke the agent with "Script Error"), and `dream_error`
  now carries the tail of the pass's stderr instead of a bare exception repr.
- `install.py --check` warns that the holographic provider writes through
  `fact_store` — a channel the approval gate does not cover.
- The diary path moved into the config (`diary.path`), state files keep their
  owner when written from `docker exec` as root, and a non-local `--deliver`
  attaches the job to a session.

### Added

- **`trust_feedback`.** A fact a human rejected with `dream-reject.py` keeps its
  trust in the store, so the holographic provider's prefetch goes on injecting
  it into ordinary turns — the rejection taught the dream, not retrieval. The
  pass now lists such facts (trust at or above `gates.feedback_trust_floor`,
  0.3) with a ready `fact_feedback(action="unhelpful", fact_id=…)` call for the
  agent. The script still never writes the store; the section is deliberately
  **not** in `precheck.actionable_keys` (worth doing while the agent is awake,
  not worth a wake), capped by `gates.feedback_cap` and rested by its own
  cooldown. Only for `fact_source: holographic` — no other source has the tool.
- `matched_entry` and the atomic `operations[]` batch are now part of the
  instructions: two or more changes go in one all-or-nothing call whose char
  limit is checked against the final state, so freeing space and adding an entry
  fit together.
- `stats.pending_writes`, `staged_suppressed`, `promotions_suppressed`.

### Documented

- The holographic provider **leaves the Hermes core on 2026-10-15** (its
  standalone copy is currently unmaintained). `fact_source: holographic` is
  still the default and still works — it just has to be installed as a plugin
  after that date. Both READMEs and `install.py --check` say so, and
  `fact_source: none` remains the zero-dependency option.

### Not confirmed

The review also reported that a CRLF memory file parses as a single entry. It
does not: the core reads memory through `read_text`, i.e. with universal
newlines (verified against 0.21.5). A test now pins that.

**Tests:** 309 (was 255).

## 2.1.1 — 2026-10-03

Catalog-review follow-up: stale job bindings, the gate's own home detection,
tests against the real registry.

## 2.1.0 — 2026-10-03

No core patching any more: the cron jobs run the plugin's own skill.

## 2.0.0 — 2026-10-02

Agent Plugins v1 package (`plugin.json` + `skills/dreaming/`), a configurable
fact source (`holographic` / `sqlite` / `jsonl` / `none`), `dream.py --dry-run`,
`scripts/install.py`. Answered "still relevant?" questions stay answered, and
the extraction cursor waits for the agent's acknowledgement.

## 1.x — 2026-08-16 … 2026-09-17

First public release: the deterministic nightly pass, the wake gate, the reject
list, cooldowns, the loss guard, `excluded_threads` and `untrusted_sources`,
waking on a full memory file, and the first external-review round (evidence
hygiene, the `(ts, id)` extraction cursor, cooldown acknowledgement,
fail-closed trust).
