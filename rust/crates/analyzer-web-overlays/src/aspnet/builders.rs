//! Port `tools/common/aspnet/builders.py` + `identity.py` (semantic_id /
//! relationship_id / module_id) — stable ids tính từ Python repr của
//! coordinates (tuple lồng nhau dùng repr kiểu `('a', 'b')`).

use serde_json::{Map, Value};

use crate::aspnet::models::{
    SemanticFact, SemanticRelationship, SourceSpan,
};
use crate::pyutil::stable_digest;

/// Coordinate kiểu Python (str hoặc tuple) — `str(x)` phía Python.
#[derive(Debug, Clone)]
pub enum Coordinate {
    Text(String),
    Tuple(Vec<Coordinate>),
}

impl Coordinate {
    pub fn text(value: &str) -> Self {
        Coordinate::Text(value.to_string())
    }

    pub fn tuple(items: Vec<Coordinate>) -> Self {
        Coordinate::Tuple(items)
    }

    /// `repr()` phía Python cho element (str → repr chuỗi, tuple → repr tuple).
    fn to_python_repr(&self) -> String {
        match self {
            Coordinate::Text(text) => py_repr(text),
            Coordinate::Tuple(items) => {
                let inner = items
                    .iter()
                    .map(|item| item.to_python_repr())
                    .collect::<Vec<_>>()
                    .join(", ");
                if items.len() == 1 {
                    format!("({inner},)")
                } else {
                    format!("({inner})")
                }
            }
        }
    }

    /// `str(value)` của Python cho tuple/scalar — str(tuple) = repr các
    /// element, không có outer quote.
    pub fn to_python_str(&self) -> String {
        match self {
            Coordinate::Text(text) => text.clone(),
            Coordinate::Tuple(items) => {
                let inner = items
                    .iter()
                    .map(|item| item.to_python_repr())
                    .collect::<Vec<_>>()
                    .join(", ");
                if items.len() == 1 {
                    format!("({inner},)")
                } else {
                    format!("({inner})")
                }
            }
        }
    }
}

/// `repr(str)` của Python (quote đơn, trừ khi chuỗi chứa `'` mà không có `"`).
pub fn py_repr(text: &str) -> String {
    let has_single = text.contains('\'');
    let has_double = text.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            control if (control as u32) < 0x20 => {
                out.push_str(&format!("\\x{:02x}", control as u32));
            }
            other => {
                if other as u32 == quote as u32 {
                    out.push('\\');
                }
                out.push(other);
            }
        }
    }
    out.push(quote);
    out
}

fn safe_token(value: &str) -> String {
    // re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    let lowered = value.to_lowercase();
    let mut out = String::with_capacity(lowered.len());
    let mut in_run = false;
    for character in lowered.chars() {
        if character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_' {
            out.push(character);
            in_run = false;
        } else if !in_run {
            out.push('_');
            in_run = true;
        }
    }
    out.trim_matches('_').to_string()
}

/// `semantic_id(framework, project_id, module_id, kind, *coordinates)`.
pub fn semantic_id(
    framework: &str,
    project_id: &str,
    module_id: &str,
    kind: &str,
    coordinates: &[String],
) -> String {
    let safe_framework = safe_token(framework);
    let safe_kind = {
        let token = safe_token(kind);
        if token.is_empty() {
            "fact".to_string()
        } else {
            token
        }
    };
    let mut parts = vec![project_id.to_string(), module_id.to_string()];
    parts.extend(coordinates.iter().cloned());
    format!("{safe_framework}::{safe_kind}::{}", stable_digest(&parts, 24))
}

/// `relationship_id(...)`.
pub fn relationship_id(
    framework: &str,
    project_id: &str,
    module_id: &str,
    relationship: &str,
    from_id: &str,
    to_id: &str,
    coordinates: &[String],
) -> String {
    let mut parts = vec![
        relationship.to_string(),
        from_id.to_string(),
        to_id.to_string(),
    ];
    parts.extend(coordinates.iter().cloned());
    semantic_id(framework, project_id, module_id, "relationship", &parts)
}

/// `module_id(framework, module_path)`.
pub fn module_id(framework: &str, module_path: &str) -> String {
    let normalized = crate::pyutil::normalize_relative_path(module_path);
    let normalized = if normalized.is_empty() { ".".to_string() } else { normalized };
    format!("{framework}::module::{}", stable_digest(&[normalized], 24))
}

/// Tham số `fact()` của Python.
pub struct FactInput<'a> {
    pub kind: &'a str,
    pub name: &'a str,
    pub framework: &'a str,
    pub project_id: &'a str,
    pub project_name: &'a str,
    pub module_id: &'a str,
    pub source: SourceSpan,
    pub coordinates: Vec<Coordinate>,
    pub confidence: f64,
    pub resolution_status: &'a str,
    pub extraction_method: &'a str,
    pub source_symbol_id: &'a str,
    pub properties: Map<String, Value>,
}

impl<'a> FactInput<'a> {
    pub fn new(kind: &'a str, name: &'a str, source: SourceSpan) -> Self {
        Self {
            kind,
            name,
            framework: "",
            project_id: "",
            project_name: "",
            module_id: "",
            source,
            coordinates: Vec::new(),
            confidence: 1.0,
            resolution_status: "resolved",
            extraction_method: "source",
            source_symbol_id: "",
            properties: Map::new(),
        }
    }
}

/// `fact(...)` — stable_id gồm (file_path, start_line, name, *coordinates).
pub fn fact(input: FactInput) -> SemanticFact {
    let mut coordinates: Vec<String> = vec![
        input.source.file_path.clone(),
        input.source.start_line.to_string(),
        input.name.to_string(),
    ];
    coordinates.extend(input.coordinates.iter().map(|item| item.to_python_str()));
    let stable = semantic_id(
        input.framework,
        input.project_id,
        input.module_id,
        input.kind,
        &coordinates,
    );
    SemanticFact {
        kind: input.kind.to_string(),
        stable_id: stable,
        name: input.name.to_string(),
        framework: input.framework.to_string(),
        project_id: input.project_id.to_string(),
        project_name: input.project_name.to_string(),
        module_id: input.module_id.to_string(),
        source: input.source,
        confidence: input.confidence,
        resolution_status: input.resolution_status.to_string(),
        extraction_method: input.extraction_method.to_string(),
        source_symbol_id: input.source_symbol_id.to_string(),
        properties: input.properties,
    }
}

/// `relationship(...)` — evidence = source hoặc source_fact.source; id gồm
/// (type, from, to, evidence.file_path, evidence.start_line).
#[allow(clippy::too_many_arguments)]
pub fn relationship(
    relationship_type: &str,
    source_fact: &SemanticFact,
    target_fact: &SemanticFact,
    source: Option<SourceSpan>,
    confidence: f64,
    resolution_status: &str,
    reason: &str,
    properties: Map<String, Value>,
) -> SemanticRelationship {
    let evidence = source.unwrap_or_else(|| source_fact.source.clone());
    let stable = relationship_id(
        &source_fact.framework,
        &source_fact.project_id,
        &source_fact.module_id,
        relationship_type,
        &source_fact.stable_id,
        &target_fact.stable_id,
        &[evidence.file_path.clone(), evidence.start_line.to_string()],
    );
    SemanticRelationship {
        stable_id: stable,
        relationship_type: relationship_type.to_string(),
        from_id: source_fact.stable_id.clone(),
        to_id: target_fact.stable_id.clone(),
        from_label: source_fact.kind.clone(),
        to_label: target_fact.kind.clone(),
        framework: source_fact.framework.clone(),
        project_id: source_fact.project_id.clone(),
        module_id: source_fact.module_id.clone(),
        source: evidence,
        confidence,
        resolution_status: resolution_status.to_string(),
        reason: reason.to_string(),
        properties,
        from_generated: true,
        to_generated: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn semantic_id_matches_python_format() {
        let id = semantic_id("aspnet_core", "p", "m", "Route", &["a".into(), "1".into()]);
        assert!(id.starts_with("aspnet_core::route::"));
        assert_eq!(id.len(), "aspnet_core::route::".len() + 24);
    }

    #[test]
    fn coordinate_tuple_repr() {
        let coordinate = Coordinate::tuple(vec![
            Coordinate::text("MapGet"),
            Coordinate::tuple(vec![Coordinate::text("/a"), Coordinate::text("/b")]),
        ]);
        assert_eq!(coordinate.to_python_str(), "('MapGet', ('/a', '/b'))");
        assert_eq!(
            Coordinate::tuple(vec![Coordinate::text("only")]).to_python_str(),
            "('only',)"
        );
    }

    #[test]
    fn module_id_empty_is_dot() {
        let id = module_id("aspnet_framework", "");
        assert!(id.starts_with("aspnet_framework::module::"));
    }
}
