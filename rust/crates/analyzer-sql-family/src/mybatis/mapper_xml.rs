//! Port `tools/mybatis/mapper_xml_analyzer.py` — tree-sitter-xml parse của
//! mapper/config XML: documents, statements, fragments, result maps,
//! includes (expand + cycle/depth guards), dynamic nodes, config facts.
//!
//! Shared XML helpers (`_attrs`, `_child_elements`, ...) expose cho
//! spring_bridge (Python import từ module này).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use regex::Regex;
use tree_sitter::{Node, Parser};

use cortex_analyzer_framework::ts::decode_ignore;

use crate::mybatis::detector::classify_xml_text;
use crate::mybatis::dynamic_sql::{dynamic_node, DYNAMIC_TAGS};
use crate::mybatis::models::{
    ConfigFact, Diagnostic, DynamicNodeFact, XmlDocumentFact, IncludeFact, ResultMapFact,
    ResultMappingFact, SourceSpan, SqlFragmentFact, StatementFact,
};

pub const STATEMENT_TAGS: [&str; 4] = ["select", "insert", "update", "delete"];
pub const RESULT_MAPPING_TAGS: [&str; 9] = [
    "id",
    "result",
    "association",
    "collection",
    "constructor",
    "arg",
    "idArg",
    "discriminator",
    "case",
];

fn doctype_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?is)<!DOCTYPE\s+([^>]+)>").unwrap())
}

fn property_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\{([^}]+)\}").unwrap())
}

pub struct MapperXmlAnalysis {
    pub documents: Vec<XmlDocumentFact>,
    pub statements: Vec<StatementFact>,
    pub fragments: Vec<SqlFragmentFact>,
    pub result_maps: Vec<ResultMapFact>,
    pub result_mappings: Vec<ResultMappingFact>,
    pub includes: Vec<IncludeFact>,
    pub dynamic_nodes: Vec<DynamicNodeFact>,
    pub config_facts: Vec<ConfigFact>,
    pub diagnostics: Vec<Diagnostic>,
}

/// View của fragment source (file + node) cho include resolution.
#[derive(Clone)]
pub struct ElementView<'tree> {
    pub file_path: String,
    pub source: &'tree [u8],
    pub node: Node<'tree>,
    pub namespace: String,
    pub database_id: String,
}

/// `analyze_mapper_xml_files`.
pub fn analyze_mapper_xml_files(
    root: &Path,
    xml_files: &[String],
    project_id: &str,
) -> MapperXmlAnalysis {
    let mut documents: Vec<XmlDocumentFact> = Vec::new();
    let mut statements: Vec<StatementFact> = Vec::new();
    let mut fragments: Vec<SqlFragmentFact> = Vec::new();
    let mut result_maps: Vec<ResultMapFact> = Vec::new();
    let mut result_mappings: Vec<ResultMappingFact> = Vec::new();
    let mut includes: Vec<IncludeFact> = Vec::new();
    let mut dynamic_nodes: Vec<DynamicNodeFact> = Vec::new();
    let mut config_facts: Vec<ConfigFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    let unique: BTreeSet<&String> = xml_files.iter().collect();

    // Ownership ổn định trước khi mượn: sources + trees tồn tại tới cuối hàm.
    let mut sources: Vec<Vec<u8>> = Vec::new();
    let mut rel_paths: Vec<String> = Vec::new();
    for rel_path in &unique {
        let path = root.join(rel_path.as_str());
        if !path.is_file() || std::fs::read(&path).is_err() {
            diagnostics.push(Diagnostic::new(
                "mybatis.xml.missing_file",
                "XML file is missing",
            ));
            continue;
        }
        let source = std::fs::read(&path).unwrap_or_default();
        sources.push(source);
        rel_paths.push(rel_path.to_string());
    }
    let trees: Vec<Option<tree_sitter::Tree>> = sources
        .iter()
        .map(|source| {
            let mut parser = Parser::new();
            parser
                .set_language(&tree_sitter_xml::LANGUAGE_XML.into())
                .expect("load xml parser");
            parser.parse(source, None)
        })
        .collect();

    // DocView mượn CHỈ sources (ổn định); strings owned.
    struct DocView<'a> {
        rel_path: String,
        source: &'a [u8],
        root_element: Node<'a>,
        root_tag: String,
        namespace: String,
    }
    let mut views: Vec<DocView<'_>> = Vec::new();
    for (index, tree_opt) in trees.iter().enumerate() {
        let Some(tree) = tree_opt else {
            continue;
        };
        let root_node = tree.root_node();
        if root_node.has_error() {
            diagnostics.push(Diagnostic::new(
                "mybatis.xml.parse_error",
                "XML parser reported syntax errors",
            ));
        }
        let Some(root_element) = first_element(root_node) else {
            diagnostics.push(Diagnostic::new(
                "mybatis.xml.no_root",
                "XML document has no root element",
            ));
            continue;
        };
        let rel_path = &rel_paths[index];
        let source = &sources[index];
        let root_tag = element_name(root_element, source);
        let decoded = decode_ignore(source);
        let (document_kind, _) = classify_xml_text(rel_path, &decoded);
        let namespace = attrs(root_element, source)
            .get("namespace")
            .cloned()
            .unwrap_or_default();
        documents.push(XmlDocumentFact {
            file_path: rel_path.clone(),
            document_kind: if document_kind.is_empty() {
                root_tag.clone()
            } else {
                document_kind
            },
            root_tag: root_tag.clone(),
            source: span_of(root_element, rel_path),
            namespace: namespace.clone(),
            doctype: doctype_of(source),
            parser_status: if root_node.has_error() {
                "parse_error".to_string()
            } else {
                "parsed".to_string()
            },
        });
        views.push(DocView {
            rel_path: rel_path.clone(),
            source,
            root_element,
            root_tag,
            namespace,
        });
    }

    // Pass 1 — fragment index từ mọi mapper doc.
    let mut fragment_index: HashMap<String, Vec<ElementView<'_>>> = HashMap::new();
    for doc in &views {
        if doc.root_tag != "mapper" {
            continue;
        }
        for child in child_elements(doc.root_element, doc.source) {
            if element_name(child, doc.source) == "sql" {
                let attrs_map = attrs(child, doc.source);
                let fragment_id = attrs_map.get("id").cloned().unwrap_or_default();
                if !fragment_id.is_empty() {
                    let key = qualified_ref(&doc.namespace, &fragment_id);
                    let database_id = attrs_map
                        .get("databaseId")
                        .cloned()
                        .unwrap_or_default();
                    fragment_index.entry(key).or_default().push(ElementView {
                        file_path: doc.rel_path.clone(),
                        source: doc.source,
                        node: child,
                        namespace: doc.namespace.clone(),
                        database_id,
                    });
                }
            }
        }
    }

    // Pass 2 — analyze từng doc.
    for doc in &views {
        if doc.root_tag == "mapper" {
            let result = analyze_mapper(
                doc.root_element,
                doc.source,
                &doc.rel_path,
                &doc.namespace,
                project_id,
                &fragment_index,
            );
            statements.extend(result.statements);
            fragments.extend(result.fragments);
            result_maps.extend(result.result_maps);
            result_mappings.extend(result.result_mappings);
            includes.extend(result.includes);
            dynamic_nodes.extend(result.dynamic_nodes);
            diagnostics.extend(result.diagnostics);
        } else if doc.root_tag == "configuration" {
            config_facts.push(analyze_config(
                doc.root_element,
                doc.source,
                &doc.rel_path,
                project_id,
            ));
        }
    }

    MapperXmlAnalysis {
        documents,
        statements,
        fragments,
        result_maps,
        result_mappings,
        includes,
        dynamic_nodes,
        config_facts,
        diagnostics,
    }
}

struct MapperPartial<'tree> {
    statements: Vec<StatementFact>,
    fragments: Vec<SqlFragmentFact>,
    result_maps: Vec<ResultMapFact>,
    result_mappings: Vec<ResultMappingFact>,
    includes: Vec<IncludeFact>,
    dynamic_nodes: Vec<DynamicNodeFact>,
    diagnostics: Vec<Diagnostic>,
    #[allow(dead_code)]
    _marker: std::marker::PhantomData<&'tree ()>,
}

/// `_analyze_mapper`.
fn analyze_mapper<'a>(
    root_element: Node<'a>,
    source: &[u8],
    file_path: &str,
    namespace: &str,
    project_id: &str,
    fragment_index: &HashMap<String, Vec<ElementView<'_>>>,
) -> MapperPartial<'a> {
    let mut statements: Vec<StatementFact> = Vec::new();
    let mut fragments: Vec<SqlFragmentFact> = Vec::new();
    let mut result_maps: Vec<ResultMapFact> = Vec::new();
    let mut result_mappings: Vec<ResultMappingFact> = Vec::new();
    let mut includes: Vec<IncludeFact> = Vec::new();
    let mut dynamic_nodes: Vec<DynamicNodeFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut seen_statement_ids: BTreeSet<(String, String)> = BTreeSet::new();

    for child in child_elements(root_element, source) {
        let tag = element_name(child, source);
        let attrs_map = attrs(child, source);
        if STATEMENT_TAGS.contains(&tag.as_str()) {
            let statement_id = attrs_map.get("id").cloned().unwrap_or_default();
            if statement_id.is_empty() {
                diagnostics.push(Diagnostic::new(
                    "mybatis.xml.statement_missing_id",
                    "Statement is missing id",
                ));
                continue;
            }
            let duplicate_key = (
                statement_id.clone(),
                attrs_map.get("databaseId").cloned().unwrap_or_default(),
            );
            if seen_statement_ids.contains(&duplicate_key) {
                diagnostics.push(Diagnostic::new(
                    "mybatis.xml.duplicate_statement",
                    &format!("Duplicate statement id {statement_id:?}"),
                ));
            }
            seen_statement_ids.insert(duplicate_key);
            let stable_id = format!(
                "mybatis_stmt::{project_id}::{namespace}::{statement_id}::{}",
                attrs_map.get("databaseId").cloned().unwrap_or_else(|| "default".to_string())
            );
            let (expanded, mut stmt_includes, include_diags, mut nodes) = expand_body(
                child,
                source,
                file_path,
                namespace,
                &stable_id,
                fragment_index,
                &[],
                &attrs_map
                    .get("databaseId")
                    .cloned()
                    .unwrap_or_default(),
                &BTreeMap::new(),
                0,
                "",
            );
            stmt_includes = renumber_includes(&stable_id, stmt_includes);
            nodes = renumber_dynamic_nodes(&stable_id, nodes);
            statements.push(StatementFact {
                stable_id: stable_id.clone(),
                namespace: namespace.to_string(),
                statement_id,
                statement_kind: tag.clone(),
                source: span_of(child, file_path),
                database_id: attrs_map.get("databaseId").cloned().unwrap_or_default(),
                attributes: attrs_map.clone(),
                raw_body: raw_body(child, source),
                expanded_body: expanded,
                includes: stmt_includes.clone(),
                dynamic_nodes: nodes.clone(),
                parser_status: "parsed".to_string(),
            });
            includes.extend(stmt_includes);
            dynamic_nodes.extend(nodes);
            diagnostics.extend(include_diags);
        } else if tag == "sql" {
            let fragment_id = attrs_map.get("id").cloned().unwrap_or_default();
            if fragment_id.is_empty() {
                continue;
            }
            let stable_id = format!(
                "mybatis_fragment::{project_id}::{namespace}::{fragment_id}::{}",
                attrs_map.get("databaseId").cloned().unwrap_or_else(|| "default".to_string())
            );
            let (expanded, frag_includes, include_diags, nodes) = expand_body(
                child,
                source,
                file_path,
                namespace,
                &stable_id,
                fragment_index,
                &[],
                &attrs_map
                    .get("databaseId")
                    .cloned()
                    .unwrap_or_default(),
                &BTreeMap::new(),
                0,
                "",
            );
            let frag_includes = renumber_includes(&stable_id, frag_includes);
            let nodes = renumber_dynamic_nodes(&stable_id, nodes);
            fragments.push(SqlFragmentFact {
                stable_id: stable_id.clone(),
                namespace: namespace.to_string(),
                fragment_id,
                source: span_of(child, file_path),
                database_id: attrs_map.get("databaseId").cloned().unwrap_or_default(),
                attributes: attrs_map.clone(),
                raw_body: raw_body(child, source),
                expanded_body: expanded,
                includes: frag_includes.clone(),
            });
            includes.extend(frag_includes);
            dynamic_nodes.extend(nodes);
            diagnostics.extend(include_diags);
        } else if tag == "resultMap" {
            let (result_map, mappings) =
                result_map_of(child, source, file_path, namespace, project_id);
            result_maps.push(result_map);
            result_mappings.extend(mappings);
        }
    }

    MapperPartial {
        statements,
        fragments,
        result_maps,
        result_mappings,
        includes,
        dynamic_nodes,
        diagnostics,
        _marker: std::marker::PhantomData,
    }
}

/// `_result_map`.
fn result_map_of(
    node: Node<'_>,
    source: &[u8],
    file_path: &str,
    namespace: &str,
    project_id: &str,
) -> (ResultMapFact, Vec<ResultMappingFact>) {
    let attrs_map = attrs(node, source);
    let map_id = attrs_map.get("id").cloned().unwrap_or_default();
    let stable_id = format!("mybatis_result_map::{project_id}::{namespace}::{map_id}");
    let mut mappings: Vec<ResultMappingFact> = Vec::new();
    let mut ordinal = 0i64;
    for child in descendant_elements(node, source) {
        let tag = element_name(child, source);
        if !RESULT_MAPPING_TAGS.contains(&tag.as_str()) {
            continue;
        }
        let child_attrs = attrs(child, source);
        mappings.push(ResultMappingFact {
            stable_id: format!(
                "mybatis_result_mapping::{stable_id}::{}:{}:{ordinal}",
                child.start_position().row + 1,
                child.start_position().column + 1
            ),
            result_map_id: stable_id.clone(),
            mapping_kind: tag.clone(),
            source: span_of(child, file_path),
            property_name: child_attrs
                .get("property")
                .cloned()
                .or_else(|| child_attrs.get("name").cloned())
                .unwrap_or_default(),
            column: child_attrs.get("column").cloned().unwrap_or_default(),
            java_type: child_attrs.get("javaType").cloned().unwrap_or_default(),
            jdbc_type: child_attrs.get("jdbcType").cloned().unwrap_or_default(),
            nested_select: child_attrs.get("select").cloned().unwrap_or_default(),
            nested_result_map: child_attrs.get("resultMap").cloned().unwrap_or_default(),
            attributes: child_attrs,
        });
        ordinal += 1;
    }
    (
        ResultMapFact {
            stable_id,
            namespace: namespace.to_string(),
            result_map_id: map_id,
            source: span_of(node, file_path),
            java_type: attrs_map.get("type").cloned().unwrap_or_default(),
            extends: attrs_map.get("extends").cloned().unwrap_or_default(),
            auto_mapping: attrs_map.get("autoMapping").cloned().unwrap_or_default(),
            mappings: mappings.clone(),
        },
        mappings,
    )
}

/// `_analyze_config`.
fn analyze_config(
    root_element: Node<'_>,
    source: &[u8],
    file_path: &str,
    project_id: &str,
) -> ConfigFact {
    let mut properties: BTreeMap<String, String> = BTreeMap::new();
    let mut settings: BTreeMap<String, String> = BTreeMap::new();
    let mut type_aliases: BTreeMap<String, String> = BTreeMap::new();
    let mut type_handlers: Vec<BTreeMap<String, String>> = Vec::new();
    let mut plugins: Vec<BTreeMap<String, String>> = Vec::new();
    let mut environments: Vec<BTreeMap<String, String>> = Vec::new();
    let mut database_id_provider: BTreeMap<String, String> = BTreeMap::new();
    let mut mappers: Vec<BTreeMap<String, String>> = Vec::new();
    for child in child_elements(root_element, source) {
        let tag = element_name(child, source);
        if tag == "properties" {
            for (key, value) in attrs(child, source) {
                if key == "resource" || key == "url" {
                    properties.insert(key, value);
                }
            }
            properties.extend(property_children(child, source));
        } else if tag == "settings" {
            for setting in child_elements(child, source) {
                let attrs_map = attrs(setting, source);
                if let Some(name) = attrs_map.get("name") {
                    settings.insert(name.clone(), attrs_map.get("value").cloned().unwrap_or_default());
                }
            }
        } else if tag == "typeAliases" {
            for alias in child_elements(child, source) {
                let attrs_map = attrs(alias, source);
                if element_name(alias, source) == "typeAlias" && attrs_map.contains_key("alias") {
                    type_aliases.insert(
                        attrs_map.get("alias").cloned().unwrap_or_default(),
                        attrs_map.get("type").cloned().unwrap_or_default(),
                    );
                } else if element_name(alias, source) == "package" && attrs_map.contains_key("name")
                {
                    let name = attrs_map.get("name").cloned().unwrap_or_default();
                    type_aliases.insert(format!("package:{name}"), name);
                }
            }
        } else if tag == "mappers" {
            for mapper in child_elements(child, source) {
                let attrs_map = attrs(mapper, source);
                if !attrs_map.is_empty() {
                    mappers.push(attrs_map);
                }
            }
        } else if tag == "typeHandlers" {
            for handler in child_elements(child, source) {
                let mut attrs_map = attrs(handler, source);
                if !attrs_map.is_empty() {
                    attrs_map.insert(
                        "kind".to_string(),
                        element_name(handler, source),
                    );
                    type_handlers.push(attrs_map);
                }
            }
        } else if tag == "plugins" {
            for plugin in child_elements(child, source) {
                let mut attrs_map = attrs(plugin, source);
                for (key, value) in property_children(plugin, source) {
                    attrs_map.insert(format!("property:{key}"), value);
                }
                if !attrs_map.is_empty() {
                    plugins.push(attrs_map);
                }
            }
        } else if tag == "environments" {
            for env in child_elements(child, source) {
                let env_attrs = attrs(env, source);
                let mut row: BTreeMap<String, String> = BTreeMap::new();
                row.insert("id".to_string(), env_attrs.get("id").cloned().unwrap_or_default());
                for env_child in child_elements(env, source) {
                    let env_tag = element_name(env_child, source);
                    let attrs_map = attrs(env_child, source);
                    if env_tag == "transactionManager" {
                        row.insert(
                            "transactionManager".to_string(),
                            attrs_map.get("type").cloned().unwrap_or_default(),
                        );
                    } else if env_tag == "dataSource" {
                        row.insert(
                            "dataSource".to_string(),
                            attrs_map.get("type").cloned().unwrap_or_default(),
                        );
                        for (key, value) in property_children(env_child, source) {
                            row.insert(format!("dataSource.{key}"), value);
                        }
                    }
                }
                environments.push(row);
            }
        } else if tag == "databaseIdProvider" {
            for (key, value) in attrs(child, source) {
                database_id_provider.insert(key, value);
            }
            for (key, value) in property_children(child, source) {
                database_id_provider.insert(format!("property:{key}"), value);
            }
        }
    }
    let digest = crate::pyutil::sha1_hex(file_path.as_bytes())[..16].to_string();
    ConfigFact {
        stable_id: format!("mybatis_config::{project_id}::{digest}"),
        file_path: file_path.to_string(),
        source: span_of(root_element, file_path),
        properties,
        settings,
        type_aliases,
        type_handlers,
        plugins,
        environments,
        database_id_provider,
        mapper_registrations: mappers,
    }
}

/// `_expand_body` — returns (expanded, includes, diagnostics, dynamic_nodes).
#[allow(clippy::too_many_arguments)]
fn expand_body<'a>(
    node: Node<'a>,
    source: &[u8],
    file_path: &str,
    namespace: &str,
    owner_id: &str,
    fragment_index: &HashMap<String, Vec<ElementView<'_>>>,
    stack: &[String],
    database_id: &str,
    inherited_props: &BTreeMap<String, String>,
    depth: usize,
    branch_role: &str,
) -> (String, Vec<IncludeFact>, Vec<Diagnostic>, Vec<DynamicNodeFact>) {
    let props = inherited_props.clone();
    let mut includes: Vec<IncludeFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut dynamic_nodes: Vec<DynamicNodeFact> = Vec::new();
    let mut parts: Vec<String> = Vec::new();
    let Some(content) = content_of(node) else {
        return (String::new(), includes, diagnostics, dynamic_nodes);
    };
    let mut cursor = content.walk();
    for child in content.children(&mut cursor) {
        if child.kind() == "CharData" || child.kind() == "CData" {
            let text = substitute(&text_of(Some(child), source), &props);
            parts.push(text.clone());
            if !text.trim().is_empty() {
                dynamic_nodes.push(dynamic_node(
                    owner_id,
                    "#text",
                    "text",
                    span_of(child, file_path),
                    dynamic_nodes.len() as i64,
                    text,
                    BTreeMap::new(),
                    "",
                ));
            }
        } else if child.kind() == "CDSect" {
            let mut inner_text = String::new();
            let mut inner = child.walk();
            for grand in child.children(&mut inner) {
                if grand.kind() == "CData" {
                    inner_text.push_str(&text_of(Some(grand), source));
                }
            }
            let text = substitute(&inner_text, &props);
            parts.push(text.clone());
            if !text.trim().is_empty() {
                dynamic_nodes.push(dynamic_node(
                    owner_id,
                    "#cdata",
                    "text",
                    span_of(child, file_path),
                    dynamic_nodes.len() as i64,
                    text,
                    BTreeMap::new(),
                    "",
                ));
            }
        } else if child.kind() == "EntityRef" {
            let text = crate::pyutil::html_unescape(&text_of(Some(child), source));
            parts.push(text.clone());
            if !text.trim().is_empty() {
                dynamic_nodes.push(dynamic_node(
                    owner_id,
                    "#entity",
                    "text",
                    span_of(child, file_path),
                    dynamic_nodes.len() as i64,
                    text,
                    BTreeMap::new(),
                    "",
                ));
            }
        } else if child.kind() == "element" {
            let tag = element_name(child, source);
            if tag == "include" {
                let attrs_map = attrs(child, source);
                let mut child_props = props.clone();
                child_props.extend(property_children(child, source));
                let refid = substitute(&attrs_map.get("refid").cloned().unwrap_or_default(), &child_props);
                let resolved = qualified_ref(namespace, &refid);
                let include_id = format!(
                    "mybatis_include::{owner_id}::{}:{}:{}",
                    child.start_position().row + 1,
                    child.start_position().column + 1,
                    includes.len()
                );
                let mut status = "resolved".to_string();
                let target = resolve_fragment(fragment_index, &resolved, database_id);
                let stack_key = format!(
                    "{resolved}::{}",
                    target
                        .as_ref()
                        .map(|t| t.database_id.as_str())
                        .unwrap_or("")
                );
                if stack.contains(&stack_key) {
                    status = "cycle".to_string();
                    diagnostics.push(Diagnostic::new(
                        "mybatis.xml.include_cycle",
                        &format!("Include cycle at {resolved}"),
                    ));
                } else if depth >= 20 {
                    status = "max_depth".to_string();
                    diagnostics.push(Diagnostic::new(
                        "mybatis.xml.include_depth",
                        &format!("Include expansion depth exceeded at {resolved}"),
                    ));
                } else if target.is_none() {
                    status = "unresolved".to_string();
                    diagnostics.push(Diagnostic::new(
                        "mybatis.xml.include_unresolved",
                        &format!("Unable to resolve include {refid:?}"),
                    ));
                }
                includes.push(IncludeFact {
                    stable_id: include_id,
                    owner_id: owner_id.to_string(),
                    refid,
                    resolved_refid: resolved,
                    source: span_of(child, file_path),
                    properties: child_props.clone(),
                    resolution_status: status.clone(),
                });
                if status == "resolved" {
                    let target = target.unwrap();
                    let (expanded, nested, nested_diags, nested_dynamic) = expand_body(
                        target.node,
                        target.source,
                        &target.file_path,
                        &target.namespace,
                        owner_id,
                        fragment_index,
                        &{
                            let mut s = stack.to_vec();
                            s.push(stack_key);
                            s
                        },
                        &if database_id.is_empty() {
                            target.database_id.clone()
                        } else {
                            database_id.to_string()
                        },
                        &child_props,
                        depth + 1,
                        branch_role,
                    );
                    parts.push(expanded);
                    dynamic_nodes.extend(nested_dynamic);
                    includes.extend(nested);
                    diagnostics.extend(nested_diags);
                }
            } else {
                let role = if tag == "when" || tag == "otherwise" {
                    tag.clone()
                } else {
                    branch_role.to_string()
                };
                if DYNAMIC_TAGS.contains(&tag.as_str()) {
                    dynamic_nodes.push(dynamic_node(
                        owner_id,
                        &tag,
                        "control",
                        span_of(child, file_path),
                        dynamic_nodes.len() as i64,
                        String::new(),
                        substitute_attrs(&attrs(child, source), &props),
                        &role,
                    ));
                }
                let (expanded, nested, nested_diags, nested_dynamic) = expand_body(
                    child,
                    source,
                    file_path,
                    namespace,
                    owner_id,
                    fragment_index,
                    stack,
                    database_id,
                    &props,
                    depth,
                    &role,
                );
                parts.push(expanded);
                includes.extend(nested);
                diagnostics.extend(nested_diags);
                dynamic_nodes.extend(nested_dynamic);
            }
        }
    }
    (parts.join(""), includes, diagnostics, dynamic_nodes)
}

/// `_renumber_includes`.
fn renumber_includes(owner_id: &str, includes: Vec<IncludeFact>) -> Vec<IncludeFact> {
    includes
        .into_iter()
        .enumerate()
        .map(|(order, mut include)| {
            let source_digest =
                crate::pyutil::sha1_hex(include.source.file_path.as_bytes())[..10].to_string();
            include.stable_id = format!(
                "mybatis_include::{owner_id}::{order}::{source_digest}:{}:{}",
                include.source.start_line, include.source.start_column
            );
            include
        })
        .collect()
}

/// `_renumber_dynamic_nodes`.
fn renumber_dynamic_nodes(owner_id: &str, nodes: Vec<DynamicNodeFact>) -> Vec<DynamicNodeFact> {
    nodes
        .into_iter()
        .enumerate()
        .map(|(order, mut node)| {
            let source_digest =
                crate::pyutil::sha1_hex(node.source.file_path.as_bytes())[..10].to_string();
            node.stable_id = format!(
                "mybatis_dynamic::{owner_id}::{order}::{source_digest}:{}:{}",
                node.source.start_line, node.source.start_column
            );
            node.order = order as i64;
            node
        })
        .collect()
}

/// `_resolve_fragment`.
fn resolve_fragment<'a>(
    fragment_index: &HashMap<String, Vec<ElementView<'a>>>,
    resolved: &str,
    database_id: &str,
) -> Option<ElementView<'a>> {
    let candidates = fragment_index.get(resolved)?;
    if candidates.is_empty() {
        return None;
    }
    if !database_id.is_empty() {
        for candidate in candidates {
            if candidate.database_id == database_id {
                return Some(candidate.clone());
            }
        }
    }
    for candidate in candidates {
        if candidate.database_id.is_empty() {
            return Some(candidate.clone());
        }
    }
    if database_id.is_empty() {
        Some(candidates[0].clone())
    } else {
        None
    }
}

// ── shared XML helpers (public cho spring_bridge) ───────────────────────────

/// `_first_element`.
pub fn first_element(node: Node<'_>) -> Option<Node<'_>> {
    if node.kind() == "element" {
        return Some(node);
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if let Some(found) = first_element(child) {
            return Some(found);
        }
    }
    None
}

/// `_child_elements`.
pub fn child_elements<'a>(node: Node<'a>, source: &[u8]) -> Vec<Node<'a>> {
    let Some(content) = content_of(node) else {
        return Vec::new();
    };
    let _ = source;
    let mut cursor = content.walk();
    content
        .children(&mut cursor)
        .filter(|child| child.kind() == "element")
        .collect()
}

/// `_descendant_elements`.
pub fn descendant_elements<'a>(node: Node<'a>, source: &[u8]) -> Vec<Node<'a>> {
    let mut out = Vec::new();
    for child in child_elements(node, source) {
        out.push(child);
        out.extend(descendant_elements(child, source));
    }
    out
}

/// `_content`.
pub fn content_of(node: Node<'_>) -> Option<Node<'_>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).find(|child| child.kind() == "content")
}

/// `_element_name`.
pub fn element_name(node: Node<'_>, source: &[u8]) -> String {
    let mut cursor = node.walk();
    let tag = node
        .children(&mut cursor)
        .find(|child| child.kind() == "STag" || child.kind() == "EmptyElemTag");
    let Some(tag) = tag else {
        return String::new();
    };
    let mut inner = tag.walk();
    let name = tag
        .children(&mut inner)
        .find(|child| child.kind() == "Name");
    text_of(name, source)
}

/// `_attrs` — attribute map với html.unescape.
pub fn attrs(node: Node<'_>, source: &[u8]) -> BTreeMap<String, String> {
    let mut cursor = node.walk();
    let tag = node
        .children(&mut cursor)
        .find(|child| child.kind() == "STag" || child.kind() == "EmptyElemTag");
    let mut attrs: BTreeMap<String, String> = BTreeMap::new();
    let Some(tag) = tag else {
        return attrs;
    };
    let mut inner = tag.walk();
    for child in tag.children(&mut inner) {
        if child.kind() != "Attribute" {
            continue;
        }
        let mut attr_cursor = child.walk();
        let name = child
            .children(&mut attr_cursor)
            .find(|item| item.kind() == "Name");
        let mut value_cursor = child.walk();
        let value = child
            .children(&mut value_cursor)
            .find(|item| item.kind() == "AttValue");
        let key = text_of(name, source);
        let raw = text_of(value, source);
        let value_text = if raw.len() >= 2 && (raw.starts_with('\'') || raw.starts_with('"')) {
            raw[1..raw.len() - 1].to_string()
        } else {
            raw
        };
        attrs.insert(key, crate::pyutil::html_unescape(&value_text));
    }
    attrs
}

/// `_property_children`.
pub fn property_children(node: Node<'_>, source: &[u8]) -> BTreeMap<String, String> {
    let mut props: BTreeMap<String, String> = BTreeMap::new();
    for child in child_elements(node, source) {
        if element_name(child, source) == "property" {
            let attrs_map = attrs(child, source);
            if let Some(name) = attrs_map.get("name") {
                props.insert(name.clone(), attrs_map.get("value").cloned().unwrap_or_default());
            }
        }
    }
    props
}

/// `_raw_body`.
pub fn raw_body(node: Node<'_>, source: &[u8]) -> String {
    match content_of(node) {
        Some(content) => text_of(Some(content), source),
        None => String::new(),
    }
}

/// `_substitute` — `${key}` → props[key] (giữ nguyên khi thiếu).
pub fn substitute(text: &str, props: &BTreeMap<String, String>) -> String {
    property_re()
        .replace_all(text, |caps: &regex::Captures<'_>| {
            props
                .get(&caps[1])
                .cloned()
                .unwrap_or_else(|| caps[0].to_string())
        })
        .into_owned()
}

/// `_substitute_attrs`.
pub fn substitute_attrs(
    attrs_map: &BTreeMap<String, String>,
    props: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    attrs_map
        .iter()
        .map(|(key, value)| (key.clone(), substitute(value, props)))
        .collect()
}

/// `_qualified_ref`.
pub fn qualified_ref(namespace: &str, refid: &str) -> String {
    if refid.contains('.') {
        refid.to_string()
    } else if !namespace.is_empty() {
        format!("{namespace}.{refid}")
    } else {
        refid.to_string()
    }
}

/// `_doctype`.
pub fn doctype_of(source: &[u8]) -> String {
    let decoded = decode_ignore(source);
    doctype_re()
        .captures(&decoded)
        .map(|caps| caps.get(0).unwrap().as_str().to_string())
        .unwrap_or_default()
}

/// `_span`.
pub fn span_of(node: Node<'_>, file_path: &str) -> SourceSpan {
    SourceSpan::with_points(
        file_path,
        node.start_position().row as i64 + 1,
        node.end_position().row as i64 + 1,
        node.start_position().column as i64 + 1,
        node.end_position().column as i64 + 1,
    )
}

/// `_text` — KHÔNG strip (khác `_node_text` của java module).
pub fn text_of(node: Option<Node<'_>>, source: &[u8]) -> String {
    match node {
        None => String::new(),
        Some(node) => decode_ignore(&source[node.start_byte()..node.end_byte()]),
    }
}
