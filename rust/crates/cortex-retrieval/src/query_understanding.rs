//! Port của `code-tiny/tools/common/query_understanding.py`.
//!
//! Zero-ML query understanding: entities/keywords/actions/domain_signals +
//! enriched `embedding_text`. Toàn bộ bảng domain (EN + VI) và action patterns
//! port nguyên văn; thứ tự insertion của `_TERM_TO_SIGNAL` được giữ bằng Vec
//! (Python dict insertion order quyết định tie-break khi sort theo length).
//! Golden fixtures: `tests/fixtures/qu_golden.json`.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use crate::intent;

/// (signal, EN terms, VI terms) — thứ tự giữ nguyên từ `_DOMAIN_TABLE`.
const DOMAIN_TABLE: &[(&str, &[&str], &[&str])] = &[
    (
        "auth",
        &[
            "login", "logout", "sign in", "sign out", "authenticate",
            "authentication", "authorize", "authorization", "token",
            "jwt", "session", "credential", "password", "oauth",
            "permission", "access control", "role", "acl", "guard",
            "middleware auth", "is logged", "is authenticated",
        ],
        &[
            "đăng nhập", "đăng xuất", "xác thực", "phân quyền",
            "quyền truy cập", "mật khẩu", "phiên làm việc",
            "chưa đăng nhập", "chưa login", "chưa xác thực",
        ],
    ),
    (
        "payment",
        &[
            "payment", "pay", "checkout", "billing", "invoice",
            "transaction", "charge", "refund", "stripe", "vnpay",
            "momo", "paypal", "order total", "price", "cart",
            "purchase", "subscription",
        ],
        &[
            "thanh toán", "thanh-toán", "hóa đơn", "giao dịch",
            "đơn hàng", "giỏ hàng", "mua hàng", "hoàn tiền",
            "phí", "giá tiền",
        ],
    ),
    (
        "order",
        &[
            "order", "orders", "order management", "order flow",
            "order status", "place order", "order item", "order detail",
            "shipment", "delivery", "fulfillment",
        ],
        &[
            "đơn hàng", "quản lý đơn", "trạng thái đơn", "xử lý đơn",
            "giao hàng", "vận chuyển", "hoàn hàng",
        ],
    ),
    (
        "error",
        &[
            "error", "exception", "bug", "fail", "failure", "crash",
            "stacktrace", "throw", "raises", "broken", "null pointer",
            "undefined", "not found", "500", "400", "403", "404",
        ],
        &[
            "lỗi", "bị lỗi", "sai", "không hoạt động", "không chạy",
            "crash", "exception", "bắn lỗi", "trả về lỗi",
        ],
    ),
    (
        "user",
        &[
            "user", "users", "account", "profile", "customer",
            "member", "subscriber", "admin", "owner",
        ],
        &[
            "người dùng", "tài khoản", "khách hàng", "thành viên",
            "quản trị viên",
        ],
    ),
    (
        "database",
        &[
            "database", "db", "sql", "query", "repository",
            "dao", "entity", "table", "schema", "migration",
            "orm", "mongo", "postgres", "mysql", "redis", "cache",
        ],
        &[
            "cơ sở dữ liệu", "truy vấn", "bảng dữ liệu", "lưu dữ liệu",
            "kho dữ liệu",
        ],
    ),
    (
        "api",
        &[
            "api", "endpoint", "rest", "graphql", "route", "controller",
            "request", "response", "http", "webhook", "grpc", "rpc",
        ],
        &[
            "api", "endpoint", "route", "gọi api", "trả về response",
        ],
    ),
    (
        "ui",
        &[
            "ui", "frontend", "component", "render", "state",
            "hook", "effect", "view", "screen", "page", "form",
            "button", "modal", "dialog",
        ],
        &[
            "giao diện", "màn hình", "trang", "nút", "form",
            "hiển thị",
        ],
    ),
    (
        "notification",
        &[
            "notification", "notify", "email", "sms", "push notification",
            "alert", "message", "event bus",
        ],
        &[
            "thông báo", "email", "tin nhắn", "cảnh báo",
        ],
    ),
    (
        "validation",
        &[
            "validate", "validation", "validator", "sanitize",
            "check", "constraint", "required", "invalid",
        ],
        &[
            "kiểm tra", "xác nhận", "hợp lệ", "không hợp lệ",
            "validate",
        ],
    ),
];

const DOMAIN_EXPANSIONS: &[(&str, &str)] = &[
    ("auth", "authentication authorization login session token credential"),
    ("payment", "payment transaction checkout billing charge invoice"),
    ("order", "order fulfillment shipment delivery purchase"),
    ("error", "error exception failure crash bug handler"),
    ("user", "user account profile customer member"),
    ("database", "database repository query entity schema"),
    ("api", "api endpoint route request response controller"),
    ("ui", "ui component render view state frontend"),
    ("notification", "notification email alert message event"),
    ("validation", "validation constraint check sanitize invalid"),
];

const STOP_WORDS: &[&str] = &[
    // English
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "and", "or", "not", "but", "if", "then", "that", "this",
    "it", "its", "i", "we", "you",
    // Vietnamese particles
    "bị", "khi", "mà", "và", "hoặc", "để", "vì", "do",
    "là", "có", "không", "được", "của", "cho", "với",
    "từ", "các", "những", "một", "hai", "đã", "đang",
    "sẽ", "chưa", "cũng", "rồi", "vẫn", "thì",
];

/// Flat lookup (term đã lowercase → signal) — **thứ tự insertion giống Python dict**
/// (signal theo table order, EN terms trước VI terms). Term trùng giữa 2 bảng
/// (vd "đơn hàng" ở cả payment lẫn order): Python dict **ghi đè value, giữ vị trí
/// đầu** — replicate đúng (order khai báo sau payment nên "đơn hàng" → "order").
fn term_to_signal() -> &'static [(String, &'static str)] {
    static TERM: OnceLock<Vec<(String, &'static str)>> = OnceLock::new();
    TERM.get_or_init(|| {
        let mut out: Vec<(String, &'static str)> = Vec::new();
        for (signal, en, vi) in DOMAIN_TABLE {
            for t in en.iter().chain(vi.iter()) {
                let term = t.to_lowercase();
                if let Some(entry) = out.iter_mut().find(|(existing, _)| *existing == term) {
                    entry.1 = signal;
                } else {
                    out.push((term, signal));
                }
            }
        }
        out
    })
}

/// (regex, action) — thứ tự nguyên văn từ `_ACTION_PATTERNS`.
fn action_patterns() -> &'static [(regex::Regex, &'static str)] {
    static ACTIONS: OnceLock<Vec<(regex::Regex, &'static str)>> = OnceLock::new();
    ACTIONS.get_or_init(|| {
        let defs: &[(&str, &str)] = &[
            (r"\b(handles?|handling)\b", "handle"),
            (r"\b(process(?:es|ing)?)\b", "process"),
            (r"\b(creates?|creating)\b", "create"),
            (r"\b(updates?|updating)\b", "update"),
            (r"\b(deletes?|deleting)\b", "delete"),
            (r"\b(validates?|validating)\b", "validate"),
            (r"\b(authenticates?|authing)\b", "authenticate"),
            (r"\b(sends?|sending)\b", "send"),
            (r"\b(receives?|receiving)\b", "receive"),
            (r"\b(parses?|parsing)\b", "parse"),
            (r"\b(renders?|rendering)\b", "render"),
            (r"\b(fetches?|fetching)\b", "fetch"),
            (r"\b(saves?|saving)\b", "save"),
            (r"\b(loads?|loading)\b", "load"),
            (r"\b(throws?|throwing)\b", "throw"),
            (r"\b(calls?|calling)\b", "call"),
            (r"\b(returns?|returning)\b", "return"),
            // Vietnamese verbs
            (r"\b(xử lý|xử-lý)\b", "process"),
            (r"\b(tạo|tạo mới)\b", "create"),
            (r"\b(cập nhật)\b", "update"),
            (r"\b(xóa|xoá)\b", "delete"),
            (r"\b(kiểm tra)\b", "validate"),
            (r"\b(gửi)\b", "send"),
            (r"\b(nhận)\b", "receive"),
            (r"\b(lưu)\b", "save"),
            (r"\b(tải|load)\b", "load"),
            (r"\b(đăng nhập|đăng-nhập)\b", "login"),
            (r"\b(thanh toán|thanh-toán)\b", "payment"),
        ];
        defs.iter()
            .map(|(pattern, action)| {
                (
                    regex::Regex::new(&format!(r"(?i){pattern}")).expect("action pattern hợp lệ"),
                    *action,
                )
            })
            .collect()
    })
}

fn regex_of(pattern: &str) -> regex::Regex {
    regex::Regex::new(pattern).expect("pattern tĩnh hợp lệ")
}

fn extract_entities(text: &str) -> Vec<String> {
    let mut entities: Vec<String> = Vec::new();

    // 1. Quoted terms
    let re_quoted = regex_of(r#"[\"']([^\"']{2,50})[\"']"#);
    for caps in re_quoted.captures_iter(text) {
        entities.push(caps[1].to_string());
    }

    // 2. CamelCase identifiers
    let re_camel = regex_of(r"\b[A-Z][a-zA-Z0-9]{2,}\b");
    for m in re_camel.find_iter(text) {
        entities.push(m.as_str().to_string());
    }

    // 3. snake_case identifiers
    let re_snake = regex_of(r"\b[a-z][a-z0-9]+_[a-z][a-zA-Z0-9_]+\b");
    for m in re_snake.find_iter(text) {
        entities.push(m.as_str().to_string());
    }

    // 4. Multi-word domain phrases — sort theo length desc, stable (tie giữ
    // insertion order như Python sorted() trên dict keys).
    let lower = text.to_lowercase();
    let mut phrases: Vec<&(String, &str)> = term_to_signal().iter().collect();
    phrases.sort_by_key(|p| std::cmp::Reverse(p.0.chars().count()));
    for (phrase, _) in phrases {
        if phrase.chars().count() >= 4 && lower.contains(phrase.as_str()) {
            let words: Vec<&str> = phrase.split_whitespace().collect();
            if !words.iter().all(|w| STOP_WORDS.contains(w)) {
                entities.push(phrase.clone());
            }
        }
    }

    // Deduplicate preserving order (key = lowercase)
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for e in entities {
        let key = e.to_lowercase();
        if seen.insert(key) {
            result.push(e);
        }
    }
    result
}

fn extract_actions(text: &str) -> Vec<String> {
    let mut actions = Vec::new();
    let mut seen = BTreeSet::new();
    for (pattern, action) in action_patterns() {
        if pattern.is_match(text) && seen.insert(action.to_string()) {
            actions.push(action.to_string());
        }
    }
    actions
}

fn detect_domain_signals(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let found: BTreeSet<&str> = term_to_signal()
        .iter()
        .filter(|(phrase, _)| lower.contains(phrase.as_str()))
        .map(|(_, signal)| *signal)
        .collect();
    found.into_iter().map(String::from).collect()
}

fn extract_keywords(text: &str) -> Vec<String> {
    // Char class port nguyên văn từ `_RE_WORD_BOUNDARY` (re.UNICODE).
    let re_word_boundary = regex_of(
        r"[^\w\s\-àáảãạăắặẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]",
    );
    let normalized = re_word_boundary.replace_all(text, " ");
    let lowered = normalized.to_lowercase();
    let tokens = lowered.split_whitespace();
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for t in tokens {
        if t.chars().count() >= 3 && !STOP_WORDS.contains(&t) && seen.insert(t.to_string()) {
            result.push(t.to_string());
        }
    }
    result
}

fn build_embedding_text(
    raw_query: &str,
    entities: &[String],
    actions: &[String],
    domain_signals: &[String],
) -> String {
    let mut parts = vec![raw_query.trim().to_string()];
    for sig in domain_signals {
        if let Some((_, expansion)) = DOMAIN_EXPANSIONS.iter().find(|(k, _)| k == sig)
            && !expansion.is_empty()
        {
            parts.push(expansion.to_string());
        }
    }
    if !entities.is_empty() {
        parts.push(entities.iter().take(8).cloned().collect::<Vec<_>>().join(" "));
    }
    if !actions.is_empty() {
        parts.push(actions.join(" "));
    }
    parts.join(" ")
}

/// Structured understanding của raw query — tương đương dataclass `QueryUnderstanding`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueryUnderstanding {
    pub raw_query: String,
    pub intent: &'static str,
    pub entities: Vec<String>,
    pub keywords: Vec<String>,
    pub actions: Vec<String>,
    pub domain_signals: Vec<String>,
    pub embedding_text: String,
}

impl QueryUnderstanding {
    /// Tương đương `QueryUnderstanding.from_text(text)`.
    pub fn from_text(text: &str) -> Self {
        if text.trim().is_empty() {
            return Self {
                raw_query: String::new(),
                intent: intent::INTENT_DEFAULT,
                entities: Vec::new(),
                keywords: Vec::new(),
                actions: Vec::new(),
                domain_signals: Vec::new(),
                embedding_text: String::new(),
            };
        }
        let raw = text.trim();
        Self {
            raw_query: raw.to_string(),
            intent: intent::classify_query(raw),
            entities: extract_entities(raw),
            actions: extract_actions(raw),
            domain_signals: detect_domain_signals(raw),
            keywords: extract_keywords(raw),
            embedding_text: String::new(),
        }
        .with_embedding_text()
    }

    /// Tương đương `QueryUnderstanding.from_paragraph(text, max_chars)`.
    /// Truncate theo **code points** (Python string slicing), không phải bytes.
    ///
    /// Lệch nhỏ có chủ ý: `splitlines()` Python tách thêm vài separator Unicode
    /// exotics (\u2028…) mà `lines()` của Rust không tách — fixture tránh
    /// các separator này.
    pub fn from_paragraph(text: &str, max_chars: usize) -> Self {
        if text.trim().is_empty() {
            return Self::from_text("");
        }
        let truncated: String = text.trim().chars().take(max_chars).collect();
        let first_line = truncated
            .lines()
            .map(str::trim)
            .find(|ln| !ln.is_empty())
            .unwrap_or(&truncated);
        Self {
            raw_query: truncated.clone(),
            intent: intent::classify_query(first_line),
            entities: extract_entities(&truncated),
            actions: extract_actions(&truncated),
            domain_signals: detect_domain_signals(&truncated),
            keywords: extract_keywords(&truncated),
            embedding_text: String::new(),
        }
        .with_embedding_text()
    }

    fn with_embedding_text(mut self) -> Self {
        self.embedding_text = build_embedding_text(
            &self.raw_query,
            &self.entities,
            &self.actions,
            &self.domain_signals,
        );
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn docstring_example_from_python() {
        let u = QueryUnderstanding::from_text(
            "function xử lý thanh toán bị lỗi khi user chưa login",
        );
        assert_eq!(u.domain_signals, vec!["auth", "error", "payment", "user"]);
        // Docstring Python ghi ['process','payment','login'] nhưng STALE —
        // pattern "login" chỉ match "đăng nhập" (VI); "login" EN không có pattern.
        // Code Python thật trả ['process','payment'] (golden fixture xác nhận).
        assert_eq!(u.actions, vec!["process", "payment"]);
    }

    #[test]
    fn empty_and_whitespace() {
        let u = QueryUnderstanding::from_text("");
        assert!(u.raw_query.is_empty() && u.intent == intent::INTENT_DEFAULT);
        assert!(QueryUnderstanding::from_text("   ").embedding_text.is_empty());
    }

    #[test]
    fn camel_and_snake_entities() {
        let u = QueryUnderstanding::from_text("\"validateToken\" calls UserService and handle_payment");
        assert!(u.entities.contains(&"validateToken".to_string()));
        assert!(u.entities.contains(&"UserService".to_string()));
        assert!(u.entities.contains(&"handle_payment".to_string()));
    }
}
