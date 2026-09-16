//! Port `tools/struts/resolver.py` — resolve_struts_project.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use super::models::{
    stable_id, ActionConfig, Diagnostic, ExceptionMappingConfig, InterceptorConfig, InterceptorRef, PackageConfig,
    Params, ResultConfig, SourceSpan, StrutsFact, StrutsRelationship, ValidationRule, WebFilterConfig,
    repr_sorted_items,
};

const VIEW_RESULT_TYPES: [&str; 4] = ["dispatcher", "freemarker", "velocity", "tiles"];
const REDIRECT_RESULT_TYPES: [&str; 2] = ["redirect", "redirectaction"];

pub struct StrutsResolution {
    pub facts: Vec<StrutsFact>,
    pub relationships: Vec<StrutsRelationship>,
    pub diagnostics: Vec<Diagnostic>,
}

struct Collector {
    project_id: String,
    project_name: String,
    module_id: String,
    facts: BTreeMap<String, StrutsFact>,
    relationships: BTreeMap<String, StrutsRelationship>,
}

impl Collector {
    fn new(project_id: &str, project_name: &str, module_id: &str) -> Self {
        Self {
            project_id: project_id.to_string(),
            project_name: project_name.to_string(),
            module_id: module_id.to_string(),
            facts: BTreeMap::new(),
            relationships: BTreeMap::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn fact(
        &mut self,
        kind: &str,
        name: &str,
        source: &SourceSpan,
        identity: Vec<String>,
        properties: Map<String, Value>,
        extraction_method: &str,
        resolution_status: &str,
        confidence: f64,
    ) -> StrutsFact {
        let mut parts = vec![self.project_id.clone(), self.module_id.clone()];
        parts.extend(identity);
        let fact_id = stable_id(kind, &parts);
        let fact = StrutsFact {
            kind: kind.to_string(),
            stable_id: fact_id.clone(),
            name: name.to_string(),
            source: source.clone(),
            project_id: self.project_id.clone(),
            project_name: self.project_name.clone(),
            module_id: self.module_id.clone(),
            confidence,
            extraction_method: extraction_method.to_string(),
            resolution_status: resolution_status.to_string(),
            properties,
        };
        self.facts.insert(fact_id, fact.clone());
        fact
    }

    fn rel(
        &mut self,
        source_fact: &StrutsFact,
        target_fact: &StrutsFact,
        relationship_type: &str,
        source: &SourceSpan,
        properties: Map<String, Value>,
        reason: &str,
    ) {
        let payload_repr = repr_sorted_items(&properties);
        let relationship_id = stable_id(
            "relationship",
            &[
                self.project_id.clone(),
                self.module_id.clone(),
                source_fact.stable_id.clone(),
                relationship_type.to_string(),
                target_fact.stable_id.clone(),
                payload_repr,
            ],
        );
        self.relationships.insert(
            relationship_id.clone(),
            StrutsRelationship {
                stable_id: relationship_id,
                from_id: source_fact.stable_id.clone(),
                to_id: target_fact.stable_id.clone(),
                from_label: source_fact.kind.clone(),
                to_label: target_fact.kind.clone(),
                rel_type: relationship_type.to_string(),
                project_id: self.project_id.clone(),
                module_id: self.module_id.clone(),
                source: source.clone(),
                confidence: 1.0,
                resolution_status: "resolved".to_string(),
                reason: reason.to_string(),
                properties,
            },
        );
    }
}

impl Default for StrutsFact {
    fn default() -> Self {
        Self {
            kind: String::new(),
            stable_id: String::new(),
            name: String::new(),
            source: SourceSpan::default(),
            project_id: String::new(),
            project_name: String::new(),
            module_id: String::new(),
            confidence: 1.0,
            extraction_method: "struts_xml".to_string(),
            resolution_status: "resolved".to_string(),
            properties: Map::new(),
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn lineage(
    package: &PackageConfig,
    packages: &BTreeMap<String, PackageConfig>,
    diagnostics: &mut Vec<Diagnostic>,
    trail: &[String],
) -> Vec<PackageConfig> {
    if trail.iter().any(|name| name == &package.name) {
        let mut chain = trail.to_vec();
        chain.push(package.name.clone());
        diagnostics.push(Diagnostic::new(
            "struts.package.inheritance_cycle",
            &format!("Package inheritance cycle: {}", chain.join(" -> ")),
            "error",
            &package.source.file_path,
        ));
        return Vec::new();
    }
    let mut result: Vec<PackageConfig> = Vec::new();
    for parent_name in &package.extends {
        let Some(parent) = packages.get(parent_name) else {
            diagnostics.push(Diagnostic::new(
                "struts.package.parent_missing",
                &format!(
                    "Package {} extends missing package {}",
                    crate::pyjson::py_repr_str(&package.name),
                    crate::pyjson::py_repr_str(parent_name)
                ),
                "warning",
                &package.source.file_path,
            ));
            continue;
        };
        let mut next_trail = trail.to_vec();
        next_trail.push(package.name.clone());
        result.extend(lineage(parent, packages, diagnostics, &next_trail));
    }
    result.push(package.clone());
    let mut deduped: BTreeMap<String, PackageConfig> = BTreeMap::new();
    for item in result {
        deduped.insert(item.name.clone(), item);
    }
    deduped.into_values().collect()
}

fn named(lineage_rows: &[PackageConfig], attribute: &str) -> BTreeMap<String, Value> {
    // Trả name → JSON của config item tương ứng (interceptor/stack/result...).
    let mut values: BTreeMap<String, Value> = BTreeMap::new();
    for package in lineage_rows {
        let items: Vec<Value> = match attribute {
            "interceptors" => package
                .interceptors
                .iter()
                .map(|item| interceptor_json(item.name.clone(), item.class_name.clone(), &item.params))
                .collect(),
            "interceptor_stacks" => package
                .interceptor_stacks
                .iter()
                .map(|item| {
                    let refs: Vec<Value> = item
                        .refs
                        .iter()
                        .map(|reference| {
                            json!({
                                "name": reference.name,
                                "params": params_json(&reference.params),
                            })
                        })
                        .collect();
                    json!({"name": item.name, "refs": refs})
                })
                .collect(),
            "result_types" => package
                .result_types
                .iter()
                .map(|item| {
                    json!({
                        "name": item.name,
                        "class_name": item.class_name,
                        "default": item.default,
                        "params": params_json(&item.params),
                    })
                })
                .collect(),
            "global_results" => package
                .global_results
                .iter()
                .map(result_json)
                .collect(),
            _ => Vec::new(),
        };
        for item in items {
            let name = item.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            values.insert(name, item);
        }
    }
    values
}

fn interceptor_json(name: String, class_name: String, params_map: &Params) -> Value {
    json!({
        "name": name,
        "class_name": class_name,
        "params": params_json(params_map),
    })
}

fn result_json(item: &ResultConfig) -> Value {
    json!({
        "name": item.name,
        "type_name": item.type_name,
        "location": item.location,
        "params": params_json(&item.params),
    })
}

fn params_json(params_map: &Params) -> Value {
    Value::Object(params_map.iter().map(|(key, value)| (key.clone(), json!(value))).collect())
}

fn default_of(lineage_rows: &[PackageConfig], attribute: &str) -> String {
    for package in lineage_rows.iter().rev() {
        let value = match attribute {
            "default_interceptor_ref" => &package.default_interceptor_ref,
            "default_result_type" => &package.default_result_type,
            _ => "",
        };
        if !value.is_empty() {
            return value.to_string();
        }
    }
    String::new()
}

#[derive(Clone)]
struct ExpandedInterceptor {
    interceptor: InterceptorConfig,
    effective_params: Params,
    ref_name: String,
}

#[allow(clippy::too_many_arguments)]
fn expand_refs(
    refs: &[InterceptorRef],
    interceptor_defs: &BTreeMap<String, Value>,
    stack_defs: &BTreeMap<String, Value>,
    diagnostics: &mut Vec<Diagnostic>,
    source: &SourceSpan,
    trail: &[String],
    inherited_params: &Params,
) -> Vec<ExpandedInterceptor> {
    let mut expanded: Vec<ExpandedInterceptor> = Vec::new();
    let inherited = inherited_params.clone();
    for reference in refs {
        if stack_defs.contains_key(&reference.name) {
            if trail.iter().any(|name| name == &reference.name) {
                let mut chain = trail.to_vec();
                chain.push(reference.name.clone());
                diagnostics.push(Diagnostic::new(
                    "struts.interceptor_stack.cycle",
                    &format!("Interceptor stack cycle: {}", chain.join(" -> ")),
                    "error",
                    &source.file_path,
                ));
                continue;
            }
            let mut stack_params = inherited.clone();
            for (key, value) in &reference.params {
                stack_params.insert(key.clone(), value.clone());
            }
            let stack = &stack_defs[&reference.name];
            let refs: Vec<InterceptorRef> = stack
                .get("refs")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .map(|row| InterceptorRef {
                            name: row.get("name").and_then(Value::as_str).unwrap_or("").to_string(),
                            params: json_params(row.get("params")),
                        })
                        .collect()
                })
                .unwrap_or_default();
            let mut next_trail = trail.to_vec();
            next_trail.push(reference.name.clone());
            expanded.extend(expand_refs(
                &refs,
                interceptor_defs,
                stack_defs,
                diagnostics,
                source,
                &next_trail,
                &stack_params,
            ));
            continue;
        }
        let Some(interceptor) = interceptor_defs.get(&reference.name) else {
            diagnostics.push(Diagnostic::new(
                "struts.interceptor.unresolved",
                &format!("Unable to resolve interceptor or stack {:?}", reference.name),
                "warning",
                &source.file_path,
            ));
            continue;
        };
        let mut effective_params: Params = json_params(interceptor.get("params"));
        let prefix = format!("{}.", reference.name);
        for (key, value) in &inherited {
            let stripped = key.strip_prefix(&prefix).unwrap_or(key);
            effective_params.insert(stripped.to_string(), value.clone());
        }
        for (key, value) in &reference.params {
            effective_params.insert(key.clone(), value.clone());
        }
        expanded.push(ExpandedInterceptor {
            interceptor: InterceptorConfig {
                name: interceptor.get("name").and_then(Value::as_str).unwrap_or("").to_string(),
                class_name: interceptor.get("class_name").and_then(Value::as_str).unwrap_or("").to_string(),
                params: json_params(interceptor.get("params")),
            },
            effective_params,
            ref_name: reference.name.clone(),
        });
    }
    expanded
}

pub fn params_json_value(value: Option<&Value>) -> Value {
    let mut out = serde_json::Map::new();
    if let Some(serde_json::Value::Object(map)) = value {
        for (key, item) in map {
            out.insert(key.clone(), json!(item));
        }
    }
    Value::Object(out)
}

#[allow(dead_code)]
fn json_params(value: Option<&Value>) -> Params {
    let mut out = Params::new();
    if let Some(Value::Object(map)) = value {
        for (key, item) in map {
            out.insert(key.clone(), crate::pyjson::py_str(item));
        }
    }
    out
}

fn route(namespace: &str, action_name: &str, extension: &str) -> String {
    let trimmed = namespace.trim_matches('/');
    let namespace = if trimmed.is_empty() {
        String::new()
    } else {
        format!("/{trimmed}")
    };
    let action_name = action_name.trim_start_matches('/').to_string();
    let suffix = if !extension.is_empty() && !action_name.ends_with(&format!(".{extension}")) {
        format!(".{extension}")
    } else {
        String::new()
    };
    let combined = format!("{namespace}/{action_name}{suffix}");
    if combined.is_empty() {
        "/".to_string()
    } else {
        combined
    }
}

fn result_type(result: &ResultConfig, default_type: &str) -> String {
    let type_name = if !result.type_name.is_empty() {
        result.type_name.clone()
    } else {
        default_type.to_string()
    };
    if type_name.is_empty() {
        "dispatcher".to_string()
    } else {
        type_name.trim().to_string()
    }
}

fn validation_matches(rule: &ValidationRule, action: &ActionConfig) -> bool {
    let simple_class = action.class_name.rsplit('.').next().unwrap_or(&action.class_name);
    let target_match = rule.target == simple_class || rule.target == action.name;
    let method_match = rule.method.is_empty() || rule.method == action.method;
    target_match && method_match
}

pub struct ResolveInput<'a> {
    pub project_id: &'a str,
    pub project_name: &'a str,
    pub module_id: &'a str,
    pub packages: &'a [PackageConfig],
    pub constants: &'a Params,
    pub web_filters: &'a [WebFilterConfig],
    pub validation_rules: &'a [ValidationRule],
}

pub fn resolve_struts_project(input: &ResolveInput) -> StrutsResolution {
    let ResolveInput {
        project_id,
        project_name,
        module_id,
        packages,
        constants,
        web_filters,
        validation_rules,
    } = input;
    let mut collector = Collector::new(project_id, project_name, module_id);
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut package_by_name: BTreeMap<String, PackageConfig> = BTreeMap::new();
    for package in packages.iter() {
        if package.name.is_empty() {
            diagnostics.push(Diagnostic::new(
                "struts.package.name_missing",
                "Ignoring unnamed Struts package",
                "warning",
                &package.source.file_path,
            ));
            continue;
        }
        if package_by_name.contains_key(&package.name) {
            diagnostics.push(Diagnostic::new(
                "struts.package.duplicate",
                &format!(
                    "Duplicate package {}; the later declaration wins",
                    crate::pyjson::py_repr_str(&package.name)
                ),
                "warning",
                &package.source.file_path,
            ));
        }
        package_by_name.insert(package.name.clone(), package.clone());
    }

    let mut filter_facts: Vec<StrutsFact> = Vec::new();
    let mut filter_patterns: Vec<String> = Vec::new();
    for web_filter in web_filters.iter() {
        let class_lower = web_filter.class_name.to_lowercase();
        if !class_lower.contains("struts") || !(class_lower.contains("filter") || class_lower.contains("dispatcher")) {
            continue;
        }
        let mut properties = Map::new();
        properties.insert("plugin_type".into(), json!("web_filter"));
        properties.insert("class_name".into(), json!(web_filter.class_name));
        properties.insert("url_patterns".into(), json!(web_filter.url_patterns));
        properties.insert(
            "init_params".into(),
            Value::Object(
                web_filter
                    .init_params
                    .iter()
                    .map(|(key, value)| (key.clone(), json!(value)))
                    .collect(),
            ),
        );
        let filter_fact = collector.fact(
            "Plugin",
            &web_filter.name,
            &web_filter.source,
            vec!["web-filter".to_string(), web_filter.name.clone()],
            properties,
            "web_xml",
            "resolved",
            1.0,
        );
        filter_facts.push(filter_fact);
        filter_patterns.extend(web_filter.url_patterns.iter().cloned());
    }

    let extension_value = constants
        .get("struts.action.extension")
        .cloned()
        .unwrap_or_else(|| "action".to_string());
    let extension = extension_value
        .split(',')
        .map(|item| item.trim().to_string())
        .find(|item| !item.is_empty())
        .unwrap_or_default();

    let mut package_facts: BTreeMap<String, StrutsFact> = BTreeMap::new();
    for package in package_by_name.values() {
        let mut properties = Map::new();
        properties.insert("namespace".into(), json!(package.namespace));
        properties.insert("extends".into(), json!(package.extends));
        properties.insert("default_interceptor_stack".into(), json!(package.default_interceptor_ref));
        properties.insert("default_result_type".into(), json!(package.default_result_type));
        let fact = collector.fact(
            "Package",
            &package.name,
            &package.source,
            vec![package.name.clone()],
            properties,
            "struts_xml",
            "resolved",
            1.0,
        );
        package_facts.insert(package.name.clone(), fact);
    }
    for package in package_by_name.values() {
        for parent_name in &package.extends {
            if let Some(parent_fact) = package_facts.get(parent_name) {
                let own = package_facts[&package.name].clone();
                collector.rel(
                    &own,
                    parent_fact,
                    "EXTENDS",
                    &package.source,
                    Map::new(),
                    "Struts package inheritance",
                );
            }
        }
    }

    let mut matched_validation_ids: BTreeSet<usize> = BTreeSet::new();
    for package in package_by_name.values() {
        let lineage_rows = lineage(package, &package_by_name, &mut diagnostics, &[]);
        let interceptor_defs = named(&lineage_rows, "interceptors");
        let stack_defs = named(&lineage_rows, "interceptor_stacks");
        let result_type_defs = named(&lineage_rows, "result_types");
        let global_results = named(&lineage_rows, "global_results");
        let default_stack = default_of(&lineage_rows, "default_interceptor_ref");
        let mut default_result_type = default_of(&lineage_rows, "default_result_type");
        if default_result_type.is_empty() {
            default_result_type = result_type_defs
                .values()
                .find(|item| item.get("default").and_then(Value::as_bool).unwrap_or(false))
                .and_then(|item| item.get("name").and_then(Value::as_str))
                .unwrap_or("dispatcher")
                .to_string();
        }

        for action in &package.actions {
            if action.name.is_empty() {
                diagnostics.push(Diagnostic::new(
                    "struts.action.name_missing",
                    "Ignoring unnamed action",
                    "warning",
                    &action.source.file_path,
                ));
                continue;
            }
            let route_path = route(&package.namespace, &action.name, &extension);
            let mut action_props = Map::new();
            action_props.insert("class_name".into(), json!(action.class_name));
            action_props.insert("method".into(), json!(action.method));
            action_props.insert("namespace".into(), json!(package.namespace));
            action_props.insert("route".into(), json!(route_path));
            action_props.insert("wildcard".into(), json!(action.name.contains('*')));
            action_props.insert("params".into(), params_json(&action.params));
            let action_fact = collector.fact(
                "Action",
                &action.name,
                &action.source,
                vec![
                    package.name.clone(),
                    action.name.clone(),
                    action.class_name.clone(),
                    action.method.clone(),
                ],
                action_props,
                "struts_xml",
                "resolved",
                1.0,
            );
            let package_fact = package_facts[&package.name].clone();
            collector.rel(&package_fact, &action_fact, "CONTAINS", &action.source, Map::new(), "");
            let mut endpoint_props = Map::new();
            endpoint_props.insert("path".into(), json!(route_path));
            endpoint_props.insert("http_method".into(), json!("ALL"));
            endpoint_props.insert("action_extension".into(), json!(extension));
            let unique_patterns: Vec<String> = {
                let mut seen = BTreeSet::new();
                filter_patterns
                    .iter()
                    .filter(|item| seen.insert((*item).clone()))
                    .cloned()
                    .collect()
            };
            endpoint_props.insert("filter_patterns".into(), json!(unique_patterns));
            endpoint_props.insert("wildcard".into(), json!(route_path.contains('*')));
            let endpoint_fact = collector.fact(
                "HttpEndpoint",
                &route_path,
                &action.source,
                vec![route_path.clone()],
                endpoint_props,
                "struts_xml",
                "resolved",
                1.0,
            );
            collector.rel(
                &endpoint_fact,
                &action_fact,
                "MAPPED_TO",
                &action.source,
                Map::new(),
                "Struts action mapping",
            );
            for filter_fact in &filter_facts {
                collector.rel(
                    &endpoint_fact,
                    filter_fact,
                    "PASSES_THROUGH",
                    &action.source,
                    Map::new(),
                    "web.xml Struts filter mapping",
                );
            }

            let declared_refs: Vec<InterceptorRef> = if !action.interceptor_refs.is_empty() {
                action.interceptor_refs.clone()
            } else if !default_stack.is_empty() {
                vec![InterceptorRef {
                    name: default_stack.clone(),
                    params: Params::new(),
                }]
            } else {
                Vec::new()
            };
            let expanded = expand_refs(
                &declared_refs,
                &interceptor_defs,
                &stack_defs,
                &mut diagnostics,
                &action.source,
                &[],
                &Params::new(),
            );
            if !declared_refs.is_empty() {
                let effective_name = format!("{}/{}:effective", package.name, action.name);
                let mut stack_props = Map::new();
                stack_props.insert(
                    "declared_refs".into(),
                    Value::Array(
                        declared_refs
                            .iter()
                            .map(|reference| json!(reference.name))
                            .collect(),
                    ),
                );
                let stack_fact = collector.fact(
                    "InterceptorStack",
                    &effective_name,
                    &action.source,
                    vec![
                        package.name.clone(),
                        action.name.clone(),
                        "effective-stack".to_string(),
                    ],
                    stack_props,
                    "struts_resolution",
                    "resolved",
                    1.0,
                );
                collector.rel(
                    &action_fact,
                    &stack_fact,
                    "USES_INTERCEPTOR_STACK",
                    &action.source,
                    Map::new(),
                    "",
                );
                for (order, item) in expanded.iter().enumerate() {
                    let mut interceptor_props = Map::new();
                    interceptor_props.insert("class_name".into(), json!(item.interceptor.class_name));
                    interceptor_props.insert("params".into(), params_json(&item.interceptor.params));
                    let interceptor_fact = collector.fact(
                        "Interceptor",
                        &item.interceptor.name,
                        &package.source,
                        vec![package.name.clone(), item.interceptor.name.clone()],
                        interceptor_props,
                        "struts_xml",
                        "resolved",
                        1.0,
                    );
                    let mut contains_props = Map::new();
                    contains_props.insert("order".into(), json!(order as i64));
                    contains_props.insert("reference".into(), json!(item.ref_name));
                    contains_props.insert("params".into(), params_json(&item.effective_params));
                    contains_props.insert(
                        "include_methods".into(),
                        json!(item.effective_params.get("includeMethods").cloned().unwrap_or_default()),
                    );
                    contains_props.insert(
                        "exclude_methods".into(),
                        json!(item.effective_params.get("excludeMethods").cloned().unwrap_or_default()),
                    );
                    collector.rel(
                        &stack_fact,
                        &interceptor_fact,
                        "CONTAINS",
                        &action.source,
                        contains_props,
                        "Resolved interceptor execution order",
                    );
                }
            }

            let mut effective_results: BTreeMap<String, ResultConfig> = BTreeMap::new();
            // global_results là JSON — convert ngược ResultConfig.
            for (name, item) in &global_results {
                effective_results.insert(
                    name.clone(),
                    ResultConfig {
                        name: name.clone(),
                        type_name: item.get("type_name").and_then(Value::as_str).unwrap_or("").to_string(),
                        location: item.get("location").and_then(Value::as_str).unwrap_or("").to_string(),
                        params: json_params(item.get("params")),
                    },
                );
            }
            for result in &action.results {
                effective_results.insert(result.name.clone(), result.clone());
            }
            for (result_name, result) in &effective_results {
                let type_name = result_type(result, &default_result_type);
                let mut result_props = Map::new();
                result_props.insert("result_type".into(), json!(type_name));
                result_props.insert("location".into(), json!(result.location));
                result_props.insert("params".into(), params_json(&result.params));
                let global_names: BTreeSet<&str> = global_results.keys().map(String::as_str).collect();
                let action_result_names: BTreeSet<&str> =
                    action.results.iter().map(|item| item.name.as_str()).collect();
                let is_global = global_names.contains(result_name.as_str()) && !action_result_names.contains(result_name.as_str());
                result_props.insert("global".into(), json!(is_global));
                let result_fact = collector.fact(
                    "Result",
                    result_name,
                    &action.source,
                    vec![
                        package.name.clone(),
                        action.name.clone(),
                        result_name.clone(),
                    ],
                    result_props,
                    "struts_xml",
                    "resolved",
                    1.0,
                );
                let mut returns_props = Map::new();
                returns_props.insert("result_name".into(), json!(result_name));
                collector.rel(
                    &action_fact,
                    &result_fact,
                    "RETURNS_RESULT",
                    &action.source,
                    returns_props,
                    "",
                );
                if let Some(result_type_def) = result_type_defs.get(&type_name) {
                    let mut type_props = Map::new();
                    type_props.insert(
                        "class_name".into(),
                        json!(result_type_def.get("class_name").and_then(Value::as_str).unwrap_or("")),
                    );
                    type_props.insert(
                        "params".into(),
                        params_json_value(result_type_def.get("params")),
                    );
                    let type_fact = collector.fact(
                        "ResultType",
                        &type_name,
                        &package.source,
                        vec![package.name.clone(), type_name.clone()],
                        type_props,
                        "struts_xml",
                        "resolved",
                        1.0,
                    );
                    collector.rel(&result_fact, &type_fact, "INSTANCE_OF", &action.source, Map::new(), "");
                }
                let normalized_type = type_name.to_lowercase();
                let is_view = VIEW_RESULT_TYPES.contains(&normalized_type.as_str());
                let is_redirect = REDIRECT_RESULT_TYPES.contains(&normalized_type.as_str()) || normalized_type == "chain";
                if !result.location.is_empty() && is_view {
                    let mut view_props = Map::new();
                    view_props.insert("template_type".into(), json!(normalized_type));
                    let view_fact = collector.fact(
                        "View",
                        &result.location,
                        &action.source,
                        vec![result.location.clone()],
                        view_props,
                        "struts_xml",
                        "resolved",
                        1.0,
                    );
                    collector.rel(&result_fact, &view_fact, "RESOLVES_TO", &action.source, Map::new(), "");
                } else if !result.location.is_empty() && is_redirect {
                    let target_route = route(&package.namespace, &result.location, &extension);
                    let mut target_props = Map::new();
                    target_props.insert("path".into(), json!(target_route));
                    target_props.insert("http_method".into(), json!("ALL"));
                    // STRING (không bool): batch UNWIND ladybug phải unify
                    // kiểu per-column — trộn BOOL/STRING trong cùng key làm
                    // vỡ binder ("Implicit cast is not supported").
                    target_props.insert("synthetic_target".into(), json!("true"));
                    let target_fact = collector.fact(
                        "HttpEndpoint",
                        &target_route,
                        &action.source,
                        vec![target_route.clone()],
                        target_props,
                        "struts_xml",
                        "unresolved",
                        0.7,
                    );
                    let relation_type = if normalized_type == "chain" {
                        "CHAINS_TO"
                    } else {
                        "REDIRECTS_TO"
                    };
                    collector.rel(&result_fact, &target_fact, relation_type, &action.source, Map::new(), "");
                }
            }

            let mut effective_exceptions: Vec<ExceptionMappingConfig> = Vec::new();
            for ancestor in &lineage_rows {
                effective_exceptions.extend(ancestor.exception_mappings.iter().cloned());
            }
            effective_exceptions.extend(action.exception_mappings.iter().cloned());
            for mapping in &effective_exceptions {
                let mut exception_props = Map::new();
                exception_props.insert("exception".into(), json!(mapping.exception));
                exception_props.insert("result".into(), json!(mapping.result));
                let exception_fact = collector.fact(
                    "ExceptionMapping",
                    &mapping.exception,
                    &action.source,
                    vec![
                        package.name.clone(),
                        action.name.clone(),
                        mapping.exception.clone(),
                        mapping.result.clone(),
                    ],
                    exception_props,
                    "struts_xml",
                    "resolved",
                    1.0,
                );
                collector.rel(
                    &exception_fact,
                    &action_fact,
                    "HANDLES_EXCEPTION",
                    &action.source,
                    Map::new(),
                    "",
                );
            }

            for (index, rule) in validation_rules.iter().enumerate() {
                if !validation_matches(rule, action) {
                    continue;
                }
                matched_validation_ids.insert(index);
                let rule_name = format!(
                    "{}:{}",
                    if rule.field_name.is_empty() {
                        "<action>"
                    } else {
                        &rule.field_name
                    },
                    rule.validator_type
                );
                let mut validation_props = Map::new();
                validation_props.insert("target".into(), json!(rule.target));
                validation_props.insert("method".into(), json!(rule.method));
                validation_props.insert("field_name".into(), json!(rule.field_name));
                validation_props.insert("validator_type".into(), json!(rule.validator_type));
                validation_props.insert("message".into(), json!(rule.message));
                validation_props.insert("message_key".into(), json!(rule.message_key));
                validation_props.insert("params".into(), params_json(&rule.params));
                let validation_fact = collector.fact(
                    "ValidationRule",
                    &rule_name,
                    &rule.source,
                    vec![
                        package.name.clone(),
                        action.name.clone(),
                        rule.method.clone(),
                        rule.field_name.clone(),
                        rule.validator_type.clone(),
                        rule.message_key.clone(),
                    ],
                    validation_props,
                    "validation_xml",
                    "resolved",
                    1.0,
                );
                collector.rel(
                    &action_fact,
                    &validation_fact,
                    "VALIDATES_WITH",
                    &rule.source,
                    Map::new(),
                    "",
                );
            }
        }
    }

    for (index, rule) in validation_rules.iter().enumerate() {
        if !matched_validation_ids.contains(&index) {
            diagnostics.push(Diagnostic::new(
                "struts.validation.action_unresolved",
                &format!(
                    "Validation target {} did not match a configured action",
                    crate::pyjson::py_repr_str(&rule.target)
                ),
                "info",
                &rule.source.file_path,
            ));
        }
    }

    let mut facts: Vec<StrutsFact> = collector.facts.into_values().collect();
    facts.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    let mut relationships: Vec<StrutsRelationship> = collector.relationships.into_values().collect();
    relationships.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    let mut sorted_diagnostics = diagnostics;
    sorted_diagnostics.sort_by(|a, b| {
        (&a.file_path, &a.code, &a.message).cmp(&(&b.file_path, &b.code, &b.message))
    });
    StrutsResolution {
        facts,
        relationships,
        diagnostics: sorted_diagnostics,
    }
}
