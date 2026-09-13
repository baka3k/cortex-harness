//! Port `tools/common/parse_quality.py` — damage summary, semantic yield,
//! parse context, tier classification, quality record dict + aggregates.
//! Dùng cho parse_meta/quality_provenance (payload) và artifact
//! `--parse-quality-report`.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

pub const PARSE_QUALITY_SCHEMA_VERSION: &str = "2";
pub const RECOVERY_POLICY_VERSION: &str = "2-tree-sitter-structure-only";
pub const MAX_DAMAGE_SIGNATURES: usize = 32;

const RETRY_DAMAGE_RATIO: f64 = 0.08;
const QUARANTINE_DAMAGE_RATIO: f64 = 0.35;

const CRITICAL_STRUCTURAL_KINDS: [&str; 12] = [
    "function_definition",
    "function_declarator",
    "declaration",
    "parameter_declaration",
    "parameter_list",
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "namespace_definition",
    "compound_statement",
    "field_declaration",
];

#[derive(Debug, Clone, Default)]
pub struct DamageSummary {
    pub error_count: i64,
    pub missing_count: i64,
    pub damaged_bytes: i64,
    pub source_bytes: i64,
    pub damaged_span_ratio: f64,
    pub critical_structural_damage: bool,
    pub structural_contexts: Vec<String>,
    pub signatures: Vec<String>,
}

impl DamageSummary {
    pub fn diagnostic_count(&self) -> i64 {
        self.error_count + self.missing_count
    }

    /// `DamageSummary.to_dict()` của dataclass (thứ tự field của dataclass).
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("error_count".into(), json!(self.error_count));
        m.insert("missing_count".into(), json!(self.missing_count));
        m.insert("damaged_bytes".into(), json!(self.damaged_bytes));
        m.insert("source_bytes".into(), json!(self.source_bytes));
        m.insert("damaged_span_ratio".into(), json!(self.damaged_span_ratio));
        m.insert(
            "critical_structural_damage".into(),
            json!(self.critical_structural_damage),
        );
        m.insert(
            "structural_contexts".into(),
            json!(self.structural_contexts),
        );
        m.insert("signatures".into(), json!(self.signatures));
        m
    }
}

#[derive(Debug, Clone, Default)]
pub struct SemanticYield {
    pub function_count: i64,
    pub type_count: i64,
    pub declaration_count: i64,
    pub stable_scope_count: i64,
    pub call_count: i64,
    pub include_count: i64,
}

impl SemanticYield {
    pub fn top_level_count(&self) -> i64 {
        self.function_count + self.type_count + self.declaration_count
    }

    pub fn useful_reference_count(&self) -> i64 {
        self.call_count + self.include_count
    }

    pub fn to_dict(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("function_count".into(), json!(self.function_count));
        m.insert("type_count".into(), json!(self.type_count));
        m.insert("declaration_count".into(), json!(self.declaration_count));
        m.insert("stable_scope_count".into(), json!(self.stable_scope_count));
        m.insert("call_count".into(), json!(self.call_count));
        m.insert("include_count".into(), json!(self.include_count));
        m
    }
}

#[derive(Debug, Clone)]
pub struct ParseContext {
    pub backend: String,
    pub parser_language: String,
    pub parser_version: String,
    pub grammar_version: String,
    pub source_encoding: String,
    pub lossy_decode: bool,
    pub compile_context_available: bool,
    pub compile_context_fingerprint: String,
    pub masking_fingerprint: String,
    pub recovery_policy_version: String,
}

impl Default for ParseContext {
    fn default() -> Self {
        Self {
            backend: "tree_sitter".into(),
            parser_language: "unknown".into(),
            parser_version: "unknown".into(),
            grammar_version: "unknown".into(),
            source_encoding: "unknown".into(),
            lossy_decode: false,
            compile_context_available: false,
            compile_context_fingerprint: String::new(),
            masking_fingerprint: String::new(),
            recovery_policy_version: RECOVERY_POLICY_VERSION.into(),
        }
    }
}

impl ParseContext {
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("backend".into(), json!(self.backend));
        m.insert("parser_language".into(), json!(self.parser_language));
        m.insert("parser_version".into(), json!(self.parser_version));
        m.insert("grammar_version".into(), json!(self.grammar_version));
        m.insert("source_encoding".into(), json!(self.source_encoding));
        m.insert("lossy_decode".into(), json!(self.lossy_decode));
        m.insert(
            "compile_context_available".into(),
            json!(self.compile_context_available),
        );
        m.insert(
            "compile_context_fingerprint".into(),
            json!(self.compile_context_fingerprint),
        );
        m.insert(
            "masking_fingerprint".into(),
            json!(self.masking_fingerprint),
        );
        m.insert(
            "recovery_policy_version".into(),
            json!(self.recovery_policy_version),
        );
        m
    }
}

pub fn source_fingerprint(source: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(source);
    hex(&hasher.finalize())
}

/// `context_fingerprint` — sha256 của canonical JSON (sort_keys, compact).
pub fn context_fingerprint(context: &ParseContext, source_hash: &str) -> String {
    let mut payload = std::collections::BTreeMap::new();
    payload.insert("schema_version", json!(PARSE_QUALITY_SCHEMA_VERSION));
    payload.insert("source_fingerprint", json!(source_hash));
    payload.insert("context", Value::Object(context.to_dict()));
    // sort_keys=True ở mức top-level + trong context dict (Map đã sorted).
    let mut encoded = String::from("{");
    let mut first = true;
    for (key, value) in &payload {
        if !first {
            encoded.push(',');
        }
        first = false;
        encoded.push_str(&crate::identity::json_ensure_ascii(key));
        encoded.push(':');
        encoded.push_str(&canonical_json(value));
    }
    encoded.push('}');
    let mut hasher = Sha256::new();
    hasher.update(encoded.as_bytes());
    hex(&hasher.finalize())
}

/// Canonical JSON gần đúng `json.dumps(..., ensure_ascii=True,
/// sort_keys=True, separators=(",", ":"))` cho values hữu hạn của record.
pub fn canonical_json(value: &Value) -> String {
    match value {
        Value::Null => "null".into(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => crate::identity::json_ensure_ascii(s),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(canonical_json).collect();
            format!("[{}]", parts.join(","))
        }
        Value::Object(map) => {
            let mut parts: Vec<String> = Vec::with_capacity(map.len());
            for (key, item) in map {
                parts.push(format!(
                    "{}:{}",
                    crate::identity::json_ensure_ascii(key),
                    canonical_json(item)
                ));
            }
            format!("{{{}}}", parts.join(","))
        }
    }
}

/// `collect_tree_sitter_damage` — walk đếm ERROR/MISSING node, gộp span,
/// signature `type@line:col:context`.
pub fn collect_tree_sitter_damage(
    root: tree_sitter::Node<'_>,
    source_size: usize,
) -> DamageSummary {
    let mut error_count = 0i64;
    let mut missing_count = 0i64;
    let mut intervals: Vec<(usize, usize)> = Vec::new();
    let mut contexts: Vec<String> = Vec::new();
    let mut signatures: Vec<String> = Vec::new();
    let mut critical = false;
    // stack: (node, parent_kind)
    let mut stack: Vec<(tree_sitter::Node<'_>, Option<String>)> = vec![(root, None)];
    while let Some((node, parent_kind)) = stack.pop() {
        let node_type = node.kind().to_string();
        let is_error = node.is_error() || node_type == "ERROR";
        let is_missing = node.is_missing();
        if is_error || is_missing {
            if is_error {
                error_count += 1;
            }
            if is_missing {
                missing_count += 1;
            }
            let start = node.start_byte().min(source_size);
            let mut end = node.end_byte().max(start).min(source_size);
            if end == start && source_size > 0 {
                end = (start + 1).min(source_size);
            }
            if end > start {
                intervals.push((start, end));
            }
            let context_kind = parent_kind.clone().unwrap_or_else(|| "root".to_string());
            if !contexts.contains(&context_kind) {
                contexts.push(context_kind.clone());
            }
            if CRITICAL_STRUCTURAL_KINDS.contains(&context_kind.as_str()) {
                critical = true;
            }
            if signatures.len() < MAX_DAMAGE_SIGNATURES {
                let point = node.start_position();
                signatures.push(format!(
                    "{node_type}@{}:{}:{context_kind}",
                    point.row + 1,
                    point.column + 1
                ));
            }
        }
        let mut cursor = node.walk();
        if cursor.goto_first_child() {
            let mut children: Vec<tree_sitter::Node<'_>> =
                vec![cursor.node()];
            while cursor.goto_next_sibling() {
                children.push(cursor.node());
            }
            // Python: for child in reversed(children): stack.append((child, node_type))
            for child in children.into_iter().rev() {
                stack.push((child, Some(node_type.clone())));
            }
        }
    }
    let damaged_bytes = merge_intervals(&intervals);
    let ratio = if source_size > 0 {
        damaged_bytes as f64 / source_size as f64
    } else {
        0.0
    };
    let mut sorted_contexts = contexts;
    sorted_contexts.sort();
    DamageSummary {
        error_count,
        missing_count,
        damaged_bytes: damaged_bytes as i64,
        source_bytes: source_size as i64,
        damaged_span_ratio: round8(ratio),
        critical_structural_damage: critical,
        structural_contexts: sorted_contexts,
        signatures,
    }
}

fn round8(value: f64) -> f64 {
    (value * 100_000_000.0).round() / 100_000_000.0
}

fn merge_intervals(intervals: &[(usize, usize)]) -> usize {
    let mut sorted = intervals.to_vec();
    sorted.sort();
    let mut total = 0usize;
    let mut current: Option<(usize, usize)> = None;
    for (start, end) in sorted {
        match current {
            None => current = Some((start, end)),
            Some((cs, ce)) => {
                if start <= ce {
                    current = Some((cs, ce.max(end)));
                } else {
                    total += ce - cs;
                    current = Some((start, end));
                }
            }
        }
    }
    if let Some((cs, ce)) = current {
        total += ce - cs;
    }
    total
}

/// `classify_quality`.
pub fn classify_quality(damage: &DamageSummary, semantic: &SemanticYield, lossy_decode: bool) -> &'static str {
    if damage.damaged_span_ratio >= QUARANTINE_DAMAGE_RATIO
        || (damage.critical_structural_damage && semantic.top_level_count() == 0)
    {
        return "quarantined";
    }
    if lossy_decode
        || damage.critical_structural_damage
        || damage.damaged_span_ratio >= RETRY_DAMAGE_RATIO
    {
        return "retry_required";
    }
    if damage.diagnostic_count() > 0 {
        return "recovered";
    }
    "clean"
}

#[derive(Debug, Clone)]
pub struct QualityRecord {
    pub file_path: String,
    pub source_fingerprint: String,
    pub context_fingerprint: String,
    pub tier: &'static str,
    pub damage: DamageSummary,
    pub semantic_yield: SemanticYield,
    pub context: ParseContext,
    pub retry_stages: Vec<&'static str>,
    pub candidate_outcome: &'static str,
    pub selected_candidate: String,
    pub selection_reason: String,
}

impl QualityRecord {
    /// `to_dict()` — đúng thứ tự field của dataclass Python.
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("file_path".into(), json!(self.file_path));
        m.insert("source_fingerprint".into(), json!(self.source_fingerprint));
        m.insert("context_fingerprint".into(), json!(self.context_fingerprint));
        m.insert("tier".into(), json!(self.tier));
        m.insert("damage".into(), Value::Object(self.damage.to_dict()));
        m.insert(
            "semantic_yield".into(),
            Value::Object(self.semantic_yield.to_dict()),
        );
        m.insert("context".into(), Value::Object(self.context.to_dict()));
        m.insert("retry_stages".into(), json!(self.retry_stages));
        m.insert("candidate_outcome".into(), json!(self.candidate_outcome));
        m.insert("selected_candidate".into(), json!(self.selected_candidate));
        m.insert("selection_reason".into(), json!(self.selection_reason));
        m.insert("elapsed_ms".into(), json!(0.0));
        m.insert(
            "schema_version".into(),
            json!(PARSE_QUALITY_SCHEMA_VERSION),
        );
        m
    }
}

/// `_tree_selection_semantic_yield` — đếm backend-neutral cho grammar retry.
pub fn tree_selection_semantic_yield(root: tree_sitter::Node<'_>) -> SemanticYield {
    let mut functions = 0i64;
    let mut types = 0i64;
    let mut declarations = 0i64;
    let mut stable_scopes = 0i64;
    let mut calls = 0i64;
    let mut includes = 0i64;
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        let kind = node.kind();
        if kind == "function_definition" {
            functions += 1;
        }
        if matches!(
            kind,
            "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier"
        ) {
            types += 1;
        }
        if matches!(kind, "declaration" | "type_definition" | "alias_declaration") {
            declarations += 1;
        }
        if matches!(
            kind,
            "function_definition"
                | "class_specifier"
                | "struct_specifier"
                | "union_specifier"
                | "enum_specifier"
                | "namespace_definition"
        ) {
            stable_scopes += 1;
        }
        if kind == "call_expression" {
            calls += 1;
        }
        if kind == "preproc_include" || kind == "preproc_import" {
            includes += 1;
        }
        let mut cursor = node.walk();
        if cursor.goto_first_child() {
            let mut children = vec![cursor.node()];
            while cursor.goto_next_sibling() {
                children.push(cursor.node());
            }
            for child in children {
                stack.push(child);
            }
        }
    }
    SemanticYield {
        function_count: functions,
        type_count: types,
        declaration_count: declarations,
        stable_scope_count: stable_scopes,
        call_count: calls,
        include_count: includes,
    }
}

/// `candidate_score` — tuple so candidate grammar retry; lower is better.
pub fn candidate_is_strictly_better(
    candidate: (&DamageSummary, &SemanticYield),
    baseline: (&DamageSummary, &SemanticYield),
) -> bool {
    let score = |damage: &DamageSummary, semantic: &SemanticYield| -> (i64, f64, i64, i64, i64, i64) {
        (
            if damage.critical_structural_damage { 1 } else { 0 },
            round8(damage.damaged_span_ratio),
            -semantic.top_level_count(),
            -semantic.stable_scope_count,
            -semantic.useful_reference_count(),
            damage.diagnostic_count(),
        )
    };
    score(candidate.0, candidate.1) < score(baseline.0, baseline.1)
}

/// `aggregate_quality_records` (artifact-only).
#[allow(dead_code)]
pub fn aggregate_quality_records(records: &[Map<String, Value>]) -> Map<String, Value> {
    let mut tiers: std::collections::BTreeMap<&str, i64> = std::collections::BTreeMap::new();
    for tier in ["clean", "recovered", "retry_required", "quarantined"] {
        tiers.insert(tier, 0);
    }
    let mut aggregates = Map::new();
    aggregates.insert("file_count".into(), json!(0));
    aggregates.insert("files_with_error".into(), json!(0));
    aggregates.insert("files_with_missing".into(), json!(0));
    aggregates.insert("lossy_decode_file_count".into(), json!(0));
    aggregates.insert("error_node_total".into(), json!(0));
    aggregates.insert("missing_node_total".into(), json!(0));
    aggregates.insert("grammar_retry_attempted".into(), json!(0));
    aggregates.insert("grammar_retry_selected".into(), json!(0));
    aggregates.insert("fallback_attempted".into(), json!(0));
    aggregates.insert("fallback_improved".into(), json!(0));
    aggregates.insert("quarantined_file_count".into(), json!(0));
    for record in records {
        *aggregates.get_mut("file_count").unwrap() = json!(aggregates["file_count"].as_i64().unwrap_or(0) + 1);
        let damage = record.get("damage").cloned().unwrap_or(Value::Null);
        let context = record.get("context").cloned().unwrap_or(Value::Null);
        let error_count = damage.get("error_count").and_then(Value::as_i64).unwrap_or(0);
        let missing_count = damage.get("missing_count").and_then(Value::as_i64).unwrap_or(0);
        *aggregates.get_mut("error_node_total").unwrap() =
            json!(aggregates["error_node_total"].as_i64().unwrap_or(0) + error_count);
        *aggregates.get_mut("missing_node_total").unwrap() =
            json!(aggregates["missing_node_total"].as_i64().unwrap_or(0) + missing_count);
        if error_count > 0 {
            *aggregates.get_mut("files_with_error").unwrap() =
                json!(aggregates["files_with_error"].as_i64().unwrap_or(0) + 1);
        }
        if missing_count > 0 {
            *aggregates.get_mut("files_with_missing").unwrap() =
                json!(aggregates["files_with_missing"].as_i64().unwrap_or(0) + 1);
        }
        if context.get("lossy_decode").and_then(Value::as_bool).unwrap_or(false) {
            *aggregates.get_mut("lossy_decode_file_count").unwrap() =
                json!(aggregates["lossy_decode_file_count"].as_i64().unwrap_or(0) + 1);
        }
        let mut tier = record
            .get("tier")
            .and_then(Value::as_str)
            .unwrap_or("clean")
            .to_string();
        if !tiers.contains_key(tier.as_str()) {
            tier = "retry_required".to_string();
        }
        *tiers.get_mut(tier.as_str()).unwrap() += 1;
        if tier == "quarantined" {
            *aggregates.get_mut("quarantined_file_count").unwrap() =
                json!(aggregates["quarantined_file_count"].as_i64().unwrap_or(0) + 1);
        }
        let stages: Vec<String> = record
            .get("retry_stages")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        if stages.iter().any(|s| s == "alternate_grammar") {
            *aggregates.get_mut("grammar_retry_attempted").unwrap() =
                json!(aggregates["grammar_retry_attempted"].as_i64().unwrap_or(0) + 1);
        }
        if record.get("selected_candidate").and_then(Value::as_str) == Some("alternate_grammar") {
            *aggregates.get_mut("grammar_retry_selected").unwrap() =
                json!(aggregates["grammar_retry_selected"].as_i64().unwrap_or(0) + 1);
        }
        if stages.iter().any(|s| s == "libclang") {
            *aggregates.get_mut("fallback_attempted").unwrap() =
                json!(aggregates["fallback_attempted"].as_i64().unwrap_or(0) + 1);
        }
        if record.get("candidate_outcome").and_then(Value::as_str) == Some("selected")
            && record.get("selected_candidate").and_then(Value::as_str) == Some("libclang")
        {
            *aggregates.get_mut("fallback_improved").unwrap() =
                json!(aggregates["fallback_improved"].as_i64().unwrap_or(0) + 1);
        }
    }
    aggregates.insert(
        "quality_tiers".into(),
        Value::Object(tiers.into_iter().map(|(k, v)| (k.to_string(), json!(v))).collect()),
    );
    aggregates
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// `build_quality_record` cho .rc (damage = DamageSummary(source_bytes=n)).
pub fn build_rc_quality_record(
    rel_path: &str,
    source: &[u8],
    semantic_yield: &SemanticYield,
    context: &ParseContext,
) -> QualityRecord {
    let damage = DamageSummary {
        source_bytes: source.len() as i64,
        ..Default::default()
    };
    let source_hash = source_fingerprint(source);
    QualityRecord {
        file_path: rel_path.to_string(),
        source_fingerprint: source_hash.clone(),
        context_fingerprint: context_fingerprint(context, &source_hash),
        tier: classify_quality(&damage, semantic_yield, context.lossy_decode),
        damage,
        semantic_yield: semantic_yield.clone(),
        context: context.clone(),
        retry_stages: Vec::new(),
        candidate_outcome: "not_attempted",
        selected_candidate: "baseline".to_string(),
        selection_reason: "first_pass".to_string(),
    }
}
