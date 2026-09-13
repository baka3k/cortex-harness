//! Node upsert query catalog — port nguyên văn các Cypher template của
//! `language_writer.py`. Mỗi hàm trả đúng chuỗi Python dùng (chỉ khác
//! indentation, semantics identical) để dual-write diff không lệch property.

/// `write_files` (default variant).
pub const WRITE_FILES: &str = r#"
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            "#;

/// `write_files_with_imports` (JS/TS/PHP/Python).
pub const WRITE_FILES_WITH_IMPORTS: &str = r#"
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.imports = row.imports,
                f.exports = row.exports,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            "#;

/// `write_files_jsx`.
pub const WRITE_FILES_JSX: &str = r#"
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.imports = row.imports,
                f.exports = row.exports,
                f.jsx_tags = row.jsx_tags,
                f.jsx_components = row.jsx_components,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            "#;

/// `write_files_with_package` (Java/Kotlin/Android).
pub const WRITE_FILES_WITH_PACKAGE: &str = r#"
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.package_name = row.package_name,
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            "#;

/// `write_classes_full`.
pub const WRITE_CLASSES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (c:Class {id: row.id})
            SET c.name = row.name,
                c.node_type = 'code',
                c.qualified_name = row.qualified_name,
                c.kind = row.kind,
                c.package_name = row.package_name,
                c.file_path = row.file_path,
                c.start_line = row.start_line,
                c.end_line = row.end_line,
                c.code = row.code,
                c.comment = row.comment,
                c.summary = row.summary,
                c.note = row.note,
                c.visibility = coalesce(row.visibility, 'unknown'),
                c.is_public_api = coalesce(row.is_public_api, false),
                c.visibility_source = coalesce(row.visibility_source, ''),
                c.export_evidence = coalesce(row.export_evidence, ''),
                c.signature = coalesce(row.signature, ''),
                c.project_id = row.project_id,
                c.project_id_normalized = row.project_id_normalized,
                c.project_name = row.project_name,
                c.language = row.language,
                c.repo = row.repo,
                c.build_system = row.build_system,
                c.updated_at = datetime()
            RETURN count(c) as count
            "#;

/// `write_types_full` — CASE file_path là min-lexicographic provenance giữ
/// nguyên chữ Python.
pub const WRITE_TYPES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (t:Type {id: row.id})
            SET t.name = row.name,
                t.qualified_name = row.qualified_name,
                t.kind = row.kind,
                t.file_path = CASE
                    WHEN coalesce(t.file_path, '') = '' THEN row.file_path
                    WHEN coalesce(row.file_path, '') = '' THEN t.file_path
                    WHEN row.file_path < t.file_path THEN row.file_path
                    ELSE t.file_path
                END,
                t.start_line = row.start_line,
                t.end_line = row.end_line,
                t.code = row.code,
                t.comment = row.comment,
                t.summary = row.summary,
                t.note = row.note,
                t.exported = coalesce(row.exported, false),
                t.project_id = row.project_id,
                t.project_id_normalized = row.project_id_normalized,
                t.project_name = row.project_name,
                t.language = row.language,
                t.repo = row.repo,
                t.build_system = row.build_system,
                t.updated_at = datetime()
            RETURN count(t) as count
            "#;

/// `write_functions_full` — supports JVM-style + JS/TS-style columns.
pub const WRITE_FUNCTIONS_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (f:Function {id: row.id})
            SET f.name = row.name,
                f.node_type = 'code',
                f.qualified_name = row.qualified_name,
                f.kind = row.kind,
                f.class_name = row.class_name,
                f.package_name = row.package_name,
                f.scope_name = row.scope_name,
                f.file_path = row.file_path,
                f.start_byte = row.start_byte,
                f.end_byte = row.end_byte,
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.arity = row.arity,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.exported = coalesce(row.exported, false),
                f.visibility = coalesce(row.visibility, 'unknown'),
                f.is_public_api = coalesce(row.is_public_api, false),
                f.visibility_source = coalesce(row.visibility_source, ''),
                f.export_evidence = coalesce(row.export_evidence, ''),
                f.signature = coalesce(row.signature, ''),
                f.external = coalesce(row.external, false),
                f.builtin = coalesce(row.builtin, false),
                f.react_role = coalesce(row.react_role, ''),
                f.middleware_kind = coalesce(row.middleware_kind, ''),
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            "#;

/// `write_projects`.
pub const WRITE_PROJECTS: &str = r#"
            UNWIND $rows AS row
            MERGE (p:Project {project_id: row.id})
            SET p.name = row.name,
                p.node_type = 'code',
                p.language = row.language,
                p.repo = row.repo,
                p.root = row.root,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            "#;

/// `write_packages_full`.
pub const WRITE_PACKAGES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (p:Package {id: row.id})
            SET p.name = row.name,
                p.start_line = row.start_line,
                p.end_line = row.end_line,
                p.code = row.code,
                p.comment = row.comment,
                p.summary = row.summary,
                p.note = row.note,
                p.project_id = row.project_id,
                p.project_id_normalized = row.project_id_normalized,
                p.project_name = row.project_name,
                p.language = row.language,
                p.repo = row.repo,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            "#;

/// `write_namespaces_full`.
pub const WRITE_NAMESPACES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (n:Namespace {id: row.id})
            SET n.name = row.name,
                n.qualified_name = row.qualified_name,
                n.file_path = row.file_path,
                n.start_line = row.start_line,
                n.end_line = row.end_line,
                n.code = row.code,
                n.comment = row.comment,
                n.summary = row.summary,
                n.note = row.note,
                n.project_id = row.project_id,
                n.project_id_normalized = row.project_id_normalized,
                n.project_name = row.project_name,
                n.language = row.language,
                n.repo = row.repo,
                n.build_system = row.build_system,
                n.updated_at = datetime()
            RETURN count(n) as count
            "#;

/// `write_function_types` (C++ typedef/using function signatures).
pub const WRITE_FUNCTION_TYPES: &str = r#"
            UNWIND $rows AS row
            MERGE (ft:FunctionType {id: row.id})
            SET ft.type_signature        = row.type_signature,
                ft.file_path             = row.file_path,
                ft.start_line            = row.start_line,
                ft.end_line              = row.end_line,
                ft.code                  = row.code,
                ft.project_id            = row.project_id,
                ft.project_id_normalized = row.project_id_normalized,
                ft.project_name          = row.project_name,
                ft.language              = row.language,
                ft.repo                  = row.repo,
                ft.build_system          = row.build_system
            RETURN count(ft) AS count
            "#;

/// `write_fields` (C++ member variables).
pub const WRITE_FIELDS: &str = r#"
            UNWIND $rows AS row
            MERGE (f:Field {id: row.id})
            SET f.name                  = row.name,
                f.qualified_name        = row.qualified_name,
                f.scope_name            = row.scope_name,
                f.type_signature        = row.type_signature,
                f.file_path             = row.file_path,
                f.start_line            = row.start_line,
                f.end_line              = row.end_line,
                f.code                  = row.code,
                f.project_id            = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name          = row.project_name,
                f.language              = row.language,
                f.repo                  = row.repo,
                f.build_system          = row.build_system
            RETURN count(f) AS count
            "#;

/// `write_aliases` (C++ typedef/using).
pub const WRITE_ALIASES: &str = r#"
            UNWIND $rows AS row
            MERGE (a:Alias {id: row.id})
            SET a.name                  = row.name,
                a.qualified_name        = row.qualified_name,
                a.kind                  = row.kind,
                a.target_name           = row.target_name,
                a.file_path             = row.file_path,
                a.start_line            = row.start_line,
                a.end_line              = row.end_line,
                a.code                  = row.code,
                a.project_id            = row.project_id,
                a.project_id_normalized = row.project_id_normalized,
                a.project_name          = row.project_name,
                a.language              = row.language,
                a.repo                  = row.repo,
                a.build_system          = row.build_system
            RETURN count(a) AS count
            "#;

/// `write_templates` (C++).
pub const WRITE_TEMPLATES: &str = r#"
            UNWIND $rows AS row
            MERGE (t:Template {id: row.id})
            SET t.name                  = row.name,
                t.file_path             = row.file_path,
                t.start_line            = row.start_line,
                t.end_line              = row.end_line,
                t.code                  = row.code,
                t.project_id            = row.project_id,
                t.project_id_normalized = row.project_id_normalized,
                t.project_name          = row.project_name,
                t.language              = row.language,
                t.repo                  = row.repo,
                t.build_system          = row.build_system
            RETURN count(t) AS count
            "#;

/// `write_properties_full` (VB.NET).
pub const WRITE_PROPERTIES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (p:Property {id: row.id})
            SET p.name = row.name,
                p.qualified_name = row.qualified_name,
                p.kind = row.kind,
                p.scope_name = row.scope_name,
                p.class_name = row.class_name,
                p.package_name = row.package_name,
                p.file_path = row.file_path,
                p.start_line = row.start_line,
                p.end_line = row.end_line,
                p.parameters = row.parameters,
                p.return_type = row.return_type,
                p.code = row.code,
                p.comment = row.comment,
                p.summary = row.summary,
                p.note = row.note,
                p.exported = row.exported,
                p.project_id = row.project_id,
                p.project_id_normalized = row.project_id_normalized,
                p.project_name = row.project_name,
                p.language = row.language,
                p.repo = row.repo,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            "#;

/// `write_events_full` (VB.NET).
pub const WRITE_EVENTS_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (e:Event {id: row.id})
            SET e.name = row.name,
                e.qualified_name = row.qualified_name,
                e.kind = row.kind,
                e.scope_name = row.scope_name,
                e.class_name = row.class_name,
                e.package_name = row.package_name,
                e.file_path = row.file_path,
                e.start_line = row.start_line,
                e.end_line = row.end_line,
                e.parameters = row.parameters,
                e.code = row.code,
                e.comment = row.comment,
                e.summary = row.summary,
                e.note = row.note,
                e.exported = row.exported,
                e.project_id = row.project_id,
                e.project_id_normalized = row.project_id_normalized,
                e.project_name = row.project_name,
                e.language = row.language,
                e.repo = row.repo,
                e.build_system = row.build_system,
                e.updated_at = datetime()
            RETURN count(e) as count
            "#;

/// `write_interfaces_full` (VB.NET).
pub const WRITE_INTERFACES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (i:Interface {id: row.id})
            SET i.name = row.name,
                i.qualified_name = row.qualified_name,
                i.kind = row.kind,
                i.file_path = row.file_path,
                i.start_line = row.start_line,
                i.end_line = row.end_line,
                i.base_interfaces = row.base_interfaces,
                i.code = row.code,
                i.comment = row.comment,
                i.summary = row.summary,
                i.note = row.note,
                i.project_id = row.project_id,
                i.project_id_normalized = row.project_id_normalized,
                i.project_name = row.project_name,
                i.language = row.language,
                i.repo = row.repo,
                i.build_system = row.build_system,
                i.updated_at = datetime()
            RETURN count(i) as count
            "#;

/// `write_enums_full` (VB.NET/VB6/VBA).
pub const WRITE_ENUMS_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (e:Enum {id: row.id})
            SET e.name = row.name,
                e.qualified_name = row.qualified_name,
                e.kind = row.kind,
                e.scope_name = row.scope_name,
                e.class_name = row.class_name,
                e.package_name = row.package_name,
                e.file_path = row.file_path,
                e.start_line = row.start_line,
                e.end_line = row.end_line,
                e.members = row.members,
                e.code = row.code,
                e.comment = row.comment,
                e.summary = row.summary,
                e.note = row.note,
                e.project_id = row.project_id,
                e.project_id_normalized = row.project_id_normalized,
                e.project_name = row.project_name,
                e.language = row.language,
                e.repo = row.repo,
                e.build_system = row.build_system,
                e.updated_at = datetime()
            RETURN count(e) as count
            "#;

/// `write_constants_full` (VB6/VBA).
pub const WRITE_CONSTANTS_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (c:Constant {id: row.id})
            SET c.name = row.name,
                c.qualified_name = row.qualified_name,
                c.kind = row.kind,
                c.scope_name = row.scope_name,
                c.class_name = row.class_name,
                c.package_name = row.package_name,
                c.file_path = row.file_path,
                c.line_number = row.line_number,
                c.value = row.value,
                c.type_name = row.type_name,
                c.code = row.code,
                c.comment = row.comment,
                c.summary = row.summary,
                c.note = row.note,
                c.project_id = row.project_id,
                c.project_id_normalized = row.project_id_normalized,
                c.project_name = row.project_name,
                c.language = row.language,
                c.repo = row.repo,
                c.build_system = row.build_system,
                c.updated_at = datetime()
            RETURN count(c) as count
            "#;

/// `write_variables_full` (VB6/VBA).
pub const WRITE_VARIABLES_FULL: &str = r#"
            UNWIND $rows AS row
            MERGE (v:Variable {id: row.id})
            SET v.name = row.name,
                v.qualified_name = row.qualified_name,
                v.kind = row.kind,
                v.scope_name = row.scope_name,
                v.class_name = row.class_name,
                v.package_name = row.package_name,
                v.file_path = row.file_path,
                v.line_number = row.line_number,
                v.type_name = row.type_name,
                v.is_global = row.is_global,
                v.is_shared = row.is_shared,
                v.code = row.code,
                v.comment = row.comment,
                v.summary = row.summary,
                v.note = row.note,
                v.project_id = row.project_id,
                v.project_id_normalized = row.project_id_normalized,
                v.project_name = row.project_name,
                v.language = row.language,
                v.repo = row.repo,
                v.build_system = row.build_system,
                v.updated_at = datetime()
            RETURN count(v) as count
            "#;

/// `write_calls` (CALLS upsert sau dedupe).
pub const WRITE_CALLS: &str = r#"
            UNWIND $rows AS row
            MATCH (caller:Function {id: row.caller_id,
                                    project_id_normalized: row.project_id_normalized})
            MATCH (callee:Function {id: row.callee_id,
                                    project_id_normalized: row.project_id_normalized})
            MERGE (caller)-[r:CALLS]->(callee)
            SET r.count                = row.count,
                r.call_type            = row.call_type,
                r.project_id           = row.project_id,
                r.project_id_normalized = row.project_id_normalized,
                r.updated_at           = datetime()
            RETURN count(r) AS count
            "#;

/// `write_repo_file_edges`.
pub const WRITE_REPO_FILE_EDGES: &str = r#"
            UNWIND $rows AS row
            MATCH (r:Repository {name: row.repo,
                                 project_id_normalized: row.project_id_normalized})
            MATCH (f:File {id: row.id,
                           project_id_normalized: row.project_id_normalized})
            MERGE (r)-[:HAS_FILE]->(f)
            RETURN count(f) AS count
            "#;

/// `write_calls_with_site`.
pub const WRITE_CALLS_WITH_SITE: &str = r#"
            UNWIND $rows AS row
            CALL {
                WITH row
                MATCH (caller:Function {id: row.caller_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN caller
                LIMIT 1
            }
            CALL {
                WITH row
                MATCH (callee:Function {id: row.callee_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN callee
                LIMIT 1
            }
            MERGE (caller)-[r:CALLS {site_id: row.site_id}]->(callee)
            SET r += row.props
            RETURN count(r) as count
            "#;

/// `write_possible_calls_with_site`.
pub const WRITE_POSSIBLE_CALLS_WITH_SITE: &str = r#"
            UNWIND $rows AS row
            CALL {
                WITH row
                MATCH (caller:Function {id: row.caller_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN caller
                LIMIT 1
            }
            CALL {
                WITH row
                MATCH (callee:Function {id: row.callee_id,
                                        project_id_normalized: row.project_id_normalized})
                RETURN callee
                LIMIT 1
            }
            MERGE (caller)-[r:POSSIBLE_CALLS {site_id: row.site_id}]->(callee)
            SET r += row.props
            RETURN count(r) as count
            "#;

/// `write_call_evidence_sites`.
pub const WRITE_CALL_EVIDENCE_SITES: &str = r#"
            UNWIND $rows AS row
            MERGE (site:CallSite {site_id: row.site_id})
            SET site += row.props,
                site.updated_at = datetime()
            RETURN count(site) as count
            "#;

/// `write_build_configurations`.
pub const WRITE_BUILD_CONFIGURATIONS: &str = r#"
            UNWIND $rows AS row
            MERGE (config:BuildConfiguration {config_fingerprint: row.config_fingerprint})
            SET config += row.props,
                config.updated_at = datetime()
            RETURN count(config) as count
            "#;

/// `write_semantic_coverage`.
pub const WRITE_SEMANTIC_COVERAGE: &str = r#"
            UNWIND $rows AS row
            MERGE (coverage:SemanticCoverage {fingerprint: row.fingerprint})
            SET coverage += row.props,
                coverage.updated_at = datetime()
            RETURN count(coverage) as count
            "#;

/// `write_navigators`.
pub const WRITE_NAVIGATORS: &str = r#"
            UNWIND $rows AS row
            MERGE (n:Navigator {id: row.id})
            SET n.var_name        = row.var_name,
                n.nav_type        = row.nav_type,
                n.factory         = row.factory,
                n.param_list_ref  = row.param_list_ref,
                n.file_path       = row.file_path,
                n.start_line      = row.start_line,
                n.project_id      = row.project_id,
                n.project_name    = row.project_name,
                n.updated_at      = datetime()
            RETURN count(n) AS count
            "#;

/// `write_has_routes`.
pub const WRITE_HAS_ROUTES: &str = r#"
            UNWIND $rows AS row
            MATCH (n:Navigator {id: row.navigator_id})
            MATCH (s:Function  {id: row.screen_id})
            MERGE (n)-[r:HAS_ROUTE {name: row.route_name}]->(s)
            SET r.param_schema = row.param_schema,
                r.updated_at   = datetime()
            RETURN count(r) AS count
            "#;

/// `write_param_lists`.
pub const WRITE_PARAM_LISTS: &str = r#"
            UNWIND $rows AS row
            MERGE (p:RouteParam {id: row.symbol_id + '::' + row.route_name})
            SET p.param_list_name = row.name,
                p.route           = row.route_name,
                p.type_str        = row.type_str,
                p.file_path       = row.file_path,
                p.project_id      = row.project_id,
                p.updated_at      = datetime()
            RETURN count(p) AS count
            "#;

/// `write_workflows`.
pub const WRITE_WORKFLOWS: &str = r#"
            UNWIND $rows AS row
            MERGE (w:Workflow {workflow_id: row.workflow_id})
            SET w.name          = row.workflow_name,
                w.domain        = row.domain,
                w.description   = row.description,
                w.confidence    = row.confidence,
                w.entrypoint_id = row.entrypoint_id,
                w.language      = row.language,
                w.project       = row.project,
                w.kind          = row.kind,
                w.updated_at    = datetime()
            RETURN count(w) as count
            "#;

/// `write_workflow_steps`.
pub const WRITE_WORKFLOW_STEPS: &str = r#"
            UNWIND $rows AS row
            MATCH (w:Workflow  {workflow_id: row.workflow_id})
            MATCH (f:Function  {id:          row.function_id})
            MERGE (w)-[s:HAS_STEP {order: row.step_order}]->(f)
            RETURN count(s) as count
            "#;

/// `write_node_properties_batch` — trusted replay compiler cho node contract
/// (`compile_persisted_mutation`, `node_identity`, merge kind).
pub fn compile_node_identity_merge(
    node_label: &str,
    identity_property: &str,
    row_identity_property: &str,
    row_properties_property: Option<&str>,
) -> String {
    let row_value = match row_properties_property {
        Some(prop) => format!("coalesce(row.{prop}, {{}})"),
        None => "row".to_string(),
    };
    format!(
        "UNWIND $rows AS row \
         MERGE (n:{node_label} {{{identity_property}: row.{row_identity_property}}}) \
         SET n += {row_value}, n.updated_at = datetime() \
         RETURN count(n) AS count"
    )
}

/// Persisted compiler `file_cleanup` (version 2).
pub const COMPILE_FILE_CLEANUP: &str = "UNWIND $rows AS row \
         OPTIONAL MATCH (n:{node_label}) \
         WHERE n.project_id = row.project_id \
         AND (coalesce(n.file_path, '') IN row.paths \
         OR coalesce(n.path, '') IN row.paths \
         OR (n:File AND n.id IN row.paths)) \
         WITH row, collect(DISTINCT n) AS nodes \
         FOREACH (node IN nodes | DETACH DELETE node) \
         RETURN count(*) AS count";

/// Persisted compiler `orphan_unknown_cleanup` (version 2).
pub const COMPILE_ORPHAN_CLEANUP: &str = "UNWIND $rows AS row \
         OPTIONAL MATCH (u:UnknownFunction) \
         WHERE u.project_id = row.project_id \
         AND NOT ()-[:UNKNOWN_CALL]->(u) \
         WITH row, collect(u) AS nodes \
         FOREACH (node IN nodes | DETACH DELETE node) \
         RETURN count(*) AS count";

/// `_CPLUS_FILE_OWNED_NODE_LABELS` — file-owned labels cho incremental
/// cleanup của cplus.
pub const CPLUS_FILE_OWNED_NODE_LABELS: [&str; 16] = [
    "Alias",
    "DatabaseTable",
    "Field",
    "File",
    "Function",
    "FunctionType",
    "Namespace",
    "ParseRun",
    "Resource",
    "SqlCursor",
    "SqlDirective",
    "SqlHostVariable",
    "SqlStatement",
    "Template",
    "Type",
    "UIControl",
];

/// `_DECLARATION_KIND_LABELS` — host/indicator declaration joins.
pub fn declaration_kind_label(kind: &str) -> Option<&'static str> {
    match kind {
        "function" | "parameter" => Some("Function"),
        "field" | "variable" | "local" | "global" => Some("Variable"),
        _ => None,
    }
}

/// `_OPTIONAL_EXTERNAL_RELATION_TYPES`.
pub const OPTIONAL_EXTERNAL_RELATION_TYPES: [&str; 4] =
    ["EXTENDS", "IMPLEMENTS", "INHERITS_FROM", "MIXES_IN"];
