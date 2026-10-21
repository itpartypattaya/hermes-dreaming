"""dream_yaml.py — the config.yaml reader dream.py falls back to when PyYAML is missing or refuses the file.

Not a YAML parser: `_scan_block` gives the direct children of one mapping of Hermes config.yaml, typed as PyYAML
types them, or None when it cannot tell what YAML reads there (unsure is closed: the block counts as unread). The
cron jobs run with Hermes' own Python, which has PyYAML; this reader matters for a manual run with a system Python.

Moved out of dream.py as it was in 2.9.8 (3.0); dream.py loads it by path, like holo_screen.py, and re-exports
`_scan_block`, `_plain`, `_UNREAD` and `_Unsure`. It needs nothing but `re`.

FROZEN since 3.0. Do not teach it new YAML forms. A form it does not take already leaves its block unread, and
unread is safe: the gates stay shut and the pass names the block. The cure for an unread block is Hermes' own
Python, which has PyYAML, not another rule here. A fix goes in only for a wrong answer it gives with confidence on a
config a real install writes, with that config as a test. Findings of a review about forms nobody writes are
declined: 2.9.5–2.9.8 were four releases in one day for this file alone.
"""
import re

# A key at the start of a line (after its indent), plain or quoted, and what follows the colon.
_KEY_RE = re.compile(r"""(["']?)(\w[\w.-]*)\1[ \t]*:(?:[ \t]+(.*)|[ \t]*$)""")
_UNREAD = object()


class _Unsure(Exception):
    """The scanner cannot tell what YAML reads here. The block it was reading counts as unread, not as empty: an
    empty store block means the defaults — `memory_store.db`, `scope: profile`, cron writes on — and the second
    review of the 2.9.7 branch (Codex) found four configs where a half-read block let those defaults open the
    extraction gate or point the pass at another database. Unsure is closed."""


# A plain scalar of this many characters or more that looks like a number is not typed: YAML's base-60 and
# arbitrary-size ints cost time that grows with the square of the length (second review of the 2.9.7 branch).
_NUMBER_LEN = 100
# Plain scalars as PyYAML (YAML 1.1) types them: "write_approval: 1" is a number, which Hermes reads as off, and
# "05000" is octal. PyYAML's own lists: "y" and "n" stay strings (third review of 2.9.6, Codex); binary, base-60
# ("1:30" is 90), ".inf" and ".nan" are numbers (review of the 2.9.7 branch, Codex).
_YAML_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_YAML_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_YAML_NULL = {"", "~", "null", "Null", "NULL"}
_YAML_INT = re.compile(r"[-+]?(?:0|[1-9][0-9_]*)$")
_YAML_OCT = re.compile(r"[-+]?0[0-7_]+$")
_YAML_HEX = re.compile(r"[-+]?0x[0-9a-fA-F_]+$")
_YAML_BIN = re.compile(r"[-+]?0b[0-1_]+$")
_YAML_INT60 = re.compile(r"[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+$")
# PyYAML's float: a digit before the point, or an unsigned ".5" ("-.5" stays a string)
_YAML_FLOAT = re.compile(r"(?:[-+]?[0-9][0-9_]*\.[0-9_]*|\.[0-9_]+)(?:[eE][-+][0-9]+)?$")
_YAML_FLOAT60 = re.compile(r"[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*$")
_YAML_INF = re.compile(r"[-+]?\.(?:inf|Inf|INF)$")
_YAML_NAN = re.compile(r"\.(?:nan|NaN|NAN)$")
_YAML_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}"
                             r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)?$")
# YAML's line breaks: str.splitlines() also breaks at \v, \f and \x1c-\x1e, which YAML does not
_YAML_LINES = re.compile("\r\n|[\r\n\x85\u2028\u2029]")
# YAML's double-quoted escapes (JSON knows only some of them)
_YAML_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
                 "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
                 "L": " ", "P": " "}
_YAML_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
# How deep a one-line flow (`{a: [b, {c: d}]}`) is followed before it counts as unread: past it, recursion would
# reach Python's limit (review of the 2.9.7 branch, Codex: a 1,100-deep list crashed the config load).
_FLOW_DEPTH = 64
# How a plain flow key may not start: `?` (explicit key), `!` (tag), `&` (anchor), `*` (alias) and YAML's indicators
_NOT_A_PLAIN_KEY = ("?", "!", "&", "*", "|", ">", "%", "@", "`")


def _quoted(text, start):
    """(value, index after the closing quote) of the quoted scalar at `text[start]`, or None when it does not
    close on this line. Single quotes double to escape (`''`); double quotes take YAML's escapes (`\\"`, `\\\\`,
    `\\n`, `\\x41`, `\\u00e9`…). An escape YAML does not know gives _UNREAD as the value, with the end still found,
    so it leaves out its own key and nothing else (review of the 2.9.7 branch, Codex)."""
    quote, i = text[start], start + 1
    if quote == "'":
        out = []
        while i < len(text):
            if text[i] == "'":
                if text[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), i + 1
            out.append(text[i])
            i += 1
        return None
    out, bad = [], False
    while i < len(text):
        c = text[i]
        if c == '"':
            return (_UNREAD if bad else "".join(out)), i + 1
        if c != "\\":
            out.append(c)
            i += 1
            continue
        esc = text[i + 1:i + 2]
        if esc in _YAML_ESCAPES:
            out.append(_YAML_ESCAPES[esc])
            i += 2
        elif esc in _YAML_HEX_ESCAPES:
            width = _YAML_HEX_ESCAPES[esc]
            digits = text[i + 2:i + 2 + width]
            if len(digits) == width and all(d in "0123456789abcdefABCDEF" for d in digits):
                out.append(chr(int(digits, 16)) if int(digits, 16) <= 0x10FFFF else "")
                bad = bad or int(digits, 16) > 0x10FFFF
                i += 2 + width
            else:
                bad, i = True, i + 2
        else:
            bad, i = True, i + 2
    return None


def _strip_comment(text, plain=False):
    """`text` without its comment: `#` at the start or after a space, outside quotes (`"a #b"` keeps it). A quote
    opens a quoted scalar only where a scalar starts — at the start of `text`, or in a flow after `[`, `{`, `,` or
    `:` — and is a character inside a plain one, as in YAML: `/data/family 'archive #1'.db` ends at the `#` (third
    review of the 2.9.7 branch, Codex). `plain`: the text continues a plain scalar (a continuation line)."""
    flow = not plain and text[:1] in ("{", "[")
    prev, i = "", 0  # prev: the last character before `i` that is not a space, outside quotes
    while i < len(text):
        c = text[i]
        if c in ("'", '"') and not plain and (i == 0 or (flow and prev in ("[", "{", ",", ":"))):
            hit = _quoted(text, i)
            if hit is None:
                return text
            prev, i = c, hit[1]
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip(" \t")
        if c not in " \t":
            prev = c
        i += 1
    return text.rstrip(" \t")


def _base60(text):
    sign = -1 if text.startswith("-") else 1
    total = 0.0 if "." in text else 0
    for part in text.lstrip("+-").replace("_", "").split(":"):
        total = total * 60 + (float(part) if "." in part else int(part))
    return sign * total


def _plain(text):
    """A plain scalar typed as PyYAML types it, or _UNREAD for what this reader does not take: a block scalar
    (`|`, `>`), an anchor, an alias, a tag, a directive, a number of _NUMBER_LEN characters or more, a timestamp
    (PyYAML makes it a date, whose string differs — third review of the 2.9.7 branch, Codex)."""
    text = text.strip(" \t")
    if text[:1] in ("|", ">", "&", "*", "!", "%", "@", "`") or _YAML_TIMESTAMP.match(text):
        return _UNREAD
    if text in _YAML_NULL:
        return None
    if text in _YAML_TRUE or text in _YAML_FALSE:
        return text in _YAML_TRUE
    digits = text.replace("_", "")
    if len(text) >= _NUMBER_LEN and any(r.match(text) for r in (_YAML_INT, _YAML_OCT, _YAML_HEX, _YAML_BIN,
                                                                  _YAML_INT60, _YAML_FLOAT60, _YAML_FLOAT)):
        return _UNREAD
    try:
        if _YAML_INT.match(text):
            return int(digits)
        if _YAML_OCT.match(text):
            return int(digits, 8)
        if _YAML_HEX.match(text):
            return int(digits, 16)
        if _YAML_BIN.match(text):
            return int(digits, 2)
        if _YAML_INT60.match(text) or _YAML_FLOAT60.match(text):
            return _base60(text)
        if _YAML_INF.match(text):
            return float("-inf") if text.startswith("-") else float("inf")
        if _YAML_NAN.match(text):
            return float("nan")
        if _YAML_FLOAT.match(text) and any(c.isdigit() for c in text):
            return float(digits)
    except (ValueError, OverflowError):
        return _UNREAD
    return text


def _flow(text, i=0, depth=0):
    """(value, index after it) of the flow collection or scalar at `text[i]` — `{…}` a dict, `[…]` a list, nested
    up to _FLOW_DEPTH — or None on what this reader cannot follow on the line. A value it does not read (an
    anchor, a tag, an escape YAML does not know, a flow deeper than _FLOW_DEPTH) raises _Unsure: leaving the pair
    out kept an earlier value of a repeated key, and leaving the mapping out let the defaults in (second review
    of the 2.9.7 branch, Codex)."""
    if depth > _FLOW_DEPTH:
        raise _Unsure
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text):
        return None
    c = text[i]
    if c in ("'", '"'):
        return _quoted(text, i)
    if c in "{[":
        close, out, i = ("}" if c == "{" else "]"), ({} if c == "{" else []), i + 1
        while True:
            while i < len(text) and text[i] in " \t":
                i += 1
            if i >= len(text):
                return None
            if text[i] == close:
                return out, i + 1
            if isinstance(out, dict):
                if text[i] in ("'", '"'):
                    hit = _quoted(text, i)
                    if hit is None:
                        return None
                    key, i = hit
                else:
                    j = i
                    while j < len(text) and text[j] not in ":,{}[]":
                        j += 1
                    if j + 1 < len(text) and text[j] == ":" and text[j + 1] not in " \t,{}[]":
                        # `{provider:holographic}` is one key in YAML, `provider:holographic` with no value (third
                        # review of the 2.9.7 branch, Codex)
                        raise _Unsure
                    key, i = text[i:j].strip(" \t"), j
                    if not key or key[:1] in _NOT_A_PLAIN_KEY or key == "<<":
                        # `{: none}`, an explicit `? key`, a tagged, anchored or aliased key, a merge `<<`: YAML reads
                        # them otherwise, and a second `? provider: none` overrode the first (fourth review of the
                        # 2.9.7 branch, Codex)
                        raise _Unsure
                while i < len(text) and text[i] in " \t":
                    i += 1
                if i >= len(text) or text[i] != ":":
                    return None
                hit = _flow(text, i + 1, depth + 1)
                if hit is None:
                    return None
                value, i = hit
                if value is _UNREAD or key is _UNREAD:
                    raise _Unsure
                out[str(key)] = value
            else:
                hit = _flow(text, i, depth + 1)
                if hit is None:
                    return None
                value, i = hit
                if value is _UNREAD:
                    raise _Unsure
                out.append(value)
            while i < len(text) and text[i] in " \t":
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
            elif i >= len(text) or text[i] != close:
                return None
    j = i
    while j < len(text) and text[j] not in ",{}[]":
        j += 1
    return _plain(text[i:j]), j


def _scalar(rest):
    """The value after `key:` on its line, typed as PyYAML types it for the forms Hermes writes: a plain scalar
    (`_plain`), a quoted string (escapes decoded), a one-line flow collection (`_flow`) — or _UNREAD for what
    this reader does not take (a block scalar, an anchor, an alias, a tag, a flow that does not close on the line);
    inside a flow, such a value raises _Unsure, which `_scan_block` turns into None. `#` starts a comment only at
    the start or after a space and outside quotes, as in YAML: `holo#graphic` and `"a #b"` are values."""
    rest = _strip_comment(rest.strip(" \t"))
    if rest[:1] in ("'", '"', "{", "["):
        hit = _flow(rest)
        if hit is None or rest[hit[1]:].strip(" \t"):
            return _UNREAD
        return hit[0]
    return _plain(rest)


def _a_plain_value(value_text):
    """Is this value on the key's line a plain scalar, which YAML continues on deeper lines?"""
    return bool(value_text) and value_text[:1] not in ("'", '"', "{", "[", "|", ">", "&", "*", "!")


def _scan_block(text, path):
    """{key: value} of the direct children of the mapping at `path` in config.yaml (`()` is the top level,
    `("memory",)`, `("plugins", "hermes-memory-store")`), or None when the scanner cannot tell what YAML reads
    there — the reader behind the config helpers when PyYAML is missing or cannot parse the file. Not a YAML
    parser: block mappings with plain or quoted keys, and values typed as PyYAML types them (`_scalar`):
    scalars, a plain scalar continued on deeper lines, one-line flow collections. A flow mapping stands for the
    block at `path` or at any key on the way to it (`plugins: {hermes-memory-store: {…}}`), or for the whole
    document. A tab indent is taken although YAML refuses it.

    None — unsure — is not {}: an empty store block means the defaults, and a half-read one let them open the
    extraction gate or point the pass at another database (second review of the 2.9.7 branch, Codex). It comes
    on a value of `path` or of a key on the way to it that this reader does not take (a tag, an anchor, a block
    scalar, an escape YAML does not know, a flow deeper than _FLOW_DEPTH or not closed on its line, a number of
    _NUMBER_LEN characters or more), a continued plain value with a blank or comment line inside (YAML keeps
    those as line breaks), a line of that mapping that is not a key or sits at another indent, an indented line
    outside any mapping, and on a file of several YAML documents or with content on a `---` line.

    A comment line belongs to no block, at any indent: YAML skips it, so a top-level "# Section heading" between
    `memory:` and its `provider:` does not end the mapping (issue #2: the old block regex stopped there and
    reported no provider while Hermes ran holographic; the same regex read `plugins:` until 2.9.7). Each mapping's
    first content line fixes the indent of its children, whatever it holds (review of 2.9.6, Codex: a quoted key
    the old extractor skipped let a nested key set it); deeper lines belong to a nested value. A key with a value
    on its line holds no children: the lines under `memory: |` are text, not settings (review of 2.9.7, Codex),
    and the lines under a plain value continue it, as YAML folds them — `provider: holographic` over
    `    disabled` is "holographic disabled", not holographic (review of the 2.9.7 branch, Codex). Every definition
    of `path`, or of a key on the way to it, replaces what an earlier one gave, as in YAML: a second `plugins:`
    without the store leaves nothing (review of 2.9.7). A key may carry a comment, the file a BOM, and the last
    line may lack a newline. A document marker is one only in column 0: `---` inside a block scalar is text."""
    path = tuple(path)
    try:
        return _scan_block_or_raise(text, path)
    except _Unsure:
        return None


def _scan_block_or_raise(text, path):
    result, found = {}, not path
    content = started = ended = False  # a content line seen; a `---` seen; a `...` seen
    gap = False  # a blank or comment line since the last content line: it ends a continued plain value
    # [indent, key, indent of its children, opaque: a value on the key's line, continuation lines of a plain
    # value of a direct child of `path` (or None), the value's first line, a list: its children are `- ` items]
    stack = []

    def on_path():
        """Is the mapping at the top of the stack `path` or a mapping on the way to it?"""
        keys = tuple(entry[1] for entry in stack)
        return path[:len(keys)] == keys

    def close(entry):
        if entry[4]:
            value = _plain(" ".join([entry[5]] + entry[4]))
            if value is _UNREAD:
                raise _Unsure
            result[entry[1]] = value

    if text[:1] == "\ufeff":
        # one BOM, as YAML: a second one is part of the first key (fourth review of the 2.9.7 branch, Codex)
        text = text[1:]
    if any(c in text for c in ("\x85", "\u2028", "\u2029")):
        # NEL, LS, PS break lines in YAML, but a plain scalar folded over one keeps it, not a space — another db_path
        # (fourth review of the 2.9.7 branch, Codex). Rare enough to leave the file unread.
        raise _Unsure
    for line in _YAML_LINES.split(text):
        # YAML's whitespace is space and tab: NBSP and the other Unicode spaces are text to it, and stripping them
        # moved keys and changed values (third review of the 2.9.7 branch, Codex)
        body = line.strip(" \t")
        if not body:
            gap = True
            continue
        if body[0].isspace():
            raise _Unsure  # a line that starts with a space YAML does not take for one
        if line.startswith(("---", "...")) and (len(line) == 3 or line[3] in " \t"):
            if _strip_comment(line[3:].strip(" \t")):
                raise _Unsure  # `--- {memory: …}`, `--- !tag`: a document on the marker's line
            if line.startswith("---"):
                if content or started or ended:
                    raise _Unsure  # a second document, empty ones too (`---` / `...` / `---`)
                started = True
            else:
                ended = True
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if body.startswith("#") and not (stack and stack[-1][3] and indent > stack[-1][0]):
            gap = True
            continue
        if ended:
            raise _Unsure  # content after `...` is another document
        content = True
        after_gap, gap = gap, False
        if (stack and indent == stack[-1][0] and not stack[-1][3] and stack[-1][2] in (None, indent)
                and (body == "-" or body.startswith(("- ", "-\t")))):
            # a list at the indent of its key, as PyYAML (and Hermes, saving its config) writes it: `toolsets:`
            # over `- hermes-cli`. Nothing on `path` lies inside a list: PyYAML reads no store under `plugins:` there
            stack[-1][2], stack[-1][6] = indent, True
            continue
        while stack and indent <= stack[-1][0]:
            close(stack.pop())
        if stack:
            top = stack[-1]
            if top[3]:
                if top[4] is not None:
                    piece = _strip_comment(body, plain=True)
                    if not piece:
                        gap = True
                        continue
                    if after_gap or _KEY_RE.match(line, indent):
                        raise _Unsure
                    top[4].append(piece)
                    gap = piece != body
                continue
            first = top[2] is None
            if first:
                top[2] = indent
            if indent != top[2]:
                if on_path() and not top[6]:
                    raise _Unsure
                continue
            if body == "-" or body.startswith(("- ", "-\t")):
                if first or top[6]:
                    top[6] = True  # a list, as above: `plugins:` over `  - holographic`
                elif on_path():
                    raise _Unsure
                continue
            if top[6]:
                if on_path():
                    raise _Unsure  # a key after list items: not YAML
                continue
        elif indent:
            raise _Unsure
        hit = _KEY_RE.match(line, indent)
        if not hit:
            if stack and tuple(entry[1] for entry in stack[:-1]) == path and not stack[-1][6]:
                # the value of a direct child of `path` on the next line: `db_path:` over `  right.db` is right.db,
                # not null — and null meant the default database (fourth review of the 2.9.7 branch, Codex)
                raise _Unsure
            if not stack and body.startswith("{"):
                # the whole document as one flow mapping: `{timezone: UTC, memory: {provider: x}}`
                value = _scalar(body)
                if value is _UNREAD:
                    raise _Unsure
                for name in path:
                    value = value.get(name) if isinstance(value, dict) else None
                found, result = True, (dict(value) if isinstance(value, dict) else {})
                stack.append([indent, "", None, True, None, "", False])
            elif on_path():
                raise _Unsure
            continue
        key, rest = hit.group(2), hit.group(3) or ""
        here = tuple(entry[1] for entry in stack) + (key,)
        value_text = _strip_comment(rest.strip(" \t"))
        continued = None
        if path[:len(here)] == here:
            # `path` or a key on the way to it is defined again: what came before is gone
            found, result = True, {}
            if value_text:
                value = _scalar(value_text)
                if value is _UNREAD:
                    raise _Unsure
                for name in path[len(here):]:
                    value = value.get(name) if isinstance(value, dict) else None
                result = dict(value) if isinstance(value, dict) else {}
        elif found and here[:-1] == path:
            value = _scalar(rest)
            if value is _UNREAD:
                raise _Unsure
            result[key] = value
            if _a_plain_value(value_text):
                continued = []
                gap = value_text != rest.strip(" \t")  # a comment on the key's line ends the value there
        stack.append([indent, key, None, bool(value_text), continued, value_text, False])
    while stack:
        close(stack.pop())
    return result
