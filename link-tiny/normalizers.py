"""Identifier normalization and exact-name candidate matching.

Pure functions only — no I/O, no graph access, no MCP. Both the engine
(candidate generation) and tests consume this module.

Matching semantics (Phase 1: exact-name only):

* A *match key* is the tuple of lowercased words of an identifier after
  splitting snake_case / camelCase / dotted / kebab forms. ``login_handler``,
  ``loginHandler`` and ``login.handler`` share the key ``("login","handler")``.
* A doc token matches a code symbol when its key equals the symbol's short
  name key or the key of any qualified-name segment suffix (last segment
  first). Every match scores ``1.0`` with provenance ``exact-name`` —
  ranking only orders output deterministically, it never ranks by fuzziness.
* Stoplist tokens (common verbs, language keywords, very short tokens)
  produce no candidates on either side — ambiguity explosion guard (RT-5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


EXACT_NAME_PROVENANCE = "exact-name"
EXACT_NAME_SCORE = 1.0

# Tokens at or below this length (after normalization) are noise.
MIN_TOKEN_LENGTH = 4

# Common verbs and language keywords that match hundreds of symbols and
# carry no traceability signal. Compared lowercased; whole words only.
STOPWORDS = frozenset({
    # generic verbs
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
    """All keys a symbol can be matched on: short name + segment suffixes.

    Module-path segments of a qualified name are excluded from keying
    (``a``/``b`` in ``a.b.C`` are not identifiers agents reference); only
    the trailing identifier-bearing segments contribute. In practice the
    short name key and the last qualified segment key cover exact-name
    matching; the full-path key guards dotted references in prose.
    """
    keys: List[Tuple[str, ...]] = []
    short = token_match_key(symbol.name)
    if short:
        keys.append(short)
    for segment in qualified_segments(symbol.qualified_name):
        segment_key = token_match_key(segment)
        if segment_key and segment_key != short:
            keys.append(segment_key)
    return keys


def build_match_index(
    code_symbols: Iterable[CodeSymbolRef],
) -> Dict[Tuple[str, ...], List[CodeSymbolRef]]:
    """Precompute ``match key → symbols`` for batch candidate lookups.

    Equivalent to calling :func:`match_candidates` per token, but O(1) per
    token instead of O(symbols): full-corpus builds (thousands of paragraphs
    × thousands of symbols) need the index, not the linear scan.
    """
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
    """Return ranked ``(symbol, score, provenance)`` pairs for a doc token.

    Exact-name only: every returned pair scores ``1.0`` with provenance
    ``exact-name``. Same name in N symbols yields N pairs — never
    auto-select (plan risk R5). Stoplisted/unlinkable tokens yield ``[]``.
    Batch builds should use :func:`build_match_index` + :func:`match_index_lookup`.
    """
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
    "is_linkable_token",
    "match_candidates",
    "match_index_lookup",
    "qualified_segments",
    "symbol_match_keys",
    "token_match_key",
]
