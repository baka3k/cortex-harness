//! Port của `tools/graph/core/query_normalize.py` — provider-neutral query
//! rewriting. Cả FalkorDB và Ladybug từ chối Neo4j-5 importing-variable
//! subquery và không có zero-arg `datetime()`; các rewriter sống ở đây để
//! quy tắc không drift giữa 2 backend.

use std::collections::BTreeMap;

use serde_json::Value;

/// `_MUTATING_CYPHER_RE` — từ khoá mutation trong query.
fn is_mutating(query: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(
            r"(?i)\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|FOREACH|LOAD\s+CSV)\b",
        )
        .unwrap()
    });
    re.is_match(query)
}

/// `normalize_call_importing_subqueries`: `CALL (var) {` → `CALL { WITH var`.
pub fn normalize_call_importing_subqueries(query: &str) -> String {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(r"CALL\s+\(([A-Za-z_][A-Za-z0-9_]*)\)\s*\{").unwrap()
    });
    re.replace_all(query, "CALL { WITH $1").into_owned()
}

/// `utc_timestamp()` của Python — ISO-8601 mili-giây, suffix `Z`.
/// (Không có phụ thuộc chrono; format khớp
/// `datetime.now(timezone.utc).isoformat(timespec="milliseconds")`.)
pub fn utc_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let millis = now.subsec_millis();
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = rem / 3600;
    let minute = (rem % 3600) / 60;
    let second = rem % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millis:03}Z")
}

/// Thuật toán Howard Hinnant `civil_from_days` — ngày epoch → (y, m, d).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Kết quả `rewrite_datetime_call`: query mới + tên param timestamp + giá trị.
pub struct DatetimeRewrite {
    pub query: String,
    pub param_name: String,
    pub param_value: String,
}

/// `rewrite_datetime_call` — thay zero-arg `datetime()` bằng tham số ISO-8601.
///
/// * `replacement_template` chứa `{param}` (ví dụ `${param}` cho FalkorDB,
///   `timestamp(${param})` cho Ladybug);
/// * tên param collided với params sẵn có bằng tiền tố `__` lặp.
pub fn rewrite_datetime_call(
    query: &str,
    parameters: &BTreeMap<String, Value>,
    replacement_template: &str,
    param_prefix: &str,
) -> Option<DatetimeRewrite> {
    if !query.contains("datetime()") {
        return None;
    }
    let mut param_name = param_prefix.to_string();
    while parameters.contains_key(&param_name) {
        param_name = format!("_{param_name}");
    }
    let rendered = replacement_template.replace("{param}", &param_name);
    Some(DatetimeRewrite {
        query: query.replace("datetime()", &rendered),
        param_name,
        param_value: utc_timestamp(),
    })
}

/// `is_retryable_read` — chỉ retry query đọc thuần.
pub fn is_retryable_read(query: &str) -> bool {
    let first = query
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_ascii_uppercase();
    matches!(
        first.as_str(),
        "MATCH" | "OPTIONAL" | "UNWIND" | "WITH" | "RETURN" | "SHOW" | "EXPLAIN" | "PROFILE"
    ) && !is_mutating(query)
}

/// `is_write_intent` của ladybug_driver — token đầu thuộc tập write-intent
/// (Cypher DML/DDL + COPY/INSTALL/LOAD/... của Ladybug).
pub fn is_write_intent(query: &str) -> bool {
    let first = query
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_ascii_uppercase();
    matches!(
        first.as_str(),
        "CREATE"
            | "MERGE"
            | "SET"
            | "DELETE"
            | "DETACH"
            | "DROP"
            | "ALTER"
            | "REMOVE"
            | "FOREACH"
            | "COPY"
            | "INSTALL"
            | "LOAD"
            | "CHECKPOINT"
            | "EXPORT"
            | "IMPORT"
    ) || is_mutating(query)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn call_importing_subquery_rewritten() {
        let q = "UNWIND $rows AS row CALL (row) { MATCH (n) RETURN n } RETURN n";
        assert_eq!(
            normalize_call_importing_subqueries(q),
            "UNWIND $rows AS row CALL { WITH row MATCH (n) RETURN n } RETURN n"
        );
        let plain = "MATCH (n) RETURN n";
        assert_eq!(normalize_call_importing_subqueries(plain), plain);
    }

    #[test]
    fn datetime_rewrite_ladybug_form() {
        let params = BTreeMap::new();
        let out = rewrite_datetime_call(
            "SET n.updated_at = datetime()",
            &params,
            "timestamp(${param})",
            "__ladybug_now",
        )
        .unwrap();
        assert!(out.query.starts_with("SET n.updated_at = timestamp($"));
        assert!(out.query.ends_with(')'));
        assert!(out.param_value.ends_with('Z'));
        assert_eq!(out.param_name, "__ladybug_now");
    }

    #[test]
    fn datetime_rewrite_noop_without_call() {
        let params = BTreeMap::new();
        assert!(rewrite_datetime_call(
            "SET n.updated_at = $ts",
            &params,
            "timestamp(${param})",
            "__now"
        )
        .is_none());
    }

    #[test]
    fn retry_classification() {
        assert!(is_retryable_read("MATCH (n) RETURN count(n)"));
        assert!(!is_retryable_read("MERGE (n:N {id: 'x'})"));
        assert!(!is_retryable_read(
            "UNWIND $rows AS row MERGE (n:N {id: row.id})"
        ));
        assert!(is_write_intent("COPY `T` FROM 'x'"));
    }
}
