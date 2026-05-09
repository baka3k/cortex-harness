"""
query_understanding.py
──────────────────────
Enriched query-understanding layer for the Graph Explorer semantic search.

Extends the lightweight ``QueryIntentClassifier`` (3 intents, keyword/regex)
with a full extraction pass that produces:

  * **intent**         — semantic | structural | temporal | default
  * **entities**       — class/function names, identifiers, domain nouns
  * **keywords**       — BM25-friendly clean tokens
  * **actions**        — verbs/operations extracted from the query
  * **domain_signals** — high-level domain labels (auth, payment, order, …)
  * **embedding_text** — enriched, cleaned string ready for vectorization.
                         Combines raw query + domain signal expansions so that
                         vague or multilingual queries still hit the right
                         Qdrant vectors.

Design goals
────────────
* Zero runtime dependencies beyond the Python stdlib — no ML model, no HTTP.
* Deterministic and fast (< 2 ms for typical queries).
* Multi-language aware: supports English and Vietnamese domain vocabulary.
* Backwards-compatible: does NOT touch ``query_intent_classifier.py``.

Public API
──────────
  from tools.common.query_understanding import QueryUnderstanding

  u = QueryUnderstanding.from_text(
      "function xử lý thanh toán bị lỗi khi user chưa login"
  )
  u.intent          # "semantic"
  u.entities        # ["thanh toán", "user", "login"]
  u.domain_signals  # ["payment", "auth", "error"]
  u.embedding_text  # "function xử lý thanh toán bị lỗi ... payment authentication error"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tools.common.query_intent_classifier import (
    classify_query,
    INTENT_SEMANTIC,
    INTENT_STRUCTURAL,
    INTENT_TEMPORAL,
    INTENT_DEFAULT,
)

# ─────────────────────────────────────────────────────────────────────────────
# Domain signal table
# Each entry: (signal_label, [trigger_terms_EN, trigger_terms_VI, patterns])
# ─────────────────────────────────────────────────────────────────────────────

# (signal, English terms, Vietnamese terms)
_DOMAIN_TABLE: List[Tuple[str, List[str], List[str]]] = [
    (
        "auth",
        [
            "login", "logout", "sign in", "sign out", "authenticate",
            "authentication", "authorize", "authorization", "token",
            "jwt", "session", "credential", "password", "oauth",
            "permission", "access control", "role", "acl", "guard",
            "middleware auth", "is logged", "is authenticated",
        ],
        [
            "đăng nhập", "đăng xuất", "xác thực", "phân quyền",
            "quyền truy cập", "mật khẩu", "phiên làm việc",
            "chưa đăng nhập", "chưa login", "chưa xác thực",
        ],
    ),
    (
        "payment",
        [
            "payment", "pay", "checkout", "billing", "invoice",
            "transaction", "charge", "refund", "stripe", "vnpay",
            "momo", "paypal", "order total", "price", "cart",
            "purchase", "subscription",
        ],
        [
            "thanh toán", "thanh-toán", "hóa đơn", "giao dịch",
            "đơn hàng", "giỏ hàng", "mua hàng", "hoàn tiền",
            "phí", "giá tiền",
        ],
    ),
    (
        "order",
        [
            "order", "orders", "order management", "order flow",
            "order status", "place order", "order item", "order detail",
            "shipment", "delivery", "fulfillment",
        ],
        [
            "đơn hàng", "quản lý đơn", "trạng thái đơn", "xử lý đơn",
            "giao hàng", "vận chuyển", "hoàn hàng",
        ],
    ),
    (
        "error",
        [
            "error", "exception", "bug", "fail", "failure", "crash",
            "stacktrace", "throw", "raises", "broken", "null pointer",
            "undefined", "not found", "500", "400", "403", "404",
        ],
        [
            "lỗi", "bị lỗi", "sai", "không hoạt động", "không chạy",
            "crash", "exception", "bắn lỗi", "trả về lỗi",
        ],
    ),
    (
        "user",
        [
            "user", "users", "account", "profile", "customer",
            "member", "subscriber", "admin", "owner",
        ],
        [
            "người dùng", "tài khoản", "khách hàng", "thành viên",
            "quản trị viên",
        ],
    ),
    (
        "database",
        [
            "database", "db", "sql", "query", "repository",
            "dao", "entity", "table", "schema", "migration",
            "orm", "mongo", "postgres", "mysql", "redis", "cache",
        ],
        [
            "cơ sở dữ liệu", "truy vấn", "bảng dữ liệu", "lưu dữ liệu",
            "kho dữ liệu",
        ],
    ),
    (
        "api",
        [
            "api", "endpoint", "rest", "graphql", "route", "controller",
            "request", "response", "http", "webhook", "grpc", "rpc",
        ],
        [
            "api", "endpoint", "route", "gọi api", "trả về response",
        ],
    ),
    (
        "ui",
        [
            "ui", "frontend", "component", "render", "state",
            "hook", "effect", "view", "screen", "page", "form",
            "button", "modal", "dialog",
        ],
        [
            "giao diện", "màn hình", "trang", "nút", "form",
            "hiển thị",
        ],
    ),
    (
        "notification",
        [
            "notification", "notify", "email", "sms", "push notification",
            "alert", "message", "event bus",
        ],
        [
            "thông báo", "email", "tin nhắn", "cảnh báo",
        ],
    ),
    (
        "validation",
        [
            "validate", "validation", "validator", "sanitize",
            "check", "constraint", "required", "invalid",
        ],
        [
            "kiểm tra", "xác nhận", "hợp lệ", "không hợp lệ",
            "validate",
        ],
    ),
]

# Pre-build a flat lookup: lowercased_term → signal
_TERM_TO_SIGNAL: Dict[str, str] = {}
for _signal, _en_terms, _vi_terms in _DOMAIN_TABLE:
    for _t in _en_terms + _vi_terms:
        _TERM_TO_SIGNAL[_t.lower()] = _signal

# Domain signal → expansion words appended to embedding_text for better recall
_DOMAIN_EXPANSIONS: Dict[str, str] = {
    "auth":         "authentication authorization login session token credential",
    "payment":      "payment transaction checkout billing charge invoice",
    "order":        "order fulfillment shipment delivery purchase",
    "error":        "error exception failure crash bug handler",
    "user":         "user account profile customer member",
    "database":     "database repository query entity schema",
    "api":          "api endpoint route request response controller",
    "ui":           "ui component render view state frontend",
    "notification": "notification email alert message event",
    "validation":   "validation constraint check sanitize invalid",
}

# ─────────────────────────────────────────────────────────────────────────────
# BM25 stop-words (EN + common VI particles)
# ─────────────────────────────────────────────────────────────────────────────

_STOP_WORDS = frozenset(
    [
        # English
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "to", "of", "in", "on", "at", "by", "for", "with", "about",
        "and", "or", "not", "but", "if", "then", "that", "this",
        "it", "its", "i", "we", "you",
        # Vietnamese particles
        "bị", "khi", "mà", "và", "hoặc", "để", "vì", "do",
        "là", "có", "không", "được", "của", "cho", "với",
        "từ", "các", "những", "một", "hai", "đã", "đang",
        "sẽ", "chưa", "cũng", "rồi", "vẫn", "thì",
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# Action verb patterns (EN + VI)
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(handles?|handling)\b", re.I),       "handle"),
    (re.compile(r"\b(process(?:es|ing)?)\b", re.I),      "process"),
    (re.compile(r"\b(creates?|creating)\b", re.I),       "create"),
    (re.compile(r"\b(updates?|updating)\b", re.I),       "update"),
    (re.compile(r"\b(deletes?|deleting)\b", re.I),       "delete"),
    (re.compile(r"\b(validates?|validating)\b", re.I),   "validate"),
    (re.compile(r"\b(authenticates?|authing)\b", re.I),  "authenticate"),
    (re.compile(r"\b(sends?|sending)\b", re.I),          "send"),
    (re.compile(r"\b(receives?|receiving)\b", re.I),     "receive"),
    (re.compile(r"\b(parses?|parsing)\b", re.I),         "parse"),
    (re.compile(r"\b(renders?|rendering)\b", re.I),      "render"),
    (re.compile(r"\b(fetches?|fetching)\b", re.I),       "fetch"),
    (re.compile(r"\b(saves?|saving)\b", re.I),           "save"),
    (re.compile(r"\b(loads?|loading)\b", re.I),          "load"),
    (re.compile(r"\b(throws?|throwing)\b", re.I),        "throw"),
    (re.compile(r"\b(calls?|calling)\b", re.I),          "call"),
    (re.compile(r"\b(returns?|returning)\b", re.I),      "return"),
    # Vietnamese verbs
    (re.compile(r"\b(xử lý|xử-lý)\b", re.I),            "process"),
    (re.compile(r"\b(tạo|tạo mới)\b", re.I),             "create"),
    (re.compile(r"\b(cập nhật)\b", re.I),                "update"),
    (re.compile(r"\b(xóa|xoá)\b", re.I),                 "delete"),
    (re.compile(r"\b(kiểm tra)\b", re.I),                "validate"),
    (re.compile(r"\b(gửi)\b", re.I),                     "send"),
    (re.compile(r"\b(nhận)\b", re.I),                    "receive"),
    (re.compile(r"\b(lưu)\b", re.I),                     "save"),
    (re.compile(r"\b(tải|load)\b", re.I),                "load"),
    (re.compile(r"\b(đăng nhập|đăng-nhập)\b", re.I),     "login"),
    (re.compile(r"\b(thanh toán|thanh-toán)\b", re.I),   "payment"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Entity extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

# CamelCase identifiers  e.g. UserService, PaymentController
_RE_CAMEL = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")

# snake_case identifiers  e.g. handle_payment, validate_token
_RE_SNAKE = re.compile(r"\b[a-z][a-z0-9]+_[a-z][a-zA-Z0-9_]+\b")

# Quoted strings  e.g. "validateToken"
_RE_QUOTED = re.compile(r'["\']([^"\']{2,50})["\']')

# Vietnamese noun phrases — run through multi-word term detection
_RE_WORD_BOUNDARY = re.compile(r"[^\w\s\-àáảãạăắặẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.UNICODE)


def _extract_entities(text: str) -> List[str]:
    """Extract named identifiers and domain noun phrases from raw text."""
    entities: List[str] = []

    # 1. Quoted terms
    for m in _RE_QUOTED.finditer(text):
        entities.append(m.group(1))

    # 2. CamelCase identifiers
    for m in _RE_CAMEL.finditer(text):
        entities.append(m.group(0))

    # 3. snake_case identifiers
    for m in _RE_SNAKE.finditer(text):
        entities.append(m.group(0))

    # 4. Multi-word domain phrases (longest match first from TERM_TO_SIGNAL)
    lower = text.lower()
    # Sort by length descending so longer phrases are matched first
    for phrase in sorted(_TERM_TO_SIGNAL.keys(), key=len, reverse=True):
        if len(phrase) >= 4 and phrase in lower:
            # Only add if it looks like a meaningful domain term, not a stop word
            words = phrase.split()
            if not all(w in _STOP_WORDS for w in words):
                entities.append(phrase)

    # Deduplicate preserving order
    seen: set = set()
    result = []
    for e in entities:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


def _extract_actions(text: str) -> List[str]:
    """Extract action verbs/operations from text."""
    actions: List[str] = []
    seen: set = set()
    for pattern, action in _ACTION_PATTERNS:
        if pattern.search(text) and action not in seen:
            seen.add(action)
            actions.append(action)
    return actions


def _detect_domain_signals(text: str) -> List[str]:
    """Return list of domain signal labels present in the text."""
    lower = text.lower()
    found: set = set()
    # Check each term in our lookup table
    for phrase, signal in _TERM_TO_SIGNAL.items():
        if phrase in lower:
            found.add(signal)
    return sorted(found)


def _extract_keywords(text: str) -> List[str]:
    """
    Tokenize text into BM25-friendly search keywords.
    Strips stop-words and short tokens.
    """
    # Normalize: replace punctuation (except hyphens) with space
    normalized = _RE_WORD_BOUNDARY.sub(" ", text)
    tokens = normalized.lower().split()
    keywords = [
        t for t in tokens
        if len(t) >= 3 and t not in _STOP_WORDS
    ]
    # Deduplicate preserving order
    seen: set = set()
    result = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result


def _build_embedding_text(
    raw_query: str,
    entities: List[str],
    actions: List[str],
    domain_signals: List[str],
) -> str:
    """
    Construct an enriched text string for Qdrant vectorization.

    Strategy:
    - Start with the raw query (preserves phrasing)
    - Append domain expansion terms so that semantic similarity catches
      related nodes even when the query is vague or in Vietnamese
    - Append action verbs for intent anchoring

    The result is NOT shown to users — it is only used as embedder input.
    """
    parts = [raw_query.strip()]

    # Append domain expansions for missing signals
    for sig in domain_signals:
        expansion = _DOMAIN_EXPANSIONS.get(sig, "")
        if expansion:
            parts.append(expansion)

    # Append unique entities (useful for exact-term anchoring)
    if entities:
        parts.append(" ".join(entities[:8]))  # cap to avoid noise

    # Append action verbs
    if actions:
        parts.append(" ".join(actions))

    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class QueryUnderstanding:
    """
    Structured understanding of a raw user query.

    Attributes
    ----------
    raw_query:      Original text as provided by the user.
    intent:         Coarse intent label (semantic / structural / temporal / default).
    entities:       Named identifiers and domain noun phrases extracted from the query.
    keywords:       Clean BM25 tokens (stop-words removed).
    actions:        Action verbs/operations inferred from the query.
    domain_signals: High-level domain labels (auth, payment, order, error, …).
    embedding_text: Enriched string ready for Qdrant embedding; combines raw
                    query with domain expansions for improved recall.
    """

    raw_query:      str
    intent:         str
    entities:       List[str] = field(default_factory=list)
    keywords:       List[str] = field(default_factory=list)
    actions:        List[str] = field(default_factory=list)
    domain_signals: List[str] = field(default_factory=list)
    embedding_text: str = ""

    # ── Structured output (mirrors the spec schema) ──────────────────────────

    def to_dict(self) -> dict:
        """Serialize to the spec's query analysis payload format."""
        return {
            "intent":         self.intent,
            "entities":       self.entities,
            "keywords":       self.keywords,
            "actions":        self.actions,
            "domain_signals": self.domain_signals,
            "embedding_text": self.embedding_text,
            "raw_query":      self.raw_query,
        }

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_text(cls, text: str) -> "QueryUnderstanding":
        """
        Parse a raw user query and return a fully-populated QueryUnderstanding.

        Example
        -------
        >>> u = QueryUnderstanding.from_text(
        ...     "function xử lý thanh toán bị lỗi khi user chưa login"
        ... )
        >>> u.domain_signals
        ['auth', 'error', 'payment', 'user']
        >>> u.actions
        ['process', 'payment', 'login']
        """
        if not text or not text.strip():
            return cls(
                raw_query="",
                intent=INTENT_DEFAULT,
                embedding_text="",
            )

        raw = text.strip()

        intent         = classify_query(raw)
        entities       = _extract_entities(raw)
        actions        = _extract_actions(raw)
        domain_signals = _detect_domain_signals(raw)
        keywords       = _extract_keywords(raw)
        embedding_text = _build_embedding_text(raw, entities, actions, domain_signals)

        return cls(
            raw_query=raw,
            intent=intent,
            entities=entities,
            keywords=keywords,
            actions=actions,
            domain_signals=domain_signals,
            embedding_text=embedding_text,
        )

    @classmethod
    def from_paragraph(cls, text: str, max_chars: int = 2000) -> "QueryUnderstanding":
        """
        Parse a multi-line paragraph. Truncates to `max_chars` to avoid
        excessively large embedding payloads.

        The first meaningful sentence is used as the primary query for intent
        classification; the full truncated text drives entity/domain extraction.
        """
        if not text or not text.strip():
            return cls.from_text("")

        truncated = text.strip()[:max_chars]

        # Use first non-empty line as the primary intent query
        first_line = next(
            (ln.strip() for ln in truncated.splitlines() if ln.strip()),
            truncated,
        )
        intent = classify_query(first_line)

        entities       = _extract_entities(truncated)
        actions        = _extract_actions(truncated)
        domain_signals = _detect_domain_signals(truncated)
        keywords       = _extract_keywords(truncated)
        embedding_text = _build_embedding_text(truncated, entities, actions, domain_signals)

        return cls(
            raw_query=truncated,
            intent=intent,
            entities=entities,
            keywords=keywords,
            actions=actions,
            domain_signals=domain_signals,
            embedding_text=embedding_text,
        )
