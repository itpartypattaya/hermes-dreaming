"""dream 3.7.1's quarantine (classify_unsafe and what it reads), verbatim from skills/dreaming/scripts/dream.py at
3.7.1, over the copy of the provider's screen it shipped with (holo_screen_0182.py, holographic 0.18.2 — the same as
0.22.0's). A frozen reference: tests/test_screen_speed_372.py holds 3.7.2's classify_unsafe to its decisions. Never
edit; it is what the decisions were."""
# flake8: noqa
# fmt: off
import functools
import importlib.util
import os
import re
import sys


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
    r"|gsk_[A-Za-z0-9]{20,}"                        # Groq
    r"|ntn_[A-Za-z0-9]{20,}|secret_[A-Za-z0-9]{32,}"  # Notion (new and legacy)
    r"|sk_[a-f0-9]{32,}"                            # ElevenLabs and friends
    r"|gh[osu]_[A-Za-z0-9]{20,}"                    # GitHub OAuth/server/user tokens
    r"|\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"            # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b[0-9a-f]{40,}\b"                           # long hex (keys/sessions)
    r")")
# A credential marker is rarely glued to the colon: "Пароль от wifi в квартире:
# X" and "Password for the router is X" both carry one. Up to four words are
# allowed between the marker and the separator — but they must not contain a
# verb of change, so "сменила пароль от Wi-Fi" (an event, not a secret) stays
# out (external review, 2026-10-07).
PASSWORD_MARKER_RE = re.compile(
    r"(?:парол[ьяию]|password|passwd|passphrase|seed[- ]?фраза|seed[- ]?phrase|"
    r"секретн\w{0,4}\s+(?:ключ|код)|api[- ]?ключ|private\s+key|2fa[- ]?код|"
    r"пин[- ]?код|\bпин\b|\bpin\b|код\s+от|密码|密碼|口令|密钥|密鑰|私钥|私鑰)"
    r"(?:[^\n:=—–：]{0,40}?)(?P<sep>[:=—–：]|\s-\s)\s*(?P<value>\S{4,})",
    re.IGNORECASE)
# …and these say "we changed it", not "here it is". A colon or "=" hands the
# value over whatever comes first ("Changed password: hunter2", "поменяла
# пароль: розовыйслон"). After a dash a verb of change in the marker's own
# sentence makes it an event — "сменила пароль от Wi-Fi — новый знает только
# она", "сбросил пароль от Zoom — 40-минутный лимит остался" — unless the value
# looks like a credential: six or more characters and a digit or a symbol
# ("— Secret123!"); with Cyrillic in it, a digit glued to a letter
# ("— Кот2024!"), because "40-минутный" and "кв.123" are words, not
# passwords. Codex review of 2.7.2, review of 2.7.4, Codex review of 2.7.5.
PASSWORD_EVENT_RE = re.compile(
    r"(?:смен\w+|помен\w+|обнов\w+|сброс\w+|забыл\w*|changed|rotated|reset|forgot)",
    re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?\n]")
_CREDENTIAL_VALUE_RE = re.compile(r"[\d!@#$%^&*_+=~|\\/<>{}\[\]]")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_GLUED_DIGIT_RE = re.compile(r"[^\W\d_]\d|\d[^\W\d_]")


def _looks_like_credential(value):
    value = value.rstrip(".,;")
    if len(value) < 6 or not _CREDENTIAL_VALUE_RE.search(value):
        return False
    return not _CYRILLIC_RE.search(value) or bool(_GLUED_DIGIT_RE.search(value))


_ZH_PASSWORD_IS_RE = re.compile(r"(?:密码|密碼|口令|密钥|密鑰|私钥|私鑰)\s*(?:是|为|為)\s*(?P<value>[^\s，。,;；]{4,})")
_ZH_VALUE_RE = re.compile(r"[0-9A-Za-z!@#$%^&*_+=~|\\/<>{}\[\]]")


def _password_marker_unsafe(text):
    if any(_ZH_VALUE_RE.search(m.group("value")) for m in _ZH_PASSWORD_IS_RE.finditer(text)):
        return True
    for marker in PASSWORD_MARKER_RE.finditer(text):
        if marker.group("sep").strip() in (":", "=", "："):
            return True
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(text, 0, marker.start())]
        if not PASSWORD_EVENT_RE.search(text[(ends[-1] if ends else 0):marker.end()]):
            return True
        if _looks_like_credential(marker.group("value")):
            return True
    return False
INJECTION_RE = re.compile(
    r"(?:"
    # Stems, not exact forms: «Игнорируйте», «игнорировать», «забудьте» are the
    # same instruction (external review, 2026-10-07). Up to two words may stand
    # between the verb and its object.
    r"игнорир\w*\s+(?:\w+\s+){0,2}?(?:инструкци\w*|правил\w*|указани\w*|предыдущ\w*|прошл\w*)"
    r"|ignore\s+(?:\w+\s+){0,3}?(?:previous|prior|above|earlier|all)\s+(?:\w+\s+){0,2}?instructions?"
    r"|disregard\s+(?:all\s+|any\s+|your\s+)?(?:previous|prior|above|instructions|rules|guidelines)"
    r"|do\s+not\s+tell\s+the\s+user|system\s+prompt\s+override"
    r"|forget\s+(?:all\s+|your\s+|the\s+)?(?:previous\s+|prior\s+)?(?:instructions?|rules?)"
    r"|забуд\w*\s+(?:\w+\s+){0,2}?(?:инструкци\w*|правил\w*|ограничени\w*)"
    r"|запомни\s*[:,]?\s*(?:всегда|никогда|что\s+тебе\s+разреш)"
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


# The holographic provider's own screen (scripts/holo_screen.py, a verbatim copy made by tools/vendor_screen.py):
# credentials in its 18 languages, token shapes (a bot token inside a Bot API address, A[KS]IA…, an address with a
# password), environment assignments, encoded blobs, instructions and requests to send secrets in every language,
# chat-role markup, hidden characters, images. The provider refuses these when storing and recalling, but never
# cleans what it already holds, nor people's messages — and the dream carries both into durable memory and its
# report (2.9.0). Two things it counts the dream does not:
# - a change of a password told after a dash, where what follows is a word of the sentence, not a value ("сбросил
#   пароль от Zoom — 40-минутный лимит остался", "забыл пароль от домофона — кв.123 не открывает"): the dream's
#   rule since 2.7.5, narrowed in 2.9.0 to a word in a non-Latin script with the sentence going on after it — a
#   short or Latin value ("— abc1", "— 1234", "— step1 …") is taken for a password (Codex review of 2.9.0);
# - a zero-width joiner inside an emoji (👩‍💻), the tags of a subdivision flag (🏴 + tags) and the zero-width space
#   that separates words in Thai, Lao, Khmer and Burmese: how these are written, not hidden text.
# A secret wins over an instruction: an injection carries a preview into the full JSON (Codex review of 2.9.0).
# A door key is not a credential: "перешли Даше ключ от дачи", "покажи ключ от квартиры соседке" read as a request
# to send a secret to both screens ("перешли … ключ"). The word before «от» + something with a lock is replaced
# before the instruction checks, not the secret ones (2.9.1). Only things with a lock: «ключ от почты», «от личного
# кабинета», «от API», a bare «ключ» stay suspicious.
def _load_holo_screen(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "holo_screen.py")
    try:
        spec = importlib.util.spec_from_file_location("_dream_holo_screen", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        missing = [n for n in ("_recall_problem", "_secret_in", "_RE_CREDENTIALS") if not hasattr(mod, n)]
        if missing:
            raise ImportError(f"no {', '.join(missing)}")
        return mod
    except Exception as exc:  # noqa: BLE001 — a broken copy must not take the pass down (review of 2.9.1)
        print(f"[dream] warn: {path} not loaded ({exc!r}) — secrets and instructions screened by the dream's own "
              f"rules only", file=sys.stderr)
        return None


HOLO_SCREEN = _load_holo_screen(os.path.join(os.path.dirname(os.path.abspath(__file__)), "holo_screen_0182.py"))
_EMOJI_JOINER_RE = re.compile(r"(?<=[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F])\u200D(?=[\U0001F000-\U0001FAFF\u2600-\u27BF])")
_FLAG_TAGS_RE = re.compile(r"(?<=\U0001F3F4)[\U000E0030-\U000E0039\U000E0061-\U000E007A]{2,6}\U000E007F")
_NO_SPACE_SCRIPTS = "\u0E00-\u0EFF\u1000-\u109F\u1780-\u17FF"  # Thai, Lao, Burmese, Khmer
_NO_SPACE_LETTER_RE = re.compile(f"[{_NO_SPACE_SCRIPTS}]")


def _strip_word_separators(text):
    """The zero-width space these scripts separate words with, also next to a digit, a space or a mark ("ลูกสาว
    7\\u200b ขวบ", "ไปตลาด\\u200b, ซื้อปลา"; review of 2.9.1): dropped when a letter of them stands within four
    characters. Elsewhere it stays hidden text."""
    if "\u200b" not in text:
        return text
    return "".join(c for i, c in enumerate(text)
                   if c != "\u200b" or not _NO_SPACE_LETTER_RE.search(text, max(0, i - 4), i + 5))


_LATIN_RE = re.compile(r"[A-Za-z]")
# Between «от» and the thing only words that describe it — "от нашей виллы", "от почтового ящика", "от Дашиной
# машины" — never another noun: "ключ от почты в офисе", "ключ от API на даче" name a digital key and a place
# (review of 2.9.1).
_DESCRIBING = r"(?:[а-яё-]*(?:ого|его|ой|ей|ых|их|ую|юю|ая|яя|ое|ее|ые|ие|ий|ый|ом|ем|им|ым|ина|иной|иного|ова|овой|ового)|его|её|ее|их)"
_DOOR_KEY_RE = re.compile(
    r"\bключ(?:и|а|у|ом|ей|ами|ах)?(?=\s+от\s+(?:" + _DESCRIBING + r"\s+){0,2}?"
    r"(?:дач|квартир|дом(?:а|у|е|ом)?\b|домофон|машин|авто(?:мобил)?|гараж|двер|подъезд|калитк|ворот|сара[яеюй]"
    r"|подвал|кладов|офис|комнат|шкаф|ящик|сейф|зам(?:ок|к)|велосипед|байк|мотоцикл|скутер|номер|вилл|кондо"
    r"|бассейн))", re.IGNORECASE)
_LETTER_RE = re.compile(r"[^\W\d_]")


def _told_change(text, match):
    """The dream's rule: a credential word, a dash, then a word of the sentence, not a value — in a non-Latin
    script, nothing of a credential's shape, the sentence going on after it — in a sentence about changing it."""
    if "sep" not in match.re.groupindex or match.group("sep").strip(" \t\"'«“„‚`「『") not in ("—", "–", "-"):
        return False
    value = match.group("value").rstrip(".,;:!?")
    if not _LETTER_RE.search(value) or _LATIN_RE.search(value) or _looks_like_credential(value):
        return False
    rest = _SENTENCE_END_RE.split(text[match.end("value"):], 1)[0]
    if not _LETTER_RE.search(rest):
        return False
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text, 0, match.start())]
    return bool(PASSWORD_EVENT_RE.search(text[(ends[-1] if ends else 0):match.end()]))


def _screened_text(text):
    """The text the provider's screen judges: emoji joiners, flag tags and word separators out, and the value of a
    told change (_told_change) blanked."""
    text = _strip_word_separators(_FLAG_TAGS_RE.sub("", _EMOJI_JOINER_RE.sub("", text)))
    told = sorted({m.span("value") for rx in HOLO_SCREEN._RE_CREDENTIALS for m in rx.finditer(text)
                   if _told_change(text, m)}, reverse=True)
    for start, end in told:
        text = text[:start] + "…" + text[end:]
    return text


@functools.lru_cache(maxsize=8192)
def classify_unsafe(content):
    """'secret' | 'injection' | None. Conservative quarantine: exclusion from
    auto-promotion with a note in the report, never deletion. The dream's own
    rules and the holographic provider's screen (see _load_holo_screen); a
    secret by either wins over an instruction."""
    text = content or ""
    if SECRET_RE.search(text) or _password_marker_unsafe(text):
        return "secret"
    screened = _screened_text(text) if HOLO_SCREEN is not None else None
    if screened is not None and HOLO_SCREEN._secret_in(screened):
        return "secret"
    if INJECTION_RE.search(_DOOR_KEY_RE.sub("вещь", text)):
        return "injection"
    if screened is not None and HOLO_SCREEN._recall_problem(_DOOR_KEY_RE.sub("вещь", screened)):
        return "injection"  # not a credential: _secret_in covers those
    return None
