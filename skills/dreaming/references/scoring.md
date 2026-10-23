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

**A negated message is no mention of the same fact (3.1.0).** Negations are
stopwords, so «Михаил не любит острое» shared every stem with «Михаил любит
острое» and corroborated it. A message that says the fact with the opposite
polarity (the same test as for conflicts below) is skipped for that fact's score
and for the decay of a memory entry; it still counts for any other fact. A `state.db`
that is no database (or a damaged one) stops the pass, and the gate reports
`dream_error`; it used to read as "no messages table" and the night ran with
nothing to corroborate. A busy or locked one is skipped with a warning, as before.

**Every language the holographic store reads (2.5.0).** Latin words are stemmed
without diacritics («szkoła» / «szkole», «öğretmen» / «ogretmeni» — one form
was ASCII and the other not, and they never met). A rare word is a mention alone
only when the two words share **6 letters**, not the 5-letter stem («comment» is
not «commercial», «schaut» is not «Schauinsland»), and only within one script
(«telegram_react» is not «telegram-юзернейм»); a weekday is never rare. Function
words of German, Spanish, French, Portuguese, Italian, Dutch, Turkish, Polish and
Indonesian are stopwords, months and today / tomorrow of all of them (and English
months) are temporal stopwords like the Russian ones. **Chinese, Japanese,
Korean, Thai** (and Lao, Khmer, Myanmar) have no spaces between words — a
sentence was one token and nothing was ever corroborated. Their runs are compared
by character n-grams, as the holographic store compares them: pairs for
Han/kana and Hangul, triples for the Southeast Asian scripts; pairs made only of
function characters (什么, 我们, には) and filler runs (ありがとう, ขอบคุณ) are
dropped, a pair with one function character or of hiragana counts half. Shared
n-grams count by the **stretch** of text they cover: the pairs that chain
(アン → ント → トン) are one shared word, so a name alone is not a mention; a Han
stretch weighs one word per two characters (搬到上海 is "move to Shanghai"),
a stretch of 5 pairs (6 for Thai triples, 4 for Hangul) counts alone, like a rare
word. Themes take only Han pairs without function characters — kana, Hangul and
Thai n-grams are pieces of words. Known gaps: Traditional and Simplified Chinese
are not folded into one another (the holographic store does that with its OpenCC
table), and store duplicates in these scripts collapse only when identical.
Measured on the holographic language benches (2026-10-13, tuned on them) — a
message mentions its fact: zh 10 → 86 %, ja 15 → 90 %, ko 0 → 90 %, th 20 →
80 %; fr false mentions 3.3 → 0.6 %; ru / en unchanged; on the live data of the
author's household (ru/en) the only change was one fewer mention for two entries.

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
`cron`, `subagent` and `tool` are always untrusted, whatever `untrusted_sources`
lists (a subagent brief reads exactly like a person talking, and the core groups
them the same way); the list adds sources such as `cli`;
`exclude_patterns` drops machine text that has no source of its own (bridge
briefs, forwarded digests) by regex on the content; group chats only from
`trusted_chat_ids`;
private chats and legacy sessions without chat_id — per
`trust_private_chats` / `trust_sessions_without_chat` (default true). If the
`sessions` table is missing (old dump) the filter is disabled with a warning.

Outputs: **mentions** (messages), **ref_days** (distinct local days —
`timezone` from the config), **evidence** (up to 3 day+snippet pairs, the
provenance shown to the reviewer).

**A cloud judge may take a pair out (3.3.0, off by default).** Every (fact, message) pair the count accepts
has an id (`PairId`: the fact id and a key of session, time and text), and `_pair_counts` is the one rule that
accepts it. With `judge.mode: veto` and a night the judge covered (verdicts for at least half the pairs it
could send), the pairs it vetoed are left out before anything is decided — promotions, confirmations, fact
decays, "said again" after a rejection, new facts and conflicts, evidence; `md_decays` never take a veto. The
judge only takes away: a pair the rules did not count is never asked about. `shadow` decides by the rules and
reports the difference in `stats.judge`. See the README, "Optional cloud judge".

## Quarantine (safety filter before publication)

Every fact passes `classify_unsafe` before any output list:
- **secret** — key/token patterns (sk-, apikey_, AKIA, AIza, ghp_, xox,
  Telegram bot token, JWT, PEM, long hex) and markers `password:` / `seed
  phrase:` (RU+EN). Content is published **nowhere** — only `fact_id` + reason.
- **injection** — embedded instructions: telling the reader to disregard
  its earlier rules, mentions of the system prompt, requests to show or send a
  key or the prompt, "save to memory that … is allowed", jailbreak phrasing;
  RU+EN. A 60-char preview goes to the full JSON only.
- **the holographic provider's screen** (2.9.0, `scripts/holo_screen.py`) — after
  the dream's own rules, the provider's: a credential with its value in its 18
  languages ("Passwort:", "密码：", "şifre:"…), token shapes (a bot token inside a
  Bot API address, `A[KS]IA…`, an address with a password `scheme://login:pass@`),
  environment assignments (`STRIPE_SECRET=…`, not `MAX_TOKENS=128000` or
  `${VAR}`), long encoded blobs → secret; instructions and requests to send a
  secret in every language, chat-role markup, hidden characters, images →
  injection. The provider refuses these when storing and recalling, but never
  cleans what it already holds, nor people's messages — the dream carries both
  into durable memory. It is a verbatim copy (`tools/vendor_screen.py`; MIT), as
  the provider's module imports Hermes itself; a test compares it with the
  provider's source when that is at hand. What it counts the dream does not: a
  change of a password told after a dash when a word of the sentence follows in
  a non-Latin script and the sentence goes on ("сбросил пароль от Zoom —
  40-минутный лимит остался", the dream's rule since 2.7.5, narrowed in 2.9.0: a
  short, all-digit or Latin value — "— abc1", "— 1234", "— step1 completed" — is
  a password); a zero-width joiner inside an emoji, the tags of a subdivision
  flag, and the zero-width space between words of Thai, Lao, Khmer or Burmese. A
  secret by either screen wins over an instruction: an instruction's preview
  goes to the full JSON. A door key is not a credential (2.9.1): "перешли ключ
  от дачи / квартиры / машины / гаража…" is no request to send a secret, while
  a key "от почты", "от личного кабинета", "от API" or a bare "ключ" stays
  suspicious; between «от» and the thing only words describing it ("от нашей
  виллы", "от почтового ящика" — not "от почты в офисе", 2.9.2). A broken or
  incomplete copy of the screen is skipped with a warning, never fatal.

**Reject list** (`cache/dream-rejected.json`, `dream-reject.py`) matches by
text — fingerprint or stemmed containment in both directions (a short human
rejection can close a long re-extracted fact under the same strict reverse
conditions as dedupe: 0.75 coverage, ≥8 stems, ≥6 shared). Key = text
fingerprint, never `fact_id` (sqlite reuses ids). Applied **before**
quarantine, so a quarantined fact can be closed; a rejected secret stores only
its fingerprint. A rejection is not forever (2.7.0): when it is older than `reject_reask_days` (180; 0 = never)
and the fact was said again after it — a store row created after the rejection and a human mention since — the
fact is a candidate again, with `previously_rejected: {at, reason}`, for the agent to ask about. Younger
rejections stay silent whatever happens: "said again" cannot be told from the core re-extracting the same stale
fact while people still talk about it, the case the list was made for; a second "no" starts the count over.

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
`fact_numbers` / `memory_numbers` and `nearest_entry`. The same goes for the
**opposite negation**: the two texts negate different words among those they
both contain ("does not eat" against "eats"; the word after a modal counts too,
"не должна использоваться"). Comparatives ("more", "больше") negate nothing. A
long entry is first narrowed to its sentences that say nearly the same (stem
Jaccard >= 0.5). A word one text says both ways ("likes peanuts but does not
like almonds") is compared together with the word after it, the thing it is said
of, when both texts speak of that thing (3.1.0). Where negations are compared, a short Latin word loses its third-person or
plural "-s" first («eats» / «eat»; not «class», «bus», «Paris») — not in the general
stem, where it would part Dutch «fiets» / «fietsen» and meet German «Dachs» / «Dach».
Chinese writes no spaces: what 不 / 没(有) / 别 / 未 / 无(法) negates is the up to five
characters after it; it is a flip when the other text says the longest piece of them,
the rest following too, without a negation of its own before it in the same clause (a
clause ends at punctuation and at 但 / 却 / 可是): «不同意» / «同意», «不吃苹果» / «吃红苹果»,
«不喝酒» / «不抽烟但喝酒» flip; «不喝酒» / «不再喝酒» agree; «不喜欢吃苹果» / «喜欢吃香蕉» is no
conflict; 无锡, 没收, 别墅 are the same word on both sides (3.1.0, 3.1.1). Still a heuristic
without a dictionary of words. Reported as `fact_negations` / `memory_negations`, and such a candidate is no
duplicate for fuzzy dedupe, for an identical head or a verbatim substring ("use X"
inside "do not use X"), or for the reject list either. An entry that holds a
secret or an instruction shows nothing of itself: `entry` is a placeholder,
`replace_unsafe: true`, and only the candidate's side (`fact_numbers` /
`fact_negations`) is given. This is deliberately
computed independently of fuzzy `in_memory` — a reworded fact with a new number
is exactly what fuzzy dedupe swallows. Only an identical head or an alias rule
suppresses it. Idea from mem0 (ADD/UPDATE/NOOP) and Graphiti (edge
invalidation), done without an LLM: the agent decides, the script never
rewrites memory. 14-day display cooldown. A month or a day of the week named in words counts as a number
(`month:MM`, `weekday:N`, 2.7.0: "ballet moved to Tuesdays" against "ballet on Mondays" is an update); in the
store's near-duplicate check a word with a digit is never an inflection, so two dates of one recurring event
stay two facts.

## What a candidate carries (and why)

Every published candidate is a small dossier, so the agent can act without
re-deriving anything:

- `why` — score plus corroboration in one line;
- `origin` (holographic >= 0.15.0) — who stands behind the fact's current text:
  `said`, `confirmed` (an inference or an unknown origin a person has confirmed
  since reads as confirmed, as the provider shows it) or `inferred`; absent when
  unknown (stored before 0.15.0, or a store the updated provider has not opened
  yet). Read from `fact_versions` / `fact_confirmations` by a query of its own,
  never added to the `facts` query. `change` takes it for the newer text;
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

## Per-chat fact stores (`chat_stores`, 2.6.0)

With holographic `scope: user / chat / thread` every chat (topic, user) has its own database next to
`db_path` (`holographic-chats/<platform>-<chat id>[-t<thread>].db`, `holographic-users/…`; the file name is the
plugin's own `_safe_part`, reproduced in `store_key()`). Memory files are one for all chats, so reading those
databases into the pass would carry one chat's facts into every chat — hence two separate switches:

- `chat_stores.read: true` — an overview of every database: facts and new ones in the theme window, promoted or
  not. Counted by SQL — no text is read, none is shown, nor any id. `chat_stores` in the JSON, one line in the
  diary.
- `chat_stores.promote_from: ["telegram:-100123", "telegram:-100123:42", "telegram:12345"]` — the chats
  (thread scope: topics; user scope: users) whose facts join the pass like the shared store's. Each such fact
  is corroborated **only by messages of its own chat** (topic; with user scope — sessions of that user, the
  session owner standing for the author in a group), and a listed chat's messages that the trust rules leave
  out (`trusted_chat_ids`) count for its own facts and nothing else — not shared facts, memory entries or
  themes. Ids become `"<store>#<id>"`, items carry `store`; no `keywords_needed` / `trust_feedback` for them
  (a cron session cannot write a chat's database). Their tombstones are swept like the shared store's — also
  after the chat leaves the list (the sweep remembers every store it was given), and one unreadable chat database
  is reported as an error without stopping the others.

Extraction stays with the shared store. A listed chat that has no database yet is reported as `missing`. The trust
rules are untouched: a chat in `trusted_chat_ids`, and with `trust_private_chats` every private chat, still
corroborates shared facts and memory entries and shows up as evidence in the report — on a bot that serves many
unrelated people, list only the owner's chats there and set `trust_private_chats: false`.

## Feeding the store (`dream-extract-precheck.py`)

**When the store cannot fill up.** holographic with `scope: user`, `chat` or `thread` keeps facts in a
separate database per user / chat / topic (`holographic-users/`, `holographic-chats/`), which the dream does
not read — promoting one chat's facts into memory every chat sees would undo that separation — and a cron
job, having no chat, writes nothing at all. With `cron_writes: false` a cron job is read-only. In both cases
the report and the diary carry `fact_store_note`, `install.py --check` warns, and the extraction gate does
not wake the model (its cursor stays). The memory-file review keeps working; `fact_source: none` says so.

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
  into sections** (dates in words in Russian, English, German, Spanish, French,
  Portuguese, Italian, Dutch, Turkish, Polish, Indonesian and Thai, 11月14日 and
  11월 14일, Buddhist years, ambiguous 5/10 counted as past only under every
  reading, relative anchors — today / tomorrow in each of these languages — never
  expire). A dated fact is an event only with a trigger word (appointment, exam,
  meeting, flight, deadline, bring … in each language; a deadline is "until / by" and a
  whole date): a birthday is not one, and a "since <date>" phrase («с 14 ноября», seit, desde, depuis …) is
  taken out before the check — it starts a state, not an event; a range «с 15.11 по 17.11» stays an event.
- **alerts** — `memory_loss`: more than `memory_loss_alert_fraction` (25%) of
  the entries present at the previous pass are gone (snapshot in
  `cache/dream-snapshot.json`). Wakes the agent; nothing is restored. `probe_missing` (2.7.0): a phrase of
  `probes` is no longer in the memory files — by its text, or every word of it in one sentence of an entry in any form. One alert
  per disappearance (the missing ones are kept in the snapshot; no snapshot state, no probes); the loss guard misses one entry merged away
  while memory was tidied at its limit. `messages_unavailable` (3.3.2): `state.db` was busy or locked, so the
  night corroborated nothing and asked no relevance question (`fact_decays`, `md_decays` empty) — "nobody said it"
  is unknown then. A memory file or one of the dream's own state files that exists but cannot be read
  (permissions) stops the pass with a `dream_error` instead: read as empty it was overwritten.
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
`cache/dream-snapshot.json`. All 0600, written whole through a temporary file. A state
file that is not a JSON object is moved aside to `<file>.corrupt-<UTC time>` with a
warning and the pass runs without it (since the review of 2.9.8; it used to be read as empty, silently, and
the next write buried it). Only a run that holds the state lock moves a file aside;
`dream-reject.py --list` reads without the lock and leaves a broken list as it is. A
set-aside copy may hold a fact's text, cut anywhere: the first forget sweep after a
`forget` deletes every `*.corrupt-*` copy, later sweeps delete one that mentions a
forgotten fact (by id, fingerprint or verbatim text); one it cannot delete is an error,
and the wipe stays owed until the next run finishes it. A missing file is a fresh install. A broken
`dreaming.json` stops the pass with `dream_error`.

The optional cloud judge (3.3.0) keeps its own files next to them: `cache/dream-judge.json` (verdicts, 90
days, hashes and fact ids), `cache/dream-judge-journal.jsonl` (the pairs sent and a record per night, 90 days),
`cache/dream-judge-health.json` (one line a night), `cache/dream-judge-export-manifest.json` (the last review
sample). A night with the judge off deletes the journal and the health file; the forget sweep cleans all four.

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
extraction gate and `dream-reject.py` all go through it. When `config.yaml` is
there but cannot be read, or the scanner cannot read the store's block for sure
(`hermes_config_unread`), there is no default: unless `$DREAM_FACT_STORE` or
`fact_store_path` names the store, the path is None, the pass reads no facts and
no tombstones, and `fact_store_note` says why (2.9.7). An unread `memory:` block
alone does not stop the facts: it shuts the extraction gate (no provider) and
drops the char limits taken from config.yaml (2.9.8).

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
