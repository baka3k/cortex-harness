//! Port `tools/servlet_jsp/resolver.py` — resolve_servlet_jsp_module.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use crate::servlet_jsp::java_semantics::JavaSemanticAnalysisResult;
use crate::servlet_jsp::jsp_parser::JspParseResult;
use crate::servlet_jsp::models::{
    fact_to_value, redact_value, stable_semantic_id, Diagnostic, ResourceBudgets, ServletJspDependencyIndex,
    ServletJspFact, ServletJspModule, ServletJspRelationship, SourceSpan,
};
use crate::servlet_jsp::properties_parser::PropertiesParseResult;
use crate::servlet_jsp::web_xml_parser::{WebXmlDescriptorData, WebXmlParseResult};

const COMPONENT_KINDS: [&str; 3] = ["Servlet", "Filter", "Listener"];
const CALLBACK_KINDS: [&str; 5] = [
    "ServletHandler",
    "ServletLifecycle",
    "FilterCallback",
    "FilterLifecycle",
    "ListenerCallback",
];

pub struct ServletJspResolution {
    pub facts: Vec<ServletJspFact>,
    pub relationships: Vec<ServletJspRelationship>,
    pub dependency_index: ServletJspDependencyIndex,
    pub diagnostics: Vec<Diagnostic>,
    pub truncation_count: i64,
    pub ambiguity_count: i64,
    pub missing_anchor_count: i64,
}

struct Collector {
    project_id: String,
    project_name: String,
    module_id: String,
    max_facts: i64,
    max_relationships: i64,
    fact_budget_hit: bool,
    relationship_budget_hit: bool,
    facts: BTreeMap<String, ServletJspFact>,
    relationships: BTreeMap<String, ServletJspRelationship>,
}

impl Collector {
    fn new(project_id: &str, project_name: &str, module_id: &str, budgets: &ResourceBudgets) -> Self {
        Self {
            project_id: project_id.to_string(),
            project_name: project_name.to_string(),
            module_id: module_id.to_string(),
            max_facts: budgets.max_facts_per_project.max(0),
            max_relationships: budgets.max_relationships_per_project.max(0),
            fact_budget_hit: false,
            relationship_budget_hit: false,
            facts: BTreeMap::new(),
            relationships: BTreeMap::new(),
        }
    }

    fn fact(&mut self, fact: ServletJspFact) -> ServletJspFact {
        let existing = self.facts.get(&fact.stable_id);
        if existing.is_none() && self.facts.len() as i64 >= self.max_facts {
            self.fact_budget_hit = true;
            return fact;
        }
        let replace = match existing {
            Some(existing) => fact_rank(&fact) > fact_rank(existing),
            None => true,
        };
        if replace {
            self.facts.insert(fact.stable_id.clone(), fact.clone());
        } else {
            return self.facts[&fact.stable_id].clone();
        }
        self.facts[&fact.stable_id].clone()
    }

    #[allow(clippy::too_many_arguments)]
    fn make_fact(
        &mut self,
        kind: &str,
        identity: Vec<String>,
        name: &str,
        source: &SourceSpan,
        extraction_method: &str,
        resolution_status: &str,
        raw_value: &str,
        resolved_value: &str,
        source_symbol_id: &str,
        confidence: f64,
        properties: Map<String, Value>,
    ) -> ServletJspFact {
        self.fact(ServletJspFact {
            kind: kind.to_string(),
            stable_id: stable_semantic_id(kind, &self.project_id, &self.module_id, &identity),
            name: name.to_string(),
            source: source.clone(),
            project_id: self.project_id.clone(),
            project_name: self.project_name.clone(),
            module_id: self.module_id.clone(),
            language: "servlet_jsp".into(),
            confidence,
            extraction_method: extraction_method.to_string(),
            resolution_status: resolution_status.to_string(),
            raw_value: raw_value.to_string(),
            resolved_value: resolved_value.to_string(),
            source_symbol_id: source_symbol_id.to_string(),
            properties,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn rel(
        &mut self,
        from_id: &str,
        from_label: &str,
        to_id: &str,
        to_label: &str,
        rel_type: &str,
        span: &SourceSpan,
        occurrence: Vec<String>,
        reason: &str,
        resolution_status: &str,
        confidence: f64,
        properties: Map<String, Value>,
        from_generated: bool,
        to_generated: bool,
    ) {
        let mut identity = vec![
            rel_type.to_string(),
            from_label.to_string(),
            from_id.to_string(),
            to_label.to_string(),
            to_id.to_string(),
        ];
        identity.extend(occurrence);
        let stable_id = stable_semantic_id("relationship", &self.project_id, &self.module_id, &identity);
        if !self.relationships.contains_key(&stable_id)
            && self.relationships.len() as i64 >= self.max_relationships
        {
            self.relationship_budget_hit = true;
            return;
        }
        self.relationships.insert(
            stable_id.clone(),
            ServletJspRelationship {
                stable_id,
                from_id: from_id.to_string(),
                to_id: to_id.to_string(),
                from_label: from_label.to_string(),
                to_label: to_label.to_string(),
                rel_type: rel_type.to_string(),
                project_id: self.project_id.clone(),
                module_id: self.module_id.clone(),
                source: span.clone(),
                confidence,
                resolution_status: resolution_status.to_string(),
                reason: reason.to_string(),
                properties,
                from_generated,
                to_generated,
            },
        );
    }
}

fn fact_rank(fact: &ServletJspFact) -> (i64, i64, f64) {
    (
        i64::from(!fact.source_symbol_id.is_empty()),
        i64::from(fact.resolution_status == "resolved"),
        fact.confidence,
    )
}

struct BoundedDiagnostics {
    rows: Vec<Diagnostic>,
    maximum: i64,
    truncated: bool,
}

impl BoundedDiagnostics {
    fn new(maximum: i64) -> Self {
        Self {
            rows: Vec::new(),
            maximum: maximum.max(0),
            truncated: false,
        }
    }

    fn append(&mut self, item: Diagnostic) {
        if (self.rows.len() as i64) < self.maximum {
            self.rows.push(item);
        } else {
            self.truncated = true;
        }
    }

    fn ensure_budget_marker(&mut self) {
        if !self.truncated || self.maximum == 0 {
            return;
        }
        let marker = Diagnostic::new(
            "servlet_jsp.budget.diagnostics",
            &format!("Project diagnostic budget {} reached", self.maximum),
            "warning",
            "",
            1,
            1,
        );
        if self.rows.len() as i64 >= self.maximum {
            let last = self.rows.len() - 1;
            self.rows[last] = marker;
        } else {
            self.rows.push(marker);
        }
    }
}

pub struct ResolveModuleInput<'a> {
    pub project_id: &'a str,
    pub project_name: &'a str,
    pub module: &'a ServletJspModule,
    pub java_results: &'a [JavaSemanticAnalysisResult],
    pub web_results: &'a [WebXmlParseResult],
    pub jsp_results: &'a [JspParseResult],
    pub properties_results: &'a [PropertiesParseResult],
    pub budgets: &'a ResourceBudgets,
}

pub fn resolve_servlet_jsp_module(input: &ResolveModuleInput) -> ServletJspResolution {
    let ResolveModuleInput {
        project_id,
        project_name,
        module,
        java_results,
        web_results,
        jsp_results,
        properties_results,
        budgets,
    } = input;
    let mut out = Collector::new(project_id, project_name, &module.module_id, budgets);
    let mut diagnostics = BoundedDiagnostics::new(budgets.max_diagnostics_per_project);
    let mut dependency_files: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut dependency_components: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut dependency_mappings: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut dependency_views: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut dependency_states: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();

    let java_facts: Vec<ServletJspFact> = java_results.iter().flat_map(|result| result.facts.clone()).collect();
    let descriptors: Vec<&WebXmlDescriptorData> = web_results
        .iter()
        .filter_map(|result| result.descriptor.as_ref())
        .collect();
    for result in java_results.iter() {
        for item in &result.diagnostics {
            diagnostics.append(item.clone());
        }
    }
    for result in web_results.iter() {
        for item in &result.diagnostics {
            diagnostics.append(item.clone());
        }
    }
    for result in jsp_results.iter() {
        for item in &result.diagnostics {
            diagnostics.append(item.clone());
        }
    }
    for result in properties_results.iter() {
        for item in &result.diagnostics {
            diagnostics.append(item.clone());
        }
    }
    let metadata_complete = descriptors.iter().any(|descriptor| descriptor.metadata_complete == Some(true));

    let (components, component_names) =
        resolve_components(&mut out, &java_facts, &descriptors, metadata_complete, &mut diagnostics);
    let handlers: Vec<ServletJspFact> = java_facts
        .iter()
        .filter(|fact| fact.kind == "ServletHandler")
        .cloned()
        .collect();
    let callbacks: Vec<ServletJspFact> = java_facts
        .iter()
        .filter(|fact| CALLBACK_KINDS.contains(&fact.kind.as_str()))
        .cloned()
        .collect();
    let callback_components: BTreeMap<String, Option<ServletJspFact>> = callbacks
        .iter()
        .map(|fact| {
            let component_id = fact.properties.get("component_id").and_then(Value::as_str).unwrap_or("");
            (fact.stable_id.clone(), components.get(component_id).cloned())
        })
        .collect();

    let mut mapping_rows: Vec<(String, String, SourceSpan, String, i64, String)> = servlet_mapping_rows(&descriptors);
    if !metadata_complete {
        for component in components.values() {
            if component.kind != "Servlet" {
                continue;
            }
            for (index, pattern) in strings(component.properties.get("url_patterns")).into_iter().enumerate() {
                mapping_rows.push((
                    component.name.clone(),
                    pattern,
                    component.source.clone(),
                    "annotation".to_string(),
                    index as i64,
                    component.stable_id.clone(),
                ));
            }
        }
    }

    let mut endpoints: Vec<ServletJspFact> = Vec::new();
    for (servlet_name, pattern, source, provenance, order, mapping_owner) in &mapping_rows {
        let mut servlet = component_names
            .get(&("Servlet".to_string(), servlet_name.clone()))
            .cloned();
        if servlet.is_none() && !mapping_owner.is_empty() {
            servlet = components.get(mapping_owner).cloned();
        }
        let mapping = out.make_fact(
            "ServletMapping",
            vec![
                if mapping_owner.is_empty() { servlet_name.clone() } else { mapping_owner.clone() },
                pattern.clone(),
                provenance.clone(),
                source.file_path.clone(),
                source.start_line.to_string(),
                order.to_string(),
            ],
            &format!("{servlet_name}:{pattern}"),
            source,
            "servlet_jsp_resolver",
            "resolved",
            "",
            "",
            "",
            1.0,
            {
                let mut map = Map::new();
                map.insert("servlet_name".into(), json!(servlet_name));
                map.insert("url_patterns".into(), json!([pattern]));
                map.insert("raw_url_pattern".into(), json!(pattern));
                map.insert("mapping_kind".into(), json!(mapping_kind(pattern)));
                map.insert("descriptor_order".into(), json!(order));
                map.insert("provenance".into(), json!(provenance));
                map
            },
        );
        let Some(servlet) = servlet else {
            diagnostics.append(Diagnostic::new(
                "servlet_jsp.resolve.servlet_mapping_unresolved",
                &format!("No servlet declaration matches {servlet_name:?}"),
                "warning",
                &source.file_path,
                source.start_line,
                source.end_line,
            ));
            continue;
        };
        out.rel(
            &mapping.stable_id,
            "ServletMapping",
            &servlet.stable_id,
            &servlet.kind,
            "MAPS_TO",
            source,
            vec![source.file_path.clone(), source.start_line.to_string(), order.to_string()],
            "servlet mapping declaration",
            "resolved",
            1.0,
            {
                let mut map = Map::new();
                map.insert("mapping_kind".into(), json!(mapping_kind(pattern)));
                map.insert("descriptor_order".into(), json!(order));
                map.insert("provenance".into(), json!(provenance));
                map
            },
            true,
            true,
        );
        dependency_mappings
            .entry(mapping.stable_id.clone())
            .or_default()
            .insert(servlet.stable_id.clone());
        let servlet_handlers: Vec<ServletJspFact> = handlers
            .iter()
            .filter(|fact| fact.properties.get("component_id").and_then(Value::as_str).unwrap_or("") == servlet.stable_id)
            .cloned()
            .collect();
        if servlet_handlers.is_empty() {
            let endpoint = make_endpoint(&mut out, &servlet, &mapping, pattern, "ALL", &[], source);
            endpoints.push(endpoint.clone());
            out.rel(
                &servlet.stable_id,
                &servlet.kind,
                &endpoint.stable_id,
                &endpoint.kind,
                "HANDLES",
                source,
                vec![mapping.stable_id.clone(), "ALL".to_string()],
                "servlet URL mapping",
                "resolved",
                1.0,
                Map::new(),
                true,
                true,
            );
        }
        for handler in &servlet_handlers {
            let method = handler
                .properties
                .get("http_method")
                .and_then(Value::as_str)
                .unwrap_or("ALL")
                .to_string();
            let endpoint = make_endpoint(&mut out, &servlet, &mapping, pattern, &method, std::slice::from_ref(handler), source);
            endpoints.push(endpoint.clone());
            out.rel(
                &servlet.stable_id,
                &servlet.kind,
                &endpoint.stable_id,
                &endpoint.kind,
                "HANDLES",
                source,
                vec![mapping.stable_id.clone(), method.clone(), handler.stable_id.clone()],
                "servlet URL mapping and callback",
                "resolved",
                1.0,
                Map::new(),
                true,
                true,
            );
            if !handler.source_symbol_id.is_empty() {
                out.rel(
                    &endpoint.stable_id,
                    &endpoint.kind,
                    &handler.source_symbol_id,
                    "Function",
                    "SEMANTIC_OF",
                    &handler.source,
                    vec![handler.stable_id.clone()],
                    "endpoint handler Java anchor",
                    "resolved",
                    1.0,
                    Map::new(),
                    true,
                    false,
                );
            }
            dependency_components
                .entry(servlet.stable_id.clone())
                .or_default()
                .insert(endpoint.stable_id.clone());
        }
    }

    // Dedupe endpoints (stable_id) rồi sort.
    let endpoint_map: BTreeMap<String, ServletJspFact> =
        endpoints.drain(..).map(|endpoint| (endpoint.stable_id.clone(), endpoint)).collect();
    let mut endpoints: Vec<ServletJspFact> = endpoint_map.into_values().collect();
    endpoints.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));

    resolve_filters(
        &mut out,
        &components,
        &component_names,
        &descriptors,
        &endpoints,
        metadata_complete,
        budgets,
        &mut diagnostics,
        &mut dependency_mappings,
    );
    let view_by_path = resolve_views(
        &mut out,
        module,
        jsp_results,
        &endpoints,
        &mut dependency_files,
        &mut dependency_views,
        &mut diagnostics,
        budgets,
    );
    resolve_jsp_servlets(&mut out, &endpoints, &component_names, &view_by_path, &mut dependency_views);
    resolve_java_operations(
        &mut out,
        &java_facts,
        &endpoints,
        &view_by_path,
        &mut dependency_files,
        &mut dependency_states,
    );
    resolve_lifecycle(&mut out, &callbacks, &callback_components, &mut dependency_components);
    resolve_descriptor_configuration(
        &mut out,
        &descriptors,
        &component_names,
        &endpoints,
        &view_by_path,
        &mut dependency_files,
    );
    resolve_properties(&mut out, properties_results, &endpoints, &view_by_path, &mut dependency_files);

    if out.fact_budget_hit {
        diagnostics.append(Diagnostic::new(
            "servlet_jsp.budget.facts",
            &format!("Fact budget {} reached", budgets.max_facts_per_project),
            "warning",
            "",
            1,
            1,
        ));
    }
    if out.relationship_budget_hit {
        diagnostics.append(Diagnostic::new(
            "servlet_jsp.budget.relationships",
            &format!("Relationship budget {} reached", budgets.max_relationships_per_project),
            "warning",
            "",
            1,
            1,
        ));
    }
    diagnostics.ensure_budget_marker();

    let relationship_budget_hit = diagnostics
        .rows
        .iter()
        .any(|item| item.code == "servlet_jsp.budget.filter_chains");
    let mut truncation_count = java_results.iter().map(|result| result.truncation_count).sum::<i64>()
        + web_results.iter().filter(|result| result.truncated).count() as i64
        + jsp_results.iter().filter(|result| result.truncated).count() as i64
        + properties_results.iter().filter(|result| result.truncated).count() as i64;
    if relationship_budget_hit {
        truncation_count += 1;
    }
    truncation_count += diagnostics
        .rows
        .iter()
        .filter(|item| item.code == "servlet_jsp.budget.include_edges")
        .count() as i64;
    truncation_count +=
        i64::from(out.fact_budget_hit) + i64::from(out.relationship_budget_hit) + i64::from(diagnostics.truncated);

    let mut facts: Vec<ServletJspFact> = out.facts.into_values().collect();
    facts.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    let mut relationships: Vec<ServletJspRelationship> = out.relationships.into_values().collect();
    relationships.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    diagnostics.rows.sort_by(|a, b| {
        (&a.file_path, a.start_line, &a.code, &a.message).cmp(&(&b.file_path, b.start_line, &b.code, &b.message))
    });

    ServletJspResolution {
        facts,
        relationships,
        dependency_index: ServletJspDependencyIndex {
            files: freeze_index(dependency_files),
            components: freeze_index(dependency_components),
            mappings: freeze_index(dependency_mappings),
            views: freeze_index(dependency_views),
            state_slots: freeze_index(dependency_states),
        },
        diagnostics: diagnostics.rows,
        truncation_count,
        ambiguity_count: java_results.iter().map(|result| result.ambiguity_count).sum(),
        missing_anchor_count: java_results.iter().map(|result| result.missing_anchor_count).sum(),
    }
}

fn class_key(kind: &str) -> &'static str {
    match kind {
        "Servlet" => "servlet_class",
        "Filter" => "filter_class",
        "Listener" => "listener_class",
        _ => "",
    }
}

fn name_key(kind: &str) -> &'static str {
    match kind {
        "Servlet" => "servlet_name",
        "Filter" => "filter_name",
        "Listener" => "listener_class",
        _ => "",
    }
}

fn strings(value: Option<&Value>) -> Vec<String> {
    match value {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::String(text)) => {
            if text.is_empty() {
                Vec::new()
            } else {
                vec![text.clone()]
            }
        }
        Some(Value::Array(items)) => items
            .iter()
            .map(crate::pyjson::py_str)
            .filter(|item| !item.is_empty())
            .collect(),
        Some(other) => vec![crate::pyjson::py_str(other)].into_iter().filter(|item| !item.is_empty()).collect(),
    }
}

fn normalize(path: &str) -> String {
    let value = path.replace('\\', "/");
    let mut value = value.as_str();
    while let Some(rest) = value.strip_prefix("./") {
        value = rest;
    }
    value.trim_start_matches('/').to_string()
}

fn jsp_kind(path: &str) -> String {
    let lower = path.to_lowercase();
    if lower.ends_with(".jspx") {
        "jspx".to_string()
    } else if lower.ends_with(".jspf") {
        "jsp_fragment".to_string()
    } else {
        "jsp".to_string()
    }
}

fn mapping_kind(pattern: &str) -> String {
    if pattern.is_empty() || pattern.contains("${") || pattern.contains("#{") {
        return "dynamic".to_string();
    }
    if pattern == "/" {
        return "default".to_string();
    }
    if pattern.starts_with("*.") {
        return "extension".to_string();
    }
    if pattern.ends_with("/*") {
        return "path-prefix".to_string();
    }
    "exact".to_string()
}

#[allow(clippy::type_complexity)]
fn resolve_components(
    out: &mut Collector,
    java_facts: &[ServletJspFact],
    descriptors: &[&WebXmlDescriptorData],
    metadata_complete: bool,
    diagnostics: &mut BoundedDiagnostics,
) -> (BTreeMap<String, ServletJspFact>, BTreeMap<(String, String), ServletJspFact>) {
    let mut descriptor_records: Vec<(String, &crate::servlet_jsp::web_xml_parser::WebXmlRecord)> = Vec::new();
    for descriptor in descriptors {
        for record in &descriptor.servlets {
            descriptor_records.push(("Servlet".to_string(), record));
        }
        for record in &descriptor.filters {
            descriptor_records.push(("Filter".to_string(), record));
        }
        for record in &descriptor.listeners {
            descriptor_records.push(("Listener".to_string(), record));
        }
    }
    let mut records_by_class: BTreeMap<(String, String), Vec<&crate::servlet_jsp::web_xml_parser::WebXmlRecord>> =
        BTreeMap::new();
    for (kind, record) in &descriptor_records {
        let fqcn = record.get_str(class_key(kind));
        if !fqcn.is_empty() {
            records_by_class.entry((kind.clone(), fqcn)).or_default().push(record);
        }
    }

    let mut components: BTreeMap<String, ServletJspFact> = BTreeMap::new();
    let mut names: BTreeMap<(String, String), ServletJspFact> = BTreeMap::new();
    for fact in java_facts {
        if !COMPONENT_KINDS.contains(&fact.kind.as_str()) {
            continue;
        }
        let fqcn = fact.properties.get("fqcn").and_then(Value::as_str).unwrap_or("").to_string();
        let rows = records_by_class.get(&(fact.kind.clone(), fqcn.clone())).cloned().unwrap_or_default();
        let annotation_registered = fact.properties.contains_key("annotation") && !metadata_complete;
        if rows.is_empty() && !annotation_registered {
            continue;
        }
        let mut merged = normalized_component_properties(&fact.kind, &Value::Object(fact.properties.clone()));
        merged.insert(
            "evidence".into(),
            Value::Array(
                strings(fact.properties.get("evidence"))
                    .into_iter()
                    .map(|t| json!(t))
                    .collect(),
            ),
        );
        let mut declaration_sources: Vec<String> = if annotation_registered {
            vec!["annotation".to_string()]
        } else {
            Vec::new()
        };
        for record in &rows {
            let descriptor_props =
                normalized_component_properties(&fact.kind, &Value::Object(record.values.clone()));
            for key in ["url_patterns", "servlet_names", "dispatcher_types"] {
                let mut merged_list = strings(merged.get(key));
                for item in strings(descriptor_props.get(key)) {
                    if !merged_list.contains(&item) {
                        merged_list.push(item);
                    }
                }
                merged.insert(key.into(), Value::Array(merged_list.into_iter().map(|t| json!(t)).collect()));
            }
            for (key, value) in &descriptor_props {
                if !["url_patterns", "servlet_names", "dispatcher_types"].contains(&key.as_str())
                    && !matches!(value, Value::Null)
                    && !matches!(value, Value::String(text) if text.is_empty())
                    && !matches!(value, Value::Array(items) if items.is_empty())
                {
                    merged.insert(key.clone(), value.clone());
                }
            }
            declaration_sources.push("web.xml".to_string());
        }
        let name = {
            let component_name = merged.get("component_name").and_then(Value::as_str).unwrap_or("");
            if component_name.is_empty() {
                fact.name.clone()
            } else {
                component_name.to_string()
            }
        };
        merged.insert("component_class".into(), json!(fqcn));
        merged.insert(class_key(&fact.kind).into(), json!(fqcn));
        merged.insert(name_key(&fact.kind).into(), json!(name));
        let mut unique_sources: Vec<String> = declaration_sources.to_vec();
        unique_sources.sort();
        unique_sources.dedup();
        merged.insert("declaration_sources".into(), Value::Array(unique_sources.into_iter().map(|t| json!(t)).collect()));
        let mut replaced = fact.clone();
        replaced.name = name;
        replaced.properties = merged;
        replaced.extraction_method = "servlet_jsp_resolver".to_string();
        let component = out.fact(replaced);
        components.insert(component.stable_id.clone(), component.clone());
        names.insert((component.kind.clone(), component.name.clone()), component.clone());
        if !component.source_symbol_id.is_empty() {
            out.rel(
                &component.stable_id,
                &component.kind,
                &component.source_symbol_id,
                "Class",
                "SEMANTIC_OF",
                &component.source,
                Vec::new(),
                "component Java anchor",
                "resolved",
                1.0,
                Map::new(),
                true,
                false,
            );
        }
    }

    for (kind, record) in &descriptor_records {
        let fqcn = record.get_str(class_key(kind));
        let existing = components
            .values()
            .find(|component| {
                component.kind == *kind
                    && component.properties.get("component_class").and_then(Value::as_str).unwrap_or("") == fqcn
                    && !fqcn.is_empty()
            })
            .cloned();
        if let Some(existing) = existing {
            let record_name = record.get_str(name_key(kind));
            let name = if record_name.is_empty() {
                existing.name.clone()
            } else {
                record_name
            };
            names.insert((kind.clone(), name), existing);
            continue;
        }
        let record_name = record.get_str(name_key(kind));
        let name = if !record_name.is_empty() {
            record_name
        } else if !fqcn.is_empty() {
            fqcn.rsplit('.').next().unwrap_or(&fqcn).to_string()
        } else {
            record.name.clone()
        };
        let jsp_file = normalize(&record.get_str("jsp_file"));
        let identity: Vec<String> = if !fqcn.is_empty() {
            vec![fqcn.clone()]
        } else if !jsp_file.is_empty() {
            vec![name.clone(), jsp_file]
        } else {
            vec![name.clone()]
        };
        let mut props = normalized_component_properties(kind, &Value::Object(record.values.clone()));
        props.insert("component_class".into(), json!(fqcn));
        props.insert(class_key(kind).into(), json!(fqcn));
        props.insert(name_key(kind).into(), json!(name));
        props.insert("component_name".into(), json!(name));
        props.insert("declaration_sources".into(), json!(["web.xml"]));
        let resolution_status = if !fqcn.is_empty() || record.get("jsp_file").map(|v| !v.as_str().unwrap_or("").is_empty()).unwrap_or(false) {
            "resolved"
        } else {
            "unresolved"
        };
        let component = out.make_fact(
            kind,
            identity,
            &name,
            &record.source,
            "tree_sitter_xml",
            resolution_status,
            "",
            "",
            "",
            1.0,
            props,
        );
        components.insert(component.stable_id.clone(), component.clone());
        names.insert((kind.clone(), name.clone()), component);
        if fqcn.is_empty()
            && record
                .get("jsp_file")
                .map(|v| v.as_str().unwrap_or("").is_empty())
                .unwrap_or(true)
        {
            diagnostics.append(Diagnostic::new(
                "servlet_jsp.resolve.component_class_missing",
                &format!("{kind} {name:?} has no implementation class"),
                "warning",
                &record.source.file_path,
                record.source.start_line,
                record.source.end_line,
            ));
        }
    }
    (components, names)
}

fn normalized_component_properties(kind: &str, values: &Value) -> Map<String, Value> {
    let Some(map) = values.as_object() else {
        return Map::new();
    };
    let _ = kind;
    let mut result = Map::new();
    result.insert(
        "component_name".into(),
        json!(map.get(name_key_kind(kind)).and_then(Value::as_str).unwrap_or("")),
    );
    result.insert(
        "component_class".into(),
        json!(map.get(class_key(kind)).and_then(Value::as_str).unwrap_or("")),
    );
    result.insert(
        "url_patterns".into(),
        Value::Array(strings(map.get("url_patterns")).into_iter().map(|t| json!(t)).collect()),
    );
    result.insert(
        "servlet_names".into(),
        Value::Array(strings(map.get("servlet_names")).into_iter().map(|t| json!(t)).collect()),
    );
    let dispatchers = strings(map.get("dispatchers"));
    let dispatcher_types_list = if dispatchers.is_empty() {
        strings(map.get("dispatcher_types"))
    } else {
        dispatchers
    };
    result.insert(
        "dispatcher_types".into(),
        Value::Array(dispatcher_types_list.into_iter().map(|t| json!(t)).collect()),
    );
    result.insert("async_supported".into(), map.get("async_supported").cloned().unwrap_or(Value::Null));
    result.insert(
        "init_params".into(),
        redacted_init_params(map.get("init_params").cloned().unwrap_or(Value::Array(Vec::new()))),
    );
    result.insert("jsp_file".into(), json!(map.get("jsp_file").and_then(Value::as_str).unwrap_or("")));
    result.insert(
        "load_on_startup".into(),
        json!(map.get("load_on_startup").and_then(Value::as_str).unwrap_or("")),
    );
    result
}

fn name_key_kind(kind: &str) -> &str {
    // normalized_component_properties dùng name_key của kind; component_name
    // fallback là "component_name" khi kind lạ — python lookup trực tiếp.
    name_key(kind)
}

fn redacted_init_params(rows: Value) -> Value {
    let mut sanitized: Vec<Value> = Vec::new();
    if let Value::Array(items) = rows {
        for row in items {
            let Some(map) = row.as_object() else {
                sanitized.push(row);
                continue;
            };
            let mut values = map.clone();
            let nested_is_map = values
                .get("values")
                .map(|value| value.is_object())
                .unwrap_or(false);
            let nested = if nested_is_map {
                values.get("values").and_then(Value::as_object).cloned().unwrap_or_default()
            } else {
                values.clone()
            };
            let name = nested
                .get("name")
                .or_else(|| nested.get("param_name"))
                .or_else(|| nested.get("raw_name"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if redact_value(&name, &Value::String(name.clone()), 4096).as_str() == Some("[REDACTED]") {
                for key in ["name", "param_name", "raw_name", "value", "param_value", "raw_value"] {
                    if nested.contains_key(key) {
                        values.insert(key.to_string(), json!("[REDACTED]"));
                    }
                }
            }
            sanitized.push(Value::Object(values));
        }
    }
    Value::Array(sanitized)
}

#[allow(clippy::type_complexity)]
fn servlet_mapping_rows(descriptors: &[&WebXmlDescriptorData]) -> Vec<(String, String, SourceSpan, String, i64, String)> {
    let mut rows: Vec<(String, String, SourceSpan, String, i64, String)> = Vec::new();
    for descriptor in descriptors {
        for record in &descriptor.servlet_mappings {
            for (pattern_index, pattern) in strings(record.get("url_patterns")).into_iter().enumerate() {
                rows.push((
                    {
                        let name = record.get_str("servlet_name");
                        if name.is_empty() {
                            record.name.clone()
                        } else {
                            name
                        }
                    },
                    pattern,
                    record.source.clone(),
                    "web.xml".to_string(),
                    record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order),
                    format!("{}:{}", record.stable_id, pattern_index),
                ));
            }
        }
    }
    rows
}

#[allow(clippy::too_many_arguments)]
fn make_endpoint(
    out: &mut Collector,
    servlet: &ServletJspFact,
    _mapping: &ServletJspFact,
    pattern: &str,
    method: &str,
    handlers: &[ServletJspFact],
    source: &SourceSpan,
) -> ServletJspFact {
    let symbols: Vec<String> = handlers
        .iter()
        .filter(|item| !item.source_symbol_id.is_empty())
        .map(|item| item.source_symbol_id.clone())
        .collect();
    let names: Vec<String> = handlers
        .iter()
        .map(|item| {
            item.properties
                .get("method_name")
                .and_then(Value::as_str)
                .unwrap_or(&item.name)
                .to_string()
        })
        .collect();
    let servlet_class = servlet
        .properties
        .get("servlet_class")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            servlet
                .properties
                .get("component_class")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        });
    let mut properties = Map::new();
    properties.insert("path".into(), json!(pattern));
    properties.insert("raw_url_pattern".into(), json!(pattern));
    properties.insert("mapping_kind".into(), json!(mapping_kind(pattern)));
    properties.insert("http_method".into(), json!(method));
    properties.insert("handler_names".into(), Value::Array(names.into_iter().map(|t| json!(t)).collect()));
    properties.insert(
        "handler_symbol_ids".into(),
        Value::Array(symbols.iter().map(|t| json!(t)).collect()),
    );
    properties.insert("servlet_class".into(), json!(servlet_class));
    properties.insert("servlet_name".into(), json!(servlet.name));
    properties.insert("controller_class".into(), json!(servlet_class));
    properties.insert(
        "declaration_sources".into(),
        Value::Array(
            strings(servlet.properties.get("declaration_sources"))
                .into_iter()
                .map(|t| json!(t))
                .collect(),
        ),
    );
    out.make_fact(
        "ApiEndpoint",
        vec![servlet.stable_id.clone(), pattern.to_string(), method.to_string()],
        &format!("{method} {pattern}"),
        source,
        "servlet_jsp_resolver",
        "resolved",
        "",
        "",
        if symbols.len() == 1 { &symbols[0] } else { "" },
        1.0,
        properties,
    )
}

#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn resolve_filters(
    out: &mut Collector,
    components: &BTreeMap<String, ServletJspFact>,
    component_names: &BTreeMap<(String, String), ServletJspFact>,
    descriptors: &[&WebXmlDescriptorData],
    endpoints: &[ServletJspFact],
    metadata_complete: bool,
    budgets: &ResourceBudgets,
    diagnostics: &mut BoundedDiagnostics,
    dependency_mappings: &mut BTreeMap<String, BTreeSet<String>>,
) {
    #[allow(clippy::type_complexity)]
    let mut rules: Vec<(i64, i64, i64, ServletJspFact, String, String, Vec<String>, SourceSpan, String)> = Vec::new();
    for descriptor in descriptors {
        for record in &descriptor.filter_mappings {
            let filter_record_name = record.get_str("filter_name");
            let filter_fact = component_names
                .get(&("Filter".to_string(), if filter_record_name.is_empty() { record.name.clone() } else { filter_record_name }));
            let Some(filter_fact) = filter_fact else {
                diagnostics.append(Diagnostic::new(
                    "servlet_jsp.resolve.filter_mapping_unresolved",
                    &format!("No filter declaration matches {:?}", record.name),
                    "warning",
                    &record.source.file_path,
                    record.source.start_line,
                    record.source.end_line,
                ));
                continue;
            };
            let mut dispatchers = strings(record.get("dispatchers"));
            if dispatchers.is_empty() {
                dispatchers = vec!["REQUEST".to_string()];
            }
            let order = record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order);
            for (index, pattern) in strings(record.get("url_patterns")).into_iter().enumerate() {
                rules.push((
                    0,
                    order,
                    index as i64,
                    filter_fact.clone(),
                    "url".to_string(),
                    pattern,
                    dispatchers.clone(),
                    record.source.clone(),
                    record.stable_id.clone(),
                ));
            }
            for (index, servlet_name) in strings(record.get("servlet_names")).into_iter().enumerate() {
                rules.push((
                    1,
                    order,
                    index as i64,
                    filter_fact.clone(),
                    "servlet".to_string(),
                    servlet_name,
                    dispatchers.clone(),
                    record.source.clone(),
                    record.stable_id.clone(),
                ));
            }
        }
    }
    if !metadata_complete {
        for component in components.values() {
            if component.kind != "Filter" {
                continue;
            }
            let sources = strings(component.properties.get("declaration_sources"));
            if !sources.contains(&"annotation".to_string()) {
                continue;
            }
            let mut dispatchers = strings(component.properties.get("dispatcher_types"));
            if dispatchers.is_empty() {
                dispatchers = vec!["REQUEST".to_string()];
            }
            for (index, pattern) in strings(component.properties.get("url_patterns")).into_iter().enumerate() {
                rules.push((
                    2,
                    0,
                    index as i64,
                    component.clone(),
                    "url".to_string(),
                    pattern,
                    dispatchers.clone(),
                    component.source.clone(),
                    component.stable_id.clone(),
                ));
            }
            for (index, servlet_name) in strings(component.properties.get("servlet_names")).into_iter().enumerate() {
                rules.push((
                    2,
                    0,
                    index as i64,
                    component.clone(),
                    "servlet".to_string(),
                    servlet_name,
                    dispatchers.clone(),
                    component.source.clone(),
                    component.stable_id.clone(),
                ));
            }
        }
    }

    rules.sort_by_key(|row| (row.0, row.1, row.2, row.3.stable_id.clone()));

    #[allow(clippy::type_complexity)]
    let mut resolved_rules: Vec<(i64, i64, ServletJspFact, ServletJspFact, String, String, Vec<String>, SourceSpan)> =
        Vec::new();
    for (group, order, index, filter_fact, mapping_kind_value, value, dispatchers, source, owner) in &rules {
        let contract_mapping_kind = if mapping_kind_value == "url" {
            "url-pattern"
        } else {
            "servlet-name"
        };
        let mapping = out.make_fact(
            "FilterMapping",
            vec![
                owner.clone(),
                mapping_kind_value.clone(),
                value.clone(),
                source.file_path.clone(),
                source.start_line.to_string(),
                index.to_string(),
            ],
            &format!("{}:{}", filter_fact.name, value),
            source,
            "servlet_jsp_resolver",
            "resolved",
            "",
            "",
            "",
            1.0,
            {
                let mut map = Map::new();
                map.insert("filter_name".into(), json!(filter_fact.name));
                map.insert(
                    "url_patterns".into(),
                    json!(if mapping_kind_value == "url" { vec![value.clone()] } else { Vec::<String>::new() }),
                );
                map.insert(
                    "servlet_names".into(),
                    json!(if mapping_kind_value == "servlet" { vec![value.clone()] } else { Vec::<String>::new() }),
                );
                map.insert("mapping_kind".into(), json!(contract_mapping_kind));
                map.insert("descriptor_order".into(), json!(order));
                map.insert("dispatcher_types".into(), Value::Array(dispatchers.iter().map(|t| json!(t)).collect()));
                map.insert("order_status".into(), json!(if *group == 2 { "unknown" } else { "exact" }));
                map.insert("provenance".into(), json!(if *group == 2 { "annotation" } else { "web.xml" }));
                map
            },
        );
        out.rel(
            &mapping.stable_id,
            "FilterMapping",
            &filter_fact.stable_id,
            &filter_fact.kind,
            "MAPS_TO",
            source,
            vec![owner.clone(), mapping_kind_value.clone(), value.clone(), index.to_string()],
            "filter mapping declaration",
            "resolved",
            1.0,
            {
                let mut map = Map::new();
                map.insert("mapping_kind".into(), json!(contract_mapping_kind));
                map.insert("descriptor_order".into(), json!(order));
                map.insert("dispatcher_types".into(), Value::Array(dispatchers.iter().map(|t| json!(t)).collect()));
                map.insert("provenance".into(), json!(if *group == 2 { "annotation" } else { "web.xml" }));
                map
            },
            true,
            true,
        );
        dependency_mappings
            .entry(mapping.stable_id.clone())
            .or_default()
            .insert(filter_fact.stable_id.clone());
        resolved_rules.push((
            *group,
            *order,
            mapping,
            filter_fact.clone(),
            mapping_kind_value.clone(),
            value.clone(),
            dispatchers.clone(),
            source.clone(),
        ));
    }

    let mut count = 0i64;
    let budget_reported = false;
    let mut chain_index: BTreeMap<(String, String), i64> = BTreeMap::new();
    for (group, order, mapping, filter_fact, mapping_kind_value, value, dispatchers, source) in &resolved_rules {
        for endpoint in endpoints {
            let matched = if mapping_kind_value == "url" {
                pattern_matches(value, endpoint.properties.get("path").and_then(Value::as_str).unwrap_or(""))
            } else {
                *value == endpoint.properties.get("servlet_name").and_then(Value::as_str).unwrap_or("")
            };
            if !matched {
                continue;
            }
            for dispatcher in dispatchers {
                if count >= budgets.max_endpoint_filter_relationships {
                    if !budget_reported {
                        // budget_reported chỉ dùng 1 lần (return ngay sau).
                        diagnostics.append(Diagnostic::new(
                            "servlet_jsp.budget.filter_chains",
                            &format!(
                                "Endpoint/filter relationship budget {} reached",
                                budgets.max_endpoint_filter_relationships
                            ),
                            "warning",
                            &source.file_path,
                            source.start_line,
                            source.end_line,
                        ));
                    }
                    return;
                }
                count += 1;
                let chain_key = (endpoint.stable_id.clone(), dispatcher.clone());
                let order_index = *chain_index.get(&chain_key).unwrap_or(&0);
                chain_index.insert(chain_key, order_index + 1);
                let mut relationship_properties = Map::new();
                relationship_properties.insert(
                    "occurrence_key".into(),
                    json!(format!(
                        "{}:{}:{}:{}",
                        mapping.stable_id, value, endpoint.stable_id, dispatcher
                    )),
                );
                relationship_properties.insert(
                    "order_status".into(),
                    json!(if *group == 2 { "unknown" } else { "exact" }),
                );
                relationship_properties.insert(
                    "mapping_kind".into(),
                    json!(if mapping_kind_value == "url" { "url-pattern" } else { "servlet-name" }),
                );
                relationship_properties.insert("dispatcher_types".into(), json!([dispatcher]));
                relationship_properties.insert("dispatch_type".into(), json!(dispatcher));
                relationship_properties.insert(
                    "async_supported".into(),
                    filter_fact.properties.get("async_supported").cloned().unwrap_or(Value::Null),
                );
                relationship_properties.insert(
                    "declaration_source".into(),
                    json!(if *group == 2 { "annotation" } else { "web.xml" }),
                );
                relationship_properties.insert("descriptor_order".into(), json!(order));
                if *group != 2 {
                    relationship_properties.insert("order_index".into(), json!(order_index));
                }
                out.rel(
                    &endpoint.stable_id,
                    &endpoint.kind,
                    &filter_fact.stable_id,
                    &filter_fact.kind,
                    "PASSES_THROUGH",
                    source,
                    vec![
                        mapping.stable_id.clone(),
                        value.clone(),
                        endpoint.stable_id.clone(),
                        dispatcher.clone(),
                    ],
                    "effective filter chain mapping",
                    "resolved",
                    1.0,
                    relationship_properties,
                    true,
                    true,
                );
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_views(
    out: &mut Collector,
    module: &ServletJspModule,
    results: &[JspParseResult],
    endpoints: &[ServletJspFact],
    dependency_files: &mut BTreeMap<String, BTreeSet<String>>,
    dependency_views: &mut BTreeMap<String, BTreeSet<String>>,
    diagnostics: &mut BoundedDiagnostics,
    budgets: &ResourceBudgets,
) -> BTreeMap<String, ServletJspFact> {
    let mut views: BTreeMap<String, ServletJspFact> = BTreeMap::new();
    let mut include_edges = 0i64;
    let mut include_budget_reported = false;
    for result in results {
        let path = normalize(&result.file_path);
        let view = out.make_fact(
            "JSPView",
            vec!["artifact".to_string(), jsp_kind(&path), path.clone()],
            &crate::pyutil::basename(&path),
            &SourceSpan::new(&path),
            "tree_sitter_jsp",
            if result.complete { "resolved" } else { "partial" },
            "",
            "",
            "",
            1.0,
            {
                let mut map = Map::new();
                map.insert("artifact_kind".into(), json!(jsp_kind(&path)));
                map.insert("module_path".into(), json!(module.rel_path));
                map.insert(
                    "coverage_status".into(),
                    json!(if result.complete { "complete" } else { "partial" }),
                );
                map.insert("truncated".into(), json!(result.truncated));
                map
            },
        );
        // Match foundation artifact identity.
        let final_view = {
            let mut replaced = view.clone();
            replaced.stable_id =
                stable_semantic_id("artifact", &out.project_id, &out.module_id, &[jsp_kind(&path), path.clone()]);
            let replaced = out.fact(replaced);
            out.facts.remove(&view.stable_id);
            replaced
        };
        views.insert(path.clone(), final_view);
    }

    for result in results {
        let path = normalize(&result.file_path);
        let Some(view) = views.get(&path).cloned() else {
            continue;
        };
        for (index, expression) in result.expressions.iter().enumerate() {
            let safe_expression = {
                let redacted = redact_value(&expression.raw, &Value::String(expression.raw.clone()), 4096);
                crate::pyjson::py_str(&redacted)
            };
            let expr = out.make_fact(
                "JspExpression",
                vec![
                    path.clone(),
                    expression.span.start_line.to_string(),
                    expression.span.start_column.to_string(),
                    index.to_string(),
                    expression.raw.clone(),
                ],
                &format!("EL at {}:{}", expression.span.start_line, expression.span.start_column),
                &expression.span,
                "jsp_el",
                "resolved",
                &safe_expression,
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("expression".into(), json!(safe_expression));
                    let variables: Vec<Value> = expression
                        .el
                        .as_ref()
                        .map(|el| {
                            el.variables()
                                .iter()
                                .map(|item| json!(crate::pyjson::py_str(&redact_value(item, &Value::String(item.clone()), 4096))))
                                .collect()
                        })
                        .unwrap_or_default();
                    map.insert("variables".into(), Value::Array(variables));
                    let property_paths: Vec<Value> = expression
                        .el
                        .as_ref()
                        .map(|el| {
                            el.property_paths()
                                .iter()
                                .map(|item| json!(crate::pyjson::py_str(&redact_value(item, &Value::String(item.clone()), 4096))))
                                .collect()
                        })
                        .unwrap_or_default();
                    map.insert("property_paths".into(), Value::Array(property_paths));
                    let functions: Vec<Value> = expression
                        .functions()
                        .iter()
                        .map(|item| json!(crate::pyjson::py_str(&redact_value(&item.raw, &Value::String(item.raw.clone()), 4096))))
                        .collect();
                    map.insert("functions".into(), Value::Array(functions));
                    map
                },
            );
            out.rel(
                &view.stable_id,
                &view.kind,
                &expr.stable_id,
                &expr.kind,
                "DECLARES",
                &expression.span,
                vec![index.to_string()],
                "JSP expression occurrence",
                "resolved",
                1.0,
                Map::new(),
                true,
                true,
            );
            if let Some(el) = &expression.el {
                for (state_index, read) in el.state_reads.iter().enumerate() {
                    let slot = state_slot(
                        out,
                        &read.scope,
                        if read.name.is_empty() { "*" } else { &read.name },
                        &expression.span,
                        read.dynamic,
                    );
                    out.rel(
                        &expr.stable_id,
                        &expr.kind,
                        &slot.stable_id,
                        &slot.kind,
                        "READS",
                        &expression.span,
                        vec![index.to_string(), state_index.to_string(), read.raw.clone()],
                        "JSP EL implicit-object read",
                        if read.dynamic { "dynamic" } else { "resolved" },
                        1.0,
                        {
                            let mut map = Map::new();
                            map.insert("correlation_status".into(), json!("possible"));
                            map.insert(
                                "raw_value".into(),
                                json!(crate::pyjson::py_str(&redact_value(&read.raw, &Value::String(read.raw.clone()), 4096))),
                            );
                            map
                        },
                        true,
                        true,
                    );
                    dependency_views
                        .entry(view.stable_id.clone())
                        .or_default()
                        .insert(slot.stable_id.clone());
                }
            }
        }
        let regions: Vec<&crate::servlet_jsp::jsp_parser::JspRegion> =
            result.actions.iter().chain(result.tags.iter()).collect();
        for (index, region) in regions.into_iter().enumerate() {
            let (prefix, local_name_value) = {
                match region.name.split_once(':') {
                    Some((prefix, local)) => (prefix.to_string(), local.to_string()),
                    None => (String::new(), region.name.clone()),
                }
            };
            let tag = out.make_fact(
                "JspTag",
                vec![
                    path.clone(),
                    region.span.start_line.to_string(),
                    region.span.start_column.to_string(),
                    index.to_string(),
                    region.name.clone(),
                ],
                &region.name,
                &region.span,
                "jsp_state_machine",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("tag_name".into(), json!(region.name));
                    map.insert(
                        "prefix".into(),
                        json!(if local_name_value.is_empty() { "" } else { prefix.as_str() }),
                    );
                    map.insert("uri".into(), json!(region.taglib_uri));
                    map.insert(
                        "attributes".into(),
                        Value::Object(
                            region
                                .attributes
                                .iter()
                                .map(|(key, value)| (key.clone(), json!(value)))
                                .collect(),
                        ),
                    );
                    map.insert("target_kind".into(), json!(region.semantic_kind));
                    map
                },
            );
            out.rel(
                &view.stable_id,
                &view.kind,
                &tag.stable_id,
                &tag.kind,
                "DECLARES",
                &region.span,
                vec!["tag".to_string(), index.to_string(), region.name.clone()],
                "JSP tag occurrence",
                "resolved",
                1.0,
                Map::new(),
                true,
                true,
            );
        }
        for (index, operation) in result.scriptlet_operations.iter().enumerate() {
            if !operation.scope.is_empty() {
                let slot = state_slot(
                    out,
                    &operation.scope,
                    if operation.name.is_empty() { "*" } else { &operation.name },
                    &operation.span,
                    operation.resolution_status != "resolved",
                );
                let rel_type = if operation.kind.to_lowercase().contains("set") { "WRITES" } else { "READS" };
                out.rel(
                    &view.stable_id,
                    &view.kind,
                    &slot.stable_id,
                    &slot.kind,
                    rel_type,
                    &operation.span,
                    vec!["scriptlet".to_string(), index.to_string(), operation.kind.clone()],
                    "JSP scriptlet state access",
                    &operation.resolution_status,
                    1.0,
                    {
                        let mut map = Map::new();
                        map.insert("correlation_status".into(), json!("possible"));
                        map.insert("raw_value".into(), json!(operation.raw));
                        map
                    },
                    true,
                    true,
                );
            }
        }
        for (index, target) in result.targets.iter().enumerate() {
            if target.kind == "include" {
                if include_edges >= budgets.max_include_edges_per_module {
                    if !include_budget_reported {
                        include_budget_reported = true;
                        diagnostics.append(Diagnostic::new(
                            "servlet_jsp.budget.include_edges",
                            &format!("Include edge budget {} reached", budgets.max_include_edges_per_module),
                            "warning",
                            &target.span.file_path,
                            target.span.start_line,
                            target.span.end_line,
                        ));
                    }
                    continue;
                }
                include_edges += 1;
            }
            let target_method = if !target.method.is_empty() {
                target.method.clone()
            } else if target.kind == "link" || target.kind == "redirect" {
                "GET".to_string()
            } else {
                String::new()
            };
            let target_fact = target_fact(
                out,
                &normalize(&target.resolved_path),
                &target.raw_value,
                &target.classification,
                &target.resolution_status,
                &target.span,
                &views,
                endpoints,
                &target_method,
            );
            let rel_type = match target.kind.as_str() {
                "include" => "INCLUDES",
                "forward" => "FORWARDS_TO",
                "redirect" => "REDIRECTS_TO",
                "form" => "SUBMITS_TO",
                "link" | "resource" => "LINKS_TO",
                _ => "LINKS_TO",
            };
            out.rel(
                &view.stable_id,
                &view.kind,
                &target_fact.stable_id,
                &target_fact.kind,
                rel_type,
                &target.span,
                vec![index.to_string(), target.kind.clone(), target.raw_value.clone()],
                "JSP target",
                &target.resolution_status,
                1.0,
                {
                    let mut map = Map::new();
                    map.insert(
                        "raw_value".into(),
                        json!(crate::pyjson::py_str(&redact_value(&target.raw_value, &Value::String(target.raw_value.clone()), 4096))),
                    );
                    map.insert("resolved_value".into(), json!(target.resolved_path));
                    map
                },
                true,
                true,
            );
            if !target.resolved_path.is_empty() {
                dependency_files
                    .entry(path.clone())
                    .or_default()
                    .insert(target.resolved_path.clone());
                dependency_views
                    .entry(view.stable_id.clone())
                    .or_default()
                    .insert(target_fact.stable_id.clone());
            }
        }
        for dependency in &result.dependencies {
            if !dependency.target_path.is_empty() {
                dependency_files
                    .entry(path.clone())
                    .or_default()
                    .insert(dependency.target_path.clone());
            }
        }
    }
    views
}

fn resolve_jsp_servlets(
    out: &mut Collector,
    endpoints: &[ServletJspFact],
    component_names: &BTreeMap<(String, String), ServletJspFact>,
    views: &BTreeMap<String, ServletJspFact>,
    dependencies: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for endpoint in endpoints {
        let servlet = component_names
            .get(&("Servlet".to_string(), endpoint.properties.get("servlet_name").and_then(Value::as_str).unwrap_or("").to_string()))
            .cloned();
        let Some(servlet) = servlet else { continue };
        let jsp_file = servlet.properties.get("jsp_file").and_then(Value::as_str).unwrap_or("").to_string();
        if jsp_file.is_empty() {
            continue;
        }
        let view = view_for_path(views, &normalize(&jsp_file));
        let Some(view) = view else { continue };
        out.rel(
            &endpoint.stable_id,
            &endpoint.kind,
            &view.stable_id,
            &view.kind,
            "FORWARDS_TO",
            &servlet.source,
            vec![servlet.stable_id.clone(), jsp_file.clone()],
            "descriptor jsp-file servlet target",
            "resolved",
            1.0,
            Map::new(),
            true,
            true,
        );
        dependencies
            .entry(view.stable_id.clone())
            .or_default()
            .insert(endpoint.stable_id.clone());
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_java_operations(
    out: &mut Collector,
    facts: &[ServletJspFact],
    endpoints: &[ServletJspFact],
    views: &BTreeMap<String, ServletJspFact>,
    dependency_files: &mut BTreeMap<String, BTreeSet<String>>,
    dependency_states: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for fact in facts {
        if (fact.kind == "DispatchOperation" || fact.kind == "RedirectOperation") && !fact.source_symbol_id.is_empty() {
            let operation = fact.properties.get("operation").and_then(Value::as_str).unwrap_or("").to_string();
            let target_value = fact
                .properties
                .get("target")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| fact.resolved_value.clone());
            let target_method = if operation == "redirect" { "GET" } else { "" };
            let target = target_fact(
                out,
                &normalize(&target_value),
                &fact.raw_value,
                "java",
                &fact.resolution_status,
                &fact.source,
                views,
                endpoints,
                target_method,
            );
            let rel_type = match operation.as_str() {
                "forward" => "FORWARDS_TO",
                "include" => "INCLUDES",
                _ => "REDIRECTS_TO",
            };
            out.rel(
                &fact.source_symbol_id,
                "Function",
                &target.stable_id,
                &target.kind,
                rel_type,
                &fact.source,
                vec![fact.stable_id.clone()],
                "Servlet API navigation",
                &fact.resolution_status,
                1.0,
                {
                    let mut map = Map::new();
                    map.insert(
                        "raw_value".into(),
                        json!(crate::pyjson::py_str(&redact_value(&fact.raw_value, &Value::String(fact.raw_value.clone()), 4096))),
                    );
                    map.insert("resolved_value".into(), json!(target_value));
                    map
                },
                false,
                true,
            );
            if !target_value.is_empty() {
                dependency_files
                    .entry(fact.source.file_path.clone())
                    .or_default()
                    .insert(normalize(&target_value));
            }
        } else if (fact.kind == "StateAccess" || fact.kind == "CookieAccess") && !fact.source_symbol_id.is_empty() {
            let scope = fact.properties.get("scope").and_then(Value::as_str).unwrap_or("unknown").to_string();
            let key = fact
                .properties
                .get("key")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| {
                    if fact.resolved_value.is_empty() {
                        "*".to_string()
                    } else {
                        fact.resolved_value.clone()
                    }
                });
            let slot = state_slot(out, &scope, &key, &fact.source, fact.resolution_status != "resolved");
            let rel_type = if fact.properties.get("access").and_then(Value::as_str).unwrap_or("read") == "write" {
                "WRITES"
            } else {
                "READS"
            };
            out.rel(
                &fact.source_symbol_id,
                "Function",
                &slot.stable_id,
                &slot.kind,
                rel_type,
                &fact.source,
                vec![fact.stable_id.clone()],
                "Servlet state access",
                &fact.resolution_status,
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("correlation_status".into(), json!("possible"));
                    map.insert(
                        "raw_value".into(),
                        json!(crate::pyjson::py_str(&redact_value(&fact.raw_value, &Value::String(fact.raw_value.clone()), 4096))),
                    );
                    map
                },
                false,
                true,
            );
            dependency_states
                .entry(slot.stable_id.clone())
                .or_default()
                .insert(fact.source.file_path.clone());
        }
    }
}

fn resolve_lifecycle(
    out: &mut Collector,
    callbacks: &[ServletJspFact],
    component_by_callback: &BTreeMap<String, Option<ServletJspFact>>,
    dependencies: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for callback in callbacks {
        if !["ServletLifecycle", "FilterLifecycle", "ListenerCallback"].contains(&callback.kind.as_str()) {
            continue;
        }
        let Some(component) = component_by_callback.get(&callback.stable_id).cloned().flatten() else {
            continue;
        };
        let event_name = callback
            .properties
            .get("lifecycle_event")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| callback.name.clone());
        let event = out.make_fact(
            "LifecycleEvent",
            vec![event_name.clone()],
            &event_name,
            &callback.source,
            "servlet_jsp_resolver",
            "resolved",
            "",
            "",
            "",
            1.0,
            {
                let mut map = Map::new();
                map.insert("event_kind".into(), json!(event_name));
                map
            },
        );
        out.rel(
            &component.stable_id,
            &component.kind,
            &event.stable_id,
            &event.kind,
            "INITIALIZES",
            &callback.source,
            vec![callback.stable_id.clone()],
            "component lifecycle callback",
            "resolved",
            1.0,
            Map::new(),
            true,
            true,
        );
        if !callback.source_symbol_id.is_empty() {
            out.rel(
                &callback.source_symbol_id,
                "Function",
                &event.stable_id,
                &event.kind,
                "HANDLES_LIFECYCLE",
                &callback.source,
                vec![callback.stable_id.clone()],
                "lifecycle Java handler",
                "resolved",
                1.0,
                Map::new(),
                false,
                true,
            );
        }
        dependencies
            .entry(component.stable_id.clone())
            .or_default()
            .insert(event.stable_id.clone());
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_descriptor_configuration(
    out: &mut Collector,
    descriptors: &[&WebXmlDescriptorData],
    component_names: &BTreeMap<(String, String), ServletJspFact>,
    endpoints: &[ServletJspFact],
    views: &BTreeMap<String, ServletJspFact>,
    dependency_files: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for descriptor in descriptors {
        let descriptor_fact = {
            let generated = out.make_fact(
                "WebDescriptor",
                vec!["artifact".to_string(), "web_xml".to_string(), descriptor.file_path.clone()],
                &crate::pyutil::basename(&descriptor.file_path),
                &descriptor.source,
                "tree_sitter_xml",
                "resolved",
                &descriptor.doctype,
                &descriptor.file_path,
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("module_path".into(), json!(descriptor.module_path));
                    map.insert("namespace".into(), json!(descriptor.namespace));
                    map.insert("version".into(), json!(descriptor.version));
                    map.insert("metadata_complete".into(), match descriptor.metadata_complete {
                        Some(value) => json!(value),
                        None => Value::Null,
                    });
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            let mut replaced = generated.clone();
            replaced.stable_id =
                stable_semantic_id("artifact", &out.project_id, &out.module_id, &["web_xml".to_string(), descriptor.file_path.clone()]);
            let replaced = out.fact(replaced);
            out.facts.remove(&generated.stable_id);
            replaced
        };
        for (kind, records) in [
            ("Servlet", &descriptor.servlets),
            ("Filter", &descriptor.filters),
            ("Listener", &descriptor.listeners),
        ] {
            for record in records {
                let record_name = record.get_str(name_key(kind));
                let lookup_name = if record_name.is_empty() { record.name.clone() } else { record_name };
                if let Some(component) = component_names.get(&(kind.to_string(), lookup_name)) {
                    out.rel(
                        &descriptor_fact.stable_id,
                        "WebDescriptor",
                        &component.stable_id,
                        &component.kind,
                        "DECLARES",
                        &record.source,
                        vec![record.stable_id.clone()],
                        "web.xml component declaration",
                        "resolved",
                        1.0,
                        {
                            let mut map = Map::new();
                            map.insert("provenance".into(), json!("web.xml"));
                            map.insert(
                                "descriptor_order".into(),
                                json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                            );
                            map
                        },
                        true,
                        true,
                    );
                }
            }
        }
        for record in descriptor
            .context_params
            .iter()
            .chain(descriptor.session_configs.iter())
            .chain(descriptor.login_configs.iter())
        {
            let config = out.make_fact(
                "WebConfiguration",
                vec![record.stable_id.clone()],
                &record.name,
                &record.source,
                "tree_sitter_xml",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("config_kind".into(), json!(record.kind));
                    map.insert("config_key".into(), json!(record.name));
                    map.insert(
                        "config_value".into(),
                        json!(record
                            .get("param_value")
                            .or_else(|| record.get("session_timeout"))
                            .or_else(|| record.get("auth_method"))
                            .and_then(Value::as_str)
                            .unwrap_or("")),
                    );
                    map.insert("auth_method".into(), json!(record.get_str("auth_method")));
                    map.insert("realm_name".into(), json!(record.get_str("realm_name")));
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            out.rel(
                &descriptor_fact.stable_id,
                "WebDescriptor",
                &config.stable_id,
                &config.kind,
                "CONFIGURES",
                &record.source,
                vec![record.stable_id.clone()],
                "web.xml configuration",
                "resolved",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("provenance".into(), json!("web.xml"));
                    map.insert(
                        "descriptor_order".into(),
                        json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                    );
                    map
                },
                true,
                true,
            );
        }
        for record in &descriptor.welcome_files {
            let welcome = out.make_fact(
                "WelcomePage",
                vec![record.stable_id.clone()],
                &record.name,
                &record.source,
                "tree_sitter_xml",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    let path = {
                        let value = record.get("path").and_then(Value::as_str).unwrap_or("");
                        if value.is_empty() {
                            record.name.clone()
                        } else {
                            value.to_string()
                        }
                    };
                    map.insert("path".into(), json!(path));
                    map.insert(
                        "order_index".into(),
                        json!(record.get("welcome_order").and_then(Value::as_i64).unwrap_or(record.order)),
                    );
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            out.rel(
                &descriptor_fact.stable_id,
                "WebDescriptor",
                &welcome.stable_id,
                &welcome.kind,
                "DECLARES",
                &record.source,
                vec![record.stable_id.clone()],
                "welcome-file declaration",
                "resolved",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("provenance".into(), json!("web.xml"));
                    map.insert(
                        "descriptor_order".into(),
                        json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                    );
                    map
                },
                true,
                true,
            );
            let path_value = {
                let value = record.get("path").and_then(Value::as_str).unwrap_or("");
                if value.is_empty() {
                    record.name.clone()
                } else {
                    value.to_string()
                }
            };
            let target = target_fact(
                out,
                &normalize(&path_value),
                &path_value,
                "descriptor",
                "resolved",
                &record.source,
                views,
                endpoints,
                "GET",
            );
            out.rel(
                &welcome.stable_id,
                &welcome.kind,
                &target.stable_id,
                &target.kind,
                "RESOLVES_TO",
                &record.source,
                vec![record.stable_id.clone()],
                "welcome-file target",
                "resolved",
                1.0,
                Map::new(),
                true,
                true,
            );
        }
        for record in &descriptor.error_pages {
            let location = record.get_str("location");
            let error = out.make_fact(
                "ErrorPage",
                vec![record.stable_id.clone()],
                &record.name,
                &record.source,
                "tree_sitter_xml",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("error_code".into(), json!(record.get_str("error_code")));
                    map.insert("exception_type".into(), json!(record.get_str("exception_type")));
                    map.insert("location".into(), json!(location));
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            out.rel(
                &descriptor_fact.stable_id,
                "WebDescriptor",
                &error.stable_id,
                &error.kind,
                "DECLARES",
                &record.source,
                vec![record.stable_id.clone()],
                "error-page declaration",
                "resolved",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("provenance".into(), json!("web.xml"));
                    map.insert(
                        "descriptor_order".into(),
                        json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                    );
                    map
                },
                true,
                true,
            );
            if !location.is_empty() {
                let target = target_fact(
                    out,
                    &normalize(&location),
                    &location,
                    "descriptor",
                    "resolved",
                    &record.source,
                    views,
                    endpoints,
                    "",
                );
                out.rel(
                    &error.stable_id,
                    &error.kind,
                    &target.stable_id,
                    &target.kind,
                    "RESOLVES_TO",
                    &record.source,
                    vec![record.stable_id.clone()],
                    "error-page target",
                    "resolved",
                    1.0,
                    Map::new(),
                    true,
                    true,
                );
                dependency_files
                    .entry(descriptor.file_path.clone())
                    .or_default()
                    .insert(normalize(&location));
            }
        }
        let mut authorities: BTreeMap<String, ServletJspFact> = BTreeMap::new();
        for record in &descriptor.security_roles {
            let role = {
                let value = record.get_str("role_name");
                if value.is_empty() {
                    record.name.clone()
                } else {
                    value
                }
            };
            let authority = out.make_fact(
                "Authority",
                vec![role.clone()],
                &role,
                &record.source,
                "tree_sitter_xml",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("role".into(), json!(role));
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            authorities.insert(role, authority);
        }
        for record in &descriptor.security_constraints {
            let mut collection_semantics: Vec<Value> = Vec::new();
            let collections_value = record.get("web_resource_collections").cloned().unwrap_or(Value::Array(Vec::new()));
            if let Value::Array(collections) = &collections_value {
                for (collection_index, collection) in collections.iter().enumerate() {
                    // Python: `values = collection.get("values") if isinstance(...,
                    // Mapping) else collection` — payload lồng "values".
                    let values = collection
                        .get("values")
                        .and_then(Value::as_object)
                        .cloned()
                        .unwrap_or_else(|| collection.as_object().cloned().unwrap_or_default());
                    // stable_id/name đọc từ payload (như python), còn
                    // methods/patterns từ nested values.
                    let payload_stable_id = collection.get("stable_id").and_then(Value::as_str).unwrap_or("");
                    let collection_id = if !payload_stable_id.is_empty() {
                        payload_stable_id.to_string()
                    } else {
                        format!("{}:{}", record.stable_id, collection_index)
                    };
                    let payload_name = collection.get("name").and_then(Value::as_str).unwrap_or("");
                    let collection_name = if !payload_name.is_empty() {
                        payload_name
                    } else {
                        values.get("web_resource_name").and_then(Value::as_str).unwrap_or("")
                    };
                    collection_semantics.push(crate::pyjson::py_object(vec![
                        ("stable_id".into(), json!(collection_id)),
                        (
                            "name".into(),
                            json!(if collection_name.is_empty() {
                                format!("collection[{collection_index}]")
                            } else {
                                collection_name.to_string()
                            }),
                        ),
                        ("index".into(), json!(collection_index as i64)),
                        (
                            "url_patterns".into(),
                            Value::Array(strings(values.get("url_patterns")).into_iter().map(|t| json!(t)).collect()),
                        ),
                        (
                            "methods".into(),
                            {
                                let mut methods: Vec<String> = strings(values.get("http_methods"));
                                methods.sort();
                                methods.dedup();
                                Value::Array(methods.into_iter().map(|t| json!(t)).collect())
                            },
                        ),
                        (
                            "method_omissions".into(),
                            {
                                let mut omissions: Vec<String> = strings(values.get("http_method_omissions"));
                                omissions.sort();
                                omissions.dedup();
                                Value::Array(omissions.into_iter().map(|t| json!(t)).collect())
                            },
                        ),
                    ]));
                }
            }
            let constraint = out.make_fact(
                "SecurityConstraint",
                vec![record.stable_id.clone()],
                &record.name,
                &record.source,
                "tree_sitter_xml",
                "resolved",
                "",
                "",
                "",
                1.0,
                {
                    let mut methods: BTreeSet<String> = BTreeSet::new();
                    let mut omissions: BTreeSet<String> = BTreeSet::new();
                    for collection in &collection_semantics {
                        for method in strings(collection.get("methods")) {
                            methods.insert(method);
                        }
                        for omission in strings(collection.get("method_omissions")) {
                            omissions.insert(omission);
                        }
                    }
                    let mut map = Map::new();
                    map.insert(
                        "methods".into(),
                        Value::Array(methods.into_iter().map(|t| json!(t)).collect()),
                    );
                    map.insert(
                        "method_omissions".into(),
                        Value::Array(omissions.into_iter().map(|t| json!(t)).collect()),
                    );
                    map.insert("resource_collections".into(), Value::Array(collection_semantics.clone()));
                    map.insert(
                        "transport_guarantee".into(),
                        Value::Array(strings(record.get("transport_guarantees")).into_iter().map(|t| json!(t)).collect()),
                    );
                    map.insert("provenance".into(), json!("web.xml"));
                    map
                },
            );
            out.rel(
                &descriptor_fact.stable_id,
                "WebDescriptor",
                &constraint.stable_id,
                &constraint.kind,
                "DECLARES",
                &record.source,
                vec![record.stable_id.clone()],
                "security constraint",
                "resolved",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("provenance".into(), json!("web.xml"));
                    map.insert(
                        "descriptor_order".into(),
                        json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                    );
                    map
                },
                true,
                true,
            );
            for collection in &collection_semantics {
                let patterns: Vec<String> = strings(collection.get("url_patterns"));
                let methods: BTreeSet<String> = strings(collection.get("methods")).into_iter().collect();
                let omissions: BTreeSet<String> = strings(collection.get("method_omissions")).into_iter().collect();
                for endpoint in endpoints {
                    let method = endpoint
                        .properties
                        .get("http_method")
                        .and_then(Value::as_str)
                        .unwrap_or("ALL")
                        .to_uppercase();
                    if !patterns.is_empty()
                        && !patterns
                            .iter()
                            .any(|pattern| pattern_matches(pattern, endpoint.properties.get("path").and_then(Value::as_str).unwrap_or("")))
                    {
                        continue;
                    }
                    if method != "ALL" && !methods.is_empty() && !methods.contains(&method) {
                        continue;
                    }
                    if method != "ALL" && !omissions.is_empty() && omissions.contains(&method) {
                        continue;
                    }
                    let mut occurrence = vec![
                        record.stable_id.clone(),
                        collection.get("stable_id").and_then(Value::as_str).unwrap_or("").to_string(),
                        collection.get("index").and_then(Value::as_i64).unwrap_or(0).to_string(),
                        endpoint.stable_id.clone(),
                    ];
                    occurrence.extend(strings(collection.get("methods")));
                    occurrence.push("omissions".to_string());
                    occurrence.extend(strings(collection.get("method_omissions")));
                    out.rel(
                        &constraint.stable_id,
                        &constraint.kind,
                        &endpoint.stable_id,
                        &endpoint.kind,
                        "PROTECTS",
                        &record.source,
                        occurrence,
                        "security URL and HTTP method constraint",
                        "resolved",
                        1.0,
                        {
                            let mut map = Map::new();
                            map.insert(
                                "occurrence_key".into(),
                                json!(format!(
                                    "{}:{}:{}",
                                    record.stable_id,
                                    collection.get("stable_id").and_then(Value::as_str).unwrap_or(""),
                                    endpoint.stable_id
                                )),
                            );
                            map.insert(
                                "resource_collection".into(),
                                collection.get("name").cloned().unwrap_or(Value::Null),
                            );
                            map.insert(
                                "resource_collection_index".into(),
                                collection.get("index").cloned().unwrap_or(Value::Null),
                            );
                            map.insert("methods".into(), collection.get("methods").cloned().unwrap_or(Value::Array(Vec::new())));
                            map.insert(
                                "method_omissions".into(),
                                collection.get("method_omissions").cloned().unwrap_or(Value::Array(Vec::new())),
                            );
                            map.insert("provenance".into(), json!("web.xml"));
                            map.insert(
                                "descriptor_order".into(),
                                json!(record.get("descriptor_order").and_then(Value::as_i64).unwrap_or(record.order)),
                            );
                            map
                        },
                        true,
                        true,
                    );
                }
            }
            for role in strings(record.get("role_names")) {
                let authority = authorities.get(&role).cloned().unwrap_or_else(|| {
                    out.make_fact(
                        "Authority",
                        vec![role.clone()],
                        &role,
                        &record.source,
                        "tree_sitter_xml",
                        "resolved",
                        "",
                        "",
                        "",
                        1.0,
                        {
                            let mut map = Map::new();
                            map.insert("role".into(), json!(role));
                            map.insert("provenance".into(), json!("web.xml"));
                            map
                        },
                    )
                });
                out.rel(
                    &constraint.stable_id,
                    &constraint.kind,
                    &authority.stable_id,
                    &authority.kind,
                    "REQUIRES_AUTHORITY",
                    &record.source,
                    vec![record.stable_id.clone(), role],
                    "auth-constraint role",
                    "resolved",
                    1.0,
                    Map::new(),
                    true,
                    true,
                );
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_properties(
    out: &mut Collector,
    results: &[PropertiesParseResult],
    endpoints: &[ServletJspFact],
    views: &BTreeMap<String, ServletJspFact>,
    dependency_files: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for result in results {
        for (index, target) in result.targets.iter().enumerate() {
            let source = out.make_fact(
                "WebConfiguration",
                vec![result.file_path.clone(), target.source_key.clone()],
                &target.source_key,
                &target.source,
                "properties",
                "resolved",
                &target.raw_value,
                &target.resolved_path,
                "",
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("config_kind".into(), json!("property"));
                    map.insert("config_key".into(), json!(target.source_key));
                    map.insert("config_value".into(), json!(target.raw_value));
                    map
                },
            );
            let destination = target_fact(
                out,
                &target.resolved_path,
                &target.raw_value,
                &target.classification,
                &target.resolution_status,
                &target.source,
                views,
                endpoints,
                "",
            );
            out.rel(
                &source.stable_id,
                &source.kind,
                &destination.stable_id,
                &destination.kind,
                "RESOLVES_TO",
                &target.source,
                vec![index.to_string(), target.source_key.clone()],
                "properties path target",
                &target.resolution_status,
                1.0,
                {
                    let mut map = Map::new();
                    map.insert("raw_value".into(), json!(target.raw_value));
                    map.insert("resolved_value".into(), json!(target.resolved_path));
                    map
                },
                true,
                true,
            );
            if !target.resolved_path.is_empty() {
                dependency_files
                    .entry(result.file_path.clone())
                    .or_default()
                    .insert(target.resolved_path.clone());
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn target_fact(
    out: &mut Collector,
    resolved: &str,
    raw: &str,
    classification: &str,
    status: &str,
    source: &SourceSpan,
    views: &BTreeMap<String, ServletJspFact>,
    endpoints: &[ServletJspFact],
    method: &str,
) -> ServletJspFact {
    let normalized = normalize(resolved);
    if let Some(view) = view_for_path(views, &normalized) {
        return view;
    }
    if let Some(endpoint) = endpoint_for_path(endpoints, &normalized, method) {
        return endpoint;
    }
    let safe_raw = crate::pyjson::py_str(&redact_value(raw, &Value::String(raw.to_string()), 4096));
    let dynamic = status == "dynamic" || normalized.is_empty();
    let mut identity = vec![if normalized.is_empty() { raw.to_string() } else { normalized.clone() }, classification.to_string()];
    if dynamic {
        identity.push(source.file_path.clone());
        identity.push(source.start_line.to_string());
        identity.push(source.start_column.to_string());
    }
    let name = if !normalized.is_empty() {
        normalized.clone()
    } else if !safe_raw.is_empty() {
        safe_raw.clone()
    } else {
        "dynamic target".to_string()
    };
    out.make_fact(
        "WebTarget",
        identity,
        &name,
        source,
        "servlet_jsp_resolver",
        if status.is_empty() { "unresolved" } else { status },
        &safe_raw,
        &normalized,
        "",
        1.0,
        {
            let mut map = Map::new();
            map.insert(
                "target".into(),
                json!(if normalized.is_empty() { safe_raw.clone() } else { normalized.clone() }),
            );
            map.insert("target_kind".into(), json!(classification));
            map.insert("dynamic".into(), json!(dynamic));
            map
        },
    )
}

fn endpoint_for_path(endpoints: &[ServletJspFact], path: &str, method: &str) -> Option<ServletJspFact> {
    let normalized = normalize(path.split('?').next().unwrap_or(path).split('#').next().unwrap_or(path));
    if normalized.is_empty() {
        return None;
    }
    let mut candidates: Vec<((i64, i64, i64), &ServletJspFact)> = Vec::new();
    for endpoint in endpoints {
        let endpoint_path = endpoint.properties.get("path").and_then(Value::as_str).unwrap_or("");
        let Some(pattern_rank) = servlet_pattern_match_rank(endpoint_path, &format!("/{normalized}")) else {
            continue;
        };
        let endpoint_method = endpoint
            .properties
            .get("http_method")
            .and_then(Value::as_str)
            .unwrap_or("ALL")
            .to_uppercase();
        if !method.is_empty() && endpoint_method != method.to_uppercase() && endpoint_method != "ALL" {
            continue;
        }
        let method_rank = if !method.is_empty() && endpoint_method == method.to_uppercase() {
            1
        } else {
            0
        };
        candidates.push(((pattern_rank.0, pattern_rank.1, method_rank), endpoint));
    }
    if candidates.is_empty() {
        return None;
    }
    let best_rank = candidates.iter().map(|(rank, _)| *rank).max().unwrap();
    // Python: max với tie-break thứ tự duyệt — tuple so sánh; chọn candidate
    // có rank == best (set) rồi trả item đầu (dict.values() thứ tự insert).
    let matches: Vec<&ServletJspFact> = candidates
        .iter()
        .filter(|(rank, _)| *rank == best_rank)
        .map(|(_, endpoint)| *endpoint)
        .collect();
    matches.first().map(|endpoint| (*endpoint).clone())
}

fn view_for_path(views: &BTreeMap<String, ServletJspFact>, path: &str) -> Option<ServletJspFact> {
    if path.is_empty() {
        return None;
    }
    if let Some(view) = views.get(path) {
        return Some(view.clone());
    }
    let suffix = format!("/{path}");
    let candidates: Vec<&ServletJspFact> = views
        .iter()
        .filter(|(candidate, _)| candidate.ends_with(&suffix))
        .map(|(_, view)| view)
        .collect();
    if candidates.len() == 1 {
        Some(candidates[0].clone())
    } else {
        None
    }
}

fn state_slot(out: &mut Collector, scope: &str, key: &str, source: &SourceSpan, dynamic: bool) -> ServletJspFact {
    let normalized_key = if key.is_empty() { "*" } else { key };
    let mut identity = vec![scope.to_string(), normalized_key.to_string()];
    if dynamic {
        identity.push(source.file_path.clone());
        identity.push(source.start_line.to_string());
        identity.push(source.start_column.to_string());
    }
    let safe_key = crate::pyjson::py_str(&redact_value(normalized_key, &Value::String(normalized_key.to_string()), 4096));
    let display = format!("{scope}:{safe_key}");
    out.make_fact(
        "StateSlot",
        identity,
        &display,
        source,
        "servlet_jsp_resolver",
        if dynamic { "dynamic" } else { "resolved" },
        if dynamic { &safe_key } else { "" },
        if dynamic { "" } else { &safe_key },
        "",
        1.0,
        {
            let mut map = Map::new();
            map.insert("scope".into(), json!(scope));
            map.insert("key".into(), json!(safe_key));
            map.insert("dynamic".into(), json!(dynamic));
            map.insert("correlation_status".into(), json!("possible"));
            map
        },
    )
}

fn pattern_matches(filter_pattern: &str, endpoint_pattern: &str) -> bool {
    let filter_kind = mapping_kind(filter_pattern);
    let endpoint_kind = mapping_kind(endpoint_pattern);
    if filter_kind == "dynamic" || endpoint_kind == "dynamic" {
        return false;
    }
    if filter_kind == "default" || endpoint_kind == "default" {
        return true;
    }
    if filter_kind == "exact" {
        return servlet_pattern_match_rank(endpoint_pattern, filter_pattern).is_some();
    }
    if endpoint_kind == "exact" {
        return servlet_pattern_match_rank(filter_pattern, endpoint_pattern).is_some();
    }
    if filter_kind == "path-prefix" && endpoint_kind == "path-prefix" {
        let first = &filter_pattern[..filter_pattern.len() - 2];
        let second = &endpoint_pattern[..endpoint_pattern.len() - 2];
        return first == second || first.starts_with(&format!("{second}/")) || second.starts_with(&format!("{first}/"));
    }
    if filter_kind == "extension" && endpoint_kind == "extension" {
        let first = &filter_pattern[1..];
        let second = &endpoint_pattern[1..];
        return first.ends_with(second) || second.ends_with(first);
    }
    (filter_kind == "path-prefix" && endpoint_kind == "extension")
        || (filter_kind == "extension" && endpoint_kind == "path-prefix")
}

fn servlet_pattern_match_rank(pattern: &str, request_path: &str) -> Option<(i64, i64)> {
    let kind = mapping_kind(pattern);
    match kind.as_str() {
        "dynamic" => None,
        "exact" => {
            if pattern == request_path {
                Some((4, pattern.len() as i64))
            } else {
                None
            }
        }
        "path-prefix" => {
            let prefix = &pattern[..pattern.len() - 2];
            if request_path == prefix || request_path.starts_with(&format!("{prefix}/")) {
                Some((3, prefix.len() as i64))
            } else {
                None
            }
        }
        "extension" => {
            let extension = &pattern[1..];
            if request_path.ends_with(extension) {
                Some((2, extension.len() as i64))
            } else {
                None
            }
        }
        _ => Some((1, 0)),
    }
}

fn freeze_index(values: BTreeMap<String, BTreeSet<String>>) -> BTreeMap<String, Vec<String>> {
    values
        .into_iter()
        .map(|(key, items)| {
            let mut sorted: Vec<String> = items.into_iter().collect();
            sorted.sort();
            (key, sorted)
        })
        .collect()
}

/// `fact_to_value` sink cho unused.
#[allow(dead_code)]
fn unused(value: &Value) {
    let _ = json!(value);
    let _ = fact_to_value;
}
