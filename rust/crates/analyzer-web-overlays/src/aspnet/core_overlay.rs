//! Port `tools/aspnet_core/` — pipeline + resolver + artifact parsers.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

use crate::aspnet::builders::{fact, relationship, Coordinate, FactInput};
use crate::aspnet::builders::relationship_id;
use crate::aspnet::models::{
    dedupe_facts, dedupe_relationships, AnalysisModule, AnalysisResult, Diagnostic,
    ParserCapability, SemanticFact, SemanticRelationship, SourceSpan,
};
use crate::aspnet::project_metadata::{
    in_module, infer_deleted_module_path, module_project_path, AspNetDetector,
};
use crate::aspnet::roslyn::analyze_csharp_files;
use crate::aspnet::safe_formats::{flatten_json, parse_json_file, read_bounded_text};
use crate::pyutil::basename;

pub const FRAMEWORK: &str = "aspnet_core";

// ─────────────────────────────────────────────────────────────────────────────
// artifact_parsers.py
// ─────────────────────────────────────────────────────────────────────────────

fn page_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r#"(?m)^\s*@page(?:\s+(["'])(.*?)\1)?"#).expect("regex"))
}

fn model_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"(?m)^\s*@model\s+([^\s]+)").expect("regex"))
}

fn layout_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r#"\bLayout\s*=\s*["']([^"']+)["']"#).expect("regex"))
}

fn partial_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r#"(?i)(?:<partial\s+[^>]*name\s*=\s*["']([^"']+)["']|PartialAsync\s*\(\s*["']([^"']+)["'])"#)
            .expect("regex")
    })
}

pub struct ArtifactParse {
    pub facts: Vec<SemanticFact>,
    pub relationships: Vec<SemanticRelationship>,
    pub diagnostics: Vec<Diagnostic>,
    pub dependencies: BTreeMap<String, BTreeSet<String>>,
}

impl ArtifactParse {
    fn empty() -> Self {
        Self {
            facts: Vec::new(),
            relationships: Vec::new(),
            diagnostics: Vec::new(),
            dependencies: BTreeMap::new(),
        }
    }

    pub fn read_error(code: &str, message: String, path: &str) -> Self {
        let mut parsed = Self::empty();
        parsed
            .diagnostics
            .push(Diagnostic::new(code, &message, "error", SourceSpan::new(path)));
        parsed
    }
}

/// `parse_razor`.
pub fn parse_razor(
    root: &Path,
    path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> ArtifactParse {
    let (text, relative, truncated) = match read_bounded_text(root, path, 2 * 1024 * 1024) {
        Ok(result) => result,
        Err(error) => return ArtifactParse::read_error("aspnet_core.razor.read_error", error, path),
    };
    let page_match = page_regex().captures(&text).ok().flatten();
    let kind = if page_match.is_some() || format!("/{}", relative.to_lowercase()).contains("/pages/") {
        "RazorPage"
    } else {
        "View"
    };
    let route = page_match
        .as_ref()
        .and_then(|capture| capture.get(2))
        .map(|matched| matched.as_str().to_string())
        .unwrap_or_default();
    let model_match = model_regex().captures(&text).ok().flatten();
    let page = fact(FactInput {
        kind,
        name: &relative,
        framework: FRAMEWORK,
        project_id,
        project_name,
        module_id,
        source: SourceSpan::new(&relative),
        coordinates: vec![Coordinate::text(&route)],
        confidence: 1.0,
        resolution_status: "resolved",
        extraction_method: "razor_source",
        source_symbol_id: "",
        properties: {
            let mut properties = Map::new();
            properties.insert("path".into(), json!(relative));
            properties.insert("route".into(), json!(route));
            properties.insert(
                "model".into(),
                json!(model_match
                    .as_ref()
                    .and_then(|capture| capture.get(1))
                    .map(|matched| matched.as_str())
                    .unwrap_or("")),
            );
            properties
        },
    });
    let mut facts = vec![page.clone()];
    let mut relationships = Vec::new();
    let mut dependencies: BTreeSet<String> = BTreeSet::new();
    if let Some(capture) = &page_match {
        let has_route = capture.get(2).is_some();
        let endpoint = fact(FactInput {
            kind: "HttpEndpoint",
            name: if has_route { &route } else { &relative },
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("razor-page"), Coordinate::text(&route)],
            confidence: 1.0,
            resolution_status: "resolved",
            extraction_method: "razor_source",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("route".into(), json!(route));
                properties.insert("http_method".into(), json!("GET|POST"));
                properties
            },
        });
        let route_fact = fact(FactInput {
            kind: "Route",
            name: if has_route { &route } else { &relative },
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("route"), Coordinate::text(&route)],
            confidence: 1.0,
            resolution_status: "resolved",
            extraction_method: "razor_source",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("route".into(), json!(route));
                properties
            },
        });
        facts.push(endpoint.clone());
        facts.push(route_fact.clone());
        relationships.push(relationship("MAPPED_TO", &endpoint, &route_fact, None, 1.0, "resolved", "", Map::new()));
        relationships.push(relationship("HANDLED_BY", &endpoint, &page, None, 1.0, "resolved", "", Map::new()));
    }
    if let Ok(Some(capture)) = layout_regex().captures(&text) {
        let layout_name = capture.get(1).map(|matched| matched.as_str()).unwrap_or("").to_string();
        let layout = fact(FactInput {
            kind: "Layout",
            name: &layout_name,
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("layout"), Coordinate::text(&layout_name)],
            confidence: 0.9,
            resolution_status: "unresolved",
            extraction_method: "razor_source",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("path".into(), json!(layout_name));
                properties
            },
        });
        relationships.push(relationship(
            "RENDERS",
            &page,
            &layout,
            None,
            0.9,
            "unresolved",
            "Razor Layout assignment",
            Map::new(),
        ));
        facts.push(layout);
        dependencies.insert(layout_name);
    }
    for (index, capture) in partial_regex()
        .captures_iter(&text)
        .enumerate()
    {
        let Ok(capture) = capture else { continue };
        let partial_name = capture
            .get(1)
            .or_else(|| capture.get(2))
            .map(|matched| matched.as_str().to_string())
            .unwrap_or_default();
        let line = line_at(&text, capture.get(0).map(|m| m.start()).unwrap_or(0));
        let partial = fact(FactInput {
            kind: "PartialView",
            name: &partial_name,
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::with_line(&relative, line),
            coordinates: vec![
                Coordinate::text("partial"),
                Coordinate::text(&partial_name),
                Coordinate::text(&index.to_string()),
            ],
            confidence: 0.9,
            resolution_status: "unresolved",
            extraction_method: "razor_source",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("path".into(), json!(partial_name));
                properties
            },
        });
        relationships.push(relationship(
            "RENDERS",
            &page,
            &partial,
            None,
            0.9,
            "unresolved",
            "Razor partial reference",
            Map::new(),
        ));
        facts.push(partial);
        dependencies.insert(partial_name);
    }
    let mut diagnostics = Vec::new();
    if truncated {
        diagnostics.push(Diagnostic::new(
            "aspnet_core.razor.truncated",
            "Razor source exceeded the 2 MiB budget",
            "warning",
            SourceSpan::new(&relative),
        ));
    }
    let mut parsed_dependencies = BTreeMap::new();
    parsed_dependencies.insert(relative.clone(), dependencies);
    ArtifactParse {
        facts,
        relationships,
        diagnostics,
        dependencies: parsed_dependencies,
    }
}

/// `line_at` — `text.count("\n", 0, offset) + 1`.
pub fn line_at(text: &str, offset: usize) -> i64 {
    text.as_bytes()[..offset.min(text.len())]
        .iter()
        .filter(|byte| **byte == b'\n')
        .count() as i64
        + 1
}

/// `parse_appsettings`.
pub fn parse_appsettings(
    root: &Path,
    path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> ArtifactParse {
    let parsed = match parse_json_file(root, path, 1024 * 1024) {
        Ok(parsed) => parsed,
        Err(error) => {
            return ArtifactParse::read_error("aspnet_core.config.parse_error", error, path);
        }
    };
    let file_name = basename(&parsed.relative);
    let environment = environment_name(&file_name);
    let flat = flatten_json(&parsed.value, "");
    let mut entries: Vec<(String, Value)> = flat.into_iter().collect();
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    let facts = entries
        .into_iter()
        .filter(|(key, _)| !key.is_empty())
        .map(|(key, item)| {
            fact(FactInput {
                kind: "ConfigurationKey",
                name: &key,
                framework: FRAMEWORK,
                project_id,
                project_name,
                module_id,
                source: SourceSpan::new(&parsed.relative),
                coordinates: vec![Coordinate::text(&environment), Coordinate::text(&key)],
                confidence: 1.0,
                resolution_status: "resolved",
                extraction_method: "safe_json",
                source_symbol_id: "",
                properties: {
                    let mut properties = Map::new();
                    properties.insert("config_key".into(), json!(key));
                    properties.insert("value".into(), item);
                    properties.insert("environment".into(), json!(environment));
                    properties.insert("provenance".into(), json!(parsed.relative));
                    properties
                },
            })
        })
        .collect();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    if parsed.truncated {
        diagnostics.push(Diagnostic::new(
            "aspnet_core.config.truncated",
            "appsettings file exceeded the 1 MiB budget",
            "warning",
            SourceSpan::new(&parsed.relative),
        ));
    }
    for key in &parsed.duplicates {
        diagnostics.push(Diagnostic::new(
            "aspnet_core.config.duplicate_key",
            &format!("Duplicate JSON key: {key}"),
            "warning",
            SourceSpan::new(&parsed.relative),
        ));
    }
    let mut dependencies = BTreeMap::new();
    dependencies.insert(parsed.relative.clone(), BTreeSet::new());
    ArtifactParse {
        facts,
        relationships: Vec::new(),
        diagnostics,
        dependencies,
    }
}

/// `os.path.basename(relative)[len("appsettings") : -len(".json")].strip(".")
/// or "default"`.
fn environment_name(file_name: &str) -> String {
    let inner = file_name
        .strip_prefix("appsettings")
        .unwrap_or(file_name)
        .strip_suffix(".json")
        .unwrap_or("");
    let trimmed = inner.trim_matches('.');
    if trimmed.is_empty() {
        "default".to_string()
    } else {
        trimmed.to_string()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// resolver.py
// ─────────────────────────────────────────────────────────────────────────────

const HTTP_METHODS_MAP: [(&str, &str); 7] = [
    ("MapGet", "GET"),
    ("MapPost", "POST"),
    ("MapPut", "PUT"),
    ("MapDelete", "DELETE"),
    ("MapPatch", "PATCH"),
    ("MapMethods", "MULTI"),
    ("Map", "ANY"),
];

const HTTP_ATTRIBUTES: [&str; 6] = [
    "HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "AcceptVerbs",
];

const TERMINAL_MIDDLEWARE: [&str; 2] = ["Run", "UseEndpoints"];

fn on_handler_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"^On(?:Get|Post|Put|Delete|Patch)").expect("regex"))
}

fn http_method_of(name: &str) -> Option<&'static str> {
    HTTP_METHODS_MAP
        .iter()
        .find(|(key, _)| *key == name)
        .map(|(_, value)| *value)
}

/// `_semantic_of`.
fn semantic_of(
    source_fact: &SemanticFact,
    anchor: &str,
    label: &str,
) -> SemanticRelationship {
    SemanticRelationship {
        stable_id: relationship_id(
            FRAMEWORK,
            &source_fact.project_id,
            &source_fact.module_id,
            "SEMANTIC_OF",
            &source_fact.stable_id,
            anchor,
            &[],
        ),
        relationship_type: "SEMANTIC_OF".into(),
        from_id: source_fact.stable_id.clone(),
        to_id: anchor.to_string(),
        from_label: source_fact.kind.clone(),
        to_label: label.to_string(),
        framework: FRAMEWORK.to_string(),
        project_id: source_fact.project_id.clone(),
        module_id: source_fact.module_id.clone(),
        source: source_fact.source.clone(),
        confidence: 1.0,
        resolution_status: "resolved".into(),
        reason: "canonical C# source coordinate".into(),
        properties: Map::new(),
        from_generated: true,
        to_generated: false,
    }
}

/// `_endpoint_and_route`.
#[allow(clippy::too_many_arguments)]
fn endpoint_and_route(
    project_id: &str,
    project_name: &str,
    module_id: &str,
    source: SourceSpan,
    route: &str,
    method: &str,
    coordinates: Vec<Coordinate>,
    mapping_kind: &str,
    resolution_status: &str,
) -> (SemanticFact, SemanticFact) {
    let confidence = if resolution_status == "resolved" { 1.0 } else { 0.65 };
    let name = format!("{method} {route}");
    let endpoint = fact(FactInput {
        kind: "HttpEndpoint",
        name: &name,
        framework: FRAMEWORK,
        project_id,
        project_name,
        module_id,
        source: source.clone(),
        coordinates: {
            let mut all = vec![Coordinate::text(method), Coordinate::text(route)];
            all.extend(coordinates.clone());
            all
        },
        confidence,
        resolution_status,
        extraction_method: "roslyn_invocation",
        source_symbol_id: "",
        properties: {
            let mut properties = Map::new();
            properties.insert("route".into(), json!(route));
            properties.insert("http_method".into(), json!(method));
            properties.insert("mapping_kind".into(), json!(mapping_kind));
            properties
        },
    });
    let route_fact = fact(FactInput {
        kind: "Route",
        name: route,
        framework: FRAMEWORK,
        project_id,
        project_name,
        module_id,
        source,
        coordinates: vec![Coordinate::text(mapping_kind), Coordinate::text(route)],
        confidence,
        resolution_status,
        extraction_method: "roslyn_invocation",
        source_symbol_id: "",
        properties: {
            let mut properties = Map::new();
            properties.insert("route".into(), json!(route));
            properties.insert("http_method".into(), json!(method));
            properties
        },
    });
    (endpoint, route_fact)
}

/// `_owner_for_member` — max() của Python trả phần tử ĐẦU TIÊN đạt max.
fn owner_for_member<'a>(
    qualified: &str,
    owners: &'a BTreeMap<String, SemanticFact>,
) -> Option<&'a SemanticFact> {
    let mut best: Option<(&SemanticFact, usize)> = None;
    for (key, item) in owners {
        if qualified.starts_with(&format!("{key}.")) {
            let length = item
                .properties
                .get("qualified_name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .len();
            match best {
                None => best = Some((item, length)),
                Some((_, best_length)) if length > best_length => best = Some((item, length)),
                _ => {}
            }
        }
    }
    best.map(|(item, _)| item)
}

/// `_method_from_attributes`.
fn method_from_attributes(attributes: &[String]) -> String {
    for attribute in attributes {
        if let Some(rest) = attribute.strip_prefix("Http") {
            return rest.to_uppercase();
        }
    }
    "ANY".to_string()
}

/// `resolve_roslyn_evidence`.
pub fn resolve_roslyn_evidence(
    payload: &Value,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> (Vec<SemanticFact>, Vec<SemanticRelationship>) {
    let mut facts: Vec<SemanticFact> = Vec::new();
    let mut relationships: Vec<SemanticRelationship> = Vec::new();
    let semantic_enabled = payload
        .get("semantic_enabled")
        .map(Value::as_bool)
        .unwrap_or(Some(false))
        .unwrap_or(false);
    let extraction = if semantic_enabled { "roslyn_semantic" } else { "roslyn_syntax" };
    let Some(results) = payload.get("results").and_then(Value::as_array) else {
        return (facts, relationships);
    };
    for result in results {
        if !result.get("ok").and_then(Value::as_bool).unwrap_or(false) {
            continue;
        }
        let Some(evidence) = result.get("evidence").filter(|item| item.is_object()) else {
            continue;
        };
        let file_path = result
            .get("file_path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let mut type_facts: BTreeMap<String, SemanticFact> = BTreeMap::new();
        for type_item in evidence
            .get("types")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let name = type_item
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let qualified = type_item
                .get("qualified_name")
                .and_then(Value::as_str)
                .unwrap_or(&name)
                .to_string();
            let bases: Vec<String> = type_item
                .get("base_types")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|item| item.as_str().unwrap_or("").to_string())
                        .collect()
                })
                .unwrap_or_default();
            let kind = if name.ends_with("Controller") || bases.iter().any(|item| item.contains("Controller")) {
                "Controller"
            } else if name.ends_with("Model") && bases.iter().any(|item| item.contains("PageModel")) {
                "RazorPage"
            } else if name.contains("Repository") {
                "Repository"
            } else if name.ends_with("Service") {
                "Service"
            } else if name.ends_with("Model")
                || name.ends_with("Dto")
                || name.ends_with("Request")
                || name.ends_with("Response")
            {
                "Model"
            } else {
                ""
            };
            if kind.is_empty() {
                continue;
            }
            let start_line = type_item
                .get("start_line")
                .and_then(Value::as_i64)
                .unwrap_or(1);
            let source = SourceSpan::with_line(&file_path, start_line);
            let anchor = type_item
                .get("canonical_symbol_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let item_fact = fact(FactInput {
                kind,
                name: &name,
                framework: FRAMEWORK,
                project_id,
                project_name,
                module_id,
                source,
                coordinates: vec![Coordinate::text(&qualified)],
                confidence: 1.0,
                resolution_status: "resolved",
                extraction_method: extraction,
                source_symbol_id: &anchor,
                properties: {
                    let mut properties = Map::new();
                    properties.insert("qualified_name".into(), json!(qualified));
                    properties.insert("base_types".into(), json!(bases));
                    properties
                },
            });
            type_facts.insert(qualified, item_fact.clone());
            facts.push(item_fact.clone());
            if !anchor.is_empty() {
                relationships.push(semantic_of(&item_fact, &anchor, "Type"));
            }
        }

        let mut attributes_by_line: BTreeMap<i64, Vec<&Value>> = BTreeMap::new();
        for attribute in evidence
            .get("attributes")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let line = attribute
                .get("start_line")
                .and_then(Value::as_i64)
                .unwrap_or(1);
            attributes_by_line.entry(line).or_default().push(attribute);
        }
        for member in evidence
            .get("members")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            if member.get("kind").and_then(Value::as_str) != Some("method") {
                continue;
            }
            let name = member
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let qualified = member
                .get("qualified_name")
                .and_then(Value::as_str)
                .unwrap_or(&name)
                .to_string();
            let line = member.get("start_line").and_then(Value::as_i64).unwrap_or(1);
            let accessibility = member
                .get("accessibility")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let attrs: Vec<String> = member
                .get("attributes")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|item| {
                            item.as_str()
                                .unwrap_or("")
                                .rsplit('.')
                                .next()
                                .unwrap_or("")
                                .strip_suffix("Attribute")
                                .unwrap_or_else(|| {
                                    item.as_str().unwrap_or("").rsplit('.').next().unwrap_or("")
                                })
                                .to_string()
                        })
                        .collect()
                })
                .unwrap_or_default();
            let source = SourceSpan::with_line(&file_path, line);
            let owner = owner_for_member(&qualified, &type_facts);
            let mut kind = "";
            if owner.is_some()
                && owner.map(|item| item.kind == "Controller").unwrap_or(false)
                && accessibility == "public"
                && (attrs.iter().any(|attr| HTTP_ATTRIBUTES.contains(&attr.as_str()))
                    || !name.starts_with('_'))
                && !attrs.iter().any(|attr| attr == "NonAction")
            {
                kind = "Action";
            } else if owner.is_some()
                && owner.map(|item| item.kind == "RazorPage").unwrap_or(false)
                && accessibility == "public"
                && on_handler_regex().is_match(&name).unwrap_or(false)
            {
                kind = "PageHandler";
            }
            if kind.is_empty() {
                continue;
            }
            let anchor = member
                .get("canonical_symbol_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let member_fact = fact(FactInput {
                kind,
                name: &name,
                framework: FRAMEWORK,
                project_id,
                project_name,
                module_id,
                source: source.clone(),
                coordinates: vec![Coordinate::text(&qualified)],
                confidence: 1.0,
                resolution_status: "resolved",
                extraction_method: extraction,
                source_symbol_id: &anchor,
                properties: {
                    let mut properties = Map::new();
                    properties.insert("qualified_name".into(), json!(qualified));
                    properties.insert("attributes".into(), json!(attrs));
                    properties
                },
            });
            facts.push(member_fact.clone());
            if !anchor.is_empty() {
                relationships.push(semantic_of(&member_fact, &anchor, "Function"));
            }
            if let Some(owner) = owner {
                relationships.push(relationship(
                    "DEPENDS_ON",
                    &member_fact,
                    owner,
                    None,
                    1.0,
                    "resolved",
                    "compiler member ownership",
                    Map::new(),
                ));
            }
            let route_attribute = attributes_by_line
                .get(&line)
                .and_then(|items| {
                    items.iter().find(|item| {
                        let attribute_name = item
                            .get("name")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .rsplit('.')
                            .next()
                            .unwrap_or("")
                            .strip_suffix("Attribute")
                            .unwrap_or_else(|| {
                                item.get("name").and_then(Value::as_str).unwrap_or("")
                                    .rsplit('.').next().unwrap_or("")
                            });
                        attribute_name == "Route" || HTTP_ATTRIBUTES.contains(&attribute_name)
                    })
                })
                .copied();
            if let Some(route_attribute) = route_attribute {
                let arguments: Vec<String> = route_attribute
                    .get("arguments")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .map(|item| item.as_str().unwrap_or("").to_string())
                            .collect()
                    })
                    .unwrap_or_default();
                let stripped: Vec<String> = arguments
                    .iter()
                    .map(|item| item.trim_matches(|c| c == '"' || c == '\'').to_string())
                    .collect();
                let route = stripped.first().cloned().unwrap_or_default();
                let fallback_route = format!(
                    "{}/{}",
                    owner.map(|item| item.name.as_str()).unwrap_or("action"),
                    name
                );
                let (endpoint, route_fact) = endpoint_and_route(
                    project_id,
                    project_name,
                    module_id,
                    source.clone(),
                    if route.is_empty() { &fallback_route } else { &route },
                    &method_from_attributes(&attrs),
                    vec![Coordinate::text(&qualified)],
                    "attribute_route",
                    if route.is_empty() { "partial" } else { "resolved" },
                );
                facts.push(endpoint.clone());
                facts.push(route_fact.clone());
                relationships.push(relationship(
                    "MAPPED_TO",
                    &endpoint,
                    &route_fact,
                    None,
                    1.0,
                    "resolved",
                    "",
                    Map::new(),
                ));
                relationships.push(relationship(
                    "HANDLED_BY",
                    &endpoint,
                    &member_fact,
                    None,
                    1.0,
                    "resolved",
                    "",
                    Map::new(),
                ));
            }
        }

        let mut middleware: Vec<SemanticFact> = Vec::new();
        let mut endpoints: Vec<SemanticFact> = Vec::new();
        for invocation in evidence
            .get("invocations")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let expression = invocation
                .get("expression")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let name = expression.rsplit('.').next().unwrap_or("").to_string();
            let line = invocation.get("start_line").and_then(Value::as_i64).unwrap_or(1);
            let constants: Vec<String> = invocation
                .get("constant_arguments")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|item| item.as_str().unwrap_or("").to_string())
                        .filter(|item| !item.is_empty())
                        .collect()
                })
                .unwrap_or_default();
            let arguments: Vec<String> = invocation
                .get("arguments")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|item| item.as_str().unwrap_or("").to_string())
                        .collect()
                })
                .unwrap_or_default();
            let source = SourceSpan::with_line(&file_path, line);
            if name.starts_with("Use") || name == "Run" || name == "MapWhen" {
                let middleware_fact = fact(FactInput {
                    kind: "Middleware",
                    name: &name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: source.clone(),
                    coordinates: vec![
                        Coordinate::text(&name),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence: 0.95,
                    resolution_status: if name == "MapWhen" { "partial" } else { "resolved" },
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("position".into(), json!(middleware.len()));
                        properties.insert("branch".into(), json!(name == "MapWhen"));
                        properties.insert("terminal".into(), json!(TERMINAL_MIDDLEWARE.contains(&name.as_str())));
                        properties.insert("arguments".into(), json!(arguments));
                        properties
                    },
                });
                middleware.push(middleware_fact.clone());
                facts.push(middleware_fact);
            }
            if let Some(method_value) = http_method_of(&name) {
                let route = constants.first().cloned().unwrap_or_default();
                let dynamic_route = format!("dynamic:{line}");
                let (endpoint, route_fact) = endpoint_and_route(
                    project_id,
                    project_name,
                    module_id,
                    source.clone(),
                    if route.is_empty() { &dynamic_route } else { &route },
                    method_value,
                    arguments.iter().map(|item| Coordinate::text(item)).collect(),
                    "minimal_api",
                    if route.is_empty() { "dynamic" } else { "resolved" },
                );
                facts.push(endpoint.clone());
                facts.push(route_fact.clone());
                endpoints.push(endpoint.clone());
                relationships.push(relationship(
                    "MAPPED_TO",
                    &endpoint,
                    &route_fact,
                    None,
                    if route.is_empty() { 0.6 } else { 1.0 },
                    if route.is_empty() { "dynamic" } else { "resolved" },
                    "",
                    Map::new(),
                ));
                let handler_name = if arguments.len() > 1 {
                    arguments[1].clone()
                } else if !arguments.is_empty() {
                    arguments[0].clone()
                } else {
                    format!("handler@{line}")
                };
                let handler = fact(FactInput {
                    kind: "Action",
                    name: &handler_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: source.clone(),
                    coordinates: vec![
                        Coordinate::text("minimal-handler"),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence: 0.75,
                    resolution_status: "partial",
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("handler_expression".into(), json!(handler_name));
                        properties.insert("minimal_api".into(), json!(true));
                        properties
                    },
                });
                facts.push(handler.clone());
                relationships.push(relationship(
                    "HANDLED_BY",
                    &endpoint,
                    &handler,
                    None,
                    0.75,
                    "partial",
                    "Minimal API handler expression",
                    Map::new(),
                ));
            }
            if name.starts_with("Add")
                && matches!(
                    name.as_str(),
                    "AddSingleton" | "AddScoped" | "AddTransient" | "AddDbContext" | "AddHostedService"
                )
            {
                let service_name = arguments
                    .iter()
                    .find(|item| !item.is_empty())
                    .cloned()
                    .unwrap_or_else(|| name.clone());
                let service_kind = if name.contains("DbContext") || service_name.contains("Repository") {
                    "Repository"
                } else {
                    "Service"
                };
                let service = fact(FactInput {
                    kind: service_kind,
                    name: &service_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: source.clone(),
                    coordinates: vec![
                        Coordinate::text(&name),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence: 0.85,
                    resolution_status: "partial",
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert(
                            "lifetime".into(),
                            json!(name.strip_prefix("Add").unwrap_or(&name).to_lowercase()),
                        );
                        properties.insert("registration".into(), json!(name));
                        properties
                    },
                });
                facts.push(service);
            }
            if (name == "GetSection" || name == "GetValue" || name == "GetConnectionString")
                && !constants.is_empty()
            {
                let config = fact(FactInput {
                    kind: "ConfigurationKey",
                    name: &constants[0],
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: source.clone(),
                    coordinates: vec![Coordinate::text(&name), Coordinate::text(&constants[0])],
                    confidence: 1.0,
                    resolution_status: "resolved",
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("config_key".into(), json!(constants[0]));
                        properties.insert("consumer".into(), json!(expression));
                        properties
                    },
                });
                facts.push(config);
            }
        }
        for endpoint in &endpoints {
            for (position, middleware_fact) in middleware.iter().enumerate() {
                let mut properties = Map::new();
                properties.insert("position".into(), json!(position));
                relationships.push(relationship(
                    "PASSES_THROUGH",
                    endpoint,
                    middleware_fact,
                    None,
                    1.0,
                    "resolved",
                    "ordered host pipeline",
                    properties,
                ));
            }
        }
    }
    (facts, relationships)
}

// ─────────────────────────────────────────────────────────────────────────────
// pipeline.py
// ─────────────────────────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub fn run_aspnet_core_analysis(
    root: &Path,
    project_id: &str,
    project_name: &str,
    semantic_mode: &str,
    deleted_paths: &[String],
    selected_paths: &[String],
    worker_project_path: Option<&str>,
    verbose: bool,
) -> Result<AnalysisResult, String> {
    let detector = AspNetDetector::new(root, FRAMEWORK);
    let mut detections = detector.discover_modules();
    let selected: BTreeSet<String> = selected_paths
        .iter()
        .filter(|path| !path.is_empty())
        .map(|path| path.replace('\\', "/"))
        .collect();
    if !selected.is_empty() {
        detections.retain(|item| selected.iter().any(|path| in_module(path, &item.module_path)));
    }
    let mut modules: Vec<AnalysisModule> = Vec::new();
    let mut facts: Vec<SemanticFact> = Vec::new();
    let mut relationships: Vec<SemanticRelationship> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut capabilities: Vec<ParserCapability> = Vec::new();
    let mut module_coverage: BTreeMap<String, String> = BTreeMap::new();
    let mut dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for detection in &detections {
        module_coverage.insert(detection.module_id.clone(), "complete".into());
        let mut evidence = detection.evidence.clone();
        evidence.extend(detection.supporting_evidence.clone());
        evidence.sort();
        modules.push(AnalysisModule {
            module_id: detection.module_id.clone(),
            module_path: detection.module_path.clone(),
            framework: FRAMEWORK.into(),
            evidence,
            confidence: detection.confidence,
            artifacts: detection.artifacts.clone(),
        });
        let csharp_files: Vec<String> = detection
            .artifacts
            .iter()
            .filter(|path| path.to_lowercase().ends_with(".cs"))
            .cloned()
            .collect();
        let roslyn = analyze_csharp_files(
            root,
            &csharp_files,
            semantic_mode,
            &module_project_path(detection),
            worker_project_path,
            crate::aspnet::roslyn::default_timeout_sec(),
            verbose,
        );
        match roslyn {
            Ok(roslyn) => {
                let coverage_status = roslyn
                    .get("coverage_status")
                    .and_then(Value::as_str)
                    .unwrap_or("partial")
                    .to_string();
                let workspace_kind = roslyn
                    .get("workspace_kind")
                    .and_then(Value::as_str)
                    .unwrap_or("none");
                let semantic_enabled = roslyn
                    .get("semantic_enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                capabilities.push(ParserCapability {
                    name: "roslyn_csharp".into(),
                    available: true,
                    mandatory: true,
                    mode: semantic_mode.into(),
                    status: coverage_status.clone(),
                    message: format!(
                        "workspace={workspace_kind} semantic={}",
                        crate::pyrepr::py_bool(semantic_enabled)
                    ),
                });
                if coverage_status != "complete" {
                    module_coverage.insert(detection.module_id.clone(), "partial".into());
                }
                let (resolved_facts, resolved_relationships) = resolve_roslyn_evidence(
                    &roslyn,
                    project_id,
                    project_name,
                    &detection.module_id,
                );
                facts.extend(resolved_facts);
                relationships.extend(resolved_relationships);
                for item in roslyn
                    .get("diagnostics")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or_default()
                {
                    diagnostics.push(Diagnostic::new(
                        item.get("code").and_then(Value::as_str).unwrap_or("aspnet_core.roslyn"),
                        item.get("message").and_then(Value::as_str).unwrap_or("Roslyn diagnostic"),
                        item.get("severity").and_then(Value::as_str).unwrap_or("warning"),
                        SourceSpan::new(item.get("file_path").and_then(Value::as_str).unwrap_or("")),
                    ));
                }
            }
            Err(error) => {
                capabilities.push(ParserCapability {
                    name: "roslyn_csharp".into(),
                    available: false,
                    mandatory: true,
                    mode: semantic_mode.into(),
                    status: "unavailable".into(),
                    message: error.clone(),
                });
                module_coverage.insert(detection.module_id.clone(), "partial".into());
                diagnostics.push(Diagnostic::new(
                    "aspnet_core.roslyn_unavailable",
                    &error,
                    if semantic_mode == "on" { "error" } else { "warning" },
                    SourceSpan::new(&detection.module_path),
                ));
            }
        }
        for path in &detection.artifacts {
            let lower = path.to_lowercase();
            let file_name = basename(&lower);
            let parsed = if lower.ends_with(".cshtml") || lower.ends_with(".razor") {
                Some(parse_razor(root, path, project_id, project_name, &detection.module_id))
            } else if file_name.starts_with("appsettings") && lower.ends_with(".json") {
                Some(parse_appsettings(root, path, project_id, project_name, &detection.module_id))
            } else {
                None
            };
            let Some(parsed) = parsed else { continue };
            facts.extend(parsed.facts);
            relationships.extend(parsed.relationships);
            let has_error = parsed.diagnostics.iter().any(|item| item.severity == "error");
            diagnostics.extend(parsed.diagnostics);
            if has_error {
                module_coverage.insert(detection.module_id.clone(), "partial".into());
            }
            for (source, targets) in &parsed.dependencies {
                dependencies
                    .entry(source.clone())
                    .or_default()
                    .extend(targets.iter().cloned());
            }
        }
        for deleted in deleted_paths {
            if in_module(deleted, &detection.module_path) {
                diagnostics.push(Diagnostic::new(
                    "aspnet_core.deleted_artifact",
                    "Deleted artifact is included in module invalidation",
                    "info",
                    SourceSpan::new(deleted),
                ));
            }
        }
    }
    let mut live_module_ids: BTreeSet<String> = modules.iter().map(|item| item.module_id.clone()).collect();
    let mut deleted_unique: Vec<String> = deleted_paths
        .iter()
        .filter(|path| !path.is_empty())
        .map(|path| path.replace('\\', "/"))
        .collect();
    deleted_unique.sort();
    deleted_unique.dedup();
    for deleted in &deleted_unique {
        let invalidated = detector.detect_path(deleted, true);
        let (invalidated_id, invalidated_path) = match invalidated {
            Some(detection) => (detection.module_id.clone(), detection.module_path.clone()),
            None => {
                let inferred = infer_deleted_module_path(FRAMEWORK, deleted);
                if inferred.is_empty() {
                    (String::new(), String::new())
                } else {
                    (crate::aspnet::builders::module_id(FRAMEWORK, &inferred), inferred)
                }
            }
        };
        if invalidated_id.is_empty() || live_module_ids.contains(&invalidated_id) {
            continue;
        }
        modules.push(AnalysisModule {
            module_id: invalidated_id.clone(),
            module_path: invalidated_path,
            framework: FRAMEWORK.into(),
            evidence: vec![format!("{deleted}:deleted")],
            confidence: 0.0,
            artifacts: Vec::new(),
        });
        module_coverage.insert(invalidated_id.clone(), "partial".into());
        live_module_ids.insert(invalidated_id);
        diagnostics.push(Diagnostic::new(
            "aspnet_core.deleted_module_cleanup",
            "The module no longer matches ASP.NET Core; an empty generation will remove stale overlay facts",
            "info",
            SourceSpan::new(deleted),
        ));
    }
    let facts_tuple = dedupe_facts(facts);
    let rels_tuple = dedupe_relationships(relationships);
    let has_errors = diagnostics.iter().any(|item| item.severity == "error");
    let partial = has_errors
        || capabilities
            .iter()
            .any(|item| item.status != "complete" && item.status != "ok");
    let coverage = if modules.is_empty() {
        "empty"
    } else if partial {
        "partial"
    } else {
        "complete"
    };
    modules.sort_by(|a, b| a.module_id.cmp(&b.module_id));
    Ok(AnalysisResult {
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        framework: FRAMEWORK.to_string(),
        modules,
        facts: facts_tuple,
        relationships: rels_tuple,
        capabilities,
        diagnostics,
        dependency_files: dependencies
            .into_iter()
            .map(|(key, value)| {
                let mut items: Vec<String> = value.into_iter().collect();
                items.sort();
                (key, items)
            })
            .collect(),
        module_coverage,
        coverage_status: coverage.to_string(),
    })
}
