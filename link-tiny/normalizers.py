"""Identifier normalization, token hygiene, and candidate matching.

Pure functions only — no I/O, no graph access, no MCP. Both the engine
(candidate generation + IDF/affinity scoring) and tests consume this module.

Matching semantics:

* A *match key* is the tuple of lowercased words of an identifier after
  splitting snake_case / camelCase / dotted / kebab forms. ``login_handler``,
  ``loginHandler`` and ``login.handler`` share the key ``("login","handler")``.
* A doc token matches a code symbol when its key equals the symbol's short
  name key or the key of any qualified-name segment suffix (last segment
  first).
* Stoplist tokens (common verbs, language keywords, UI/framework boilerplate,
  very short tokens) produce no candidates on either side — ambiguity
  explosion guard (RT-5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


EXACT_NAME_PROVENANCE = "exact-name"
EXACT_NAME_SCORE = 1.0

# Tokens at or below this length (after normalization) are noise.
MIN_TOKEN_LENGTH = 4

# Common verbs, language keywords, and UI/framework boilerplate single words
# that match hundreds of symbols and carry no traceability signal. Compared
# lowercased; whole words only (a compound identifier like ``login_handler``
# or ``DEPT_CODE`` stays linkable because at least one word is not in STOPWORDS).
STOPWORDS = frozenset({
    # generic verbs & nouns
    "init", "setup", "run", "handle", "main", "exec", "execute", "process",
    "start", "stop", "load", "save", "open", "close", "read", "write",
    "parse", "build", "make", "check", "create", "update", "delete",
    "remove", "add", "get", "set", "put", "post", "send", "recv", "call",
    "apply", "do", "use", "used", "using", "new", "test", "tests", "spec",
    "example", "samples", "demo", "tmp", "temp", "misc", "utils", "util",
    "helper", "helpers", "common", "general", "base", "default", "config",
    "data", "info", "item", "items", "value", "values", "name", "type",
    "kind", "node", "nodes", "list", "dict", "map", "set", "entry",
    "result", "results", "output", "input", "param", "params", "args",
    # UI / framework / prose single-word boilerplate (v2 explosion guard)
    "source", "code", "screen", "components", "component", "confirm",
    "dispose", "disposing", "sender", "eventargs", "eventsender", "event",
    "initializecomponent", "initialize", "suspendlayout", "resumelayout",
    "performlayout", "suspend", "resume", "perform", "layout",
    "container", "resources", "defaultinstance", "system", "status",
    "header", "detail", "button", "label", "panel", "column", "record",
    "table", "master", "search", "target", "option", "number", "amount",
    "format", "length", "message", "action", "method", "object", "module",
    "window", "dialog", "report", "folder", "client", "index", "count",
    "total", "error", "errors", "clear", "focus", "enabled", "visible",
    "active", "change", "dummy", "select", "order", "insert", "group",
    "left", "right", "inner", "outer", "join", "where", "having", "distinct",
    "into", "step", "only", "begin", "max", "min", "spread",
    # language keywords / literals
    "self", "this", "none", "null", "nil", "true", "false", "void", "int",
    "str", "string", "bool", "float", "double", "long", "char", "byte",
    "def", "func", "function", "class", "struct", "enum", "interface",
    "return", "import", "from", "const", "let", "var", "public", "private",
    "static", "async", "await", "if", "else", "for", "while", "try",
    "catch", "finally", "with", "as", "in", "is", "not", "and", "or",
    "case", "switch", "break", "continue", "throw", "throws", "extends",
    "implements", "package", "namespace", "using", "include",
})

_IDENTIFIER_PARTS_RE = re.compile(r"[A-Za-z0-9]+")
# dotted paths (a.b.C), route-ish fragments (/api/users/{id}), plain words
_DOTTED_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_ROUTE_RE = re.compile(r"/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)+")
_WORD_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def casefold_name(value: Optional[str]) -> str:
    """Casefold + strip an identifier for comparison."""
    return str(value or "").strip().casefold()


def _split_camel(chunk: str) -> List[str]:
    """Split one alphanumeric run at camelCase boundaries.

    Boundaries: lowercase/digit → uppercase (``loginHandler``) and the last
    capital of an acronym when a lowercase follows (``HTTPServer``).
    """
    words: List[str] = []
    buffer = ""
    for index, char in enumerate(chunk):
        if (
            buffer
            and char.isupper()
            and (
                buffer[-1].islower()
                or buffer[-1].isdigit()
                or (
                    buffer[-1].isupper()
                    and index + 1 < len(chunk)
                    and chunk[index + 1].islower()
                )
            )
        ):
            words.append(buffer)
            buffer = char
        else:
            buffer += char
    if buffer:
        words.append(buffer)
    return words


def split_identifier_words(value: Optional[str]) -> Tuple[str, ...]:
    """Split an identifier into lowercased words.

    Handles snake_case, camelCase, PascalCase, kebab-case, dotted and
    route-ish spellings: ``login_handler`` / ``LoginHandler`` /
    ``login.handler`` all split to ``("login", "handler")``.
    """
    text = str(value or "").strip()
    if not text:
        return ()
    parts: List[str] = []
    for chunk in _IDENTIFIER_PARTS_RE.findall(text):
        parts.extend(_split_camel(chunk))
    return tuple(word.lower() for word in parts if word)


def token_match_key(value: Optional[str]) -> Tuple[str, ...]:
    """Return the comparison key for a token or symbol name."""
    return split_identifier_words(value)


def qualified_segments(qualified_name: Optional[str]) -> List[str]:
    """Return ``[full, …suffixes…]`` for a dotted qualified name.

    ``"a.b.C"`` → ``["a.b.C", "b.C", "C"]``; a plain name returns
    ``[name]``; empty input returns ``[]``.
    """
    text = str(qualified_name or "").strip().strip(".")
    if not text:
        return []
    parts = [part for part in text.split(".") if part]
    return [".".join(parts[index:]) for index in range(len(parts))]


def _all_stopwords(key: Sequence[str]) -> bool:
    return bool(key) and all(word in STOPWORDS for word in key)


def is_linkable_token(value: Optional[str]) -> bool:
    """True when a token may produce link candidates (RT-5 guard)."""
    key = token_match_key(value)
    if not key:
        return False
    joined = "".join(key)
    if len(joined) < MIN_TOKEN_LENGTH:
        return False
    if joined.isdigit():
        return False
    if _all_stopwords(key):
        return False
    return True


def is_linkable_entity_token(value: Optional[str]) -> bool:
    """True when a doc-graph Entity name is identifier-shaped and linkable.

    Entity extractors (YAKE/GLiNER) emit natural-language phrases and plain
    dictionary words alongside real code identifiers. An entity mention must
    pass :func:`is_linkable_token`, contain no whitespace, and carry at least
    one identifier cue (``_``, ``.``, ``/``, ``-``, digits, or inner capital
    letters / all-caps acronym).
    """
    raw = str(value or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return False
    if not is_linkable_token(raw):
        return False
    if any(sep in raw for sep in ("_", ".", "/", "-")):
        return True
    if any(ch.isdigit() for ch in raw):
        return True
    # camelCase, PascalCase with inner capital, or ALLCAPS acronym (e.g. ADODB)
    if any(ch.isupper() for ch in raw[1:]):
        return True
    return False


def extract_identifier_tokens(text: Optional[str]) -> List[str]:
    """Extract identifier-like tokens from prose, positional order, deduped.

    Catches dotted paths (``a.b.C``), route fragments (``/api/users/{id}``),
    and bare words that look identifier-ish: snake_case (underscore), inner
    capitals (camelCase) or digits attached to letters. Plain lowercase
    prose words are noise (RT-3/R3) and never become tokens; segments of an
    already-extracted dotted path are still collected individually because
    they name real symbols.
    """
    raw = str(text or "")
    if not raw.strip():
        return []
    structured: List[Tuple[int, int, str]] = []
    for matcher in (_DOTTED_RE, _ROUTE_RE):
        for match in matcher.finditer(raw):
            structured.append((match.start(), match.end(), match.group(0)))
    structured.sort(key=lambda item: item[0])
    spans: List[Tuple[int, str]] = [
        (start, token) for start, _end, token in structured
    ]
    for match in _WORD_RE.finditer(raw):
        start, end = match.span()
        if any(start < span_end and end > span_start
               for span_start, span_end, _token in structured):
            continue  # already captured as part of a dotted path / route
        spans.append((start, match.group(0)))
    spans.sort(key=lambda item: item[0])

    candidates: List[str] = []
    seen: set[str] = set()

    def consider(token: str) -> None:
        cleaned = token.strip(".,;:!?()[]{}'\"")
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        candidates.append(cleaned)

    for _start, token in spans:
        if _WORD_RE.fullmatch(token):
            looks_identifier = (
                "_" in token
                or any(char.isupper() for char in token[1:])
                or any(char.isdigit() for char in token)
            )
            if not looks_identifier:
                continue
        consider(token)
    return candidates


@dataclass(frozen=True)
class CodeSymbolRef:
    """Minimal code-side identity used for matching (engine mirrors it)."""

    name: str
    qualified_name: str = ""
    path: str = ""
    target_type: str = "symbol"
    identity: Tuple[str, ...] = ()  # disambiguation: (path, name, lines…)


def symbol_match_keys(symbol: CodeSymbolRef) -> List[Tuple[str, ...]]:
    """All keys a symbol can be matched on: short name + segment suffixes."""
    keys: List[Tuple[str, ...]] = []
    seen_keys: set[Tuple[str, ...]] = set()
    short = token_match_key(symbol.name)
    if short:
        keys.append(short)
        seen_keys.add(short)
    for segment in qualified_segments(symbol.qualified_name):
        segment_key = token_match_key(segment)
        if segment_key and segment_key not in seen_keys:
            keys.append(segment_key)
            seen_keys.add(segment_key)
    return keys


def build_match_index(
    code_symbols: Iterable[CodeSymbolRef],
) -> Dict[Tuple[str, ...], List[CodeSymbolRef]]:
    """Precompute ``match key → symbols`` for batch candidate lookups."""
    index: Dict[Tuple[str, ...], List[CodeSymbolRef]] = {}
    seen: set[Tuple[str, ...]] = set()
    for symbol in code_symbols:
        identity = symbol.identity or (symbol.path, symbol.name)
        if identity in seen:
            continue
        seen.add(identity)
        for key in symbol_match_keys(symbol):
            index.setdefault(key, []).append(symbol)
    return index


def match_candidates(
    doc_token: Optional[str],
    code_symbols: Iterable[CodeSymbolRef],
) -> List[Tuple[CodeSymbolRef, float, str]]:
    """Return ranked ``(symbol, score, provenance)`` pairs for a doc token."""
    return match_index_lookup(build_match_index(code_symbols), doc_token)


def match_index_lookup(
    index: Dict[Tuple[str, ...], List[CodeSymbolRef]],
    doc_token: Optional[str],
) -> List[Tuple[CodeSymbolRef, float, str]]:
    """Indexed variant of :func:`match_candidates` (identical semantics)."""
    if not is_linkable_token(doc_token):
        return []
    key = token_match_key(doc_token)
    symbols = index.get(key)
    if not symbols:
        return []
    return [(symbol, EXACT_NAME_SCORE, EXACT_NAME_PROVENANCE) for symbol in symbols]


__all__ = [
    "EXACT_NAME_PROVENANCE",
    "EXACT_NAME_SCORE",
    "MIN_TOKEN_LENGTH",
    "STOPWORDS",
    "CodeSymbolRef",
    "build_match_index",
    "casefold_name",
    "extract_identifier_tokens",
    "is_linkable_entity_token",
    "is_linkable_token",
    "match_candidates",
    "match_index_lookup",
    "qualified_segments",
    "symbol_match_keys",
    "token_match_key",
]
