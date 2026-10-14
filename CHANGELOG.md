# Changelog

All notable changes to hermes-dreaming. Dates are the release day; the version
is the one in `plugin.json`.

## 2.7.1 — 2026-10-14

Checked against holographic **0.14.3** (the pass was paired with 0.13.0). The store's `facts` table
is unchanged, history and tombstones were already supported, and the provider's new search by
meaning applies the same trust floor to recall (`by_meaning(..., min_trust=…)`), so `trust_feedback`
still takes a rejected fact out of ordinary turns. Two things of ours were wrong.

### Fixed

- **The recall floor is the provider's, not a constant of ours.** holographic recalls nothing below
  its `min_trust_threshold` — words, structure and meaning alike — and that threshold is
  configurable (its default is 0.3). The pass and the nightly prompt named a hard-coded 0.3, so on
  a store tuned to 0.5 the `trust_feedback` section offered facts retrieval already ignored and the
  report named the wrong number. The floor is now the explicit `gates.feedback_trust_floor`, else
  the provider's value, else 0.3; the config default became the sentinel `null` ("ask the
  provider"). A provider config that cannot be parsed falls back to 0.3 rather than "offer
  everything". The effective floor travels in the payload's `gates`, and the prompt points at it
  instead of a number.

### Added

- **`install.py --check` knows the 0.14 settings.** `embeddings: on` → one line saying the pass
  itself still reads words only and that keywords remain the offline path when the endpoint is
  unreachable; `mirror_memory_writes: true` → a warning, because then the pass's own promotions come
  back as new facts and prefetch injects what the system prompt already holds in full (the
  provider's default is false); a non-default `min_trust_threshold` is named.
- Documentation: what keywords and search by meaning each do, and that the provider can name the
  gap itself — `fact_store enrich` lists facts without keywords, `stats` counts them as
  `without_keywords`.

**Tests:** 438 (was 428).

## 2.7.0 — 2026-10-13

From a review of what people report about agent memory (bugs and requests on GitHub, mem0, Graphiti).

### Added

- **A rejection is asked about again after half a year** (`reject_reask_days`, 180; `0` = never). The reject list
  silenced a fact forever, so a fact that became true again ("no longer does ballet" in May, back at it in
  September) never returned — mem0#7612 is the same trap with expiry. Now a rejection older than the setting is
  reopened when the fact was said again after it: a store row created after the rejection and a human mention
  since. The item carries `previously_rejected: {at, reason}` and the agent asks rather than writes; a second "no"
  starts the count over. Younger rejections stay silent whatever happens: "said again" cannot be told from the core
  re-extracting the same stale fact while people still talk about it — the case the list exists for.
- **Probes** (`probes`, empty by default): phrases that must stay in the memory files — by their text, or every word
  in one sentence of an entry, in any form. One `probe_missing` alert when one disappears (the missing ones are kept
  in the snapshot; without a snapshot state probes are not checked), again only if it comes back and goes again. The
  loss guard catches a mass loss; a single entry merged away while memory was tidied at its limit was under its
  threshold.

### Fixed

- **A day of the week counts as a number.** "Ballet moved to Tuesdays" against the entry "ballet on Mondays" shared
  every word, so the fuzzy dedupe called it "already in memory"; a weekday is now a marker like a month in words,
  and the move is a `conflict` (an update to ask about). Eleven languages plus Chinese, Japanese and Korean;
  ambiguous words are left out (Turkish `pazar`, Indonesian `minggu`, Polish `środek`; Russian by its forms, so
  «рабочая среда», «субботник» and «воскресение» are no weekdays). Abbreviations (`Tue`, `Wed`) are not read: `sat`,
  `wed`, `sun` and French `mon` are ordinary words; and `周` + a numeral may be a name (周一鸣). Reviewed by Codex CLI
  before release.
- **Two dates of one recurring event are no longer store duplicates.** In the near-duplicate check `2026-10-20`
  and `2026-09-15` shared the five-letter stem `2026-` and counted as one word in two forms, so one appointment
  was hidden behind the other; a word with a digit is never an inflection now.

## 2.6.1 — 2026-10-13

Fixes from an audit of 2.4.0–2.6.0.

### Fixed

- **The forget-sweep gate no longer waits for a running pass.** `dream-forget-sweep.py` runs `dream.py
  --forget-sweep` under a 120 s timeout, and the pass holds the state lock for its whole run (up to
  `precheck.timeout_sec`): a long pass crossing a gate tick killed the gate, and Hermes delivered a "cron
  watchdog script failed" alert for nothing. The gate now takes the lock without waiting and ends quietly when
  the pass holds it — the pass sweeps before it starts and scrubs its output after, so nothing is left for the
  gate; the next tick looks again.
- **An unusual `plugins:` shape in config.yaml no longer ends the night.** `plugins:` as a list, or
  `hermes-memory-store` as a bare value, raised inside the pass (`dream_error`) and in `install.py --check`;
  such shapes now read as "unset".
- **`install.py --check` warns when the forget-sweep cron job is missing** (jobs created before 2.4.0), and
  `install_cron.py` refuses to create the job while `scripts/dream-forget-sweep.py` is not in place (the job
  would fail at its first tick); `--sweep-schedule ''` skips both.

### Documentation

- With `scope: user`, a promoted user's facts are corroborated by the messages of that user's own sessions —
  Hermes keeps no author per message, so in a group the session owner stands for the author (README).

## 2.6.0 — 2026-10-13

### Added

- **Per-chat fact stores** (`chat_stores`). holographic with `scope: user / chat / thread` keeps a database per
  user / chat / topic, which the dream did not read. Two separate switches now: `read` lists every database in
  the report and the diary as counts (facts, new in the window; counted by SQL) — no text, no ids; `promote_from`
  (`"telegram:-100123"`, `"telegram:-100123:42"` for a topic, `"telegram:12345"` for a user, or the file name) makes
  the facts of those chats candidates like the shared store's. Each such fact is corroborated only by messages of
  its own chat (topic; for a user, sessions of that user), and a listed chat's messages that the trust rules leave
  out count for nothing else. Their ids are `"<store>#<id>"` and items carry `store`: the agent writes them only
  through `memory` (a cron session cannot reach a chat's database), they get no `keywords_needed` or
  `trust_feedback`, and their tombstones are swept like the shared store's (`--explain` and `dream-reject.py` take
  the composite id). Off by default: nothing changes for a single shared store (the output gains no keys, and
  `fact_store_note` from 2.5.3 now appears only when there is a note). Reviewed by Codex before release: the sweep
  remembers every store it was given (a chat leaving the list is still swept), one unreadable chat database no
  longer stops the others, message keys strip ids as the plugin does, a forgotten fact leaves `duplicates` too,
  chat facts are indexed without a shared store, `dream-reject.py` resolves a chat id without reading the shared
  store, and the overview counts by SQL instead of reading every text. Trust rules are unchanged — a trusted chat
  (or, with `trust_private_chats`, a private one) still corroborates shared facts; the README says how to set a
  multi-tenant bot.

### Changed

- Test fixtures no longer use the maintainer's family names.

## 2.5.3 — 2026-10-13

### Added

- **The dream says when the fact store cannot fill up.** holographic with `scope: user`, `chat` or `thread` keeps
  facts in a database per user / chat / topic, which the dream does not read (it would carry one chat's facts
  into memory every chat sees), and a cron job — no chat — writes nothing, so the weekly extraction stored nothing
  without a word. With `cron_writes: false` a cron job is read-only. Now the report (`fact_store_note`), the diary
  and `install.py --check` name the reason, and the extraction gate does not wake the model, keeping its cursor.
  The memory-file review is unaffected; `fact_source: none` makes it explicit.

### Changed

- Examples in comments, tests and this changelog use fictional names only.

## 2.5.2 — 2026-10-13

Fixes from a second external review (Codex) of 2.5.1, each with a test. It confirmed 7 of the 8 earlier findings
fixed and one partly, and found what the 2.5.1 fixes broke.

### Fixed

- **"Since <date>" hid real events.** 2.5.1 treated a whole fact as durable when "since / с / od / dal …" and a
  date appeared anywhere in it, so a range («Экзамен с 15.11.2026 по 17.11.2026», «Egzaminy od 14 listopada do 16
  listopada»), a time («с 15.00») or an unrelated clause ("Call on 14 November; Tom has worked here since 2020")
  was no longer an event. Now only the "since <date>" phrase itself is taken out before the check, a range stays
  an event, and a day-month in digits needs a real month (15.00 is a time).
- **Deadlines with dashes** ("by 14-11-2026", «до 14-11-2026») were missed by the 2.5.1 deadline pattern.
- **Doctor visits** ("see a doctor", "doctor visit", «врач придёт», «вызвать врача») were lost when 2.5.1 narrowed
  "doctor" / «врач» to keep professions durable.
- **Chaining shared n-grams** was still quadratic when one character joins hundreds of pairs (一中 … 中一); it is
  linear now (pairs are joined through the character they share), with the same stretches as before.

Measured: the language benches, the blind sets and the author's live household data give the same numbers as 2.5.1.

## 2.5.1 — 2026-10-13

Fixes from an external review of 2.5.0 (Codex), each with a test in `tests/test_languages.py`.

### Fixed

- **A month name in capitals crashed the pass**: «14 KASIM 2026» matched the Turkish month case-insensitively,
  but its lower case was not a key — `KeyError`, and the night's dream failed. Month names are looked up without
  case and diacritics.
- **A profession with a date was a one-off event**: "Tom is a doctor since 14 November 2020" and «Илья врач с
  14 ноября 2020 года» were recognised as a visit that had passed, so the fact could never be promoted. "doctor" and
  «врач» are event words only as a visit ("to the doctor", «к врачу»), and nothing "since <date>" (since, с, seit,
  desde, depuis, dal, sinds, od, sejak, «'den beri») is a one-off.
- **A deadline needs a whole date**: "can do 20 pushups" was Polish «do 20.»; the deadline pattern now wants a day
  with a month («до 1 декабря», «bis 30. Nov», 30/11). Its old open-ended form before Turkish «kadar» took
  quadratic time on long numbers.
- **Japanese particles** (には, ので) counted half a word each and four of them made a mention; a pair of
  function characters is dropped before the kana rule.
- **English content words** «solo» and «fare» were Spanish / Italian stopwords: "solo travel" and "travel" were one
  fact for the store-duplicate check.
- **Shared n-grams** were chained by comparing every pair with every other (quadratic on long Chinese texts); they
  are indexed now, and two shared words decide before any chaining.
- **"Jane 14" and "Mary 5"** were still January and March dates; English month names are matched whole.

Measured: the language benches, the blind sets and the author's live household data give the same numbers as 2.5.0.

## 2.5.0 — 2026-10-13

Every language the holographic store reads (0.11.0 reads 18). The dream compared words with its own
code, written for Russian and English: a Chinese, Japanese or Thai sentence was one token, so nothing
in these languages was ever corroborated, and a dated appointment was recognised as a one-off event
only in Russian, English and Thai.

### Changed

- **Chinese, Japanese, Korean, Thai** (and Lao, Khmer, Myanmar) are compared by character n-grams,
  as the holographic store compares them: pairs for Han/kana and Hangul, triples for the Southeast
  Asian scripts. Pairs of function characters (什么, 我们, には) and filler runs (ありがとう, ขอบคุณ)
  are dropped. Shared n-grams count by the stretch of text they cover — the three pairs of ソフィア
  are one name, so a name alone is not a mention; a Han stretch weighs a word per two characters, a
  lone Thai triple half a word. Themes take only Han pairs that are words.
- **Latin languages**: stems without diacritics (szkoła / szkole, öğretmen / ogretmeni were two
  words), function words of German, Spanish, French, Portuguese, Italian, Dutch, Turkish, Polish and
  Indonesian are stopwords, months and today / tomorrow of all of them temporal stopwords. One shared
  rare word is a mention only when six letters match (not «comment» / «commercial»), within one
  script, and a weekday is never rare.
- **Dated one-off events in 15 languages**: dates in words in each of them («14. November», «14 de
  noviembre», «14 Kasım'da», «14 listopada»), 11月14日, 11월 14일, 12. 31., numbers against unspaced
  script (奶奶11/23上午), event words (appointment, exam, meeting, flight, deadline, bring …) and
  deadlines («до 1 декабря», «bis 30. Nov», «30/11'e kadar», 之前, 까지, ภายใน).
- **Numbers** next to Chinese, Japanese, Korean or Thai characters are numbers (每月500美元): those
  texts had none, so a changed price was never a conflict.

### Fixed

- Month markers in the conflict check matched word starts: «мама», «машина», «Марина», "maybe" and
  "market" were May or March. English month dates matched any word starting like a month ("mardi 10",
  "Marina 5" were March dates); "Dec 17th" was not a date.

### Measured

Blind sets written by agents that never saw the code (15 languages, 12 facts, 24 mentions, 12
unrelated lines, 6 events, 4 dated durable facts each), first run: a message finds its fact 56 → 72 %
(ja 0 → 46, zh 0 → 54, ko 0 → 50, th 0 → 88 %; Latin unchanged or better), false mentions down in
every Latin language; a one-off event is recognised in 6 → 53 of 90 cases, its date in 37 → 83, it
expires the day after in 4 → 45; dated durable facts stay durable 60/60. After fixes from that run
(not blind): Thai false mentions 4.4 → 1.0 % (mentions 71 %), events 88/90, dates 90/90, expiry 78/90
— the rest are 3/12-style dates, which expire only when both readings have passed. On the author's
live household data (Russian / English) the pass decides exactly as before. Known gaps: Traditional
and Simplified Chinese are not folded into one another; store duplicates in unspaced scripts collapse
only when identical.

## 2.4.0 — 2026-10-12

Works with holographic 0.8.0, which keeps the history of every fact and makes "forget" final.

### Added

- **Forgotten facts are cleaned from the skill's own copies.** holographic 0.8.0 deletes a fact for
  good on `fact_store forget` and keeps a tombstone (the id, never the text). Every pass now starts
  with a sweep that removes that id from `cache/dream.json`, the reject list, the cooldowns, the
  diary and its archive (and the diary text inside the payload), and deletes the dream cron jobs'
  logs that mention it. Every run looks for every tombstone again — a copy written back by a pass
  that loaded the fact before the forget, or a log that could not be removed, is caught next time —
  and changes nothing when nothing is left; what could not be cleaned is reported and makes the
  gate fail. The pass sweeps again after it wrote its output, and on POSIX one lock keeps the pass
  and the gate from rewriting the state at the same time. Diary items are written on one line with
  "; " only between items, and carry `[#<fact_id>]` from now on; older lines are found
  through `cache/dream-diary-index.json`, hashes of every fact's diary previews kept while the fact
  exists (after the forget the text is gone, so the index is built ahead of time). `dream.py
  --forget-sweep` runs the sweep alone, and the new `no_agent` gate `dream-forget-sweep.py` (created
  by `install_cron.py`, every 15 minutes; `--sweep-schedule ""` skips it) does it between nights
  without the model. The hashes move to the sweep state afterwards, so the owner of another copy of
  the diary (a git backup) can clean it with `forgotten_diary_index()` + `scrub_diary_text()`.
- Limits, said plainly: diary lines written before 2.4.0 carry no id and are matched by the hashes of
  what the diary wrote; a fact whose own text held a newline was split over lines then, and only
  the lines after it that do not look like diary structure go with it. Hermes's session log, backups
  and other tools' copies are their owners' to clean.
- **`stats.fact_history`**: the store keeps history. SKILL.md and the nightly prompt map a human's
  answer to the store: "no longer true" → `end`, "it changed" → `change`, "that was wrong" →
  `correct`, "forget it" → `forget` plus `memory remove` of the entry carrying it; a conflict whose
  older statement is a store fact → `change` (one history instead of a second fact). Without the
  flag the reject list works as before.
- Extraction passes `valid_from` when a message says since when something holds, and treats an
  `add` answered `past_version` / `forgotten` as stored-nothing-on-purpose.
- `install.py --check` warns about holographic 0.2.0–0.7.x: they refuse every `fact_store` write
  from a cron job, so extraction and the keyword pass stored nothing (the copy bundled in Hermes core
  never refused). Fixed in holographic 0.8.0 (`cron_writes`).

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
