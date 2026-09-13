//! Port `tools/web_framework/models.py` — EndpointFact + WebAnalysisResult +
//! `stable_id`.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::pyutil::sha256_hex;

/// `stable_id(*parts)` — `"web::" + sha256("\x1f".join(str(p).strip()))[:32]`.
/// Mỗi part được `str()` rồi `.strip()` trước khi nối.
pub fn stable_id(parts: &[String]) -> String {
    let normalized = parts
        .iter()
        .map(|part| part.trim().to_string())
        .collect::<Vec<_>>()
        .join("\u{1f}");
    format!("web::{}", &sha256_hex(normalized.as_bytes())[..32])
}

#[derive(Debug, Clone)]
pub struct EndpointFact {
    pub endpoint_id: String,
    pub project_id: String,
    pub framework: String,
    pub http_method: String,
    pub path: String,
    pub file_path: String,
    pub start_line: i64,
    pub handler_name: String,
    pub handler_scope: String,
    pub handler_file: String,
    pub handler_label: String,
    pub resolution_status: String,
    pub confidence: f64,
}

impl EndpointFact {
    /// `node_row()` — key order khớp dict Python (quan trọng cho dry-run JSON).
    pub fn node_row(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.endpoint_id));
        row.insert("symbol_id".into(), json!(self.endpoint_id));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("framework".into(), json!(self.framework));
        row.insert("name".into(), json!(format!("{} {}", self.http_method, self.path)));
        row.insert("http_method".into(), json!(self.http_method));
        row.insert("path".into(), json!(self.path));
        row.insert("route".into(), json!(self.path));
        row.insert("file_path".into(), json!(self.file_path));
        row.insert("start_line".into(), json!(self.start_line));
        row.insert("handler_name".into(), json!(self.handler_name));
        row.insert("handler_scope".into(), json!(self.handler_scope));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("confidence".into(), json!(self.confidence));
        row
    }

    /// `relationship_row()`.
    pub fn relationship_row(&self) -> Map<String, Value> {
        let id = stable_id(&[
            self.endpoint_id.clone(),
            "HANDLES".into(),
            self.handler_name.clone(),
            self.handler_file.clone(),
        ]);
        let mut row = Map::new();
        row.insert("id".into(), json!(id));
        row.insert("type".into(), json!("HANDLES"));
        row.insert("endpoint_id".into(), json!(self.endpoint_id));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("framework".into(), json!(self.framework));
        row.insert("handler_name".into(), json!(self.handler_name));
        row.insert("handler_scope".into(), json!(self.handler_scope));
        row.insert("handler_file".into(), json!(self.handler_file));
        row.insert("handler_label".into(), json!(self.handler_label));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("confidence".into(), json!(self.confidence));
        row
    }
}

#[derive(Debug, Clone)]
pub struct WebAnalysisResult {
    pub project_id: String,
    pub endpoints: Vec<EndpointFact>,
}

/// Cặp (node_rows, relationship_rows) trả về cho writer.
pub type GraphRows = (Vec<Map<String, Value>>, Vec<Map<String, Value>>);

impl WebAnalysisResult {
    /// `graph_rows()` — (node_rows, relationship_rows); relationships chỉ cho
    /// endpoint resolved có handler_file.
    pub fn graph_rows(&self) -> GraphRows {
        let node_rows = self.endpoints.iter().map(EndpointFact::node_row).collect();
        let relationship_rows = self
            .endpoints
            .iter()
            .filter(|item| item.resolution_status == "resolved" && !item.handler_file.is_empty())
            .map(EndpointFact::relationship_row)
            .collect();
        (node_rows, relationship_rows)
    }
}

/// Dedupe endpoint theo `endpoint_id` giữ LAST occurrence (dict comprehension
/// Python: key trùng bị ghi đè bởi phần tử sau), rồi sort theo
/// (framework, file_path, start_line) — sort ổn định nên tie giữ insertion order.
pub fn dedupe_and_sort(endpoints: Vec<EndpointFact>) -> Vec<EndpointFact> {
    type IdIndex = BTreeMap<String, usize>;
    let mut by_id: IdIndex = BTreeMap::new();
    let mut ordered: Vec<EndpointFact> = Vec::new();
    for endpoint in endpoints {
        match by_id.get(&endpoint.endpoint_id) {
            Some(&index) => ordered[index] = endpoint,
            None => {
                by_id.insert(endpoint.endpoint_id.clone(), ordered.len());
                ordered.push(endpoint);
            }
        }
    }
    let mut order: Vec<usize> = (0..ordered.len()).collect();
    order.sort_by(|a, b| {
        let (fa, file_a, line_a) = (&ordered[*a].framework, &ordered[*a].file_path, ordered[*a].start_line);
        let (fb, file_b, line_b) = (&ordered[*b].framework, &ordered[*b].file_path, ordered[*b].start_line);
        (fa, file_a, line_a).cmp(&(fb, file_b, line_b))
    });
    order.into_iter().map(|index| ordered[index].clone()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_id_matches_python() {
        // stable_id("proj", "fastapi", "GET", "/x", "main.py", 3)
        let id = stable_id(&[
            "proj".into(),
            "fastapi".into(),
            "GET".into(),
            "/x".into(),
            "main.py".into(),
            "3".into(),
        ]);
        assert!(id.starts_with("web::"));
        assert_eq!(id.len(), 5 + 32);
        // Dấu xuống dòng và khoảng trắng hai đầu bị strip.
        let id2 = stable_id(&[" proj ".into(), "fastapi".into(), "GET".into(), "/x".into(), "main.py".into(), "3".into()]);
        assert_eq!(id, id2);
    }

    #[test]
    fn dedupe_keeps_last_then_sorts() {
        let make = |method: &str, line: i64, framework: &str| EndpointFact {
            endpoint_id: format!("{method}-{line}-{framework}"),
            project_id: "p".into(),
            framework: framework.into(),
            http_method: method.into(),
            path: "/".into(),
            file_path: "a.py".into(),
            start_line: line,
            handler_name: "h".into(),
            handler_scope: String::new(),
            handler_file: String::new(),
            handler_label: "Function".into(),
            resolution_status: "unresolved".into(),
            confidence: 0.7,
        };
        let result = dedupe_and_sort(vec![
            make("GET", 5, "django"),
            make("GET", 1, "fastapi"),
            make("GET", 5, "django"), // trùng id → ghi đè (last wins)
        ]);
        assert_eq!(result.len(), 2);
        // Sort key (framework, file_path, start_line): "django" < "fastapi".
        assert_eq!(result[0].endpoint_id, "GET-5-django");
        assert_eq!(result[1].endpoint_id, "GET-1-fastapi");
    }
}
