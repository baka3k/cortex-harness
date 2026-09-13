//! Port `tools/aspnet_framework/` — pipeline + resolver + artifact parsers.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

use crate::aspnet::builders::{fact, relationship, relationship_id, Coordinate, FactInput};
use crate::aspnet::core_overlay::{line_at, ArtifactParse};
use crate::aspnet::models::{
    dedupe_facts, dedupe_relationships, AnalysisModule, AnalysisResult, Diagnostic,
    ParserCapability, SemanticFact, SemanticRelationship, SourceSpan,
};
use crate::aspnet::project_metadata::{
    in_module, infer_deleted_module_path, module_project_path, AspNetDetector,
};
use crate::aspnet::roslyn::analyze_csharp_files;
use crate::aspnet::safe_formats::{parse_xml_file, read_bounded_text, redact_value};
use crate::xmlmini::local_name;
use crate::xmlmini::Element;

pub const FRAMEWORK: &str = "aspnet_framework";

// ─────────────────────────────────────────────────────────────────────────────
// artifact_parsers.py
// ─────────────────────────────────────────────────────────────────────────────

fn directive_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"(?is)<%@\s*(Page|Control|Master|WebService|WebHandler)\b(.*?)%>").expect("regex"))
}

fn attribute_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r#"(?s)([A-Za-z_:][\w:.-]*)\s*=\s*(["'])(.*?)\2"#).expect("regex"))
}

fn server_tag_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"(?is)<(?P<tag>[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*)\b(?P<attrs>[^>]*)>").expect("regex"))
}

fn event_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r#"\b(On[A-Z][A-Za-z0-9_]*)\s*=\s*(["'])(.*?)\2"#).expect("regex"))
}

fn attribute_map(text: &str) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    for capture in attribute_regex().captures_iter(text).flatten() {
        let key = capture.get(1).map(|m| m.as_str()).unwrap_or("").to_string();
        let value = capture.get(3).map(|m| m.as_str()).unwrap_or("").to_string();
        // Python dict comprehension: key sau ghi đè.
        map.insert(key, value);
    }
    map
}

/// `parse_legacy_markup`.
pub fn parse_legacy_markup(
    root: &Path,
    path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> ArtifactParse {
    let (text, relative, truncated) = match read_bounded_text(root, path, 2 * 1024 * 1024) {
        Ok(result) => result,
        Err(error) => {
            return ArtifactParse::read_error("aspnet_framework.markup.read_error", error, path);
        }
    };
    let mut diagnostics = Vec::new();
    if truncated {
        diagnostics.push(Diagnostic::new(
            "aspnet_framework.markup.truncated",
            "Markup exceeded the 2 MiB budget",
            "warning",
            SourceSpan::new(&relative),
        ));
    }
    let match_text = directive_regex()
        .captures(&text)
        .ok()
        .flatten()
        .map(|capture| {
            (
                capture.get(1).map(|m| m.as_str()).unwrap_or("").to_string(),
                capture.get(2).map(|m| m.as_str()).unwrap_or("").to_string(),
            )
        });
    let directive = match_text
        .as_ref()
        .map(|(name, _)| name.to_lowercase())
        .unwrap_or_default();
    let attributes = attribute_map(match_text.as_ref().map(|(_, body)| body.as_str()).unwrap_or(""));
    let mut kind = "WebFormPage";
    if directive == "webservice"
        || directive == "webhandler"
        || relative.to_lowercase().ends_with(".asmx")
        || relative.to_lowercase().ends_with(".ashx")
    {
        kind = "HttpHandler";
    }
    let inherits = attributes.get("Inherits").cloned().unwrap_or_default();
    let name = if !inherits.is_empty() {
        inherits.clone()
    } else {
        stem_of(&relative)
    };
    let page = fact(FactInput {
        kind,
        name: &name,
        framework: FRAMEWORK,
        project_id,
        project_name,
        module_id,
        source: SourceSpan::new(&relative),
        coordinates: Vec::new(),
        confidence: if match_text.is_some() { 1.0 } else { 0.7 },
        resolution_status: if inherits.is_empty() { "partial" } else { "resolved" },
        extraction_method: "legacy_markup",
        source_symbol_id: "",
        properties: {
            let mut properties = Map::new();
            let artifact_kind = if !directive.is_empty() {
                directive.clone()
            } else {
                extension_of(&relative)
            };
            properties.insert("artifact_kind".into(), json!(artifact_kind));
            properties.insert(
                "code_behind".into(),
                json!(attributes
                    .get("CodeBehind")
                    .or_else(|| attributes.get("CodeFile"))
                    .cloned()
                    .unwrap_or_default()),
            );
            properties.insert("inherits".into(), json!(inherits));
            properties.insert(
                "master_page_file".into(),
                json!(attributes.get("MasterPageFile").cloned().unwrap_or_default()),
            );
            properties.insert(
                "language".into(),
                json!(attributes.get("Language").cloned().unwrap_or_default()),
            );
            properties
        },
    });
    let mut facts = vec![page.clone()];
    let mut relationships = Vec::new();
    if let Some(master_path) = attributes.get("MasterPageFile").cloned() {
        let layout = fact(FactInput {
            kind: "Layout",
            name: &master_path,
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("master"), Coordinate::text(&master_path)],
            confidence: 0.9,
            resolution_status: "unresolved",
            extraction_method: "legacy_markup",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("path".into(), json!(master_path));
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
            "MasterPageFile directive",
            Map::new(),
        ));
        facts.push(layout);
    }
    for (index, capture) in server_tag_regex()
        .captures_iter(&text)
        .enumerate()
    {
        let Ok(capture) = capture else { continue };
        let tag = capture.name("tag").map(|m| m.as_str()).unwrap_or("").to_string();
        let attrs = capture.name("attrs").map(|m| m.as_str()).unwrap_or("").to_string();
        let attr_values = attribute_map(&attrs);
        let control_id = attr_values.get("ID").cloned().unwrap_or_default();
        let control_name = if !control_id.is_empty() {
            control_id.clone()
        } else {
            format!("{tag}#{index}")
        };
        let line = line_at(&text, capture.get(0).map(|m| m.start()).unwrap_or(0));
        let control = fact(FactInput {
            kind: "PartialView",
            name: &control_name,
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::with_line(&relative, line),
            coordinates: vec![Coordinate::text(&tag), Coordinate::text(&index.to_string())],
            confidence: 0.85,
            resolution_status: "partial",
            extraction_method: "legacy_markup",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("tag_name".into(), json!(tag));
                properties.insert("control_id".into(), json!(control_id));
                properties
            },
        });
        relationships.push(relationship(
            "DEPENDS_ON",
            &page,
            &control,
            None,
            0.85,
            "partial",
            "server control declaration",
            Map::new(),
        ));
        for event_capture in event_regex().captures_iter(&attrs).flatten() {
            let event_name = event_capture.get(1).map(|m| m.as_str()).unwrap_or("").to_string();
            let handler_name = event_capture.get(3).map(|m| m.as_str()).unwrap_or("").to_string();
            let handler = fact(FactInput {
                kind: "PageHandler",
                name: &handler_name,
                framework: FRAMEWORK,
                project_id,
                project_name,
                module_id,
                source: control.source.clone(),
                coordinates: vec![
                    Coordinate::text(&control_name),
                    Coordinate::text(&event_name),
                ],
                confidence: 0.9,
                resolution_status: "unresolved",
                extraction_method: "legacy_markup",
                source_symbol_id: "",
                properties: {
                    let mut properties = Map::new();
                    properties.insert("event".into(), json!(event_name));
                    properties.insert("control_id".into(), json!(control_name));
                    properties
                },
            });
            relationships.push(relationship(
                "POSTS_BACK_TO",
                &control,
                &handler,
                None,
                0.9,
                "unresolved",
                "server control event",
                Map::new(),
            ));
            facts.push(handler);
        }
        facts.push(control);
    }
    if view_state_regex().is_match(&text).unwrap_or(false) {
        let state = fact(FactInput {
            kind: "SessionState",
            name: "ViewState",
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("ViewState")],
            confidence: 0.9,
            resolution_status: "resolved",
            extraction_method: "legacy_markup",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("state_kind".into(), json!("view_state"));
                properties
            },
        });
        relationships.push(relationship(
            "WRITES_SESSION",
            &page,
            &state,
            None,
            0.75,
            "partial",
            "ViewState usage",
            Map::new(),
        ));
        facts.push(state);
    }
    // dependencies = sorted({f.source.file_path for f in facts if != relative})
    let dependencies_set: BTreeSet<String> = facts
        .iter()
        .map(|item| item.source.file_path.clone())
        .filter(|item| *item != relative)
        .collect();
    let mut parsed_dependencies = BTreeMap::new();
    parsed_dependencies.insert(relative.clone(), dependencies_set);
    ArtifactParse {
        facts,
        relationships,
        diagnostics,
        dependencies: parsed_dependencies,
    }
}

fn view_state_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"(?i)\bViewState\b|EnableViewState\s*=").expect("regex"))
}

fn stem_of(relative: &str) -> String {
    let file = crate::pyutil::basename(relative);
    match file.rfind('.') {
        Some(index) => file[..index].to_string(),
        None => file,
    }
}

fn extension_of(relative: &str) -> String {
    let file = crate::pyutil::basename(relative);
    match file.rfind('.') {
        Some(index) => file[index + 1..].to_string(),
        None => String::new(),
    }
}

/// `parse_web_config`.
pub fn parse_web_config(
    root: &Path,
    path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> ArtifactParse {
    let (tree, relative, truncated) = match parse_xml_file(root, path, 1024 * 1024) {
        Ok(result) => result,
        Err(error) => {
            return ArtifactParse::read_error("aspnet_framework.config.parse_error", error, path);
        }
    };
    let mut diagnostics = Vec::new();
    if truncated {
        diagnostics.push(Diagnostic::new(
            "aspnet_framework.config.truncated",
            "web.config exceeded the 1 MiB budget",
            "warning",
            SourceSpan::new(&relative),
        ));
    }
    let mut facts: Vec<SemanticFact> = Vec::new();
    let mut relationships = Vec::new();

    #[allow(clippy::too_many_arguments)]
    fn visit(
        element: &Element,
        path_parts: &[String],
        facts: &mut Vec<SemanticFact>,
        relationships: &mut Vec<SemanticRelationship>,
        project_id: &str,
        project_name: &str,
        module_id: &str,
        relative: &str,
    ) {
        let tag = local_name(&element.tag);
        let mut current = path_parts.to_vec();
        current.push(tag.clone());
        let key = current.join(":");
        let mut attributes = Map::new();
        let mut sorted_attribs = element.attributes.clone();
        sorted_attribs.sort_by(|a, b| a.0.cmp(&b.0));
        for (name, value) in &sorted_attribs {
            attributes.insert(name.clone(), redact_value(name, &Value::String(value.clone())));
        }
        let text = element.text.trim().to_string();
        if !element.attributes.is_empty() || !text.is_empty() {
            let mut coordinate_items: Vec<Coordinate> = vec![Coordinate::text(&key)];
            coordinate_items.push(Coordinate::Tuple(
                sorted_attribs
                    .iter()
                    .map(|(name, value)| {
                        Coordinate::Tuple(vec![
                            Coordinate::text(name),
                            Coordinate::text(value),
                        ])
                    })
                    .collect(),
            ));
            let config_fact = fact(FactInput {
                kind: "ConfigurationKey",
                name: &key,
                framework: FRAMEWORK,
                project_id,
                project_name,
                module_id,
                source: SourceSpan::new(relative),
                coordinates: coordinate_items,
                confidence: 1.0,
                resolution_status: "resolved",
                extraction_method: "safe_xml",
                source_symbol_id: "",
                properties: {
                    let mut properties = Map::new();
                    properties.insert("config_key".into(), json!(key));
                    properties.insert("attributes".into(), Value::Object(attributes.clone()));
                    properties.insert("value".into(), redact_value(&key, &Value::String(text)));
                    properties
                },
            });
            let lower_tag = tag.to_lowercase();
            let joined = current.join(":").to_lowercase();
            if (lower_tag == "add" || lower_tag == "handler")
                && (joined.contains("httphandlers") || joined.contains("handlers"))
            {
                let handler_name = element
                    .attrib("type")
                    .or_else(|| element.attrib("name"))
                    .or_else(|| element.attrib("path"))
                    .unwrap_or(&key)
                    .to_string();
                let handler = fact(FactInput {
                    kind: "HttpHandler",
                    name: &handler_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: SourceSpan::new(relative),
                    coordinates: vec![Coordinate::text("handler"), Coordinate::text(&handler_name)],
                    confidence: 0.95,
                    resolution_status: "unresolved",
                    extraction_method: "safe_xml",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert(
                            "path".into(),
                            json!(element.attrib("path").unwrap_or("")),
                        );
                        properties.insert(
                            "verb".into(),
                            json!(element.attrib("verb").unwrap_or("")),
                        );
                        properties
                    },
                });
                relationships.push(relationship(
                    "LOADS_FROM",
                    &handler,
                    &config_fact,
                    None,
                    0.95,
                    "resolved",
                    "web.config handler declaration",
                    Map::new(),
                ));
                facts.push(handler);
            }
            if (lower_tag == "add" || lower_tag == "module") && joined.contains("modules") {
                let module_name = element
                    .attrib("type")
                    .or_else(|| element.attrib("name"))
                    .unwrap_or(&key)
                    .to_string();
                let position = facts
                    .iter()
                    .filter(|item| item.kind == "HttpModule")
                    .count();
                let module = fact(FactInput {
                    kind: "HttpModule",
                    name: &module_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: SourceSpan::new(relative),
                    coordinates: vec![Coordinate::text("module"), Coordinate::text(&module_name)],
                    confidence: 0.95,
                    resolution_status: "unresolved",
                    extraction_method: "safe_xml",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("position".into(), json!(position));
                        properties
                    },
                });
                relationships.push(relationship(
                    "LOADS_FROM",
                    &module,
                    &config_fact,
                    None,
                    0.95,
                    "resolved",
                    "web.config module declaration",
                    Map::new(),
                ));
                facts.push(module);
            }
            facts.push(config_fact);
        }
        for child in &element.children {
            visit(
                child,
                &current,
                facts,
                relationships,
                project_id,
                project_name,
                module_id,
                relative,
            );
        }
    }

    visit(
        &tree,
        &[],
        &mut facts,
        &mut relationships,
        project_id,
        project_name,
        module_id,
        &relative,
    );
    let mut dependencies = BTreeMap::new();
    dependencies.insert(relative.clone(), BTreeSet::new());
    ArtifactParse {
        facts,
        relationships,
        diagnostics,
        dependencies,
    }
}

/// `parse_resx`.
pub fn parse_resx(
    root: &Path,
    path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
) -> ArtifactParse {
    let (tree, relative, truncated) = match parse_xml_file(root, path, 1024 * 1024) {
        Ok(result) => result,
        Err(error) => {
            let mut parsed = ArtifactParse::read_error(
                "aspnet_framework.resx.parse_error",
                error,
                path,
            );
            parsed
                .diagnostics
                .iter_mut()
                .for_each(|item| item.severity = "warning".into());
            return parsed;
        }
    };
    let mut facts = Vec::new();
    for element in &tree.children {
        if local_name(&element.tag) != "data" {
            continue;
        }
        let name = element.attrib("name").unwrap_or("").to_string();
        let value = element
            .children
            .iter()
            .find(|child| local_name(&child.tag) == "value")
            .map(|child| child.text.clone())
            .unwrap_or_default();
        facts.push(fact(FactInput {
            kind: "ConfigurationKey",
            name: &format!("resource:{name}"),
            framework: FRAMEWORK,
            project_id,
            project_name,
            module_id,
            source: SourceSpan::new(&relative),
            coordinates: vec![Coordinate::text("resource"), Coordinate::text(&name)],
            confidence: 1.0,
            resolution_status: "resolved",
            extraction_method: "safe_xml",
            source_symbol_id: "",
            properties: {
                let mut properties = Map::new();
                properties.insert("config_key".into(), json!(name));
                properties.insert("value".into(), redact_value(&name, &Value::String(value)));
                properties.insert("resource".into(), json!(true));
                properties
            },
        }));
    }
    let diagnostics = if truncated {
        vec![Diagnostic::new(
            "aspnet_framework.resx.truncated",
            "Resource file exceeded budget",
            "warning",
            SourceSpan::new(&relative),
        )]
    } else {
        Vec::new()
    };
    let mut dependencies = BTreeMap::new();
    dependencies.insert(relative.clone(), BTreeSet::new());
    ArtifactParse {
        facts,
        relationships: Vec::new(),
        diagnostics,
        dependencies,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// resolver.py
// ─────────────────────────────────────────────────────────────────────────────

const APPLICATION_EVENTS: [&str; 11] = [
    "Application_Start", "Session_Start", "Application_Error", "Application_BeginRequest",
    "Application_EndRequest", "Application_AuthenticateRequest", "Application_AuthorizeRequest",
    "BeginRequest", "EndRequest", "AuthenticateRequest", "AuthorizeRequest",
];

const FRAMEWORK_HTTP_ATTRIBUTES: [&str; 6] = [
    "HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "AcceptVerbs",
];

fn app_session_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"^(?:Application|Session)_[A-Za-z]+$").expect("regex"))
}

fn semantic_of_framework(
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

fn owner_for_member_framework<'a>(
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

/// `resolve_roslyn_evidence` (framework).
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
        .and_then(Value::as_bool)
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
            } else if bases.iter().any(|item| item.contains("IHttpModule")) {
                "HttpModule"
            } else if bases
                .iter()
                .any(|item| item.contains("IHttpHandler") || item.contains("IHttpAsyncHandler"))
            {
                "HttpHandler"
            } else if name.to_lowercase().contains("service") || name.to_lowercase().contains("repository") {
                if name.to_lowercase().contains("repository") {
                    "Repository"
                } else {
                    "Service"
                }
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
                relationships.push(semantic_of_framework(&item_fact, &anchor, "Type"));
            }
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
            let accessibility = member
                .get("accessibility")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let attributes: Vec<String> = member
                .get("attributes")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|item| {
                            let last = item.as_str().unwrap_or("").rsplit('.').next().unwrap_or("");
                            last.strip_suffix("Attribute").unwrap_or(last).to_string()
                        })
                        .collect()
                })
                .unwrap_or_default();
            let line = member.get("start_line").and_then(Value::as_i64).unwrap_or(1);
            let source = SourceSpan::with_line(&file_path, line);
            let owner = owner_for_member_framework(&qualified, &type_facts);
            let mut member_kind = "";
            if APPLICATION_EVENTS.contains(&name.as_str())
                || app_session_regex().is_match(&name).unwrap_or(false)
            {
                member_kind = "ApplicationEvent";
            } else if (name == "ProcessRequest" && accessibility == "public")
                || (owner.is_some()
                    && owner.map(|item| item.kind == "Controller").unwrap_or(false)
                    && accessibility == "public"
                    && (attributes
                        .iter()
                        .any(|attr| FRAMEWORK_HTTP_ATTRIBUTES.contains(&attr.as_str()))
                        || !name.starts_with('_'))
                    && !attributes.iter().any(|attr| attr == "NonAction"))
            {
                member_kind = "Action";
            }
            if member_kind.is_empty() {
                continue;
            }
            let anchor = member
                .get("canonical_symbol_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let item_fact = fact(FactInput {
                kind: member_kind,
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
                    properties.insert("attributes".into(), json!(attributes));
                    properties
                },
            });
            facts.push(item_fact.clone());
            if !anchor.is_empty() {
                relationships.push(semantic_of_framework(&item_fact, &anchor, "Function"));
            }
            if let Some(owner) = owner {
                relationships.push(relationship(
                    if member_kind == "Action" { "HANDLED_BY" } else { "INITIALIZES" },
                    &item_fact,
                    owner,
                    None,
                    1.0,
                    "resolved",
                    "compiler member ownership",
                    Map::new(),
                ));
            }
        }

        let mut module_position = 0usize;
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
            if matches!(name.as_str(), "MapRoute" | "MapHttpRoute" | "MapPageRoute") {
                let route_value = constants
                    .iter()
                    .find(|item| item.contains('/') || item.contains('{'))
                    .cloned()
                    .unwrap_or_default();
                let dynamic_name = format!("dynamic:{line}");
                let route_display = if route_value.is_empty() {
                    format!("{name}@{line}")
                } else {
                    route_value.clone()
                };
                let endpoint_name = if route_value.is_empty() {
                    dynamic_name
                } else {
                    route_value.clone()
                };
                let confidence = if route_value.is_empty() { 0.65 } else { 0.95 };
                let resolution = if route_value.is_empty() { "dynamic" } else { "resolved" };
                let route_fact = fact(FactInput {
                    kind: "Route",
                    name: &route_display,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source: source.clone(),
                    coordinates: vec![
                        Coordinate::text(&name),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence,
                    resolution_status: resolution,
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("route".into(), json!(route_value));
                        properties.insert("mapping_kind".into(), json!(name));
                        properties.insert("arguments".into(), json!(arguments));
                        properties
                    },
                });
                let endpoint = fact(FactInput {
                    kind: "HttpEndpoint",
                    name: &endpoint_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source,
                    coordinates: vec![
                        Coordinate::text("endpoint"),
                        Coordinate::text(&name),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence,
                    resolution_status: resolution,
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("route".into(), json!(route_value));
                        properties.insert("arguments".into(), json!(arguments));
                        properties
                    },
                });
                facts.push(route_fact.clone());
                facts.push(endpoint.clone());
                relationships.push(relationship(
                    "MAPPED_TO",
                    &endpoint,
                    &route_fact,
                    None,
                    confidence,
                    resolution,
                    "",
                    Map::new(),
                ));
            } else if matches!(
                name.as_str(),
                "AddModule" | "RegisterModule" | "UseStageMarker"
            ) {
                let module_name = constants
                    .first()
                    .or_else(|| arguments.first())
                    .cloned()
                    .unwrap_or_else(|| name.clone());
                let module_fact = fact(FactInput {
                    kind: "HttpModule",
                    name: &module_name,
                    framework: FRAMEWORK,
                    project_id,
                    project_name,
                    module_id,
                    source,
                    coordinates: vec![
                        Coordinate::text(&name),
                        Coordinate::tuple(arguments.iter().map(|item| Coordinate::text(item)).collect()),
                    ],
                    confidence: 0.7,
                    resolution_status: "partial",
                    extraction_method: "roslyn_invocation",
                    source_symbol_id: "",
                    properties: {
                        let mut properties = Map::new();
                        properties.insert("position".into(), json!(module_position));
                        properties.insert("registration".into(), json!(name));
                        properties
                    },
                });
                module_position += 1;
                facts.push(module_fact);
            }
        }
    }
    (facts, relationships)
}

/// `connect_request_pipeline`.
pub fn connect_request_pipeline(
    facts: &[SemanticFact],
    relationships: Vec<SemanticRelationship>,
) -> Vec<SemanticRelationship> {
    let mut values = relationships;
    let mut endpoints: Vec<&SemanticFact> = facts
        .iter()
        .filter(|item| item.kind == "HttpEndpoint")
        .collect();
    endpoints.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    let mut modules: Vec<&SemanticFact> = facts
        .iter()
        .filter(|item| item.kind == "HttpModule")
        .collect();
    modules.sort_by(|a, b| {
        let position_a = a
            .properties
            .get("position")
            .and_then(Value::as_i64)
            .unwrap_or(10_000);
        let position_b = b
            .properties
            .get("position")
            .and_then(Value::as_i64)
            .unwrap_or(10_000);
        (
            position_a,
            a.source.file_path.clone(),
            a.source.start_line,
        )
            .cmp(&(
                position_b,
                b.source.file_path.clone(),
                b.source.start_line,
            ))
    });
    let mut handlers: Vec<&SemanticFact> = facts
        .iter()
        .filter(|item| {
            item.kind == "HttpHandler" || item.kind == "Controller" || item.kind == "WebFormPage"
        })
        .collect();
    handlers.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    for endpoint in &endpoints {
        for (position, module) in modules.iter().enumerate() {
            let mut properties = Map::new();
            properties.insert("position".into(), json!(position));
            values.push(relationship(
                "PASSES_THROUGH",
                endpoint,
                module,
                None,
                0.7,
                "partial",
                "declared Framework request pipeline",
                properties,
            ));
        }
        let argument_text = endpoint
            .properties
            .get("arguments")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| match item {
                        Value::String(text) => text.clone(),
                        other => other.to_string(),
                    })
                    .collect::<Vec<_>>()
                    .join(" ")
            })
            .unwrap_or_default()
            .to_lowercase();
        let matched_handlers: Vec<&&SemanticFact> = handlers
            .iter()
            .filter(|item| {
                !item.source.file_path.is_empty()
                    && argument_text.contains(&item.source.file_path.to_lowercase())
            })
            .collect();
        if !matched_handlers.is_empty() {
            for handler in matched_handlers {
                values.push(relationship(
                    "HANDLED_BY",
                    endpoint,
                    handler,
                    None,
                    0.95,
                    "resolved",
                    "constant route target",
                    Map::new(),
                ));
            }
        } else if handlers.len() == 1 {
            values.push(relationship(
                "HANDLED_BY",
                endpoint,
                handlers[0],
                None,
                0.75,
                "partial",
                "single handler candidate",
                Map::new(),
            ));
        }
    }
    values
}

// ─────────────────────────────────────────────────────────────────────────────
// pipeline.py
// ─────────────────────────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub fn run_aspnet_framework_analysis(
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
                        item.get("code").and_then(Value::as_str).unwrap_or("aspnet_framework.roslyn"),
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
                    "aspnet_framework.roslyn_unavailable",
                    &error,
                    if semantic_mode == "on" { "error" } else { "warning" },
                    SourceSpan::new(&detection.module_path),
                ));
            }
        }
        for path in &detection.artifacts {
            let lower = path.to_lowercase();
            let file_name = crate::pyutil::basename(&lower);
            let parsed = if lower.ends_with(".aspx")
                || lower.ends_with(".ascx")
                || lower.ends_with(".master")
                || lower.ends_with(".asmx")
                || lower.ends_with(".ashx")
                || file_name == "global.asax"
            {
                Some(parse_legacy_markup(root, path, project_id, project_name, &detection.module_id))
            } else if file_name == "web.config" {
                Some(parse_web_config(root, path, project_id, project_name, &detection.module_id))
            } else if lower.ends_with(".resx") {
                Some(parse_resx(root, path, project_id, project_name, &detection.module_id))
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
                    "aspnet_framework.deleted_artifact",
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
            "aspnet_framework.deleted_module_cleanup",
            "The module no longer matches ASP.NET Framework; an empty generation will remove stale overlay facts",
            "info",
            SourceSpan::new(deleted),
        ));
    }
    let relationships = connect_request_pipeline(&facts, relationships);
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
