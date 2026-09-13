//! Port `tools/mybatis/pipeline.py` — run_mybatis_foundation + semantic fact
//! builders (mirror `_module_fact` ... `_sql_parameter_fact`).

use std::path::Path;

use serde_json::{json, Value};

use crate::mybatis::annotation_mapper::analyze_annotation_mappers;
use crate::mybatis::detector::{safe_rel_path, MyBatisProjectDetector};
use crate::mybatis::mapper_interface::analyze_mapper_interfaces;
use crate::mybatis::mapper_xml::analyze_mapper_xml_files;
use crate::mybatis::models::{
    Artifact, CacheFact, ConfigFact, Diagnostic, DynamicNodeFact, ExtensionFact, IncludeFact,
    JavaPropertyFact, MapperInterfaceFact, MapperMethodFact, MapperParameterFact, Module, MyBatisFact,
    ProviderFact, ResultMapFact, ResultMappingFact, SourceSpan, SpringBridgeFact, SqlColumnFact,
    SqlFragmentFact, SqlJoinFact, SqlParameterFact, SqlStatementSemanticFact, SqlTableFact,
    StatementFact, XmlDocumentFact,
};
use crate::mybatis::resolver::resolve_mybatis_relationships;
use crate::mybatis::spring_bridge::analyze_optional_extensions;
use crate::mybatis::sql_semantic::analyze_sql_semantics;

pub struct MyBatisAnalysisResult {
    pub project_id: String,
    pub project_name: String,
    pub root: String,
    pub modules: Vec<Module>,
    pub artifacts: Vec<Artifact>,
    pub parser_capabilities: usize,
    pub semantic_facts: Vec<MyBatisFact>,
    pub relationships: Vec<crate::mybatis::models::MyBatisRelationship>,
    pub diagnostics_count: usize,
}

/// `run_mybatis_foundation`.
pub fn run_mybatis_foundation(
    root: &str,
    project_id: &str,
    project_name: &str,
    languages: &[&str],
) -> MyBatisAnalysisResult {
    let root_abs = absolute(root);
    let detector = MyBatisProjectDetector::new(Path::new(&root_abs));
    let modules_raw = detector.discover_modules(languages);
    // parser_capabilities — java/sql/xml đều khả dụng (grammar pinned) →
    // 3 capabilities, 0 parser diagnostics như Python khi parse OK.
    let parser_capabilities = 3usize;
    let parser_diagnostics: Vec<Diagnostic> = Vec::new();

    let mut modules: Vec<Module> = Vec::new();
    let mut artifacts: Vec<Artifact> = Vec::new();
    let mut mapper_interfaces: Vec<MapperInterfaceFact> = Vec::new();
    let mut mapper_methods: Vec<MapperMethodFact> = Vec::new();
    let mut mapper_parameters: Vec<MapperParameterFact> = Vec::new();
    let mut java_properties: Vec<JavaPropertyFact> = Vec::new();
    let mut xml_documents: Vec<XmlDocumentFact> = Vec::new();
    let mut statements: Vec<StatementFact> = Vec::new();
    let mut sql_fragments: Vec<SqlFragmentFact> = Vec::new();
    let mut result_maps: Vec<ResultMapFact> = Vec::new();
    let mut result_mappings: Vec<ResultMappingFact> = Vec::new();
    let mut includes: Vec<IncludeFact> = Vec::new();
    let mut dynamic_nodes: Vec<DynamicNodeFact> = Vec::new();
    let mut config_facts: Vec<ConfigFact> = Vec::new();
    let mut provider_facts: Vec<ProviderFact> = Vec::new();
    let mut spring_bridge_facts: Vec<SpringBridgeFact> = Vec::new();
    let mut extension_facts: Vec<ExtensionFact> = Vec::new();
    let mut cache_facts: Vec<CacheFact> = Vec::new();
    let mut sql_statement_semantics: Vec<SqlStatementSemanticFact> = Vec::new();
    let mut sql_tables: Vec<SqlTableFact> = Vec::new();
    let mut sql_columns: Vec<SqlColumnFact> = Vec::new();
    let mut sql_joins: Vec<SqlJoinFact> = Vec::new();
    let mut sql_parameters: Vec<SqlParameterFact> = Vec::new();
    let mut semantic_facts: Vec<MyBatisFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = parser_diagnostics;

    for item in &modules_raw {
        let module = Module {
            rel_path: item.rel_path.clone(),
            mapper_xml_files: item.mapper_xml_files.clone(),
            config_xml_files: item.config_xml_files.clone(),
            java_files: item.java_files.clone(),
            build_files: item.build_files.clone(),
            spring_config_files: item.spring_config_files.clone(),
            evidence: item.evidence.clone(),
            confidence: item.confidence,
        };
        modules.push(module.clone());
        semantic_facts.push(module_fact(project_id, project_name, &module));
        for (kind, paths) in [
            ("mapper_xml", &module.mapper_xml_files),
            ("config_xml", &module.config_xml_files),
            ("java_mapper", &module.java_files),
            ("build", &module.build_files),
            ("spring_xml", &module.spring_config_files),
        ] {
            for rel_path in paths {
                let detection = detector.detect_path(rel_path);
                let artifact = Artifact {
                    kind: kind.to_string(),
                    file_path: rel_path.clone(),
                    module_path: module.rel_path.clone(),
                    evidence: detection.evidence.clone(),
                    confidence: detection.confidence,
                    source: SourceSpan::new(rel_path),
                };
                artifacts.push(artifact.clone());
                semantic_facts.push(artifact_fact(project_id, project_name, &artifact));
            }
        }

        if !module.java_files.is_empty() {
            let mapper_analysis = analyze_mapper_interfaces(
                Path::new(&root_abs),
                &module.java_files,
                project_id,
                project_name,
            );
            for mapper in &mapper_analysis.interfaces {
                semantic_facts.push(mapper_interface_fact(project_id, project_name, mapper));
            }
            for method in &mapper_analysis.methods {
                semantic_facts.push(mapper_method_fact(project_id, project_name, method));
            }
            for param in &mapper_analysis.parameters {
                semantic_facts.push(mapper_parameter_fact(project_id, project_name, param));
            }
            for prop in &mapper_analysis.java_properties {
                semantic_facts.push(java_property_fact(project_id, project_name, prop));
            }

            let annotation_analysis =
                analyze_annotation_mappers(&mapper_analysis.methods, project_id);
            for stmt in &annotation_analysis.statements {
                semantic_facts.push(statement_fact(
                    project_id,
                    project_name,
                    stmt,
                    "mybatis_annotation_mapper",
                ));
            }
            for result_map in &annotation_analysis.result_maps {
                semantic_facts.push(result_map_fact(
                    project_id,
                    project_name,
                    result_map,
                    "mybatis_annotation_mapper",
                ));
            }
            for mapping in &annotation_analysis.result_mappings {
                semantic_facts.push(result_mapping_fact(
                    project_id,
                    project_name,
                    mapping,
                    "mybatis_annotation_mapper",
                ));
            }
            for provider in &annotation_analysis.providers {
                semantic_facts.push(provider_fact(project_id, project_name, provider));
            }

            if !annotation_analysis.statements.is_empty() {
                append_sql_analysis(
                    &annotation_analysis.statements,
                    project_id,
                    project_name,
                    &mut sql_statement_semantics,
                    &mut sql_tables,
                    &mut sql_columns,
                    &mut sql_joins,
                    &mut sql_parameters,
                    &mut semantic_facts,
                    &mut diagnostics,
                );
            }

            mapper_interfaces.extend(mapper_analysis.interfaces);
            mapper_methods.extend(mapper_analysis.methods);
            mapper_parameters.extend(mapper_analysis.parameters);
            java_properties.extend(mapper_analysis.java_properties);
            diagnostics.extend(mapper_analysis.diagnostics);
            statements.extend(annotation_analysis.statements);
            result_maps.extend(annotation_analysis.result_maps);
            result_mappings.extend(annotation_analysis.result_mappings);
            provider_facts.extend(annotation_analysis.providers);
            diagnostics.extend(annotation_analysis.diagnostics);
        }

        let mut module_xml_files: Vec<String> = Vec::new();
        module_xml_files.extend(module.mapper_xml_files.clone());
        module_xml_files.extend(module.config_xml_files.clone());
        module_xml_files.extend(module.spring_config_files.clone());
        if !module_xml_files.is_empty() {
            let xml_analysis =
                analyze_mapper_xml_files(Path::new(&root_abs), &module_xml_files, project_id);
            xml_documents.extend(xml_analysis.documents.clone());
            statements.extend(xml_analysis.statements.clone());
            sql_fragments.extend(xml_analysis.fragments.clone());
            result_maps.extend(xml_analysis.result_maps.clone());
            result_mappings.extend(xml_analysis.result_mappings.clone());
            includes.extend(xml_analysis.includes.clone());
            dynamic_nodes.extend(xml_analysis.dynamic_nodes.clone());
            config_facts.extend(xml_analysis.config_facts.clone());
            diagnostics.extend(xml_analysis.diagnostics);
            for doc in &xml_analysis.documents {
                semantic_facts.push(xml_document_fact(project_id, project_name, doc));
            }
            for stmt in &xml_analysis.statements {
                semantic_facts.push(statement_fact(project_id, project_name, stmt, "mybatis_mapper_xml"));
            }
            for fragment in &xml_analysis.fragments {
                semantic_facts.push(fragment_fact(project_id, project_name, fragment));
            }
            for result_map in &xml_analysis.result_maps {
                semantic_facts.push(result_map_fact(
                    project_id,
                    project_name,
                    result_map,
                    "mybatis_mapper_xml",
                ));
            }
            for mapping in &xml_analysis.result_mappings {
                semantic_facts.push(result_mapping_fact(
                    project_id,
                    project_name,
                    mapping,
                    "mybatis_mapper_xml",
                ));
            }
            for include in &xml_analysis.includes {
                semantic_facts.push(include_fact(project_id, project_name, include));
            }
            for dynamic in &xml_analysis.dynamic_nodes {
                semantic_facts.push(dynamic_node_fact(project_id, project_name, dynamic));
            }
            for config in &xml_analysis.config_facts {
                semantic_facts.push(config_fact(project_id, project_name, config));
            }

            if !xml_analysis.statements.is_empty() {
                append_sql_analysis(
                    &xml_analysis.statements,
                    project_id,
                    project_name,
                    &mut sql_statement_semantics,
                    &mut sql_tables,
                    &mut sql_columns,
                    &mut sql_joins,
                    &mut sql_parameters,
                    &mut semantic_facts,
                    &mut diagnostics,
                );
            }

            let extension_analysis = analyze_optional_extensions(
                Path::new(&root_abs),
                &module.java_files,
                &module_xml_files,
                &xml_analysis.config_facts,
                project_id,
            );
            spring_bridge_facts.extend(extension_analysis.bridge_facts.clone());
            extension_facts.extend(extension_analysis.extension_facts.clone());
            cache_facts.extend(extension_analysis.cache_facts.clone());
            diagnostics.extend(extension_analysis.diagnostics);
            for bridge in &extension_analysis.bridge_facts {
                semantic_facts.push(spring_bridge_fact(project_id, project_name, bridge));
            }
            for extension in &extension_analysis.extension_facts {
                semantic_facts.push(extension_fact(project_id, project_name, extension));
            }
            for cache in &extension_analysis.cache_facts {
                semantic_facts.push(cache_fact(project_id, project_name, cache));
            }
        }
    }

    let resolution = resolve_mybatis_relationships(
        project_id,
        &mapper_interfaces,
        &mapper_methods,
        &mapper_parameters,
        &statements,
        &sql_tables,
        &sql_columns,
        &sql_joins,
        &sql_parameters,
        &result_maps,
        &result_mappings,
        &java_properties,
    );
    diagnostics.extend(resolution.diagnostics);

    MyBatisAnalysisResult {
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        root: ".".to_string(),
        modules,
        artifacts,
        parser_capabilities,
        semantic_facts,
        relationships: resolution.relationships,
        diagnostics_count: diagnostics.len(),
    }
}

#[allow(clippy::too_many_arguments)]
fn append_sql_analysis(
    statements: &[StatementFact],
    project_id: &str,
    project_name: &str,
    sql_statement_semantics: &mut Vec<SqlStatementSemanticFact>,
    sql_tables: &mut Vec<SqlTableFact>,
    sql_columns: &mut Vec<SqlColumnFact>,
    sql_joins: &mut Vec<SqlJoinFact>,
    sql_parameters: &mut Vec<SqlParameterFact>,
    semantic_facts: &mut Vec<MyBatisFact>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let sql_analysis = analyze_sql_semantics(statements, project_id);
    sql_statement_semantics.extend(sql_analysis.statements.clone());
    sql_tables.extend(sql_analysis.tables.clone());
    sql_columns.extend(sql_analysis.columns.clone());
    sql_joins.extend(sql_analysis.joins.clone());
    sql_parameters.extend(sql_analysis.parameters.clone());
    diagnostics.extend(sql_analysis.diagnostics);
    for sql_stmt in &sql_analysis.statements {
        semantic_facts.push(sql_statement_fact(project_id, project_name, sql_stmt));
    }
    for table in &sql_analysis.tables {
        semantic_facts.push(sql_table_fact(project_id, project_name, table));
    }
    for column in &sql_analysis.columns {
        semantic_facts.push(sql_column_fact(project_id, project_name, column));
    }
    for join in &sql_analysis.joins {
        semantic_facts.push(sql_join_fact(project_id, project_name, join));
    }
    for param in &sql_analysis.parameters {
        semantic_facts.push(sql_parameter_fact(project_id, project_name, param));
    }
}

fn absolute(path: &str) -> String {
    let path_buf = std::path::PathBuf::from(path);
    if path_buf.is_absolute() {
        normalize_absolute(&path_buf).to_string_lossy().to_string()
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
        normalize_absolute(&cwd.join(path_buf)).to_string_lossy().to_string()
    }
}

fn normalize_absolute(path: &std::path::Path) -> std::path::PathBuf {
    let mut out = std::path::PathBuf::from("/");
    for component in path.components() {
        match component {
            std::path::Component::RootDir | std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

// ── fact builders (pipeline.py) ─────────────────────────────────────────────

fn fact_base(
    kind: &str,
    stable_id: String,
    name: String,
    source: SourceSpan,
    project_id: &str,
    project_name: &str,
) -> MyBatisFact {
    MyBatisFact {
        kind: kind.to_string(),
        stable_id,
        name,
        source,
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        language: "mybatis".to_string(),
        confidence: 1.0,
        extraction_method: "mybatis_foundation".to_string(),
        resolution_status: "resolved".to_string(),
        raw_value: String::new(),
        resolved_value: String::new(),
        source_symbol_id: String::new(),
        properties: Vec::new(),
    }
}

fn string_list_value(items: &[String]) -> Value {
    Value::Array(items.iter().map(|item| json!(item)).collect())
}

fn string_map_value(map: &std::collections::BTreeMap<String, String>) -> Value {
    let mut obj = serde_json::Map::new();
    for (key, value) in map {
        obj.insert(key.clone(), json!(value));
    }
    Value::Object(obj)
}

fn string_map_list_value(maps: &[std::collections::BTreeMap<String, String>]) -> Value {
    Value::Array(maps.iter().map(string_map_value).collect())
}

/// `_module_fact`.
fn module_fact(project_id: &str, project_name: &str, module: &Module) -> MyBatisFact {
    let stable_module_raw = module.rel_path.replace('/', ".");
    let stable_module_trimmed = safe_rel_path(&stable_module_raw);
    let stable_module = if stable_module_trimmed.is_empty() {
        "root"
    } else {
        &stable_module_trimmed
    };
    let source_file = [&module.mapper_xml_files, &module.config_xml_files, &module.java_files, &module.build_files]
        .iter()
        .find(|group| !group.is_empty())
        .map(|group| group[0].clone())
        .unwrap_or_else(|| ".".to_string());
    let mut fact = fact_base(
        "MyBatisModule",
        format!("mybatis_module::{project_id}::{stable_module}"),
        if module.rel_path.is_empty() {
            ".".to_string()
        } else {
            module.rel_path.clone()
        },
        SourceSpan::new(&source_file),
        project_id,
        project_name,
    );
    fact.confidence = module.confidence;
    fact.properties = vec![
        ("module_path".to_string(), json!(module.rel_path)),
        ("mapper_xml_files".to_string(), string_list_value(&module.mapper_xml_files)),
        ("config_xml_files".to_string(), string_list_value(&module.config_xml_files)),
        ("java_files".to_string(), string_list_value(&module.java_files)),
        ("build_files".to_string(), string_list_value(&module.build_files)),
        (
            "spring_config_files".to_string(),
            string_list_value(&module.spring_config_files),
        ),
        ("evidence".to_string(), string_list_value(&module.evidence)),
    ];
    fact
}

/// `_artifact_fact`.
fn artifact_fact(project_id: &str, project_name: &str, artifact: &Artifact) -> MyBatisFact {
    let digest = crate::pyutil::sha1_hex(
        format!("{}:{}", artifact.kind, safe_rel_path(&artifact.file_path)).as_bytes(),
    )[..16]
        .to_string();
    let name = artifact
        .file_path
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(&artifact.file_path)
        .to_string();
    let mut fact = fact_base(
        "MyBatisArtifact",
        format!("mybatis_artifact::{project_id}::{digest}"),
        if name.is_empty() {
            artifact.kind.clone()
        } else {
            name
        },
        artifact.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = artifact.confidence;
    fact.resolution_status = if artifact.evidence.is_empty() {
        "unresolved".to_string()
    } else {
        "resolved".to_string()
    };
    fact.properties = vec![
        ("artifact_kind".to_string(), json!(artifact.kind)),
        ("module_path".to_string(), json!(artifact.module_path)),
        ("evidence".to_string(), string_list_value(&artifact.evidence)),
    ];
    fact
}

/// `_mapper_interface_fact`.
fn mapper_interface_fact(
    project_id: &str,
    project_name: &str,
    mapper: &MapperInterfaceFact,
) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisMapper",
        mapper.stable_id.clone(),
        mapper.fqcn.clone(),
        mapper.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_interface".to_string();
    fact.source_symbol_id = mapper.java_class_symbol_id.clone();
    fact.properties = vec![
        ("namespace".to_string(), json!(mapper.fqcn)),
        ("interface_fqcn".to_string(), json!(mapper.fqcn)),
        ("package_name".to_string(), json!(mapper.package_name)),
        (
            "extended_interfaces".to_string(),
            string_list_value(&mapper.extended_interfaces),
        ),
        (
            "type_parameters".to_string(),
            string_list_value(&mapper.type_parameters),
        ),
        ("modifiers".to_string(), string_list_value(&mapper.modifiers)),
        (
            "annotations".to_string(),
            Value::Array(
                mapper
                    .annotations
                    .iter()
                    .map(|item| json!(item.resolved_name))
                    .collect(),
            ),
        ),
    ];
    fact
}

/// `_mapper_method_fact`.
fn mapper_method_fact(project_id: &str, project_name: &str, method: &MapperMethodFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisMapperMethod",
        method.stable_id.clone(),
        method.name.clone(),
        method.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_interface".to_string();
    fact.resolution_status = method.ambiguity_status.clone();
    fact.source_symbol_id = method.java_symbol_id.clone();
    fact.properties = vec![
        ("mapper_fqcn".to_string(), json!(method.mapper_fqcn)),
        ("signature".to_string(), json!(method.signature)),
        ("return_type".to_string(), json!(method.return_type)),
        ("parameter_types".to_string(), string_list_value(&method.parameter_types)),
        ("bindable".to_string(), json!(method.bindable)),
        ("overload_count".to_string(), json!(method.overload_count)),
        ("modifiers".to_string(), string_list_value(&method.modifiers)),
        ("throws".to_string(), string_list_value(&method.throws)),
        (
            "annotations".to_string(),
            Value::Array(
                method
                    .annotations
                    .iter()
                    .map(|item| json!(item.resolved_name))
                    .collect(),
            ),
        ),
        ("has_body".to_string(), json!(method.has_body)),
    ];
    fact
}

/// `_mapper_parameter_fact`.
fn mapper_parameter_fact(
    project_id: &str,
    project_name: &str,
    param: &MapperParameterFact,
) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisParameter",
        param.stable_id.clone(),
        param.name.clone(),
        param.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_interface".to_string();
    fact.properties = vec![
        ("mapper_method_id".to_string(), json!(param.mapper_method_id)),
        ("position".to_string(), json!(param.position)),
        ("java_type".to_string(), json!(param.java_type)),
        ("canonical_type".to_string(), json!(param.canonical_type)),
        ("param_alias".to_string(), json!(param.param_alias)),
        ("special_role".to_string(), json!(param.special_role)),
        (
            "annotations".to_string(),
            Value::Array(
                param
                    .annotations
                    .iter()
                    .map(|item| json!(item.resolved_name))
                    .collect(),
            ),
        ),
    ];
    fact
}

/// `_java_property_fact`.
fn java_property_fact(project_id: &str, project_name: &str, prop: &JavaPropertyFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisJavaProperty",
        prop.stable_id.clone(),
        prop.property_name.clone(),
        prop.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_java_type_index".to_string();
    fact.source_symbol_id = prop.source_symbol_id.clone();
    fact.properties = vec![
        ("java_type_fqcn".to_string(), json!(prop.java_type_fqcn)),
        ("property_name".to_string(), json!(prop.property_name)),
        ("property_type".to_string(), json!(prop.property_type)),
        ("source_kind".to_string(), json!(prop.source_kind)),
        ("readable".to_string(), json!(prop.readable)),
        ("writable".to_string(), json!(prop.writable)),
    ];
    fact
}

/// `_xml_document_fact`.
fn xml_document_fact(project_id: &str, project_name: &str, doc: &XmlDocumentFact) -> MyBatisFact {
    let digest = crate::pyutil::sha1_hex(doc.file_path.as_bytes())[..16].to_string();
    let mut fact = fact_base(
        "MyBatisXmlDocument",
        format!("mybatis_xml_document::{project_id}::{digest}"),
        doc.file_path.clone(),
        doc.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_xml".to_string();
    fact.resolution_status = doc.parser_status.clone();
    fact.properties = vec![
        ("document_kind".to_string(), json!(doc.document_kind)),
        ("root_tag".to_string(), json!(doc.root_tag)),
        ("namespace".to_string(), json!(doc.namespace)),
        ("doctype".to_string(), json!(doc.doctype)),
    ];
    fact
}

/// `_statement_fact`.
fn statement_fact(
    project_id: &str,
    project_name: &str,
    stmt: &StatementFact,
    extraction_method: &str,
) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisStatement",
        stmt.stable_id.clone(),
        stmt.statement_id.clone(),
        stmt.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = extraction_method.to_string();
    fact.resolution_status = stmt.parser_status.clone();
    fact.raw_value = stmt.raw_body.clone();
    fact.resolved_value = stmt.expanded_body.clone();
    fact.properties = vec![
        ("namespace".to_string(), json!(stmt.namespace)),
        ("statement_id".to_string(), json!(stmt.statement_id)),
        ("statement_kind".to_string(), json!(stmt.statement_kind)),
        ("database_id".to_string(), json!(stmt.database_id)),
        ("attributes".to_string(), string_map_value(&stmt.attributes)),
    ];
    fact
}

/// `_fragment_fact`.
fn fragment_fact(project_id: &str, project_name: &str, fragment: &SqlFragmentFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisSqlFragment",
        fragment.stable_id.clone(),
        fragment.fragment_id.clone(),
        fragment.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_xml".to_string();
    fact.raw_value = fragment.raw_body.clone();
    fact.resolved_value = fragment.expanded_body.clone();
    fact.properties = vec![
        ("namespace".to_string(), json!(fragment.namespace)),
        ("fragment_id".to_string(), json!(fragment.fragment_id)),
        ("database_id".to_string(), json!(fragment.database_id)),
        ("attributes".to_string(), string_map_value(&fragment.attributes)),
    ];
    fact
}

/// `_result_map_fact`.
fn result_map_fact(
    project_id: &str,
    project_name: &str,
    result_map: &ResultMapFact,
    extraction_method: &str,
) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisResultMap",
        result_map.stable_id.clone(),
        result_map.result_map_id.clone(),
        result_map.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = extraction_method.to_string();
    fact.properties = vec![
        ("namespace".to_string(), json!(result_map.namespace)),
        ("result_map_id".to_string(), json!(result_map.result_map_id)),
        ("java_type".to_string(), json!(result_map.java_type)),
        ("extends".to_string(), json!(result_map.extends)),
        ("auto_mapping".to_string(), json!(result_map.auto_mapping)),
    ];
    fact
}

/// `_result_mapping_fact`.
fn result_mapping_fact(
    project_id: &str,
    project_name: &str,
    mapping: &ResultMappingFact,
    extraction_method: &str,
) -> MyBatisFact {
    let name = if !mapping.property_name.is_empty() {
        mapping.property_name.clone()
    } else if !mapping.column.is_empty() {
        mapping.column.clone()
    } else {
        mapping.mapping_kind.clone()
    };
    let mut fact = fact_base(
        "MyBatisResultMapping",
        mapping.stable_id.clone(),
        name,
        mapping.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = extraction_method.to_string();
    fact.properties = vec![
        ("result_map_id".to_string(), json!(mapping.result_map_id)),
        ("mapping_kind".to_string(), json!(mapping.mapping_kind)),
        ("property_name".to_string(), json!(mapping.property_name)),
        ("column".to_string(), json!(mapping.column)),
        ("java_type".to_string(), json!(mapping.java_type)),
        ("jdbc_type".to_string(), json!(mapping.jdbc_type)),
        ("nested_select".to_string(), json!(mapping.nested_select)),
        (
            "nested_result_map".to_string(),
            json!(mapping.nested_result_map),
        ),
        ("attributes".to_string(), string_map_value(&mapping.attributes)),
    ];
    fact
}

/// `_include_fact`.
fn include_fact(project_id: &str, project_name: &str, include: &IncludeFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisInclude",
        include.stable_id.clone(),
        include.refid.clone(),
        include.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_mapper_xml".to_string();
    fact.resolution_status = include.resolution_status.clone();
    fact.properties = vec![
        ("owner_id".to_string(), json!(include.owner_id)),
        ("refid".to_string(), json!(include.refid)),
        ("resolved_refid".to_string(), json!(include.resolved_refid)),
        ("properties".to_string(), string_map_value(&include.properties)),
    ];
    fact
}

/// `_dynamic_node_fact`.
fn dynamic_node_fact(project_id: &str, project_name: &str, dynamic: &DynamicNodeFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisDynamicNode",
        dynamic.stable_id.clone(),
        dynamic.tag.clone(),
        dynamic.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_dynamic_sql".to_string();
    fact.raw_value = dynamic.text.clone();
    fact.properties = vec![
        ("owner_id".to_string(), json!(dynamic.owner_id)),
        ("tag".to_string(), json!(dynamic.tag)),
        ("node_kind".to_string(), json!(dynamic.node_kind)),
        ("order".to_string(), json!(dynamic.order)),
        ("attributes".to_string(), string_map_value(&dynamic.attributes)),
        ("test".to_string(), json!(dynamic.test)),
        ("branch_role".to_string(), json!(dynamic.branch_role)),
        (
            "referenced_variables".to_string(),
            string_list_value(&dynamic.referenced_variables),
        ),
    ];
    fact
}

/// `_config_fact`.
fn config_fact(project_id: &str, project_name: &str, config: &ConfigFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisConfig",
        config.stable_id.clone(),
        config.file_path.clone(),
        config.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_config_xml".to_string();
    fact.properties = vec![
        ("properties".to_string(), string_map_value(&config.properties)),
        ("settings".to_string(), string_map_value(&config.settings)),
        ("type_aliases".to_string(), string_map_value(&config.type_aliases)),
        (
            "type_handlers".to_string(),
            string_map_list_value(&config.type_handlers),
        ),
        ("plugins".to_string(), string_map_list_value(&config.plugins)),
        (
            "environments".to_string(),
            string_map_list_value(&config.environments),
        ),
        (
            "database_id_provider".to_string(),
            string_map_value(&config.database_id_provider),
        ),
        (
            "mapper_registrations".to_string(),
            string_map_list_value(&config.mapper_registrations),
        ),
    ];
    fact
}

/// `_provider_fact`.
fn provider_fact(project_id: &str, project_name: &str, provider: &ProviderFact) -> MyBatisFact {
    let name = if !provider.provider_type.is_empty() {
        provider.provider_type.clone()
    } else {
        provider.statement_id.clone()
    };
    let mut fact = fact_base(
        "MyBatisSqlProvider",
        provider.stable_id.clone(),
        name,
        provider.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = 0.55;
    fact.extraction_method = "mybatis_annotation_mapper".to_string();
    fact.resolution_status = provider.resolution_status.clone();
    fact.raw_value = provider.raw_arguments.clone();
    fact.properties = vec![
        ("mapper_method_id".to_string(), json!(provider.mapper_method_id)),
        ("namespace".to_string(), json!(provider.namespace)),
        ("statement_id".to_string(), json!(provider.statement_id)),
        ("provider_kind".to_string(), json!(provider.provider_kind)),
        ("provider_type".to_string(), json!(provider.provider_type)),
        (
            "provider_method".to_string(),
            json!(provider.provider_method),
        ),
        ("attributes".to_string(), string_map_value(&provider.attributes)),
    ];
    fact
}

/// `_spring_bridge_fact`.
fn spring_bridge_fact(project_id: &str, project_name: &str, bridge: &SpringBridgeFact) -> MyBatisFact {
    let name = if !bridge.name.is_empty() {
        bridge.name.clone()
    } else {
        bridge.bridge_kind.clone()
    };
    let mut fact = fact_base(
        "MyBatisSpringBridge",
        bridge.stable_id.clone(),
        name,
        bridge.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = 0.7;
    fact.extraction_method = "mybatis_optional_bridge".to_string();
    fact.resolution_status = bridge.resolution_status.clone();
    fact.resolved_value = bridge.target.clone();
    fact.properties = vec![
        ("bridge_kind".to_string(), json!(bridge.bridge_kind)),
        ("target".to_string(), json!(bridge.target)),
        ("attributes".to_string(), string_map_value(&bridge.attributes)),
    ];
    fact
}

/// `_extension_fact`.
fn extension_fact(project_id: &str, project_name: &str, extension: &ExtensionFact) -> MyBatisFact {
    let name = if !extension.name.is_empty() {
        extension.name.clone()
    } else {
        extension.extension_kind.clone()
    };
    let mut fact = fact_base(
        "MyBatisExtension",
        extension.stable_id.clone(),
        name,
        extension.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = 0.75;
    fact.extraction_method = "mybatis_optional_extension".to_string();
    fact.resolution_status = extension.resolution_status.clone();
    fact.resolved_value = extension.java_type.clone();
    fact.properties = vec![
        ("extension_kind".to_string(), json!(extension.extension_kind)),
        ("java_type".to_string(), json!(extension.java_type)),
        ("attributes".to_string(), string_map_value(&extension.attributes)),
    ];
    fact
}

/// `_cache_fact`.
fn cache_fact(project_id: &str, project_name: &str, cache: &CacheFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisCache",
        cache.stable_id.clone(),
        cache.namespace.clone(),
        cache.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = 0.8;
    fact.extraction_method = "mybatis_optional_extension".to_string();
    fact.resolution_status = cache.resolution_status.clone();
    fact.resolved_value = cache.target_namespace.clone();
    fact.properties = vec![
        ("namespace".to_string(), json!(cache.namespace)),
        ("cache_kind".to_string(), json!(cache.cache_kind)),
        (
            "target_namespace".to_string(),
            json!(cache.target_namespace),
        ),
        ("attributes".to_string(), string_map_value(&cache.attributes)),
    ];
    fact
}

/// `_sql_statement_fact`.
fn sql_statement_fact(
    project_id: &str,
    project_name: &str,
    stmt: &SqlStatementSemanticFact,
) -> MyBatisFact {
    let name = if !stmt.crud.is_empty() {
        stmt.crud.clone()
    } else if !stmt.xml_statement_kind.is_empty() {
        stmt.xml_statement_kind.clone()
    } else {
        "sql".to_string()
    };
    let mut fact = fact_base(
        "MyBatisSqlStatement",
        stmt.stable_id.clone(),
        name,
        stmt.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = stmt.confidence;
    fact.extraction_method = "mybatis_sql_semantic".to_string();
    fact.resolution_status = stmt.parser_status.clone();
    fact.raw_value = stmt.raw_sql.clone();
    fact.resolved_value = stmt.normalized_sql.clone();
    fact.properties = vec![
        (
            "owner_statement_id".to_string(),
            json!(stmt.owner_statement_id),
        ),
        ("crud".to_string(), json!(stmt.crud)),
        ("xml_statement_kind".to_string(), json!(stmt.xml_statement_kind)),
        ("database_id".to_string(), json!(stmt.database_id)),
        (
            "parser_error_count".to_string(),
            json!(stmt.parser_error_count),
        ),
        (
            "has_textual_substitution".to_string(),
            json!(stmt.has_textual_substitution),
        ),
    ];
    fact
}

/// `_sql_table_fact`.
fn sql_table_fact(project_id: &str, project_name: &str, table: &SqlTableFact) -> MyBatisFact {
    let mut fact = fact_base(
        "DatabaseTable",
        table.stable_id.clone(),
        table.raw_name.clone(),
        table.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = if table.is_dynamic { 0.7 } else { 1.0 };
    fact.extraction_method = "mybatis_sql_semantic".to_string();
    fact.resolution_status = table.resolution_status.clone();
    fact.properties = vec![
        ("sql_statement_id".to_string(), json!(table.sql_statement_id)),
        ("raw_name".to_string(), json!(table.raw_name)),
        ("normalized_name".to_string(), json!(table.normalized_name)),
        ("role".to_string(), json!(table.role)),
        ("alias".to_string(), json!(table.alias)),
        ("catalog".to_string(), json!(table.catalog)),
        ("schema".to_string(), json!(table.schema)),
        ("is_cte".to_string(), json!(table.is_cte)),
        ("is_dynamic".to_string(), json!(table.is_dynamic)),
        (
            "dynamic_node_ids".to_string(),
            string_list_value(&table.dynamic_node_ids),
        ),
        ("branch_roles".to_string(), string_list_value(&table.branch_roles)),
    ];
    fact
}

/// `_sql_column_fact`.
fn sql_column_fact(project_id: &str, project_name: &str, column: &SqlColumnFact) -> MyBatisFact {
    let mut fact = fact_base(
        "DatabaseColumn",
        column.stable_id.clone(),
        column.raw_name.clone(),
        column.source.clone(),
        project_id,
        project_name,
    );
    fact.confidence = 0.75;
    fact.extraction_method = "mybatis_sql_semantic".to_string();
    fact.resolution_status = column.resolution_status.clone();
    fact.raw_value = column.expression.clone();
    fact.properties = vec![
        ("sql_statement_id".to_string(), json!(column.sql_statement_id)),
        ("raw_name".to_string(), json!(column.raw_name)),
        ("normalized_name".to_string(), json!(column.normalized_name)),
        ("role".to_string(), json!(column.role)),
        ("qualifier".to_string(), json!(column.qualifier)),
        ("table_ref".to_string(), json!(column.table_ref)),
        (
            "dynamic_node_ids".to_string(),
            string_list_value(&column.dynamic_node_ids),
        ),
        ("branch_roles".to_string(), string_list_value(&column.branch_roles)),
    ];
    fact
}

/// `_sql_join_fact`.
fn sql_join_fact(project_id: &str, project_name: &str, join: &SqlJoinFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisSqlJoin",
        join.stable_id.clone(),
        join.right_table.clone(),
        join.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_sql_semantic".to_string();
    fact.resolution_status = join.resolution_status.clone();
    fact.raw_value = join.condition.clone();
    fact.properties = vec![
        ("sql_statement_id".to_string(), json!(join.sql_statement_id)),
        ("join_type".to_string(), json!(join.join_type)),
        ("right_table".to_string(), json!(join.right_table)),
        ("right_alias".to_string(), json!(join.right_alias)),
        (
            "dynamic_node_ids".to_string(),
            string_list_value(&join.dynamic_node_ids),
        ),
        ("branch_roles".to_string(), string_list_value(&join.branch_roles)),
    ];
    fact
}

/// `_sql_parameter_fact`.
fn sql_parameter_fact(project_id: &str, project_name: &str, param: &SqlParameterFact) -> MyBatisFact {
    let mut fact = fact_base(
        "MyBatisSqlParameter",
        param.stable_id.clone(),
        param.name.clone(),
        param.source.clone(),
        project_id,
        project_name,
    );
    fact.extraction_method = "mybatis_sql_semantic".to_string();
    fact.resolution_status = param.parameter_kind.clone();
    fact.properties = vec![
        ("sql_statement_id".to_string(), json!(param.sql_statement_id)),
        ("token".to_string(), json!(param.token)),
        ("parameter_kind".to_string(), json!(param.parameter_kind)),
        ("options".to_string(), string_map_value(&param.options)),
        ("position".to_string(), json!(param.position)),
        (
            "dynamic_node_ids".to_string(),
            string_list_value(&param.dynamic_node_ids),
        ),
        ("branch_roles".to_string(), string_list_value(&param.branch_roles)),
    ];
    fact
}
