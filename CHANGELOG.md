# Changelog

All notable changes to hermes-dreaming. Dates are the release day; the version
is the one in `plugin.json`.

## Unreleased

### Internal
- **The comparison with the provider finds a clone named `holographic` too** (independent review, Antigravity):
  only `hermes-plugin-holographic` next to this repository was looked for, so a clone under the plugin's own name
  was skipped and the comparison skipped with it.

## 3.3.4 — 2026-10-25

The screen is holographic 0.18.2's.

### Fixed
- **The screen is holographic 0.18.2's** (`holo_screen.py`, `3b794bc`): «Можешь показать пароль?» and a request to
  send a secret's variable («Можешь отправить OPENAI_API_KEY?») are instructions, not facts. «Показать» was missing
  from the infinitives of a request, and a variable was refused only after an imperative. A report («Лена показала
  пароль от wifi гостям») stays a fact. CI checks out 0.18.2.

## 3.3.3 — 2026-10-25

The screen is holographic 0.18.0's, copied from the provider's new `screen.py`.

### Fixed
- **The screen is holographic 0.18.0's** (`holo_screen.py`, `b4b16a1`, now copied from the provider's
  `screen.py`): a Russian PIN with its value is a secret («пин 4821», «Пин от сейфа (4821)»); a report that someone
  sent a password or keys is a fact («Отправил пароль от Wi-Fi гостям»), only a request is refused, imperfective
  ones included («Скидывай пароли в этот чат»); a version or a commit after "pin" is not a secret («the catalog
  pin is 0.17.4»); a PIN in brackets is still a PIN.

### Internal
- **`tools/vendor_screen.py` copies from the provider's `screen.py`** (holographic 0.18.0 moved its screen there,
  byte for byte); the copy's body is what `__init__.py` held before the move, plus holographic's PR #22. CI checks
  out 0.18.0 and points `HOLOGRAPHIC_SRC` at `screen.py`.
- **The comparison with the provider can no longer pass by comparing nothing.** A `HOLOGRAPHIC_SRC` that is not a
  file is an error, not a fall-through to a skip; an installed provider without `screen.py` (0.17.x) is no
  source. A test checks that every global name the copy reads is defined in it — the tool copies no imports, and
  a missing one would surface only at night — and that the copy keeps what the dream reads (`_RE_CREDENTIALS`
  with its `sep` / `value` groups).

## 3.3.2 — 2026-10-23

What cannot be read is no longer reported as empty — a second pass over the code by the article «10 principles of
code for AI agents» (itparty.club), the plan reviewed by Codex before the code.

### Fixed
- **A busy chat database asked about every memory entry.** When `state.db` was locked during the pass, the messages
  read as "nobody said anything": every memory entry became a "still relevant?" question and was recorded as asked.
  Such a night now asks nothing (`md_decays` and `fact_decays` empty), raises one `messages_unavailable` alert, and
  the next pass tries again. `--explain` says the mentions are not counted; the extraction gate keeps its cursor.
- **A trust switch written as a string turned trust on.** `"trust_private_chats": "false"` was `True` for Python.
  `trust_private_chats` and `trust_sessions_without_chat` take `true` / `false` only; anything else is named in
  stderr and trusts nothing.
- **An unreadable state file was overwritten.** A reject list, cooldowns or snapshot that exists but cannot be read
  (permissions) was read as empty, and the next write of the pass or of `dream-reject.py` replaced it — every
  earlier rejection was gone. The pass now stops with a `dream_error`, `dream-reject.py` exits 1, nothing is written.
  An unreadable judge cache costs the judge's night, not the dream's.
- **An unreadable memory file read as no memory**: promotions offered what memory held, the loss guard saw every
  entry gone and emptied its snapshot. The pass stops instead.
- **A broken `cron/jobs.json` let the forget sweep report success** while a forgotten fact stayed in the dream's
  cron logs (and `{"jobs": {...}}` crashed it). It is an error of the sweep now, retried until the file is whole;
  the file itself is still never touched.
- **`dream-reject.py` said "not found" for a store it could not read**; it says "cannot read the fact store" now.
  Its fallback fingerprint, which was not dream.py's formula (no NFC, no ё → е), is gone.
- **Values the dream cannot use are named in stderr**, not taken in silence: a `DREAM_*` environment value of the
  wrong type, `feedback_cap`, `keywords.cap`, `pending.*`, `reject_reask_days`, a `memory_loss_alert_fraction`
  outside (0, 1] or NaN. One bad `pending` value no longer resets the other.
- **`precheck.actionable_keys` as a string** ("promotions") became its letters and the gate never woke: the default
  keys are used, with a warning.
- **"Sept 5" and "Dec. 5"** were dates for the expiry check but not for the event check, so such an event could
  be promoted: one English date rule now.
- `tools/vendor_screen.py` refuses a provider source that lacks one of the roots it copies, instead of writing a
  screen without it.

### Changed
- Tests pin the copies the gates keep of dream.py's rules: the precheck defaults, the lists of gate scripts, how
  each gate finds its home. The cloud judge's fallback night is built by field name. Stale comments fixed.
- Checked against Hermes 0.22.5: agent cron jobs run through the gateway since 0.22.3 (sessions `local-…`, source
  still `cron`) — nothing to change.

## 3.3.1 — 2026-10-23

A fix for scripts that reuse `dream.py`, and the judge described properly.

### Fixed
- **`dream.py` loads again the way another script loads it** — by path, with `spec.loader.exec_module()` and no
  entry in `sys.modules`. 3.3.0's two decision records (`DecisionInputs`, `Decisions`) were dataclasses, and under
  `from __future__ import annotations` a dataclass looks its module up in `sys.modules`: such a load failed with
  `AttributeError: 'NoneType' object has no attribute '__dict__'`. On a live install it stopped the backup scrubber
  that removes forgotten facts from the backups. They are named tuples now, like the other records; the nights are
  unchanged (the golden record). Test: `tests/test_load_331.py` loads `dream.py` and every script next to it that
  way, each in its own interpreter.

### Documentation
- **The judge, written out.** The README section now tells how a night with the judge goes, step by step, with the
  question word for word; what a decision model is; Jev and OpenAI Decisions side by side — who makes and serves
  them, the key, the measured cost and latency of a call, what each removed on the holdout and per language, their
  data terms as documented in October 2026 — how it was measured and what that does not prove, what was tried
  before (local models), what leaves the machine and what does not (long messages stay home, unjudged). A diagram
  of the judge's night, `docs/judge.png` (source `docs/src/judge.svg`); the overview no longer says "no network"
  without "by default".
- **A manual run is a real night.** `hermes cron run`, a gate or `dream.py` without `--dry-run` writes the state,
  marks what it showed and, with the judge on, sends pairs. To look without consequences: `--dry-run`.

## 3.3.0 — 2026-10-23

An optional cloud judge, off by default, may take a mention the rules counted out of the count — never add one.
Off, its code is not loaded, nothing leaves the machine and every night is decided as in 3.2.0; a golden record
of two nights, taken before the refactor that made room for the judge, proves the rules unchanged. Measured
before the code on a frozen holdout of 360 pairs, at τ 0.50 the judge removed 84 % (Jev) / 75 % (OpenAI) of
the false confirmations the rules counted and no true one; a replay of those recorded answers through the
plugin vetoes the same pairs, one by one. A Codex review of the code found three defects, fixed before the
release.

### Added — an optional cloud judge, off by default (`judge` in dreaming.json)
- **What it does.** The rules count a message as a mention of a fact when they share enough words, and many such
  mentions confirm nothing ("Гоша тренирует юниоров" counted for "Гоша не пьёт кофе"). The judge asks a decision
  model whether the message confirms the fact, contradicts it, or neither, and may take a counted pair out of the
  count — never add one. `shadow` only reports what it would change; `veto` applies it to everything the count
  decides (promotions, confirmations, fact decays, "said again" after a rejection, new facts, conflicts, evidence);
  memory-entry questions never take a veto. Measured before the code on a frozen holdout of 360 pairs: at τ 0.50
  the judge removed 84 % (Jev) / 75 % (OpenAI) of the false confirmations the rules counted, and no true one.
- **What leaves the machine, and only when turned on.** The fact's text and the person's own words — no reply
  quote, link (with or without a scheme: `t.me/…`, `mailto:`), id, sender or time — of the chats and sources listed in `send_from` / `send_sources`
  (`"trusted"` = the trusted group and private chats); a promoted chat store's pairs only by that chat's id and
  `send_fact_stores`; sessions without a chat only with `send_no_chat_sessions`. Nothing with a secret or an
  instruction, nothing long. To OpenRouter (model Jev by TypeSafe) and, listed second, to OpenAI as the fallback:
  when the first gives no answer, the same pair reaches both. Keys: `OPENROUTER_API_KEY` / `OPENAI_API_KEY` from
  the environment or their line in `$HERMES_HOME/.env` (Hermes keeps provider keys out of cron scripts).
- **Within limits.** At most 250 attempts and $0.05 a night by the providers' own prices (soft: the last call may
  pass it), 120 s, and never past the gate's deadline (`dream-precheck.py` passes `--deadline`). HTTPS to fixed
  endpoints, no proxy, no redirect. A provider without a key, or that refuses it, is off for the night.
- **Never on a half-judged night.** A night that judged under half of what it could send applies no veto; three
  such nights without two good ones between raise one `judge_degraded` alert a week. An answer, cache, health or
  replay file of the wrong shape is unusable, not fatal; anything else that fails inside the judge leaves the night
  to the rules (degraded, with a warning that names only the error's type).
- **Its files** (`cache/`, 0600): verdicts for 90 days (hashes and fact ids, no text), reused only for the same pair
  with the same text that may still be sent; a journal of every pair sent, as sent, for 90 days; a health record.
  A night with the judge off deletes the journal and the health record; the forget sweep cleans all of them, even
  with the judge off. `--dry-run` and `--explain` send nothing; `--judge-replay FILE` judges from recorded answers.
- **Deciding between shadow and veto:** `--judge-export OUT` writes a blind review sample (one pair per fact) and
  `--judge-score LABELS` applies the rule: ready when the upper 95 % bound of wrong vetoes is ≤ 10 %, of lost
  confirmations ≤ 20 %, coverage ≥ 90 % and no wrong veto changed a decision; when the fallback gave over a fifth
  of the sample's verdicts (those left for a person included), each provider must pass on its own.
  `install.py --check` reports the
  judge's mode, recipients and keys (never their values).
- Tests: `tests/test_judge_330.py` (71) against a provider on 127.0.0.1 — permissions row by row, the payload, the
  key, every failure, the fallback, money, time, cache, journal, health, export, the rule.

### Fixed
- **Emerging themes with the same count came out in an order that depended on `PYTHONHASHSEED`.** A message's
  tokens are a set, so two runs of the same night could list tied themes differently — and, under the publish cap,
  show different ones. Ties are now ordered by the word. Found by a golden record of two nights
  (`tests/test_golden_330.py`) taken before the refactor for 3.3.0; the record must stay the same through it.

### Changed (internal — room for an optional cloud judge in 3.3.0; the night decides exactly as before)
- **One rule per (fact, message) pair.** `_pair_counts` is the body of the count, and a pair has a stable id
  (`PairId`: the fact id and a key of session, time and text). A set of vetoed pair ids removes exactly those pairs
  from the count — of promotions, confirmations and everything else that reads it; memory-entry questions
  (`md_decays`) never take a veto.
- **The night's decisions are one function of explicit inputs.** `load_inputs()` reads the facts, messages, memory,
  pending writes, rejections, the store's recent confirmations, the trust floor and the "already shown" state once;
  `_decide()` turns them into every section that reads the mention count. It reads no clock, database, environment
  or config file and leaves the settings alone (`settings_fingerprint`), so it can run twice in one night.
- **A message keeps where it came from:** source, chat, thread, session, whether its session row exists and whether
  the trust rules let it in. Messages are read in row order (`ORDER BY m.id`), so which compaction copy is kept no
  longer follows the query plan.
- **"Recently confirmed" uses the night's time**, not the database clock (`recent_confirmations(now=…)`).
- **The night's decisions use one time, taken before the databases are read** (3.2.0 took it after reading, and
  some decisions read the clock again). Only a pass that runs across midnight can tell: it decides by the time it
  began (Codex review of 3.3.0, kept on purpose).
- Contract tests: `tests/test_refactor_330.py` (15). The golden record of two nights is unchanged.

## 3.2.0 — 2026-10-22

From an experiment with decision models (Laya, mDeBERTa-xnli, OpenAI Decisions, Jev) on dirty pairs and on a live
family install, measured in aggregate. The polarity rules were not the weak part: in 60 days of live messages no
message flipped a fact, while 59 of 60 sampled corroborations were about something else. Corroboration counts
differently, hence a minor release. Still deterministic, no model, no network.

### Changed

- **A reply quote counts only when the person's own words touch the fact.** A reply carries the message it answers
  (`[Replying to: "…"]`, mostly the agent's own text), and the quote is unwrapped so that "yes, still the Vespa"
  under a question about the bike is a mention. On the live install 46 % of human messages were replies, the quotes
  ran 523 characters against 63 of the person's own, and they corroborated facts the person never spoke of: 535
  fact-message mentions from 81 replies, 64 without the quote. The quote now counts for a fact only when the
  person's own words share a stem with it. The `your previous message` pointer and the Discord note in front of it
  are quotes too (Hermes's own format, `gateway/run_inbound.py`).
- **Words nearly every fact of a home shares make no mention by themselves.** A stem in a fifth of the facts or more
  ("чат", "ветка", "Telegram" in a store of thread rules; a name) still counts towards a mention, but a mention needs
  at least one of the fact's own words too. A store under ten facts has none. A first, strict version dropped such
  stems altogether and lost names (李雷 + 有车 stopped being a mention).
- **A new number for the same thing is no corroboration.** «теперь 1500» under «1200 в месяц», «с 00:30» under «между
  23:30 и 06:30» corroborated the old number. A message whose own words hold a number the fact lacks and lack one it
  holds, of the same kind (times with times, dates, whole numbers of a similar size, months, weekdays), now counts
  as saying the fact otherwise, like a negation, for the fact's score, a memory entry's decay and confirmations. A
  number restated beside an extra one is no change; a number in the quote or in [bracketed] metadata does not count.
  On the live install it removed 77 of 324 mentions; ten of them read by hand were all mentions of something else
  (pasted reports, long texts), none a real confirmation.
- **A fact of short words is read with a wide tokenizer.** «Гоша пьёт кофе по утрам», "Bob likes tea" had one token
  or none under the usual minimum (Latin ≥ 4, other ≥ 5 letters), and nothing ever mentioned them. A fact with fewer
  than two word stems is read with words of three letters and more, against messages read the same way; the short
  function words that minimum used to drop are a new list, `WIDE_STOP`.

### Fixed

- **Chinese aspect particles after a negation.** "不吃肉了", "没抽过烟", "再也不打网球了" negate 吃肉, 抽烟, 打网球; with 了 / 过
  / 着 kept as part of the negated piece the other text never held it, and the flip was lost. They are dropped past
  the first character (过敏, 了解, 着急 stay words).
- **Another tense under a negation.** "eats" / "hasn't eaten", «курит» / «никогда не курил», «любит» / «не любила»
  met no flip: the stem kept them apart. A tense ending is cut where negations are compared (a root keeps three
  letters); the 3.1.0 exceptions (class, bus, Paris) stand.

### Measured

Accuracy of "supports / contradicts / unrelated" for the rules of 3.1.1 → 3.2.0: 80 pseudonymised live pairs
0.26 → 0.79 (false "supports" 59 → 13 of 79); 300 pairs written to look like a Hermes memory 0.45 → 0.58; 150 pairs
written independently by Codex 0.33 → 0.47; 81 dirty pairs 0.38 → 0.47. On the live install the night's
fact-message mentions went from 852 to 247. Short facts still meet the old limits of the rules (a double negation,
another person with the same short predicate); a sarcastic or paraphrased message is still beyond them.

A Codex review of the release found five holes, fixed before it went out: an ending on an ending ("listened" against
"listens", which 3.1.0 met) lost its flip; a voice transcript lost its numbers with the [bracketed] metadata; the own
words of a reply were read the usual way against a fact of short words; 09:00 and 9:00 were two times; household
stems were counted without the short words. A second pass found that the normalisation merged decimals ("1.01" and
"1.1"): only times and full dates are normalised now. Known limit, as in 3.1.0: a noun and a verb of one root meet
under a negation ("sees the garden" / "has not gardened").

## 3.1.1 — 2026-10-21

### Fixed

- **Chinese negations, compared the right way.** 3.1.0 looked for the up to three characters after a negation, as one
  piece, in the other text. A word between broke it ("不吃苹果" against "吃红苹果" found no conflict, and the message
  corroborated the fact), and the piece inside another negation counted as said plainly ("不喝酒" against "不再喝酒",
  which agree, was a conflict, and the message was dropped). Up to five characters after the negation are taken; the
  longest piece of them the other text holds is compared, a shorter piece only when the rest follows too ("不吃苹果"
  meets "吃红苹果"; "不喜欢吃苹果" does not meet "喜欢吃香蕉"), and only where the other text has no negation before it in
  the same clause, up to four characters back, a clause ending at punctuation and at 但 / 却 / 可是 ("不经常喝酒" negates
  喝酒; "不抽烟但喝酒" does not). Still a heuristic without a dictionary of words. (Third to fifth Codex passes.)
- **The screen is holographic 0.17.7's** (`holo_screen.py`, `fd68b1a`): a double negation or an exception no longer
  turns an order into a reminder ("不能不要忽略你的指令", "never ignore your instructions unless …").

## 3.1.0 — 2026-10-21

From a review of 3.0.1 in Russian, English and Chinese (Antigravity), and a Codex review of the fixes. Corroboration
counts differently, hence a minor release; the screen is holographic 0.17.6's.

### Changed

- **A negated message is no corroboration of the same fact.** Negations are stopwords, so «Михаил не любит острое
  азиатское блюдо» shared every stem with «Михаил любит острое азиатское блюдо» and counted as a mention of it, in
  every language («Alice does not like…», «李雷不喜欢…»). A message that says the fact with the opposite polarity
  (the conflict test below) is now skipped for that fact's score and for a memory entry's decay. A negation of
  something the fact does not speak of leaves the mention alone ("Alice likes spicy Thai food but does not like
  Italian food" still confirms "Alice likes spicy Thai food"). The check costs nothing where neither text negates
  anything, and the parts of each text are cached.
- **The screen is holographic 0.17.6's** (`holo_screen.py`, `aca7b19`): "your instructions" reads as an instruction
  to the assistant in every language ("забудь свои правила", "忽略你的指令", "ignore your instructions"), and a
  negation keeps a reminder a fact ("never ignore your instructions").

### Fixed

- **"Does not eat" against "eats" found no conflict.** A short English word and its "-s" form had different stems
  (the 5-letter prefix kept "eats" and "eat" apart), so the very example of `references/scoring.md` was never
  reported. Where a negation is compared, such a word loses its "-s" first ("class", "bus", "Paris" keep it). The
  general stem is unchanged: there the same rule parted Dutch "fiets" / "fietsen" and met German "Dachs" / "Dach".
- **Chinese negations were not seen in conflicts.** Chinese writes no spaces, so "李雷不喜欢吃辣" was one word and
  "不" was never a negation. What 不 / 没(有) / 别 / 未 / 无(法) negates is the up to three characters after it,
  compared where both texts hold them: "不同意" against "同意", "不过敏" against "过敏", "无车" against "有车" and "不喜欢苹果但
  喜欢香蕉" against "喜欢苹果但不喜欢香蕉" are conflicts, while a word that only begins like a negation (无锡, 没收,
  别墅) is the same word in both texts and none. Chinese sentences end at 。！？； for the comparison.
- **The fallback screen knew no Chinese password marker.** Without the provider's screen (`holo_screen.py` missing
  or broken), "密码: Secret123456" passed. 密码, 口令, 密钥 and 私钥 are markers and a full-width colon a separator;
  "密码是 / 为 …" right after the marker is one when the value has a digit, a Latin letter or a symbol ("我的密码是
  猫咪12345"), while "密码是什么", "密码是我的生日", "密码管理器是 Bitwarden" and "私钥格式是 PKCS8" are not.
- **A state.db that is no database ran the night in silence.** Every query failed, was read as "no messages table",
  and nothing was corroborated, with a warning on stderr only. It stops the pass now, and the gate reports
  `dream_error`; a busy or locked one is skipped with a warning, as before.

## 3.0.1 — 2026-10-21

### Fixed

- **A setting written on the module raises in every loader, not only after an import.** 3.0.0 made
  `dream.MIN_SCORE = 1` an error, because it changes nothing the pass reads. The guard took effect only when
  dream.py was registered in `sys.modules`, and the extraction gate, `dream-reject.py` and `install.py` load it by
  path without that: there the write passed, read back the new value, and the pass went on with `SETTINGS` (Codex
  review of 3.0.0). No loader writes a setting today, so no run changed; each now registers the module as an
  import does, and a test calls the three loaders as they run.

## 3.0.0 — 2026-10-21

A structural release: `dream.py` gets smaller and every loader configures it the same way. The nightly pass works
as before (the `dream-reject.py --hermes-home` fix this split began with shipped in 2.9.9 as B3).

### Structure

- **Importing `dream.py` reads no file.** It used to read `dreaming.json` and `config.yaml` of the home in the
  environment on import, and print their warnings, even when the caller then pointed it at another home. Now the
  module starts on the built-in defaults, and `load_for_home(home, cfg_path=None)` is the one call that points it
  at a home and applies that home's config. The CLI, `install.py`, the extraction gate and `dream-reject.py` all
  use it.
- **The config.yaml fallback reader is a file of its own, `scripts/dream_yaml.py`.** It moved unchanged from
  `dream.py` (about 430 lines), which loads it by path, like `holo_screen.py`. The name avoids Hermes' own
  `hermes_yaml` module. A missing or broken copy is reported on stderr and does not stop the pass: with PyYAML the
  reader is never asked, and without it every block it would read counts as unread, which keeps the gates shut.
  Nothing changes for installation: the gates copied to `~/.hermes/scripts/` still find `dream.py` in the plugin,
  and the new files sit next to it. The reader is frozen: a YAML form it does not take stays unread, and the fix
  for that is Hermes' own Python with PyYAML, not a new rule (2.9.5–2.9.8 were four releases in a day for it).
- **The word lists of the languages are a file of their own, `scripts/dream_lang.py`.** Stopwords, function words
  of Chinese, Japanese, Korean and Thai, weekdays, months, negations, Russian endings, the words of one-off events
  and the date patterns moved unchanged (about 280 lines). Unlike the YAML reader, it has no stand-in: without it
  `dream.py` does not load, and the gates report `dream_error`.
- **`corroborate()` no longer changes the shape of its answer by a flag.** `corroborate()` gives (mentions, days,
  last time); `corroborate_with_evidence()` adds the samples. The `evidence=` argument is gone.
- **The settings are one object, `SETTINGS`.** `configure()` used to set 41 module globals one by one; now it
  builds a whole `Settings` and swaps it in at the end, so a config that fails half-way leaves the previous
  settings untouched, and the code reads `SETTINGS.MIN_SCORE` instead of a bare global. `dream.MIN_SCORE` and
  the other names still read, so gates of an older install keep working. Writing one on the module
  (`dream.MIN_SCORE = 1`) raises: it would change nothing the pass reads, and a test that did it would pass
  without testing anything. The tests set `dream.SETTINGS.<name>` instead.
- **`run()` and `forget_sweep()` are split into named steps**, in the same order and with the same writes:
  scoring, promotions, conflicts, decays, themes and the cooldown stamps for the pass; the payload, the reject
  list, the cooldowns, the diary and the cron logs for the sweep. ruff's complexity of the two went from 18 and 33
  to under 10.

### Fixed

- **The `confirm` cooldowns are pruned by age.** They were stamped like the other ride-along sections and never
  pruned, so the seen-state kept one key for every fact ever offered for a confirmation. A stamp older than two
  cooldowns holds nothing back, so no answer changes.

## 2.9.9 — 2026-10-21

From the review of 2.9.8 against the articles «10 принципов кода для AI-агентов» and the vibe-coding checklist.
Nothing changes on a healthy install; what changes is what
happens when a file is broken.

### Fixed

- **A broken state file is no longer read as "no state" in silence (A1).** The reject list, the cooldowns, the
  "still relevant?" questions, the loss-guard snapshot, the sweep state, the diary index, the last payload and the
  extraction cursor were read as `{}` when they were not valid JSON — every rejection and cooldown gone, the loss
  guard blind for a night, extraction sent `backfill_days` back to hand two months of messages over again — and the
  next write buried the evidence. Such a file is now moved aside to `<file>.corrupt-<UTC time>` with a warning, and
  the pass runs without it. A missing file is still a fresh install, without noise. Hermes' own `cron/jobs.json`
  is read as before and never moved. Only a run that holds the state lock moves a file aside: `dream-reject.py
  --list` reports a broken list and leaves it (moving it there raced a locked writer, and the good list it had just
  written could be the one moved away — Codex review). Set-aside copies cannot be parsed and may hold a fact's text
  cut anywhere, so the first forget sweep after a `forget` deletes all of them; later sweeps delete a copy that
  mentions a forgotten fact by its id, fingerprint or verbatim text. A copy that cannot be deleted is an error, the
  gate retries, and the wipe stays owed until it is done (Codex review: a forgotten fact stayed in a `.corrupt-*`
  copy for good).
- **State files are written whole or not at all (A2).** Every whole-file write goes to a temporary file renamed
  over the target (as `dream-reject.py` has done since 2.7.2): a run killed by the gate's timeout or a full disk
  left an empty file before. The extraction cursor is written the same way. A symlinked state file is written
  through. On POSIX a file that cannot be made 0600 says so.
- **`install_cron.py` no longer duplicates the jobs when it cannot list them (A3).** Any failure of `list_jobs` was
  read as "no jobs", and every job was created a second time. The jobs are listed once, before anything is created;
  a failure stops the run with the reason, and nothing is created.
- **`dream-reject.py` stores no text when the screen cannot be loaded (A4).** It used to treat the text as ordinary
  and could copy a password into the reject list; now only the fingerprint is kept, with a warning.
- **A broken `dreaming.json` stops the night instead of running it on the defaults (B1).** On the defaults no group
  chat is trusted: the night asked up to 15 wrong "still relevant?" questions, and the extraction gate moved its
  cursor past every group message for good. The pass now exits with the reason (the gate reports `dream_error`), and
  the extraction gate answers `dream_error` and keeps its cursor. A missing file still means the defaults;
  `--dry-run`, `--explain`, `--forget-sweep` and `install.py --check` still read past a broken one and say so.
- **`dream-reject.py --hermes-home` read the other home's config (B3).** dream.py configured itself at import for
  `$HERMES_HOME` / `~/.hermes`, so `fact_source` and `fact_store_path` came from there, and a fact of the home given
  on the command line was "not found". Reproduced on two homes; the home's config is loaded now.

- **"unknown timezone 'UTC'" on every run without a zone database.** Python's zoneinfo knows no names without the
  `tzdata` package (Windows, slim containers), so the default `UTC` was reported as unknown each time. UTC by any of
  its names needs no database now; a zone that really is unknown still warns, and says when the database is missing.

### Changed

- CI: a `lint` job runs `ruff check --select E,F,BLE --ignore E501 skills tests` (ruff 0.15.16) (C1); the test job
  checks out `itpartypattaya/hermes-plugin-holographic` at the commit `scripts/holo_screen.py` names (827c639) and
  passes it as `HOLOGRAPHIC_SRC`, so `test_the_copy_matches_the_provider` runs instead of skipping, and a step fails
  when the pinned ref and `SOURCE_COMMIT` disagree (C2).
- The extraction gate takes the reply-quote pattern from dream.py instead of a copy of its own (D1); the dry run's
  "would wake" verdict reads `precheck.actionable_keys` like the gate (D2); the gate's list of fields for the prompt
  is `PUBLISHED_FIELDS`, not a second `FACT_FIELDS` (D3).
- Tests: broken and non-object state files, an interrupted write, a failing and an odd `list_jobs`, a broken config
  for the pass and the extraction gate, `--hermes-home` on two homes, the forget-sweep gate's `find_dream` (D4).
  Unused imports and one-line `;` statements in the tests removed (A5).
- README: `DREAM_SCRIPT` named under "Safety and privacy" (D5); `tests/evals/evals.json` described (D6); broken
  state files and a broken `dreaming.json` described. `references/scoring.md` likewise.

## 2.9.8 — 2026-10-20

A fourth review of the config reader, by another model, found valid YAML that the 2.9.7 fallback read with
confidence and wrongly. That fallback is used without PyYAML. With PyYAML, as in Hermes' own interpreter and so in
the scheduled jobs, nothing changes. Each form below now leaves its block unread, like everything 2.9.7 does not
take:
- **A value on the line after its key.** In `db_path:` over `  /data/facts.db`, the value is the next line. 2.9.7
  read it as null, and the pass went to the default database. The same happened to `scope`, `cron_writes`,
  `write_approval`, the char limits and `timezone`. A nested mapping or a list under a key is still read as before.
- **Flow keys YAML gives a meaning.** These are `? key`, a tagged, anchored or aliased key, and a merge `<<`. In
  `{provider: holographic, ? provider: none}`, YAML keeps `none`. 2.9.7 kept `holographic` and let the extraction
  gate open. A quoted `"<<"` is an ordinary key.
- **Two BOMs at the start of the file.** YAML drops only one, and the second becomes part of the first key. 2.9.7
  dropped both and found a `memory:` YAML does not see.
- **NEL, LS or PS line breaks** (U+0085, U+2028, U+2029). A plain value folded over one keeps the character, not a
  space, so 2.9.7 named another file.

Also:
- **A config.yaml that cannot be opened is unread.** Before, a file that could not even be looked up could pass
  for a missing one, and a missing one means the defaults.
- **The README says what a plain `python3` without PyYAML reads.** It also says to run the scripts by hand with
  Hermes' interpreter.
- **What 2.9.7 said about "no facts" was broader than the code.** Only an unread store block stops the facts, and
  `$DREAM_FACT_STORE` names the store as `fact_store_path` does. An unread `memory:` block alone shuts the
  extraction gate (no provider) and drops the char limits from config.yaml, but the store is read.

## 2.9.7 — 2026-10-20

The rest of issue #2: the holographic plugin's settings were read by the same block regex, and `timezone` not at
all, when PyYAML is not installed or cannot parse config.yaml.

- `plugins.hermes-memory-store` (`db_path`, `scope`, `cron_writes`, `min_trust_threshold`, and in `install.py --check`
  `embeddings`, `mirror_memory_writes`): a comment between `plugins:` and the store's block, or inside it, a comment
  after `plugins:`, a quoted key, a BOM or a `#` inside a value hid the setting, and of two `plugins:` the first won.
  Behind them the pass read the default database instead of the configured one, took a split `scope` for the shared
  store, and missed `cron_writes: false`. One scanner (`_scan_block`) now reads `memory:`, the store's block and the
  top level, with the rules of 2.9.6.
- What it reads besides, after three Codex reviews of this release: one-line flow collections up to 64 levels deep,
  also for a key on the way to the block (`plugins: {hermes-memory-store: {…}}`) and for the whole document —
  quotes, `#` and commas inside quoted values, and YAML's escapes in double quotes (`\"`, `\x41`, `é`, `\e`…).
  A plain value continued on deeper lines is folded into one line (`provider: holographic` over `disabled` is
  "holographic disabled", not holographic). Numbers are typed as PyYAML types them, binary and base-60 (`1:30` is
  90) too. A second definition of a key on the way (`plugins: {}`, `plugins: null`) clears what the first gave, a
  list in place of a mapping (`plugins:` over `- holographic`) holds no store, as in YAML, and a list at its key's
  indent (`toolsets:` over `- hermes-cli`, as Hermes saves config.yaml) is skipped.
- What it cannot read for sure, it does not guess. The second and third reviews of the branch found configs where a
  half-read or misread block let the defaults in — the default database, `scope: profile`, cron writes on — kept
  the first value of a repeated key, or gave a setting YAML does not have, and the extraction gate opened. Now the
  block (`memory:`, or the store's) counts as unread, not empty: the extraction gate stays shut, the pass reads no
  facts and no tombstones and says why in the report, and `install.py --check` says the block cannot be read. That
  is: a value of the block it does not take (a tag, an anchor, an alias, a block scalar, an escape YAML does not
  know, a flow deeper than 64 levels or not closed on its line, a number of 100 characters or more — base-60
  arithmetic on a long one took time growing with the square of its length — and a date or timestamp, which PyYAML
  makes a date with another string), a flow key with `:` and no space after it (`{provider:holographic}` is one key
  to YAML, with no value), a continued value with a blank or comment line inside (YAML keeps a line break there,
  and `db_path` named another file), a line of the block that is not a key or sits at another indent, a line that
  starts with a space YAML does not take for one (NBSP), a whole document indented, a file of several YAML
  documents, empty ones too (`---` / `...` / `---`), or with content on a `---` line, and a config.yaml that is
  there but cannot be read (not UTF-8 — it read as "no config", and the pass went to the default database). A store
  named by `fact_store_path` in dreaming.json is still read, and the report says so; its scope and `cron_writes`
  stay unknown, so the extraction gate stays shut. Only space and tab are whitespace now, as in YAML: an NBSP is
  text, and `write_approval: 1` with one after it is the string Hermes reads as on, not the number it reads as
  off. A quote inside a plain value is a character, as in YAML: `/data/family 'archive #1'.db` ends at the `#`.
  When PyYAML parses config.yaml, its answer stands and none of this applies — Hermes' own interpreter has PyYAML,
  so the scheduled jobs read config.yaml with it. It matters for a manual run with a system `python3`, and for a
  file PyYAML refuses, which Hermes does not load either. Hermes' example config reads the same with the scanner as
  with PyYAML.
- A memory char limit too large for a float (`9` × 400) made the 2.9.6 check overflow, and then the fill level in
  the report (`memory_usage`); with PyYAML both count it exactly now (without it, a number that long leaves
  `memory:` unread, above). A fraction below one (`.5`) passed the 2.9.6 check and became a limit of 0 — no limit
  at all; it is not a limit.
- `timezone` had no fallback without PyYAML, so the pass ran on UTC; the scanner reads the top-level scalars.
- `install.py --check` read the core's cron scheduler for the exact line `skip_memory=False,`. Spaces or a comment
  around it are taken now, and a source with no `skip_memory=` line it knows says "cannot tell" instead of "upgrade".

## 2.9.6 — 2026-10-20

`install.py --check` said `memory.provider` was not set when a top-level comment stood inside the `memory:` block
(issue #2 by blopa24): its own block regex stopped at the first unindented line, a comment included, while YAML — and
Hermes — skip comments and read the `provider:` after it. The same regex was the pass's fallback when PyYAML is not
installed or cannot parse the file, so there the keys after such a comment (the provider, `write_approval`, the
memory char limits) were not read either.

- The installer has no reader of its own any more: it reads `memory:` as the pass does (`hermes_memory_settings`).
- With PyYAML its answer is final, an empty one too: a `memory:` without these keys no longer falls through to the
  scanner, which read the first of two `memory:` blocks while YAML keeps the last.
- The scanner is still a narrow reader, not YAML: a block mapping of one-line scalars. Within that it agrees with
  YAML: comment lines at any indent are skipped, the first indented line fixes the indent of the direct children (a
  nested mapping, also under a quoted key, is not read), plain and quoted keys are read, plain values are typed as
  PyYAML types them (bool — `y` and `n` stay strings —, a finite number — `05000` is octal —, null), a quoted value is a string up to its closing quote
  (escapes are not decoded), `#` starts a comment only after a space, and every top-level `memory` replaces the one
  before. A flow mapping, a block scalar, an anchor, an alias or a tag leave the key unread — and `memory: {…}` or
  `memory: null` after a block leaves nothing. A BOM, a comment after `memory:` and a last line without a newline are
  taken.
- `write_approval` is read as Hermes reads it (`tools/write_approval._normalize_enabled`): a bool as it is, the
  strings on / true / yes / 1 / approve / enabled, anything else — a number too — off.
- A memory char limit from config.yaml is taken only when it is a positive finite number: `true` (an int to Python)
  was a limit of one character, and `.inf` / `.nan` made the pass fail.
- The extraction gate wakes the agent only when the provider is read as `holographic`. It used to let a provider it
  could not find through ("provider is not None"); a config without one has no `fact_store` tool in the session.

## 2.9.5 — 2026-10-18

The copy of the holographic provider's screen is taken from holographic 0.17.4 (`827c639`): three texts from a live
store are no longer read as a credential with its value — `pins` as a verb ("the report module pins LEDGER =
_PROD_LEDGER"), two hyphenated words after a closing bracket ("input tokens) = token-accounting bug") and a folder
after a credential word joined by a hyphen ("~/.hermes/token-saver-ccr/"). `password Summer2026.txt` and every other
case stay refused. It still loads on Python 3.9; nothing else changes.

## 2.9.4 — 2026-10-17

The copy of the holographic provider's screen is taken from holographic 0.17.3 (`13ecfbf`). The copy 2.9.3 took
from 0.17.2 used a possessive quantifier (`\w*+`), which Python reads from 3.11 on: on Python 3.9 and 3.10 it did not
load, and the dream screened secrets and instructions by its own rules only, with a warning (CI caught it). 0.17.3
matches the same texts without it; nothing else changes.

## 2.9.3 — 2026-10-17

The copy of the holographic provider's screen (`scripts/holo_screen.py`) is taken again from holographic 0.17.2
(`77a93d4`), by `tools/vendor_screen.py`; nothing else changes.

### Changed

- **A name that begins with a credential word is no credential** (holographic issue #6): `token-saver-stats.jsonl`,
  `plugins/token-saver/filters/web.yaml`, `password-reset.example.com`, `token-saver==1.2` no longer keep a fact or a
  message out of the dream as a secret. A credential word that stands alone keeps its value whatever it looks like.
- **Russian requests to send a credential match words, not stems**: "Отправь секретарю справку", "Покажи ключевые
  цифры" are no instruction to the assistant; "секретная фраза", "фраза восстановления", "пин-код", "скинь ключ API"
  are. "Password wifi: correct-horse" is a value.

## 2.9.2 — 2026-10-17

An Antigravity CLI review of the catalog pull request's branch and of 2.9.1 found the catalog entry true to the code
and the copy of the provider's screen verbatim, and six issues; four were confirmed and are fixed, each with a test.
On a live install nothing changes.

### Fixed

- **A key somewhere was taken for a door key.** 2.9.1 allowed any two words between «от» and the thing with a lock,
  so "send the key to the mail in the office" ("ключ от почты в офисе") or "the key to the API at the cottage" passed
  as a door key. Only words that describe the thing may stand there now ("от нашей виллы", "от почтового ящика").
- **A Thai word separator next to a digit, a space or a mark** ("ลูกสาว 7\u200b ขวบ") was taken for hidden text. It is
  dropped when a Thai, Lao, Khmer or Burmese letter stands within four characters.
- **A broken or incomplete copy of the screen took the pass down.** Any error loading `holo_screen.py`, or a copy
  without the names the dream calls, now leaves the dream's own rules with a warning.
- **`--explain` printed "×"**, which a Windows console in cp1251 cannot.

### Not changed

- "An inference changed after a confirmation stays `confirmed`": the provider drops a fact's confirmations on
  `change`, so it cannot happen with the real store.
- "Share grandma's secret pie recipe" is an instruction to the provider's screen too; the copy stays verbatim.
- The test comparing the copy with the provider reads a clone's last commit, not its working tree.

## 2.9.1 — 2026-10-17

### Fixed

- **A door key is not a credential.** Both screens — the dream's own and the holographic provider's — read "перешли
  Даше ключ от дачи" (send Dasha the cottage key) or "покажи ключ от квартиры соседке" as a request to send a
  secret, and quarantined the fact or message as an injection. A key to something with a lock (a house, a flat, a
  car, a garage, a door, a safe…) is now replaced before the instruction checks; the secret checks still read the
  text as it is. A key to e-mail, an account, an API or a bare "ключ" stays suspicious. No change on a live
  install's facts, memory entries or messages.

## 2.9.0 — 2026-10-17

For holographic 0.15–0.17. The plan and the code were each reviewed in Codex CLI (gpt-6.1-sol); every finding was
checked by a reproduction. On a live install the decisions on its 58 facts and 66 memory entries are unchanged
against 2.8.0; of 136 messages two change, both a newsletter carrying invisible watermark characters.

### Added

- **The provider's screen.** holographic 0.14.5–0.15.2 refuses to store or recall a credential in its 18 languages,
  a token by its shape (a bot token inside a Bot API address, `A[KS]IA…`, an address with a password), an
  environment assignment, an encoded blob, an instruction or a request to send a secret in any language, chat-role
  markup, hidden characters and images — but it never cleans what it already holds, nor people's messages, and the
  dream carries both into durable memory and its report. The dream missed 15 of 19 such shapes. It now runs the
  provider's screen after its own rules: `scripts/holo_screen.py` is a verbatim copy of what `_recall_problem` and
  `_secret_in` need (the provider's module imports Hermes itself), made by `tools/vendor_screen.py`, which refuses a
  source it cannot copy whole; a test compares the copy with the provider's source when it is at hand. A secret by
  either screen wins over an instruction (an instruction keeps a preview in the full JSON). Not taken over: a zero-width
  joiner inside an emoji, the tags of a subdivision flag and the zero-width space between Thai, Lao, Khmer or Burmese
  words.
- **`origin`** (holographic >= 0.15.0). Items carry who stands behind the fact's current text — `said`, `confirmed`
  or `inferred` — read from `fact_versions` / `fact_confirmations` by a query of its own (the `facts` query is
  unchanged); an inference a person confirmed reads as `confirmed`, as the provider shows it. The wake gate keeps the
  field. The prompt: `change` on a conflict takes the newer statement's own origin (none when it has none); an
  unconfirmed inference is asked about, not written.
- **The extraction job gives `origin`**: `said` for what the sender says about themselves or their world, `inferred`
  for a conclusion. An `add` of a stored text counts as the person saying it again, so the job never re-adds one; a
  chunk handed over again is marked `window.attempt`, and the facts the unanswered hand-over stored travel in
  `existing_facts` whatever its cap.
- **`install.py --check`** says whether the store keeps origins and confirmations — by the file's schema, not the
  plugin's version (the columns appear on a writer's open).

### Changed

- **The told-change rule of 2.7.5 is narrower.** A change of a password told after a dash stays no secret only when
  a word in a non-Latin script follows and the sentence goes on ("сбросил пароль от Zoom — 40-минутный лимит
  остался"). A short, all-digit or Latin value is a password: "— abc1", "— 1234", "— 5GHz сеть…", "— step1
  completed".
- **No `forget` to tidy up.** Neither cron job forgets a fact to deduplicate, whatever an `add` hint suggests: a
  forget is final and rewrites every copy, backups included. "That was wrong" with nothing true left is a question
  first, not a forget.

## 2.8.0 — 2026-10-15

### Added

- **`confirm` — facts people said again.** holographic 0.15.0 records who stands behind a fact (the person said it,
  confirmed it, or the agent inferred it) and counts confirmations through `fact_feedback(action="confirmed")`. The
  nightly pass now offers the facts of the main store that people in trusted chats said again on at least two
  different days, by messages that hold most of the fact's words (60 % of its stems) and do not negate it —
  naming the topic is not saying it again. Measured on a live family store (58 facts, 178 human messages in the
  window), counting facts only: the corroboration rule alone (two shared stems or one rare one) would have
  offered 26 — a chat names a child and her school every day; 20 % coverage, 15; 30 %, 3; 35 % and up, none. So
  it is rare by design, and the dry run says how many it would offer. The agent calls
  `fact_feedback confirmed` once per item: trust +0.05 (at most once a night per fact, once per person), and a
  conclusion the agent stored as `inferred` counts as confirmed. At most 10 a night; a fact the store counted a
  confirmation of in the last 30 days rests; chat-store facts get no call. Like `trust_feedback`, it rides along when
  the agent is awake and never wakes it. With a store that has no confirmations table (holographic before 0.15.0)
  the section is empty. On holographic's own store the agent added 22 facts in four months and never the same text
  twice, so this pass, not repetition, is where confirmations come from. Negation is read as the conflict check
  reads it, by the same word form — best effort.

## 2.7.6 — 2026-10-15

A narrow Codex CLI review of the 2.7.2 → 2.7.5 diff (gpt-6.1-sol) found six issues, each confirmed by a
reproduction; all are fixed, each with a regression test. With 2.7.6 a live install's decisions on its 58
facts and the secret screening of a month of its messages are still the same as with 2.7.2.

### Fixed

- **A secret entry leaked through a conflict's numbers.** The text of an entry holding a secret or an
  instruction was replaced by a placeholder, but its numbers went out as `memory_numbers` (a PIN among
  them), and its negated words as `memory_negations`. Such a conflict now carries the placeholder,
  `replace_unsafe: true` and the candidate's side only; the prompt says to ask, never to replace it.
- **A Cyrillic password after a dash passed.** Any Cyrillic letter in the value made it "a word", so
  "changed the password — Кот2024!" was no secret. With Cyrillic in it, a value is a credential when a
  digit is glued to a letter; "40-минутный" and "кв.123" stay words.
- **A verbatim substring skipped the negation check.** A fact standing whole inside an entry that says
  "not" ("use Telegram for alerts" in "do not use Telegram for alerts") counted as already written, and
  its conflict was dropped with it. The negation check now runs first.
- **A negation lost its object.** "Likes peanuts but not almonds" against "likes almonds but not
  peanuts" was a duplicate: both negate "likes". A word one text says both ways is now compared together
  with the word after it.
- **The forget sweep took a kept fact by its beginning.** The sweep matched fact items by the hashes of
  their first 85 characters, so a kept fact sharing them with a forgotten one left `promotions` every
  night. Items are matched by the fingerprint of the full text (already in the diary index); the prefix
  hashes stay for memory entries and previews, whose full text is not known.
- **`dream-reject.py --hermes-home`.** The reject command locked `$HERMES_HOME` (or `~/.hermes`) even
  when the dream ran with `--hermes-home`; it takes the same flag now, for the lock, the config and the
  fact store.

## 2.7.5 — 2026-10-15

An Antigravity CLI review of the 2.7.2 → 2.7.4 diff (Gemini 3.8 Flash, with the test suite and its own
reproductions) found eight issues; six were confirmed and are fixed, each with a regression test. With
2.7.5 a live install's decisions on its 58 facts and the secret screening of a month of its messages are
still the same as with 2.7.2.

### Fixed

- **Comparatives counted as negations.** "more" / "больше" were in the negation list, so "drinks more
  coffee" was a flip of "drinks coffee", and in "больше не ест" the comparative took the verb and the real
  flip was lost. Negations are a list of their own now; comparatives stay significant only for the
  store-duplicate check.
- **A shared negation masked another flip.** "Does not eat meat and does not drink milk" against "does not
  eat meat and drinks milk" was no flip because both negate "eat". The sets of negated words are compared
  as a whole now. The window is one word, two after a modal ("не должна использоваться"); the fixed
  two-word window of 2.7.4 is what made the same negation in another word order look different.
- **Password separators.** A spaced hyphen or an en dash after a credential marker was not a separator, and
  an all-letter password after a verb of change and a colon ("changed the password: correcthorse") passed.
  A colon or "=" now always hands the value over. After a dash a verb of change makes the sentence an event
  unless the value looks like a credential (six or more characters, no Cyrillic, a digit or a symbol), so
  "reset the Zoom password — 40-minute limit still there" is no longer quarantined.
- **A forgotten fact's text left a placeholder candidate.** An item whose own text is a forgotten fact
  stayed in `promotions` as "[removed: …]", kept the gate open and offered the placeholder for memory. The
  item now leaves the list.
- **`keywords_needed` keyed its cooldown on the first 300 characters**, so a longer fact's mark never
  matched the forget sweep. It keys on the full text, like `trust_feedback`.
- **`dream-reject.py` wrote without the lock** when `_sweep_lock` could not take it; it now writes nothing
  and exits 1.

### Not changed

- A negation in another sentence of a multi-sentence entry is still missed when that sentence alone is not
  close to the candidate (stem Jaccard < 0.5). Looser matching invented conflicts on live data.
- `dream-reject.py` locks `$HERMES_HOME`; a `--state` file in another home is not covered. Hermes runs it
  with its own `HERMES_HOME`.

## 2.7.4 — 2026-10-14

### Fixed

- **The negation check of 2.7.3 was too coarse.** Run against a live install's store, memory and reject list
  next to 2.7.2, it gathered the negations of every sentence of an entry that shared two words with the
  candidate: a long profile without `§` entries has a "not" in some sentence about something else, which
  invented three conflicts and turned a fact already in memory back into a promotion. It now compares what
  each side NEGATES — the words right after a negation, and only words both texts contain — after narrowing
  a long entry to its sentences that say nearly the same (stem Jaccard >= 0.5). A flip is one side negating
  and the other not, or both negating but never the same word. Two intermediate drafts were rejected on the
  same data (a rejected fact split differently into sentences came back; the same negation in another word
  order counted as a flip); each of the three cases has a regression test. With 2.7.4 the decisions on the
  install's 58 facts and the secret screening of a month of its messages are the same as with 2.7.2.

## 2.7.3 — 2026-10-14

A full Codex CLI review of the 2.7.2 skill (every script, `SKILL.md`, references, examples and tests) found
fourteen defects. Each was confirmed by a reproduction before the fix, and each has a test that fails on
2.7.2 (24 failures against 2.7.2, none against 2.7.3). Checked against Hermes Agent 0.22.0: the core's
`old_text` matching, `matched_entry` and batch rules are the ones the tests model.

### Security

- **A password after "changed" or "forgot" passed the quarantine.** The verb of change was looked for in
  the whole text before the marker, so "Changed password: hunter2", "I forgot the list. Password: hunter2"
  and "Updated the router, wifi password: Qw3rty!2026" were not secrets. The verb now counts only in the
  marker's own sentence, a value with a digit or a symbol is a credential whatever comes before it, and
  every marker in the text is checked, not the first.
- **A rejected secret came back through `trust_feedback`.** Rejected facts leave before the quarantine,
  and the section carried their text. A fact that `classify_unsafe` flags now arrives as
  `[hidden: secret]` with its `fact_id` (all the call needs) and no reason; the cooldown keys on the real
  text's fingerprint, so two hidden facts do not share one.
- **A memory entry with a secret reached the prompt as `nearest_entry`.** The candidate's closest entry was
  shown whole (`entry`, `full_entry`, the `old_text` anchor). An entry that holds a secret or an
  instruction now shows a placeholder and `replace_unsafe: true`, without the text or the anchor; a
  conflict's `entry` is screened the same way.
- **The gate dropped `previously_rejected` and `store`.** Both are rules, not details ("ask the human
  again before writing", "a chat's own database — memory tool only"), and the compacted payload lost
  them, so an old rejection arrived as an ordinary promotion. They are kept now, and the cron prompt
  states both rules.
- **`subagent` and `tool` sessions counted as people.** A list in `dreaming.json` replaces the default,
  and the shipped example listed `cron` alone. `cron`, `subagent` and `tool` are now always untrusted,
  whatever the list says; the example lists all three.

### Fixed

- **"Does not" was a duplicate of "does".** Negations are stopwords for corroboration, so fuzzy dedupe
  called "X does not eat peanuts" already in memory next to "X eats peanuts" — the update was dropped
  silently — and an old rejection closed the opposite fact. A candidate whose negations differ from the
  clauses of an entry that speak about the same thing is no duplicate now, for dedupe and for the reject
  list, and it is offered in `conflicts` with `fact_negations` / `memory_negations`.
- **Copies of a forgotten fact without its id stayed.** The sweep found payload items by `fact_id` only;
  a memory entry written from the fact rode on as another candidate's `nearest_entry`, a "still
  relevant?" entry or a conflict's entry. Strings that hold a forgotten fact verbatim (as a whole, a line
  or a sentence) are now replaced, a `nearest_entry` as a whole.
- **Extraction logs were not swept.** `existing_facts` were bare strings, so a log was never found by id.
  They are `{fact_id, text}` now, and a log is also swept when any JSON string in it holds a forgotten
  fact verbatim — which covers logs written before 2.7.3.
- **An unreadable store looked like a clean sweep.** A `sqlite3.Error` while reading the shared store's
  tombstones became "nothing was forgotten" and the gate exited clean. It is reported in `errors` now,
  like a chat store's, so the gate fails and the next run tries again.
- **Extraction did not say who spoke.** Messages carried `t` and `text` only; in private chats two
  people's first-person statements could not be told apart. Each message now carries `chat` (the chat
  type) and `from` (the sender's id) when known, and the extraction prompt attributes a first-person
  statement only to its sender.
- **`dream-reject.py` could lose a rejection.** It rewrote the file in place, outside the lock the pass and
  the forget gate hold: a rejection added between their read and their write was lost, and a reader in
  between saw an empty list. It now writes under that lock, to a temporary file renamed over the old one.
- **`trust_feedback` and `keywords_needed` rested when nobody was awake.** Both were marked as shown even
  when the gate did not wake the agent, and they were missing from the acknowledgement record, so a quiet
  night put them to rest for the whole cooldown. They join the record now and are reopened silently.
- **The approval-queue alert woke the agent every night.** Its cooldown stamp was set after the state file
  had been written. It is computed before the write now.
- **A vanished file with one to three big entries raised no alert.** The missing-file branch counted
  entries only; it now uses the same character floor as a file that is still there.
- `SKILL.md` carried `version: 2.7.0`; it follows `plugin.json` now (a test keeps them together).

## 2.7.2 — 2026-10-14

A Codex CLI review of 2.7.1's provider-floor change found eight defects. Every one was confirmed in the
code and is fixed; each has a test that fails on 2.7.1 (the new tests give 10 failures against 2.7.1 and
none against 2.7.2).

### Fixed

- **The floor never reached the agent.** The gate script hands the agent the sections' items, not
  `gates`, so the prompt's pointer to `gates.feedback_trust_floor` led nowhere and `SKILL.md` still said
  "below 0.3". Every `trust_feedback` item now carries its `floor`, and the prompt and `SKILL.md` point
  at it.
- **A nested key read as the setting.** When PyYAML had parsed `config.yaml` but the key was not a direct
  child of `hermes-memory-store`, the reader fell through to the line scanner, which found
  `hermes-memory-store.diagnostics.min_trust_threshold` (and nested `embeddings` / `mirror_memory_writes`
  in `--check`). A parsed file's answer is now final; the scanner runs only without PyYAML and reads
  direct children only. This also applies to `db_path`, `scope` and `cron_writes`.
- **The scanner skipped valid YAML.** Without PyYAML a last line with no trailing newline and a one-line
  flow mapping (`hermes-memory-store: {scope: chat, min_trust_threshold: 0.4}`) were not read, so a
  configured floor silently became the default. Both are read now.
- **The floor was cached for the whole process.** A module-level cache was shared by every home and every
  run, so a second run — or a run for another home — got the first one's floor. No cache now: two config
  reads per run.
- **A bad `feedback_cap` reset an explicit floor**, so an override of 0.2 turned into the provider's 0.9.
  The two settings are parsed separately, and a floor outside 0..1 counts as no override.
- **`--check` named the wrong floor** — "trust_feedback uses that floor" even when `dreaming.json`
  overrode it. It now names the override.
- **A `config.yaml` that is not UTF-8 crashed `--check`** (`UnicodeDecodeError`). It now reads as "no
  config".

**Tests:** 447 (was 438).

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
