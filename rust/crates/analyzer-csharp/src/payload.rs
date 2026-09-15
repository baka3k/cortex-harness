//! Port `code-tiny/tools/csharp/models.py` — chuyển `DocumentEvidence` của
//! Roslyn worker về payload dict của analyzer (legacy FunctionDef/TypeDef
//! shape + member inventory) để graph writer dùng chung không phải đổi.

use serde_json::{json, Map, Value};

use crate::adapter::CSHARP_ROSLYN_CACHE_VERSION;

pub const CSHARP_ROSLYN_MODEL_VERSION: &str = "csharp-primary-v1";

fn to_str(value: Option<&Value>, default: &str) -> String {
    match value {
        None | Some(Value::Null) => default.to_string(),
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.to_string(),
    }
}

fn to_str_or(value: &Value, default: &str) -> String {
    if value.is_null() {
        default.to_string()
    } else {
        to_str(Some(value), default)
    }
}

fn to_int(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(text)) => text.parse::<i64>().unwrap_or(default),
        _ => default,
    }
}

fn to_bool(value: Option<&Value>) -> bool {
    value.and_then(Value::as_bool).unwrap_or(false)
}

fn str_list(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| {
                if item.is_null() {
                    None
                } else {
                    Some(Value::String(to_str_or(item, "")))
                }
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn raw_list<'a>(value: Option<&'a Value>) -> &'a [Value] {
    match value {
        Some(Value::Array(items)) => items,
        _ => &[],
    }
}

/// `_strip_xml_doc` — reduce nhẹ `///` doc strings.
pub fn strip_xml_doc(xml: &str) -> String {
    if xml.is_empty() {
        return String::new();
    }
    let mut cleaned = String::with_capacity(xml.len());
    let mut chars = xml.chars().peekable();
    while let Some(character) = chars.next() {
        if character == '<' {
            // bỏ tag `</?[^>]+>`
            let mut tag = String::from("<");
            let mut closed = false;
            for next in chars.by_ref() {
                if next == '>' {
                    closed = true;
                    break;
                }
                tag.push(next);
            }
            let _ = tag;
            if closed {
                cleaned.push(' ');
                continue;
            }
            cleaned.push_str(&tag);
            break;
        }
        cleaned.push(character);
    }
    cleaned.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `_build_note` — khớp Python: Summary/Comment/Code blocks join bằng 2 newline.
pub fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// `_scope_of` — phần trước `::` hoặc `.` cuối cùng.
pub fn scope_of(qualified_name: &str) -> String {
    if qualified_name.is_empty() {
        return String::new();
    }
    if let Some(position) = qualified_name.rfind("::") {
        return qualified_name[..position].to_string();
    }
    if let Some(position) = qualified_name.rfind('.') {
        return qualified_name[..position].to_string();
    }
    String::new()
}

/// `_type_kind_to_function_kind`.
fn type_kind_to_function_kind(kind: &str) -> &'static str {
    match kind {
        "constructor" => "constructor",
        "property" => "property",
        "indexer" => "indexer",
        _ => "method",
    }
}

/// `_symbol_id_for_member`.
fn symbol_id_for_member(file_rel: &str, qualified_name: &str, kind: &str, arity: i64) -> String {
    if qualified_name.is_empty() {
        return format!("{}@anon@{file_rel}", kind.to_lowercase());
    }
    format!("{qualified_name}/{arity}@{file_rel}")
}

fn parameter_from_evidence(payload: &Value) -> Value {
    let get = |key: &str| payload.get(key);
    json!({
        "name": to_str(get("name"), ""),
        "type_name": to_str(get("type_name"), ""),
        "is_optional": to_bool(get("is_optional")),
        "default_value": match get("default_value") {
            Some(Value::Null) | None => Value::Null,
            Some(Value::String(text)) if text.is_empty() => Value::Null,
            Some(other) => other.clone(),
        },
        "is_params": to_bool(get("is_params")),
        "is_ref": to_bool(get("is_ref")),
        "is_out": to_bool(get("is_out")),
        "is_in": to_bool(get("is_in")),
    })
}

/// Cặp (key, value) project metadata dùng chung cho node rows của graph.rs.
pub fn project_meta(
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Vec<(&'static str, Value)> {
    vec![
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ]
}

fn member_properties(evidence: &Value, file_path: &str) -> Vec<Value> {
    raw_list(evidence.get("members"))
        .iter()
        .filter(|member| matches!(to_str_or(&member["kind"], "").as_str(), "property" | "indexer"))
        .map(|member| {
            json!({
                "name": member.get("name").cloned().unwrap_or(Value::Null),
                "qualified_name": member.get("qualified_name").cloned().unwrap_or(Value::Null),
                "type_name": member.get("return_type").cloned().unwrap_or(Value::Null),
                "accessibility": member.get("accessibility").cloned().unwrap_or(Value::Null),
                "is_static": member.get("is_static").cloned().unwrap_or(Value::Null),
                "is_virtual": member.get("is_virtual").cloned().unwrap_or(Value::Null),
                "is_override": member.get("is_override").cloned().unwrap_or(Value::Null),
                "is_abstract": member.get("is_abstract").cloned().unwrap_or(Value::Null),
                "kind": member.get("kind").cloned().unwrap_or(Value::Null),
                "file_path": file_path,
                "start_line": to_int(member.get("start_line"), 0),
                "end_line": to_int(member.get("end_line"), 0),
            })
        })
        .collect()
}

/// `roslyn_evidence_to_payload` — trả payload dict giống hệt keys Python.
pub fn roslyn_evidence_to_payload(evidence: &Value) -> Value {
    let file_path = to_str(evidence.get("file_path"), "");
    let namespace_name = to_str(evidence.get("namespace"), "");
    let members = raw_list(evidence.get("members"));
    let types = raw_list(evidence.get("types"));
    let fields = raw_list(evidence.get("fields"));
    let events = raw_list(evidence.get("events"));
    let delegates = raw_list(evidence.get("delegates"));
    let parameters = raw_list(evidence.get("parameters"));
    let usings = raw_list(evidence.get("usings"));
    let attributes = raw_list(evidence.get("attributes"));
    let calls = raw_list(evidence.get("calls"));

    // Members/constructors/properties/indexers → legacy FunctionDef shape.
    let mut legacy_functions: Vec<Value> = Vec::new();
    for member in members {
        let kind = {
            let raw = to_str(member.get("kind"), "method");
            if raw.is_empty() {
                "method".to_string()
            } else {
                raw
            }
        };
        let qualified_name = {
            let from_qualified = to_str(member.get("qualified_name"), "");
            if from_qualified.is_empty() {
                to_str(member.get("name"), "")
            } else {
                from_qualified
            }
        };
        let parameters_in_member: Vec<Value> = raw_list(member.get("parameters"))
            .iter()
            .map(parameter_from_evidence)
            .collect();
        let arity = parameters_in_member.len() as i64;
        let symbol_id = {
            let canonical = to_str(member.get("canonical_symbol_id"), "");
            if canonical.is_empty() {
                symbol_id_for_member(&file_path, &qualified_name, &kind, arity)
            } else {
                canonical
            }
        };
        let xml_doc = to_str(member.get("xml_doc"), "");
        let summary = strip_xml_doc(&xml_doc);
        legacy_functions.push(json!({
            "symbol_id": symbol_id,
            "qualified_name": qualified_name,
            "name": to_str(member.get("name"), ""),
            "kind": type_kind_to_function_kind(&kind),
            "scope_name": scope_of(&qualified_name),
            "file_path": file_path,
            "start_line": to_int(member.get("start_line"), 0),
            "end_line": to_int(member.get("end_line"), 0),
            "arity": arity,
            "code": "",
            "comment": "",
            "summary": summary,
            "note": build_note("", "", &summary),
            "class_name": scope_of(&qualified_name),
            "package_name": namespace_name,
            "member_kind": kind,
            "is_async": to_bool(member.get("is_async")),
            "is_static": to_bool(member.get("is_static")),
            "is_virtual": to_bool(member.get("is_virtual")),
            "is_override": to_bool(member.get("is_override")),
            "is_abstract": to_bool(member.get("is_abstract")),
            "is_extension_method": to_bool(member.get("is_extension_method")),
            "accessibility": to_str(member.get("accessibility"), ""),
            "attributes": member.get("attributes").cloned().unwrap_or(json!([])),
            "return_type": to_str(member.get("return_type"), ""),
            "type_parameters": member.get("type_parameters").cloned().unwrap_or(json!([])),
            "xml_doc": xml_doc,
            "parameters": parameters_in_member,
        }));
    }

    let mut legacy_types: Vec<Value> = Vec::new();
    for type_evidence in types {
        let qualified_name = {
            let from_qualified = to_str(type_evidence.get("qualified_name"), "");
            if from_qualified.is_empty() {
                to_str(type_evidence.get("name"), "")
            } else {
                from_qualified
            }
        };
        let mut kind = to_str(type_evidence.get("kind"), "type");
        if kind.is_empty() {
            kind = "type".to_string();
        }
        let xml_doc = to_str(type_evidence.get("xml_doc"), "");
        let summary = strip_xml_doc(&xml_doc);
        let mut type_constraints = Map::new();
        if let Some(Value::Object(map)) = type_evidence.get("type_constraints") {
            for (key, value) in map {
                type_constraints.insert(key.clone(), value.clone());
            }
        }
        let type_symbol_id = {
            let canonical = to_str(type_evidence.get("canonical_symbol_id"), "");
            if canonical.is_empty() {
                qualified_name.clone()
            } else {
                canonical
            }
        };
        legacy_types.push(json!({
            "symbol_id": type_symbol_id,
            "qualified_name": qualified_name,
            "name": to_str(type_evidence.get("name"), ""),
            "kind": kind,
            "namespace_name": namespace_name,
            "file_path": file_path,
            "start_line": to_int(type_evidence.get("start_line"), 0),
            "end_line": to_int(type_evidence.get("end_line"), 0),
            "code": "",
            "comment": "",
            "summary": summary,
            "note": build_note("", "", &summary),
            "base_types": type_evidence.get("base_types").cloned().unwrap_or(json!([])),
            "implemented_interfaces": type_evidence.get("implemented_interfaces").cloned().unwrap_or(json!([])),
            "type_parameters": type_evidence.get("type_parameters").cloned().unwrap_or(json!([])),
            "type_constraints": Value::Object(type_constraints),
            "is_abstract": to_bool(type_evidence.get("is_abstract")),
            "is_sealed": to_bool(type_evidence.get("is_sealed")),
            "is_static": to_bool(type_evidence.get("is_static")),
            "is_partial": to_bool(type_evidence.get("is_partial")),
            "is_record": to_bool(type_evidence.get("is_record")),
            "accessibility": to_str(type_evidence.get("accessibility"), ""),
            "attributes": type_evidence.get("attributes").cloned().unwrap_or(json!([])),
            "xml_doc": xml_doc,
        }));
    }

    let mut legacy_fields: Vec<Value> = Vec::new();
    for field_evidence in fields {
        let qualified_name = {
            let from_qualified = to_str(field_evidence.get("qualified_name"), "");
            if from_qualified.is_empty() {
                to_str(field_evidence.get("name"), "")
            } else {
                from_qualified
            }
        };
        let symbol_id = {
            let canonical = to_str(field_evidence.get("canonical_symbol_id"), "");
            if canonical.is_empty() {
                symbol_id_for_member(&file_path, &qualified_name, "field", 0)
            } else {
                canonical
            }
        };
        legacy_fields.push(json!({
            "symbol_id": symbol_id,
            "qualified_name": qualified_name,
            "name": to_str(field_evidence.get("name"), ""),
            "kind": "field",
            "namespace_name": namespace_name,
            "class_name": scope_of(&qualified_name),
            "file_path": file_path,
            "start_line": to_int(field_evidence.get("start_line"), 0),
            "end_line": to_int(field_evidence.get("end_line"), 0),
            "type_name": to_str(field_evidence.get("type_name"), ""),
            "accessibility": to_str(field_evidence.get("accessibility"), ""),
            "is_static": to_bool(field_evidence.get("is_static")),
            "is_const": to_bool(field_evidence.get("is_const")),
            "is_readonly": to_bool(field_evidence.get("is_readonly")),
            "is_volatile": to_bool(field_evidence.get("is_volatile")),
            "constant_value": match field_evidence.get("constant_value") {
                Some(Value::String(text)) if text.is_empty() => Value::Null,
                Some(Value::Null) | None => Value::Null,
                Some(other) => other.clone(),
            },
            "attributes": field_evidence.get("attributes").cloned().unwrap_or(json!([])),
            "xml_doc": to_str(field_evidence.get("xml_doc"), ""),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        }));
    }

    let mut legacy_events: Vec<Value> = Vec::new();
    for event_evidence in events {
        let qualified_name = {
            let from_qualified = to_str(event_evidence.get("qualified_name"), "");
            if from_qualified.is_empty() {
                to_str(event_evidence.get("name"), "")
            } else {
                from_qualified
            }
        };
        let symbol_id = {
            let canonical = to_str(event_evidence.get("canonical_symbol_id"), "");
            if canonical.is_empty() {
                symbol_id_for_member(&file_path, &qualified_name, "event", 0)
            } else {
                canonical
            }
        };
        legacy_events.push(json!({
            "symbol_id": symbol_id,
            "qualified_name": qualified_name,
            "name": to_str(event_evidence.get("name"), ""),
            "kind": "event",
            "namespace_name": namespace_name,
            "class_name": scope_of(&qualified_name),
            "file_path": file_path,
            "start_line": to_int(event_evidence.get("start_line"), 0),
            "end_line": to_int(event_evidence.get("end_line"), 0),
            "delegate_type": to_str(event_evidence.get("delegate_type"), ""),
            "accessibility": to_str(event_evidence.get("accessibility"), ""),
            "is_static": to_bool(event_evidence.get("is_static")),
            "is_abstract": to_bool(event_evidence.get("is_abstract")),
            "attributes": event_evidence.get("attributes").cloned().unwrap_or(json!([])),
            "xml_doc": to_str(event_evidence.get("xml_doc"), ""),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        }));
    }

    let mut legacy_delegates: Vec<Value> = Vec::new();
    for delegate_evidence in delegates {
        let qualified_name = {
            let from_qualified = to_str(delegate_evidence.get("qualified_name"), "");
            if from_qualified.is_empty() {
                to_str(delegate_evidence.get("name"), "")
            } else {
                from_qualified
            }
        };
        let symbol_id = {
            let canonical = to_str(delegate_evidence.get("canonical_symbol_id"), "");
            if canonical.is_empty() {
                symbol_id_for_member(&file_path, &qualified_name, "delegate", 0)
            } else {
                canonical
            }
        };
        legacy_delegates.push(json!({
            "symbol_id": symbol_id,
            "qualified_name": qualified_name,
            "name": to_str(delegate_evidence.get("name"), ""),
            "kind": "delegate",
            "namespace_name": namespace_name,
            "class_name": scope_of(&qualified_name),
            "file_path": file_path,
            "start_line": to_int(delegate_evidence.get("start_line"), 0),
            "end_line": to_int(delegate_evidence.get("end_line"), 0),
            "return_type": to_str(delegate_evidence.get("return_type"), ""),
            "type_parameters": delegate_evidence.get("type_parameters").cloned().unwrap_or(json!([])),
            "parameters": raw_list(delegate_evidence.get("parameters")).iter().map(parameter_from_evidence).collect::<Vec<_>>(),
            "accessibility": to_str(delegate_evidence.get("accessibility"), ""),
            "attributes": delegate_evidence.get("attributes").cloned().unwrap_or(json!([])),
            "xml_doc": to_str(delegate_evidence.get("xml_doc"), ""),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        }));
    }

    let mut legacy_parameters: Vec<Value> = Vec::new();
    for param_evidence in parameters {
        let mut param = parameter_from_evidence(param_evidence);
        if let Some(object) = param.as_object_mut() {
            object.insert("file_path".to_string(), json!(file_path));
        }
        legacy_parameters.push(param);
    }

    // Type-level edges: EXTENDS_CLASS / IMPLEMENTS_INTERFACE (properties
    // `resolved` khớp Python bool(list)).
    let mut legacy_relations: Vec<Value> = Vec::new();
    for type_evidence in types {
        let type_symbol_id = {
            let canonical = to_str(type_evidence.get("canonical_symbol_id"), "");
            if !canonical.is_empty() {
                canonical
            } else {
                let from_qualified = to_str(type_evidence.get("qualified_name"), "");
                if !from_qualified.is_empty() {
                    from_qualified
                } else {
                    to_str(type_evidence.get("name"), "")
                }
            }
        };
        let base_types = str_list(type_evidence.get("base_types"));
        let has_base = !base_types.is_empty();
        for base_type in base_types {
            legacy_relations.push(json!({
                "source_id": type_symbol_id,
                "source_label": "Type",
                "target_id": base_type,
                "target_label": "Type",
                "rel_type": "EXTENDS_CLASS",
                "properties": { "resolved": has_base },
            }));
        }
        let interfaces = str_list(type_evidence.get("implemented_interfaces"));
        let has_iface = !interfaces.is_empty();
        for interface in interfaces {
            legacy_relations.push(json!({
                "source_id": type_symbol_id,
                "source_label": "Type",
                "target_id": interface,
                "target_label": "Type",
                "rel_type": "IMPLEMENTS_INTERFACE",
                "properties": { "resolved": has_iface },
            }));
        }
    }

    let mut legacy_calls: Vec<Value> = Vec::new();
    for call in calls {
        let caller_id = to_str(call.get("caller_id"), "");
        // Python: caller_id.rsplit("::", 1)[0] — phần TRƯỚC "::" cuối.
        let caller_scope = caller_id
            .rsplit_once("::")
            .map(|(head, _)| head.to_string())
            .unwrap_or_default();
        let expression = to_str(call.get("expression"), "");
        let callee_name = expression
            .split('(')
            .next()
            .unwrap_or("")
            .rsplit('.')
            .next()
            .unwrap_or("")
            .to_string();
        let resolved_callee = to_str(call.get("resolved_callee_id"), "");
        legacy_calls.push(json!({
            "caller_id": caller_id,
            "caller_scope": caller_scope,
            "callee_name": callee_name,
            "callee_id": if resolved_callee.is_empty() { Value::Null } else { json!(resolved_callee) },
            "callee_arity": call.get("callee_arity").cloned().unwrap_or(Value::Null),
            "call_line": to_int(call.get("start_line"), 0),
            "resolved": to_bool(call.get("resolved")),
            "is_async": to_bool(call.get("is_async")),
            "is_virtual_dispatch": to_bool(call.get("is_virtual_dispatch")),
            "arguments": call.get("arguments").cloned().unwrap_or(json!([])),
        }));
    }

    let namespace_payload = if namespace_name.is_empty() {
        vec![]
    } else {
        vec![json!({
            "symbol_id": format!("namespace::{namespace_name}"),
            "qualified_name": namespace_name,
            "name": namespace_name,
            "file_path": file_path,
            "start_line": 1,
            "end_line": 1,
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        })]
    };

    let usings_names: Vec<Value> = usings
        .iter()
        .filter_map(|using| {
            let name = to_str(using.get("name"), "");
            if name.is_empty() {
                None
            } else {
                Some(json!(name))
            }
        })
        .collect();
    let attribute_names: Vec<Value> = attributes
        .iter()
        .filter_map(|attribute| {
            let name = to_str(attribute.get("name"), "");
            if name.is_empty() {
                None
            } else {
                Some(json!(name))
            }
        })
        .collect();

    json!({
        "namespaces": namespace_payload,
        "types": legacy_types,
        "functions": legacy_functions,
        "calls": legacy_calls,
        "fields": legacy_fields,
        "events": legacy_events,
        "delegates": legacy_delegates,
        "parameters": legacy_parameters,
        "properties": member_properties(evidence, &file_path),
        "usings": usings,
        "attributes": attributes,
        "relations": legacy_relations,
        "file_def": {
            "file_path": file_path,
            "start_line": 1,
            "end_line": 1,
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
            "imports": usings_names,
            "attributes": attribute_names,
        },
        "parse_meta": {
            "parser_language": "csharp_roslyn",
            "parser_available": true,
            "has_error": false,
            "error_nodes": 0,
            "semantic_enabled": true,
            "coverage_status": "full",
            "roslyn_workspace_kind": "safe_compilation",
        },
        "parse_cache_version": CSHARP_ROSLYN_CACHE_VERSION,
        "model_version": CSHARP_ROSLYN_MODEL_VERSION,
    })
}

/// `project_metadata_to_payload`.
pub fn project_metadata_to_payload(project: Option<&Value>) -> Value {
    let Some(project) = project.filter(|value| !value.is_null()) else {
        return json!({"packages": [], "project_references": []});
    };
    json!({
        "project_path": to_str(project.get("project_path"), ""),
        "target_framework": to_str(project.get("target_framework"), ""),
        "sdk": to_str(project.get("sdk"), ""),
        "output_type": to_str(project.get("output_type"), ""),
        "packages": raw_list(project.get("nuget_packages"))
            .iter()
            .map(|package| {
                json!({
                    "name": to_str(package.get("name"), ""),
                    "version": to_str(package.get("version"), ""),
                    "is_development": to_bool(package.get("is_development")),
                })
            })
            .collect::<Vec<_>>(),
        "project_references": project.get("project_references").cloned().unwrap_or(json!([])),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn evidence_converts_to_legacy_payload() {
        let evidence = json!({
            "file_path": "Program.cs",
            "namespace": "App",
            "types": [{
                "name": "Program",
                "qualified_name": "App.Program",
                "kind": "class",
                "start_line": 3,
                "end_line": 10,
                "canonical_symbol_id": "App.Program",
                "base_types": ["App.Base"],
                "implemented_interfaces": ["System.IDisposable"],
                "xml_doc": "<summary>Hello <see cref=\"X\"/></summary>",
            }],
            "members": [{
                "name": "Main",
                "qualified_name": "App.Program.Main",
                "kind": "method",
                "start_line": 5,
                "end_line": 9,
                "canonical_symbol_id": "App.Program.Main/1@Program.cs",
                "accessibility": "public",
                "return_type": "void",
                "parameters": [{"name": "args", "type_name": "string[]", "is_params": true}],
                "xml_doc": "<summary>Entry.</summary>",
            }],
            "fields": [{
                "name": "counter",
                "qualified_name": "App.Program.counter",
                "kind": "field",
                "type_name": "int",
                "is_static": true,
                "constant_value": "",
            }],
            "events": [],
            "delegates": [],
            "parameters": [],
            "usings": [{"name": "System"}],
            "attributes": [{"name": "Obsolete"}],
            "calls": [{
                "expression": "Console.WriteLine(x)",
                "caller_id": "App.Program::Main",
                "start_line": 6,
                "resolved": false,
            }],
        });
        let payload = roslyn_evidence_to_payload(&evidence);
        assert_eq!(payload["functions"].as_array().expect("fns").len(), 1);
        let function = &payload["functions"][0];
        assert_eq!(function["symbol_id"], "App.Program.Main/1@Program.cs");
        assert_eq!(function["kind"], "method");
        assert_eq!(function["scope_name"], "App.Program");
        assert_eq!(function["package_name"], "App");
        assert_eq!(function["arity"], 1);
        // member.xml_doc = "<summary>Entry.</summary>" → summary = "Entry."
        // (khớp Python `_strip_xml_doc`).
        assert_eq!(function["summary"], "Entry.");
        assert_eq!(function["note"], "Summary:\nEntry.");
        assert_eq!(payload["types"][0]["symbol_id"], "App.Program");
        assert_eq!(payload["types"][0]["summary"], "Hello");
        assert_eq!(payload["fields"][0]["constant_value"], Value::Null);
        assert_eq!(payload["namespaces"][0]["symbol_id"], "namespace::App");
        assert_eq!(payload["file_def"]["imports"], json!(["System"]));
        assert_eq!(payload["file_def"]["attributes"], json!(["Obsolete"]));
        let relations = payload["relations"].as_array().expect("rels");
        assert_eq!(relations.len(), 2);
        assert_eq!(relations[0]["rel_type"], "EXTENDS_CLASS");
        assert_eq!(relations[0]["properties"]["resolved"], true);
        assert_eq!(relations[1]["rel_type"], "IMPLEMENTS_INTERFACE");
        let calls = payload["calls"].as_array().expect("calls");
        assert_eq!(calls[0]["callee_name"], "WriteLine");
        assert_eq!(calls[0]["caller_scope"], "App.Program");
        assert_eq!(calls[0]["callee_id"], Value::Null);
        assert_eq!(
            payload["parse_meta"]["parser_language"],
            "csharp_roslyn"
        );
        assert_eq!(payload["model_version"], CSHARP_ROSLYN_MODEL_VERSION);
    }

    #[test]
    fn project_metadata_defaults() {
        let empty = project_metadata_to_payload(None);
        assert_eq!(empty["packages"], json!([]));
        let project = json!({
            "project_path": "App.csproj",
            "target_framework": "net8.0",
            "nuget_packages": [{"name": "Newtonsoft.Json", "version": "13.0.3"}],
        });
        let converted = project_metadata_to_payload(Some(&project));
        assert_eq!(converted["target_framework"], "net8.0");
        assert_eq!(converted["packages"][0]["name"], "Newtonsoft.Json");
    }

    #[test]
    fn scope_of_matches_python() {
        assert_eq!(scope_of("App.Program::Main"), "App.Program");
        assert_eq!(scope_of("App.Program"), "App");
        assert_eq!(scope_of("Program"), "");
        assert_eq!(scope_of(""), "");
    }
}
