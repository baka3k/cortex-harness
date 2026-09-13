//! Port `tools/mybatis/spring_bridge.py` — optional Spring bridge +
//! extensions + cache facts (regex + XML helpers).

use std::collections::BTreeMap;
use std::path::Path;

use regex::Regex;

use crate::mybatis::detector::read_limited;
use crate::mybatis::mapper_xml::{
    attrs, child_elements, descendant_elements, element_name, first_element, span_of,
};
use crate::mybatis::models::{
    CacheFact, ConfigFact, Diagnostic, ExtensionFact, SourceSpan, SpringBridgeFact,
};

const SPRING_BRIDGE_CLASSES: [(&str, &str); 8] = [
    ("org.mybatis.spring.SqlSessionFactoryBean", "sql_session_factory"),
    ("org.mybatis.spring.SqlSessionTemplate", "sql_session_template"),
    (
        "org.mybatis.spring.mapper.MapperScannerConfigurer",
        "mapper_scanner",
    ),
    ("org.mybatis.spring.mapper.MapperFactoryBean", "mapper_factory"),
    ("SqlSessionFactoryBean", "sql_session_factory"),
    ("SqlSessionTemplate", "sql_session_template"),
    ("MapperScannerConfigurer", "mapper_scanner"),
    ("MapperFactoryBean", "mapper_factory"),
];

pub struct SpringBridgeAnalysis {
    pub bridge_facts: Vec<SpringBridgeFact>,
    pub extension_facts: Vec<ExtensionFact>,
    pub cache_facts: Vec<CacheFact>,
    pub diagnostics: Vec<Diagnostic>,
}

/// `analyze_optional_extensions`.
pub fn analyze_optional_extensions(
    root: &Path,
    java_files: &[String],
    xml_files: &[String],
    config_facts: &[ConfigFact],
    project_id: &str,
) -> SpringBridgeAnalysis {
    let mut bridge_facts: Vec<SpringBridgeFact> = Vec::new();
    let mut extension_facts: Vec<ExtensionFact> = Vec::new();
    let mut cache_facts: Vec<CacheFact> = Vec::new();
    let diagnostics: Vec<Diagnostic> = Vec::new();

    for config in config_facts {
        for (index, handler) in config.type_handlers.iter().enumerate() {
            extension_facts.push(config_extension(project_id, config, "type_handler", handler, index));
        }
        for (index, plugin) in config.plugins.iter().enumerate() {
            extension_facts.push(config_extension(project_id, config, "plugin", plugin, index));
        }
    }

    let unique_java: std::collections::BTreeSet<&String> = java_files.iter().collect();
    for rel_path in unique_java {
        let abs_path = root.join(rel_path.as_str());
        if !abs_path.is_file() {
            continue;
        }
        let text = read_limited(&abs_path);
        bridge_facts.extend(java_bridge_facts(project_id, rel_path, &text));
        extension_facts.extend(java_extension_facts(project_id, rel_path, &text));
    }

    let unique_xml: std::collections::BTreeSet<&String> = xml_files.iter().collect();
    for rel_path in unique_xml {
        let abs_path = root.join(rel_path.as_str());
        if !abs_path.is_file() {
            continue;
        }
        let source = match std::fs::read(&abs_path) {
            Ok(source) => source,
            Err(_) => continue,
        };
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_xml::LANGUAGE_XML.into())
            .expect("load xml parser");
        let Some(tree) = parser.parse(&source, None) else {
            continue;
        };
        let Some(root_element) = first_element(tree.root_node()) else {
            continue;
        };
        let root_tag = element_name(root_element, &source);
        if root_tag == "mapper" {
            let namespace = attrs(root_element, &source)
                .get("namespace")
                .cloned()
                .unwrap_or_default();
            cache_facts.extend(mapper_cache_facts(
                project_id, root_element, &source, rel_path, &namespace,
            ));
        } else if root_tag == "beans" || root_tag == "beans:beans" {
            bridge_facts.extend(spring_xml_bridge_facts(
                project_id, root_element, &source, rel_path,
            ));
        }
    }

    SpringBridgeAnalysis {
        bridge_facts,
        extension_facts,
        cache_facts,
        diagnostics,
    }
}

/// `_config_extension`.
fn config_extension(
    project_id: &str,
    config: &ConfigFact,
    kind: &str,
    attrs_map: &BTreeMap<String, String>,
    index: usize,
) -> ExtensionFact {
    let java_type = attrs_map
        .get("handler")
        .cloned()
        .or_else(|| attrs_map.get("interceptor").cloned())
        .or_else(|| attrs_map.get("type").cloned())
        .unwrap_or_default();
    let name = if !java_type.is_empty() {
        java_type.clone()
    } else {
        attrs_map
            .get("javaType")
            .cloned()
            .unwrap_or_else(|| kind.to_string())
    };
    ExtensionFact {
        stable_id: format!(
            "mybatis_extension::{project_id}::{kind}::{}",
            digest(&[&config.file_path, &index.to_string(), &name])
        ),
        extension_kind: kind.to_string(),
        name,
        java_type,
        source: config.source.clone(),
        attributes: attrs_map.clone(),
        resolution_status: "evidence_only".to_string(),
    }
}

/// `_java_bridge_facts`.
fn java_bridge_facts(project_id: &str, rel_path: &str, text: &str) -> Vec<SpringBridgeFact> {
    let mut rows: Vec<SpringBridgeFact> = Vec::new();
    let patterns: Vec<(Regex, &str)> = vec![
        (
            Regex::new(r"(?s)@MapperScan\s*\((?P<args>[^)]*)\)").unwrap(),
            "mapper_scan",
        ),
        (Regex::new(r"\bSqlSessionFactoryBean\b").unwrap(), "sql_session_factory"),
        (Regex::new(r"\bSqlSessionTemplate\b").unwrap(), "sql_session_template"),
        (Regex::new(r"\bMapperScannerConfigurer\b").unwrap(), "mapper_scanner"),
        (Regex::new(r"\bMapperFactoryBean\b").unwrap(), "mapper_factory"),
    ];
    for (pattern, kind) in &patterns {
        for (index, m) in pattern.find_iter(text).enumerate() {
            let line = line_for_index(text, m.start());
            let mut attrs_map: BTreeMap<String, String> = BTreeMap::new();
            attrs_map.insert("source_kind".to_string(), "java".to_string());
            if let Some(args) = pattern
                .captures(m.as_str())
                .and_then(|caps| caps.name("args"))
            {
                attrs_map.insert("arguments".to_string(), args.as_str().trim().to_string());
            }
            rows.push(SpringBridgeFact {
                stable_id: format!(
                    "mybatis_spring_bridge::{project_id}::{kind}::{}",
                    digest(&[rel_path, &line.to_string(), &index.to_string()])
                ),
                bridge_kind: kind.to_string(),
                name: kind.to_string(),
                source: SourceSpan::with_points(rel_path, line, line, 1, 1),
                target: String::new(),
                attributes: attrs_map,
                resolution_status: "evidence_only".to_string(),
            });
        }
    }
    rows
}

/// `_java_extension_facts`.
fn java_extension_facts(project_id: &str, rel_path: &str, text: &str) -> Vec<ExtensionFact> {
    let mut rows: Vec<ExtensionFact> = Vec::new();
    let class_name = java_class_name(text);
    let patterns: [(Regex, &str); 2] = [
        (
            Regex::new(
                r"\bBaseTypeHandler\s*<|\bextends\s+BaseTypeHandler\b|@MappedTypes\b|@MappedJdbcTypes\b",
            )
            .unwrap(),
            "type_handler",
        ),
        (
            Regex::new(r"\bimplements\s+Interceptor\b|@Intercepts\b|@Signature\b").unwrap(),
            "plugin",
        ),
    ];
    for (pattern, kind) in &patterns {
        for (index, m) in pattern.find_iter(text).enumerate() {
            let line = line_for_index(text, m.start());
            rows.push(ExtensionFact {
                stable_id: format!(
                    "mybatis_extension::{project_id}::{kind}::{}",
                    digest(&[rel_path, &line.to_string(), &index.to_string()])
                ),
                extension_kind: kind.to_string(),
                name: if class_name.is_empty() {
                    kind.to_string()
                } else {
                    class_name.clone()
                },
                java_type: class_name.clone(),
                source: SourceSpan::with_points(rel_path, line, line, 1, 1),
                attributes: {
                    let mut attrs_map: BTreeMap<String, String> = BTreeMap::new();
                    attrs_map.insert("source_kind".to_string(), "java".to_string());
                    attrs_map.insert(
                        "evidence".to_string(),
                        m.as_str().chars().take(120).collect(),
                    );
                    attrs_map
                },
                resolution_status: "evidence_only".to_string(),
            });
        }
    }
    rows
}

/// `_spring_xml_bridge_facts`.
fn spring_xml_bridge_facts(
    project_id: &str,
    root_element: tree_sitter::Node<'_>,
    source: &[u8],
    rel_path: &str,
) -> Vec<SpringBridgeFact> {
    let mut rows: Vec<SpringBridgeFact> = Vec::new();
    for (index, elem) in descendant_elements(root_element, source).iter().enumerate() {
        let tag = element_name(*elem, source)
            .rsplit(':')
            .next()
            .unwrap_or("")
            .to_string();
        let attrs_map = attrs(*elem, source);
        let class_name = attrs_map.get("class").cloned().unwrap_or_default();
        let mut bridge_kind = SPRING_BRIDGE_CLASSES
            .iter()
            .find(|(key, _)| *key == class_name)
            .map(|(_, kind)| kind.to_string())
            .unwrap_or_default();
        if tag == "scan" && text_around(source, *elem).to_lowercase().contains("mybatis") {
            bridge_kind = "mapper_scan".to_string();
        }
        if bridge_kind.is_empty() {
            continue;
        }
        rows.push(SpringBridgeFact {
            stable_id: format!(
                "mybatis_spring_bridge::{project_id}::{bridge_kind}::{}",
                digest(&[rel_path, &index.to_string(), &class_name])
            ),
            bridge_kind: bridge_kind.clone(),
            name: attrs_map
                .get("id")
                .cloned()
                .unwrap_or_else(|| {
                    if class_name.is_empty() {
                        bridge_kind.clone()
                    } else {
                        class_name.clone()
                    }
                }),
            target: class_name.clone(),
            source: span_of(*elem, rel_path),
            attributes: attrs_map.clone(),
            resolution_status: "evidence_only".to_string(),
        });
        for child in child_elements(*elem, source) {
            let child_attrs = attrs(child, source);
            let prop_name = child_attrs.get("name").cloned().unwrap_or_default();
            if [
                "mapperLocations",
                "configLocation",
                "basePackage",
                "sqlSessionFactoryBeanName",
            ]
            .contains(&prop_name.as_str())
            {
                rows.push(SpringBridgeFact {
                    stable_id: format!(
                        "mybatis_spring_bridge::{project_id}::{prop_name}::{}",
                        digest(&[rel_path, &index.to_string(), &prop_name])
                    ),
                    bridge_kind: prop_name.clone(),
                    name: prop_name.clone(),
                    target: child_attrs
                        .get("value")
                        .cloned()
                        .or_else(|| child_attrs.get("ref").cloned())
                        .unwrap_or_default(),
                    source: span_of(child, rel_path),
                    attributes: child_attrs,
                    resolution_status: "evidence_only".to_string(),
                });
            }
        }
    }
    rows
}

/// `_mapper_cache_facts`.
fn mapper_cache_facts(
    project_id: &str,
    root_element: tree_sitter::Node<'_>,
    source: &[u8],
    rel_path: &str,
    namespace: &str,
) -> Vec<CacheFact> {
    let mut rows: Vec<CacheFact> = Vec::new();
    for (index, child) in child_elements(root_element, source).iter().enumerate() {
        let tag = element_name(*child, source);
        if tag != "cache" && tag != "cache-ref" {
            continue;
        }
        let attrs_map = attrs(*child, source);
        rows.push(CacheFact {
            stable_id: format!(
                "mybatis_cache::{project_id}::{namespace}::{tag}:{}",
                digest(&[
                    rel_path,
                    &index.to_string(),
                    &attrs_map.get("namespace").cloned().unwrap_or_default()
                ])
            ),
            namespace: namespace.to_string(),
            cache_kind: tag.clone(),
            target_namespace: attrs_map.get("namespace").cloned().unwrap_or_default(),
            source: span_of(*child, rel_path),
            attributes: attrs_map,
            resolution_status: "evidence_only".to_string(),
        });
    }
    rows
}

/// `_java_class_name`.
fn java_class_name(text: &str) -> String {
    let mut package = String::new();
    let package_re = Regex::new(r"\bpackage\s+([\w.]+)\s*;").unwrap();
    if let Some(caps) = package_re.captures(text) {
        package = caps.get(1).unwrap().as_str().to_string();
    }
    let class_re = Regex::new(r"\b(?:class|interface|record)\s+([A-Za-z_]\w*)").unwrap();
    let Some(caps) = class_re.captures(text) else {
        return String::new();
    };
    let class = caps.get(1).unwrap().as_str();
    if package.is_empty() {
        class.to_string()
    } else {
        format!("{package}.{class}")
    }
}

fn line_for_index(text: &str, index: usize) -> i64 {
    text.as_bytes()[..index.min(text.len())]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as i64
        + 1
}

fn digest(parts: &[&str]) -> String {
    crate::pyutil::sha1_hex(parts.join("|").as_bytes())[..16].to_string()
}

fn text_around(source: &[u8], node: tree_sitter::Node<'_>) -> String {
    cortex_analyzer_framework::ts::decode_ignore(&source[node.start_byte()..node.end_byte()])
}
