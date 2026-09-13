//! Port của `tools/graph/operations/*` — 9 op modules (package, class,
//! namespace, type, function, infra, document, flow, cross_edge). Mỗi query
//! giữ nguyên chữ Python; write ops là free functions trên `&mut dyn
//! GraphStore`, read ops trả records JSON.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use cortex_graph_core::schema_manifest::validate_cypher_identifier;

use crate::json_row::Row;
use crate::query_contract::{
    compile_relationship_upsert, group_typed_relations, RelationshipGroup,
};
use crate::store::{GraphStore, StoreError};

type OpResult<T> = Result<T, StoreError>;

fn rows_param(rows: &[Row]) -> BTreeMap<String, Value> {
    let mut params = BTreeMap::new();
    params.insert(
        "rows".to_string(),
        Value::Array(rows.iter().cloned().map(Value::Object).collect()),
    );
    params
}

fn records(store: &mut dyn GraphStore, query: &str, params: &BTreeMap<String, Value>, database: Option<&str>) -> OpResult<Vec<Row>> {
    store.execute_query(query, params, database)
}

fn count_from(records: &[Row]) -> i64 {
    records
        .first()
        .and_then(|record| record.get("count"))
        .and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_f64().map(|f| f as i64))
        })
        .unwrap_or(0)
}

// ── package_ops ──────────────────────────────────────────────────────────────

pub mod package {
    use super::*;

    /// `batch_create_packages` — writer dùng qua `write_packages`.
    pub const BATCH_CREATE: &str = r#"
        UNWIND $rows AS row
        MERGE (p:Package {id: row.id})
        SET p.name = row.name,
            p.start_line = row.start_line,
            p.end_line = row.end_line,
            p.code = row.code,
            p.comment = row.comment,
            p.summary = row.summary,
            p.note = row.note,
            p.updated_at = datetime()
        RETURN count(p) as count
        "#;

    pub fn batch_create(
        store: &mut dyn GraphStore,
        packages: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if packages.is_empty() {
            return Ok(0);
        }
        let records = records(store, BATCH_CREATE, &rows_param(packages), database)?;
        Ok(count_from(&records))
    }

    /// `create_package_node` (CREATE đơn lẻ).
    pub const CREATE: &str = r#"
        CREATE (p:Package {
            id: $id,
            name: $name,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN p.id as id
        "#;

    /// `link_file_to_package`.
    pub fn link_file_to_package(
        store: &mut dyn GraphStore,
        file_path: &str,
        package_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (f:File {id: $file_path})
        MATCH (p:Package {id: $package_id})
        MERGE (f)-[r:BELONGS_TO_PACKAGE]->(p)
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("file_path".to_string(), json!(file_path));
        params.insert("package_id".to_string(), json!(package_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_package_contents`.
    pub fn get_package_contents(
        store: &mut dyn GraphStore,
        package_id: &str,
        database: Option<&str>,
    ) -> OpResult<Row> {
        let query = r#"
        MATCH (p:Package {id: $package_id})
        OPTIONAL MATCH (f:File)-[:BELONGS_TO_PACKAGE]->(p)
        OPTIONAL MATCH (c:Class)-[:BELONGS_TO_PACKAGE]->(p)
        OPTIONAL MATCH (fn:Function)-[:BELONGS_TO_PACKAGE]->(p)
        RETURN
            p.id as package_id,
            p.name as package_name,
            collect(DISTINCT f.file_path) as files,
            collect(DISTINCT c.id) as classes,
            count(DISTINCT fn) as function_count
        "#;
        let mut params = BTreeMap::new();
        params.insert("package_id".to_string(), json!(package_id));
        let records = records(store, query, &params, database)?;
        Ok(records.first().cloned().unwrap_or_default())
    }

    /// `find_packages_by_prefix`.
    pub fn find_packages_by_prefix(
        store: &mut dyn GraphStore,
        prefix: &str,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (p:Package)
        WHERE p.name STARTS WITH $prefix
        RETURN
            p.id as id,
            p.name as name,
            p.summary as summary
        ORDER BY p.name
        LIMIT $limit
        "#;
        let mut params = BTreeMap::new();
        params.insert("prefix".to_string(), json!(prefix));
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }
}

// ── class_ops ────────────────────────────────────────────────────────────────

pub mod class {
    use super::*;

    /// `batch_create_classes`.
    pub const BATCH_CREATE: &str = r#"
        UNWIND $rows AS row
        MERGE (c:Class {id: row.id})
        SET c.qualified_name = row.qualified_name,
            c.node_type = 'code',
            c.name = row.name,
            c.kind = row.kind,
            c.package_name = row.package_name,
            c.file_path = row.file_path,
            c.start_line = row.start_line,
            c.end_line = row.end_line,
            c.code = row.code,
            c.comment = row.comment,
            c.summary = row.summary,
            c.note = row.note,
            c.updated_at = datetime()
        RETURN count(c) as count
        "#;

    pub fn batch_create(
        store: &mut dyn GraphStore,
        classes: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if classes.is_empty() {
            return Ok(0);
        }
        let records = records(store, BATCH_CREATE, &rows_param(classes), database)?;
        Ok(count_from(&records))
    }

    /// `create_class_node`.
    pub const CREATE: &str = r#"
        CREATE (c:Class {
            id: $id,
            node_type: 'code',
            qualified_name: $qualified_name,
            name: $name,
            kind: $kind,
            package_name: $package_name,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN c.id as id
        "#;

    /// `link_class_inheritance` — inheritance_type hợp lệ = EXTENDS/IMPLEMENTS.
    pub fn link_class_inheritance(
        store: &mut dyn GraphStore,
        child_id: &str,
        parent_id: &str,
        inheritance_type: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let group = RelationshipGroup::new_unchecked("Class", "Class", inheritance_type);
        let query = format!(
            r#"
        MATCH (child:Class {{id: $child_id}})
        MATCH (parent:Class {{id: $parent_id}})
        MERGE (child)-[r:{}]->(parent)
        SET r.created_at = datetime()
        RETURN r
        "#,
            group.relationship_type
        );
        let mut params = BTreeMap::new();
        params.insert("child_id".to_string(), json!(child_id));
        params.insert("parent_id".to_string(), json!(parent_id));
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_class_to_package`.
    pub fn link_class_to_package(
        store: &mut dyn GraphStore,
        class_id: &str,
        package_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (c:Class {id: $class_id})
        MATCH (p:Package {id: $package_id})
        MERGE (c)-[r:BELONGS_TO_PACKAGE]->(p)
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("class_id".to_string(), json!(class_id));
        params.insert("package_id".to_string(), json!(package_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_method_to_class`.
    pub fn link_method_to_class(
        store: &mut dyn GraphStore,
        method_id: &str,
        class_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (m:Function {id: $method_id})
        MATCH (c:Class {id: $class_id})
        MERGE (m)-[r:BELONGS_TO_CLASS]->(c)
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("method_id".to_string(), json!(method_id));
        params.insert("class_id".to_string(), json!(class_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_class_hierarchy` — direction up/down/both.
    pub fn get_class_hierarchy(
        store: &mut dyn GraphStore,
        class_id: &str,
        direction: &str,
        max_depth: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let (pattern, return_clause) = match direction {
            "up" => (
                format!(
                    "(c:Class {{id: $class_id}})-[:EXTENDS|IMPLEMENTS*1..{max_depth}]->(parent:Class)"
                ),
                "parent",
            ),
            "down" => (
                format!(
                    "(child:Class)-[:EXTENDS|IMPLEMENTS*1..{max_depth}]->(c:Class {{id: $class_id}})"
                ),
                "child",
            ),
            _ => (
                format!(
                    "(c:Class {{id: $class_id}})-[:EXTENDS|IMPLEMENTS*1..{max_depth}]-(related:Class)"
                ),
                "related",
            ),
        };
        let query = format!(
            r#"
        MATCH {pattern}
        RETURN DISTINCT
            {rc}.id as id,
            {rc}.name as name,
            {rc}.qualified_name as qualified_name,
            {rc}.kind as kind
        "#,
            rc = return_clause
        );
        let mut params = BTreeMap::new();
        params.insert("class_id".to_string(), json!(class_id));
        records(store, &query, &params, database)
    }

    /// `get_class_methods`.
    pub fn get_class_methods(
        store: &mut dyn GraphStore,
        class_id: &str,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (m:Function)-[:BELONGS_TO_CLASS]->(c:Class {id: $class_id})
        RETURN
            m.id as id,
            m.name as name,
            m.qualified_name as qualified_name,
            m.kind as kind,
            m.arity as arity,
            m.summary as summary
        ORDER BY m.name
        "#;
        let mut params = BTreeMap::new();
        params.insert("class_id".to_string(), json!(class_id));
        records(store, query, &params, database)
    }

    /// `find_inner_classes`.
    pub fn find_inner_classes(
        store: &mut dyn GraphStore,
        outer_class_id: &str,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (inner:Class)-[:NESTED_IN]->(outer:Class {id: $outer_class_id})
        RETURN
            inner.id as id,
            inner.name as name,
            inner.kind as kind
        ORDER BY inner.name
        "#;
        let mut params = BTreeMap::new();
        params.insert("outer_class_id".to_string(), json!(outer_class_id));
        records(store, query, &params, database)
    }
}

// ── namespace_ops ────────────────────────────────────────────────────────────

pub mod namespace {
    use super::*;

    /// `batch_create_namespaces`.
    pub const BATCH_CREATE: &str = r#"
        UNWIND $rows AS row
        MERGE (n:Namespace {id: row.id})
        SET n.qualified_name = row.qualified_name,
            n.name = row.name,
            n.file_path = row.file_path,
            n.start_line = row.start_line,
            n.end_line = row.end_line,
            n.code = row.code,
            n.comment = row.comment,
            n.summary = row.summary,
            n.note = row.note,
            n.updated_at = datetime()
        RETURN count(n) as count
        "#;

    pub fn batch_create(
        store: &mut dyn GraphStore,
        namespaces: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if namespaces.is_empty() {
            return Ok(0);
        }
        let records = records(store, BATCH_CREATE, &rows_param(namespaces), database)?;
        Ok(count_from(&records))
    }

    /// `create_namespace_node`.
    pub const CREATE: &str = r#"
        CREATE (n:Namespace {
            id: $id,
            qualified_name: $qualified_name,
            name: $name,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN n.id as id
        "#;

    /// `link_namespace_hierarchy`.
    pub fn link_namespace_hierarchy(
        store: &mut dyn GraphStore,
        child_id: &str,
        parent_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (child:Namespace {id: $child_id})
        MATCH (parent:Namespace {id: $parent_id})
        MERGE (child)-[r:NESTED_IN]->(parent)
        SET r.created_at = datetime()
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("child_id".to_string(), json!(child_id));
        params.insert("parent_id".to_string(), json!(parent_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_entity_to_namespace` — entity label qua RelationshipGroup
    /// validation (identity index bắt buộc).
    pub fn link_entity_to_namespace(
        store: &mut dyn GraphStore,
        entity_id: &str,
        entity_label: &str,
        namespace_id: &str,
        database: Option<&str>,
    ) -> Result<bool, crate::store::StoreError> {
        RelationshipGroup::new(entity_label, "Namespace", "IN_NAMESPACE")
            .map_err(StoreError::Invalid)?;
        let query = format!(
            r#"
        MATCH (e:{entity_label} {{id: $entity_id}})
        MATCH (n:Namespace {{id: $namespace_id}})
        MERGE (e)-[r:IN_NAMESPACE]->(n)
        RETURN r
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("entity_id".to_string(), json!(entity_id));
        params.insert("namespace_id".to_string(), json!(namespace_id));
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_namespace_contents`.
    pub fn get_namespace_contents(
        store: &mut dyn GraphStore,
        namespace_id: &str,
        database: Option<&str>,
    ) -> OpResult<Row> {
        let query = r#"
        MATCH (n:Namespace {id: $namespace_id})
        OPTIONAL MATCH (child:Namespace)-[:NESTED_IN]->(n)
        OPTIONAL MATCH (c:Class)-[:IN_NAMESPACE]->(n)
        OPTIONAL MATCH (f:Function)-[:IN_NAMESPACE]->(n)
        OPTIONAL MATCH (t:Type)-[:IN_NAMESPACE]->(n)
        RETURN
            n.id as namespace_id,
            n.name as namespace_name,
            n.qualified_name as qualified_name,
            collect(DISTINCT child.id) as child_namespaces,
            collect(DISTINCT c.id) as classes,
            collect(DISTINCT f.id) as functions,
            collect(DISTINCT t.id) as types
        "#;
        let mut params = BTreeMap::new();
        params.insert("namespace_id".to_string(), json!(namespace_id));
        let records = records(store, query, &params, database)?;
        Ok(records.first().cloned().unwrap_or_default())
    }

    /// `find_namespaces_by_pattern` — pattern `*` → `.*`.
    pub fn find_namespaces_by_pattern(
        store: &mut dyn GraphStore,
        pattern: &str,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (n:Namespace)
        WHERE n.qualified_name =~ $regex_pattern
        RETURN
            n.id as id,
            n.name as name,
            n.qualified_name as qualified_name,
            n.summary as summary
        ORDER BY n.qualified_name
        LIMIT $limit
        "#;
        let regex_pattern = pattern.replace('*', ".*");
        let mut params = BTreeMap::new();
        params.insert("regex_pattern".to_string(), json!(regex_pattern));
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }
}

// ── type_ops ─────────────────────────────────────────────────────────────────

pub mod r#type {
    use super::*;

    /// `batch_create_types`.
    pub const BATCH_CREATE: &str = r#"
        UNWIND $rows AS row
        MERGE (t:Type {id: row.id})
        SET t.qualified_name = row.qualified_name,
            t.name = row.name,
            t.kind = row.kind,
            t.file_path = row.file_path,
            t.start_line = row.start_line,
            t.end_line = row.end_line,
            t.code = row.code,
            t.comment = row.comment,
            t.summary = row.summary,
            t.note = row.note,
            t.updated_at = datetime()
        RETURN count(t) as count
        "#;

    pub fn batch_create(
        store: &mut dyn GraphStore,
        types: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if types.is_empty() {
            return Ok(0);
        }
        let records = records(store, BATCH_CREATE, &rows_param(types), database)?;
        Ok(count_from(&records))
    }

    /// `create_type_node`.
    pub const CREATE: &str = r#"
        CREATE (t:Type {
            id: $id,
            qualified_name: $qualified_name,
            name: $name,
            kind: $kind,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN t.id as id
        "#;

    /// `link_type_usage` — optional per-property SET append (giữ format
    /// `\nSET r.<prop> = $<prop>` như Python).
    pub fn link_type_usage(
        store: &mut dyn GraphStore,
        user_id: &str,
        user_label: &str,
        type_id: &str,
        usage_kind: &str,
        properties: &BTreeMap<String, Value>,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        RelationshipGroup::new(user_label, "Type", usage_kind).map_err(StoreError::Invalid)?;
        let mut query = format!(
            r#"
        MATCH (user:{user_label} {{id: $user_id}})
        MATCH (t:Type {{id: $type_id}})
        MERGE (user)-[r:{usage_kind}]->(t)
        "#
        );
        for key in properties.keys() {
            let property_name = validate_cypher_identifier(key, "property")
                .map_err(StoreError::Invalid)?;
            query.push_str(&format!("\nSET r.{property_name} = ${property_name}"));
        }
        query.push_str("\nRETURN r");
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("user_id".to_string(), json!(user_id));
        params.insert("type_id".to_string(), json!(type_id));
        for (key, value) in properties {
            params.insert(key.clone(), value.clone());
        }
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_type_alias`.
    pub fn link_type_alias(
        store: &mut dyn GraphStore,
        alias_id: &str,
        target_type_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (alias:Type {id: $alias_id})
        MATCH (target:Type {id: $target_type_id})
        MERGE (alias)-[r:ALIAS_OF]->(target)
        SET r.created_at = datetime()
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("alias_id".to_string(), json!(alias_id));
        params.insert("target_type_id".to_string(), json!(target_type_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_type_usages`.
    pub fn get_type_usages(
        store: &mut dyn GraphStore,
        type_id: &str,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (entity)-[r:USES_TYPE|POINTER_TO|REFERENCE_TO]->(t:Type {id: $type_id})
        RETURN
            entity.id as entity_id,
            labels(entity)[0] as entity_label,
            entity.name as entity_name,
            entity.qualified_name as qualified_name,
            type(r) as usage_kind
        ORDER BY entity_label, entity_name
        LIMIT $limit
        "#;
        let mut params = BTreeMap::new();
        params.insert("type_id".to_string(), json!(type_id));
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }

    /// `find_primitive_types`.
    pub fn find_primitive_types(
        store: &mut dyn GraphStore,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (t:Type)
        WHERE t.kind = 'primitive' OR t.kind = 'builtin'
        RETURN
            t.id as id,
            t.name as name,
            t.kind as kind
        ORDER BY t.name
        "#;
        records(store, query, &BTreeMap::new(), database)
    }

    /// `resolve_type_chain`.
    pub fn resolve_type_chain(
        store: &mut dyn GraphStore,
        type_id: &str,
        max_depth: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = format!(
            r#"
        MATCH path = (t:Type {{id: $type_id}})-[:ALIAS_OF*0..{max_depth}]->(final:Type)
        WHERE NOT (final)-[:ALIAS_OF]->()
        RETURN
            [node in nodes(path) | {{
                id: node.id,
                name: node.name,
                kind: node.kind
            }}] as type_chain
        LIMIT 1
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("type_id".to_string(), json!(type_id));
        let records = records(store, &query, &params, database)?;
        Ok(records
            .first()
            .and_then(|record| record.get("type_chain"))
            .and_then(Value::as_array)
            .map(|chain| {
                chain
                    .iter()
                    .filter_map(Value::as_object)
                    .cloned()
                    .collect::<Vec<Row>>()
            })
            .unwrap_or_default())
    }
}

// ── function_ops ─────────────────────────────────────────────────────────────

pub mod function {
    use super::*;

    /// `create_function_node`.
    pub const CREATE: &str = r#"
        CREATE (f:Function {
            id: $id,
            node_type: 'code',
            name: $name,
            qualified_name: $qualified_name,
            code: $code,
            language: $language,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            comment: $comment,
            summary: $summary,
            created_at: datetime()
        })
        RETURN f.id as id
        "#;

    /// `batch_create_functions` — deleg qua `driver.batch_write_nodes`
    /// (ladybug: CREATE + per-property SET; falkordb: batch MERGE).
    pub fn batch_create(
        store: &mut dyn GraphStore,
        functions: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        batch_write_nodes(store, functions, "Function", database)
    }

    /// `update_function_summary`.
    pub fn update_function_summary(
        store: &mut dyn GraphStore,
        function_id: &str,
        summary: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (f:Function {id: $function_id})
        SET f.summary = $summary,
            f.summary_updated_at = datetime()
        RETURN f.id as id
        "#;
        let mut params = BTreeMap::new();
        params.insert("function_id".to_string(), json!(function_id));
        params.insert("summary".to_string(), json!(summary));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_function_call` — count increment COALESCE.
    pub fn link_function_call(
        store: &mut dyn GraphStore,
        caller_id: &str,
        callee_id: &str,
        call_data: &BTreeMap<String, Value>,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        let mut query = r#"
        MATCH (caller:Function {id: $caller_id})
        MATCH (callee:Function {id: $callee_id})
        MERGE (caller)-[r:CALLS]->(callee)
        SET r.count = COALESCE(r.count, 0) + 1,
            r.last_updated = datetime()
        "#
        .to_string();
        for key in call_data.keys() {
            query.push_str(&format!("\nSET r.{key} = ${key}"));
        }
        query.push_str("\nRETURN r");
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("caller_id".to_string(), json!(caller_id));
        params.insert("callee_id".to_string(), json!(callee_id));
        for (key, value) in call_data {
            params.insert(key.clone(), value.clone());
        }
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_function_calls` — direction out/in/both (variable rename của
    /// Python: related→target/source).
    pub fn get_function_calls(
        store: &mut dyn GraphStore,
        function_id: &str,
        direction: &str,
        max_depth: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let (pattern, related) = match direction {
            "outgoing" => (
                format!(
                    "(f:Function {{id: $function_id}})-[r:CALLS*1..{max_depth}]->(related:Function)"
                ),
                "target",
            ),
            "incoming" => (
                format!(
                    "(source:Function)-[r:CALLS*1..{max_depth}]->(f:Function {{id: $function_id}})"
                ),
                "source",
            ),
            _ => (
                format!(
                    "(f:Function {{id: $function_id}})-[r:CALLS*1..{max_depth}]-(related:Function)"
                ),
                "related",
            ),
        };
        let mut query = format!(
            r#"
        MATCH {pattern}
        RETURN DISTINCT
            f.id as function_id,
            {related}.id as related_id,
            {related}.name as related_name,
            {related}.qualified_name as related_qualified_name,
            length(r) as depth
        ORDER BY depth, related_name
        LIMIT 100
        "#
        );
        if related != "related" {
            // Python replace("related", ...) đè cả tên cột related_* —
            // giữ đúng hành vi đó.
            query = query.replace("related", related);
        }
        let mut params = BTreeMap::new();
        params.insert("function_id".to_string(), json!(function_id));
        records(store, &query, &params, database)
    }

    /// `get_functions_without_summary`.
    pub fn get_functions_without_summary(
        store: &mut dyn GraphStore,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (f:Function)
        WHERE f.summary IS NULL OR f.summary = ''
        AND f.code IS NOT NULL AND f.code <> ''
        RETURN
            f.id as id,
            f.name as name,
            f.qualified_name as qualified_name,
            f.code as code,
            f.language as language,
            f.file_path as file_path
        LIMIT $limit
        "#;
        let mut params = BTreeMap::new();
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }
}

/// `GraphDriver.batch_write_nodes` — port của ladybug dialect (không có
/// `SET n = map`): CREATE + per-property SET. FalkorDB cũng chạy được query
/// này (dùng chung một query như ladybug_driver làm để 2 backend cùng text).
pub fn batch_write_nodes(
    store: &mut dyn GraphStore,
    nodes: &[Row],
    label: &str,
    database: Option<&str>,
) -> OpResult<i64> {
    if nodes.is_empty() {
        return Ok(0);
    }
    validate_cypher_identifier(label, "label").map_err(StoreError::Invalid)?;
    let mut property_names: BTreeSet<String> = BTreeSet::new();
    for node in nodes {
        for key in node.keys() {
            if key != "id" {
                property_names.insert(key.clone());
            }
        }
    }
    for prop in &property_names {
        validate_cypher_identifier(prop, "property").map_err(StoreError::Invalid)?;
    }
    let assignments: Vec<String> = property_names
        .iter()
        .map(|prop| format!("n.`{prop}` = node.`{prop}`"))
        .collect();
    let mut query =
        format!("UNWIND $nodes AS node CREATE (n:{label} {{id: node.id}})");
    if !assignments.is_empty() {
        query.push_str(&format!(" SET {}", assignments.join(", ")));
    }
    query.push_str(" RETURN count(n) as count");
    let mut params = BTreeMap::new();
    params.insert(
        "nodes".to_string(),
        Value::Array(nodes.iter().cloned().map(Value::Object).collect()),
    );
    let records = records(store, &query, &params, database)?;
    Ok(count_from(&records))
}

/// `GraphDriver.batch_write_edges` — flatten edge payloads (ladybug dialect).
pub fn batch_write_edges(
    store: &mut dyn GraphStore,
    edges: &[Row],
    relationship_type: &str,
    source_label: &str,
    target_label: &str,
    database: Option<&str>,
) -> OpResult<i64> {
    if edges.is_empty() {
        return Ok(0);
    }
    let rel_type =
        validate_cypher_identifier(relationship_type, "relationship type")
            .map_err(StoreError::Invalid)?;
    let source_node_label =
        validate_cypher_identifier(source_label, "source label").map_err(StoreError::Invalid)?;
    let target_node_label =
        validate_cypher_identifier(target_label, "target label").map_err(StoreError::Invalid)?;

    let mut property_names: BTreeSet<String> = BTreeSet::new();
    for edge in edges {
        if let Some(props) = edge.get("properties").and_then(Value::as_object) {
            for key in props.keys() {
                if key != "source_id" && key != "target_id" {
                    property_names.insert(key.clone());
                }
            }
        }
    }
    for prop in &property_names {
        validate_cypher_identifier(prop, "property").map_err(StoreError::Invalid)?;
    }
    let rows: Vec<Row> = edges
        .iter()
        .map(|edge| {
            let mut row = Map::new();
            row.insert(
                "source_id".to_string(),
                edge.get("source_id").cloned().unwrap_or(Value::Null),
            );
            row.insert(
                "target_id".to_string(),
                edge.get("target_id").cloned().unwrap_or(Value::Null),
            );
            for prop in &property_names {
                row.insert(
                    prop.clone(),
                    edge.get("properties")
                        .and_then(Value::as_object)
                        .and_then(|props| props.get(prop))
                        .cloned()
                        .unwrap_or(Value::Null),
                );
            }
            row
        })
        .collect();
    let assignments: Vec<String> = property_names
        .iter()
        .map(|prop| format!("r.`{prop}` = edge.`{prop}`"))
        .collect();
    let mut query = format!(
        "UNWIND $edges AS edge \
         MATCH (source:{source_node_label} {{id: edge.source_id}}) \
         MATCH (target:{target_node_label} {{id: edge.target_id}}) \
         MERGE (source)-[r:{rel_type}]->(target)"
    );
    if !assignments.is_empty() {
        query.push_str(&format!(" SET {}", assignments.join(", ")));
    }
    query.push_str(" RETURN count(r) as count");
    let mut params = BTreeMap::new();
    params.insert(
        "edges".to_string(),
        Value::Array(rows.into_iter().map(Value::Object).collect()),
    );
    let records = records(store, &query, &params, database)?;
    Ok(count_from(&records))
}

// ── infra_ops ────────────────────────────────────────────────────────────────

pub mod infra {
    use super::*;

    /// `create_infra_node`.
    pub const CREATE: &str = r#"
        CREATE (i:InfraNode {
            id: $id,
            name: $name,
            type: $type,
            description: $description,
            module_path: $module_path,
            cohesion_score: $cohesion_score,
            coupling_score: $coupling_score,
            status: $status,
            created_at: datetime()
        })
        RETURN i.id as id
        "#;

    /// `run_louvain_clustering` (GDS — chỉ chạy trên backend có plugin).
    pub fn run_louvain_clustering(
        store: &mut dyn GraphStore,
        label: &str,
        relationship: &str,
        weight_property: Option<&str>,
        min_community_size: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let weight_clause = weight_property
            .map(|prop| format!(", relationshipWeightProperty: '{prop}'"))
            .unwrap_or_default();
        let query = format!(
            r#"
        CALL gds.louvain.stream({{
            nodeLabels: ['{label}'],
            relationshipTypes: ['{relationship}']
            {weight_clause}
        }})
        YIELD nodeId, communityId
        WITH communityId, collect(gds.util.asNode(nodeId)) as members
        WHERE size(members) >= $min_community_size
        RETURN
            communityId,
            size(members) as size,
            [m in members | {{
                id: m.id,
                name: m.name
            }}] as members
        ORDER BY size DESC
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("min_community_size".to_string(), json!(min_community_size));
        records(store, &query, &params, database)
    }

    /// `link_node_to_infra`.
    pub fn link_node_to_infra(
        store: &mut dyn GraphStore,
        node_id: &str,
        infra_id: &str,
        relationship_type: &str,
        node_label: &str,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        let source_label = validate_cypher_identifier(node_label, "node label")
            .map_err(StoreError::Invalid)?;
        let rel_type = validate_cypher_identifier(relationship_type, "relationship type")
            .map_err(StoreError::Invalid)?;
        RelationshipGroup::new_unchecked(&source_label, "InfraNode", &rel_type);
        let query = format!(
            r#"
        MATCH (node:{source_label} {{id: $node_id}})
        MATCH (infra:InfraNode {{id: $infra_id}})
        MERGE (node)-[r:{rel_type}]->(infra)
        RETURN r
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("node_id".to_string(), json!(node_id));
        params.insert("infra_id".to_string(), json!(infra_id));
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `update_infra_summary`.
    pub fn update_infra_summary(
        store: &mut dyn GraphStore,
        infra_id: &str,
        summary: &str,
        status: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (i:InfraNode {id: $infra_id})
        SET i.summary = $summary,
            i.status = $status,
            i.summary_updated_at = datetime()
        RETURN i.id as id
        "#;
        let mut params = BTreeMap::new();
        params.insert("infra_id".to_string(), json!(infra_id));
        params.insert("summary".to_string(), json!(summary));
        params.insert("status".to_string(), json!(status));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `get_infra_nodes_pending_summary`.
    pub fn get_infra_nodes_pending_summary(
        store: &mut dyn GraphStore,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (i:InfraNode)
        WHERE i.status = 'pending_summary'
        OPTIONAL MATCH (i)<-[:BELONGS_TO]-(member)
        RETURN
            i.id as id,
            i.name as name,
            i.type as type,
            i.module_path as module_path,
            i.cohesion_score as cohesion_score,
            collect({
                id: member.id,
                name: member.name,
                summary: member.summary
            }) as members
        LIMIT $limit
        "#;
        let mut params = BTreeMap::new();
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }

    /// `calculate_module_metrics`.
    pub fn calculate_module_metrics(
        store: &mut dyn GraphStore,
        infra_id: &str,
        database: Option<&str>,
    ) -> OpResult<BTreeMap<String, f64>> {
        let query = r#"
        MATCH (i:InfraNode {id: $infra_id})<-[:BELONGS_TO]-(member)

        // Cohesion: internal connections
        OPTIONAL MATCH (member)-[internal:CALLS]->(other)
        WHERE (other)-[:BELONGS_TO]->(i)
        WITH i, member, count(internal) as internal_calls

        // Coupling: external connections
        OPTIONAL MATCH (member)-[external:CALLS]->(outside)
        WHERE NOT (outside)-[:BELONGS_TO]->(i)
        WITH i,
             sum(internal_calls) as total_internal,
             count(external) as total_external,
             count(DISTINCT member) as member_count

        RETURN
            CASE
                WHEN member_count > 1
                THEN toFloat(total_internal) / (member_count * (member_count - 1))
                ELSE 0.0
            END as cohesion_score,
            CASE
                WHEN total_internal + total_external > 0
                THEN toFloat(total_external) / (total_internal + total_external)
                ELSE 0.0
            END as coupling_score
        "#;
        let mut params = BTreeMap::new();
        params.insert("infra_id".to_string(), json!(infra_id));
        let records = records(store, query, &params, database)?;
        if let Some(first) = records.first() {
            return Ok(BTreeMap::from([
                (
                    "cohesion_score".to_string(),
                    first
                        .get("cohesion_score")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                ),
                (
                    "coupling_score".to_string(),
                    first
                        .get("coupling_score")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                ),
            ]));
        }
        Ok(BTreeMap::from([
            ("cohesion_score".to_string(), 0.0),
            ("coupling_score".to_string(), 0.0),
        ]))
    }
}

// ── document_ops ─────────────────────────────────────────────────────────────

pub mod document {
    use super::*;

    /// `create_document_node`.
    pub const CREATE_DOCUMENT: &str = r#"
        CREATE (d:Document {
            id: $id,
            title: $title,
            file_path: $file_path,
            content: $content,
            doc_type: $doc_type,
            created_at: datetime()
        })
        RETURN d.id as id
        "#;

    /// `create_paragraph_node`.
    pub const CREATE_PARAGRAPH: &str = r#"
        CREATE (p:Paragraph {
            id: $id,
            content: $content,
            embedding: $embedding,
            chunk_index: $chunk_index,
            created_at: datetime()
        })
        RETURN p.id as id
        "#;

    /// `link_document_to_paragraph`.
    pub fn link_document_to_paragraph(
        store: &mut dyn GraphStore,
        document_id: &str,
        paragraph_id: &str,
        database: Option<&str>,
    ) -> OpResult<bool> {
        let query = r#"
        MATCH (d:Document {id: $document_id})
        MATCH (p:Paragraph {id: $paragraph_id})
        MERGE (d)-[r:HAS_PARAGRAPH]->(p)
        RETURN r
        "#;
        let mut params = BTreeMap::new();
        params.insert("document_id".to_string(), json!(document_id));
        params.insert("paragraph_id".to_string(), json!(paragraph_id));
        let records = records(store, query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `link_code_to_document` — metadata per-property SET append.
    pub fn link_code_to_document(
        store: &mut dyn GraphStore,
        code_id: &str,
        document_id: &str,
        relationship_type: &str,
        metadata: &BTreeMap<String, Value>,
        code_label: &str,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        let source_label = validate_cypher_identifier(code_label, "code label")
            .map_err(StoreError::Invalid)?;
        let rel_type = validate_cypher_identifier(relationship_type, "relationship type")
            .map_err(StoreError::Invalid)?;
        RelationshipGroup::new_unchecked(&source_label, "Document", &rel_type);
        let mut query = format!(
            r#"
        MATCH (code:{source_label} {{id: $code_id}})
        MATCH (doc:Document {{id: $document_id}})
        MERGE (code)-[r:{rel_type}]->(doc)
        "#
        );
        for key in metadata.keys() {
            let property_name = validate_cypher_identifier(key, "property")
                .map_err(StoreError::Invalid)?;
            query.push_str(&format!("\nSET r.{property_name} = ${property_name}"));
        }
        query.push_str("\nRETURN r");
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("code_id".to_string(), json!(code_id));
        params.insert("document_id".to_string(), json!(document_id));
        for (key, value) in metadata {
            params.insert(key.clone(), value.clone());
        }
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `find_similar_paragraphs` (GDS cosine).
    pub fn find_similar_paragraphs(
        store: &mut dyn GraphStore,
        embedding: &[f64],
        top_k: i64,
        min_similarity: f64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (p:Paragraph)
        WHERE p.embedding IS NOT NULL
        WITH p,
             gds.similarity.cosine(p.embedding, $embedding) AS similarity
        WHERE similarity >= $min_similarity
        RETURN
            p.id as id,
            p.content as content,
            similarity
        ORDER BY similarity DESC
        LIMIT $top_k
        "#;
        let mut params = BTreeMap::new();
        params.insert(
            "embedding".to_string(),
            Value::Array(embedding.iter().map(|v| json!(v)).collect()),
        );
        params.insert("min_similarity".to_string(), json!(min_similarity));
        params.insert("top_k".to_string(), json!(top_k));
        records(store, query, &params, database)
    }

    /// `get_document_with_paragraphs`.
    pub fn get_document_with_paragraphs(
        store: &mut dyn GraphStore,
        document_id: &str,
        database: Option<&str>,
    ) -> OpResult<Row> {
        let query = r#"
        MATCH (d:Document {id: $document_id})
        OPTIONAL MATCH (d)-[:HAS_PARAGRAPH]->(p:Paragraph)
        RETURN
            d.id as document_id,
            d.title as title,
            d.file_path as file_path,
            d.doc_type as doc_type,
            collect({
                id: p.id,
                content: p.content,
                chunk_index: p.chunk_index
            }) as paragraphs
        "#;
        let mut params = BTreeMap::new();
        params.insert("document_id".to_string(), json!(document_id));
        let records = records(store, query, &params, database)?;
        Ok(records.first().cloned().unwrap_or_default())
    }
}

// ── flow_ops ─────────────────────────────────────────────────────────────────

pub mod flow {
    use super::*;

    /// `upsert_workflows`.
    pub const UPSERT_WORKFLOWS: &str = r#"
        UNWIND $rows AS row
        MERGE (w:Workflow {workflow_id: row.workflow_id})
        SET w.name             = row.workflow_name,
            w.domain           = row.domain,
            w.description      = row.description,
            w.confidence       = row.confidence,
            w.entrypoint_id    = row.entrypoint_id,
            w.language         = row.language,
            w.project          = row.project,
            w.kind             = row.kind,
            w.updated_at       = datetime()
        RETURN count(w) AS count
        "#;

    pub fn upsert_workflows(
        store: &mut dyn GraphStore,
        workflows: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if workflows.is_empty() {
            return Ok(0);
        }
        let records = records(store, UPSERT_WORKFLOWS, &rows_param(workflows), database)?;
        Ok(count_from(&records))
    }

    /// `upsert_workflow_steps`.
    pub const UPSERT_STEPS: &str = r#"
        UNWIND $rows AS row
        MATCH  (w:Workflow  {workflow_id: row.workflow_id})
        MATCH  (f:Function  {id:          row.function_id})
        MERGE  (w)-[s:HAS_STEP {order: row.step_order}]->(f)
        RETURN count(s) AS count
        "#;

    pub fn upsert_workflow_steps(
        store: &mut dyn GraphStore,
        step_rows: &[Row],
        database: Option<&str>,
    ) -> OpResult<i64> {
        if step_rows.is_empty() {
            return Ok(0);
        }
        let records = records(store, UPSERT_STEPS, &rows_param(step_rows), database)?;
        Ok(count_from(&records))
    }

    /// `list_workflows` — optional filters project/language/domain.
    pub fn list_workflows(
        store: &mut dyn GraphStore,
        project: Option<&str>,
        language: Option<&str>,
        domain: Option<&str>,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let mut filters: Vec<String> = Vec::new();
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("limit".to_string(), json!(limit));
        if let Some(project) = project {
            filters.push("w.project = $project".to_string());
            params.insert("project".to_string(), json!(project));
        }
        if let Some(language) = language {
            filters.push("w.language = $language".to_string());
            params.insert("language".to_string(), json!(language));
        }
        if let Some(domain) = domain {
            filters.push("w.domain = $domain".to_string());
            params.insert("domain".to_string(), json!(domain));
        }
        let where_clause = if filters.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", filters.join(" AND "))
        };
        let query = format!(
            r#"
        MATCH (w:Workflow)
        {where_clause}
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        ORDER BY w.confidence DESC, w.name ASC
        LIMIT $limit
        "#
        );
        records(store, &query, &params, database)
    }

    /// `get_workflow_steps` — trả {workflow, steps}.
    pub fn get_workflow_steps(
        store: &mut dyn GraphStore,
        workflow_id: &str,
        database: Option<&str>,
    ) -> OpResult<Row> {
        let wf_query = r#"
        MATCH (w:Workflow {workflow_id: $wid})
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        "#;
        let steps_query = r#"
        MATCH (w:Workflow {workflow_id: $wid})-[s:HAS_STEP]->(f:Function)
        RETURN s.order         AS step_order,
               f.id            AS id,
               f.name          AS name,
               f.qualified_name AS qualified_name,
               f.file_path     AS file_path,
               f.start_line    AS start_line,
               f.end_line      AS end_line,
               f.summary       AS summary,
               f.kind          AS kind
        ORDER BY s.order ASC
        "#;
        let mut params = BTreeMap::new();
        params.insert("wid".to_string(), json!(workflow_id));
        let wf_records = records(store, wf_query, &params, database)?;
        let step_records = records(store, steps_query, &params, database)?;
        if wf_records.is_empty() {
            return Ok(Row::new());
        }
        let mut result = Map::new();
        result.insert(
            "workflow".to_string(),
            Value::Object(wf_records.first().cloned().unwrap_or_default()),
        );
        result.insert(
            "steps".to_string(),
            Value::Array(
                step_records
                    .into_iter()
                    .map(Value::Object)
                    .collect::<Vec<_>>(),
            ),
        );
        Ok(result)
    }

    /// `search_workflows`.
    pub fn search_workflows(
        store: &mut dyn GraphStore,
        query_text: &str,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = r#"
        MATCH (w:Workflow)
        WHERE toLower(w.name) CONTAINS toLower($q)
           OR toLower(w.description) CONTAINS toLower($q)
           OR toLower(w.domain) CONTAINS toLower($q)
        RETURN w.workflow_id  AS workflow_id,
               w.name         AS name,
               w.domain       AS domain,
               w.description  AS description,
               w.confidence   AS confidence,
               w.entrypoint_id AS entrypoint_id,
               w.language     AS language,
               w.project      AS project,
               w.kind         AS kind
        ORDER BY w.confidence DESC, w.name ASC
        LIMIT $limit
        "#;
        let mut params = BTreeMap::new();
        params.insert("q".to_string(), json!(query_text));
        params.insert("limit".to_string(), json!(limit));
        records(store, query, &params, database)
    }
}

// ── cross_edge_ops ───────────────────────────────────────────────────────────

pub mod cross_edge {
    use super::*;

    /// `link_code_to_document` (cross-edge variant; document_label tuỳ chọn).
    #[allow(clippy::too_many_arguments)]
    pub fn link_code_to_document(
        store: &mut dyn GraphStore,
        code_id: &str,
        document_id: &str,
        link_type: &str,
        confidence: f64,
        metadata: &BTreeMap<String, Value>,
        code_label: &str,
        document_label: &str,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        let code_node_label = validate_cypher_identifier(code_label, "code label")
            .map_err(StoreError::Invalid)?;
        let document_node_label =
            validate_cypher_identifier(document_label, "document label")
                .map_err(StoreError::Invalid)?;
        let relationship = validate_cypher_identifier(link_type, "relationship type")
            .map_err(StoreError::Invalid)?;
        RelationshipGroup::new_unchecked(&code_node_label, &document_node_label, &relationship);
        let mut query = format!(
            r#"
        MATCH (code:{code_node_label} {{id: $code_id}})
        MATCH (doc:{document_node_label} {{id: $document_id}})
        MERGE (code)-[r:{relationship}]->(doc)
        SET r.confidence = $confidence,
            r.created_at = datetime()
        "#
        );
        for key in metadata.keys() {
            let property_name = validate_cypher_identifier(key, "property")
                .map_err(StoreError::Invalid)?;
            query.push_str(&format!("\nSET r.{property_name} = ${property_name}"));
        }
        query.push_str("\nRETURN r");
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("code_id".to_string(), json!(code_id));
        params.insert("document_id".to_string(), json!(document_id));
        params.insert("confidence".to_string(), json!(confidence));
        for (key, value) in metadata {
            params.insert(key.clone(), value.clone());
        }
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `create_semantic_link`.
    #[allow(clippy::too_many_arguments)]
    pub fn create_semantic_link(
        store: &mut dyn GraphStore,
        source_id: &str,
        target_id: &str,
        similarity_score: f64,
        link_reason: &str,
        source_label: &str,
        target_label: &str,
        database: Option<&str>,
    ) -> Result<bool, StoreError> {
        let source_node_label = validate_cypher_identifier(source_label, "source label")
            .map_err(StoreError::Invalid)?;
        let target_node_label = validate_cypher_identifier(target_label, "target label")
            .map_err(StoreError::Invalid)?;
        RelationshipGroup::new_unchecked(&source_node_label, &target_node_label, "SIMILAR_TO");
        let query = format!(
            r#"
        MATCH (source:{source_node_label} {{id: $source_id}})
        MATCH (target:{target_node_label} {{id: $target_id}})
        MERGE (source)-[r:SIMILAR_TO]->(target)
        SET r.similarity_score = $similarity_score,
            r.reason = $link_reason,
            r.created_at = datetime()
        RETURN r
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("source_id".to_string(), json!(source_id));
        params.insert("target_id".to_string(), json!(target_id));
        params.insert("similarity_score".to_string(), json!(similarity_score));
        params.insert("link_reason".to_string(), json!(link_reason));
        let records = records(store, &query, &params, database)?;
        Ok(!records.is_empty())
    }

    /// `find_code_without_documentation`.
    pub fn find_code_without_documentation(
        store: &mut dyn GraphStore,
        code_label: &str,
        limit: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = format!(
            r#"
        MATCH (code:{code_label})
        WHERE NOT (code)-[:DOCUMENTED_BY|IMPLEMENTS_LOGIC]->(:Document)
        AND NOT (code)-[:DOCUMENTED_BY|IMPLEMENTS_LOGIC]->(:Paragraph)
        RETURN
            code.id as id,
            code.name as name,
            code.qualified_name as qualified_name,
            code.file_path as file_path
        LIMIT $limit
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("limit".to_string(), json!(limit));
        records(store, &query, &params, database)
    }

    /// `batch_create_cross_links` — group typed + integrity check.
    pub fn batch_create_cross_links(
        store: &mut dyn GraphStore,
        links: &[Row],
        relationship_type: &str,
        database: Option<&str>,
    ) -> Result<i64, StoreError> {
        let typed_rows: Vec<Row> = links
            .iter()
            .map(|link| {
                let mut row = Map::new();
                row.insert(
                    "source_label".to_string(),
                    link.get("source_label").cloned().unwrap_or(Value::Null),
                );
                row.insert(
                    "target_label".to_string(),
                    link.get("target_label").cloned().unwrap_or(Value::Null),
                );
                row.insert("rel_type".to_string(), json!(relationship_type));
                row.insert(
                    "source_id".to_string(),
                    link.get("source_id").cloned().unwrap_or(Value::Null),
                );
                row.insert(
                    "target_id".to_string(),
                    link.get("target_id").cloned().unwrap_or(Value::Null),
                );
                row.insert(
                    "properties".to_string(),
                    link.get("properties")
                        .and_then(Value::as_object)
                        .map(|props| Value::Object(props.clone()))
                        .unwrap_or_else(|| Value::Object(Map::new())),
                );
                row
            })
            .collect();
        let groups = group_typed_relations(&typed_rows, None).map_err(StoreError::Invalid)?;
        let mut total = 0i64;
        for (group, rows) in groups {
            let query = compile_relationship_upsert(&group);
            let records = records(store, &query, &rows_param(&rows), database)?;
            let matched = count_from(&records);
            if matched != rows.len() as i64 {
                return Err(StoreError::Invalid(format!(
                    "cross-link integrity failure for {}: expected={} matched={matched}",
                    group.state_key(),
                    rows.len()
                )));
            }
            total += matched;
        }
        Ok(total)
    }

    /// `get_connected_documentation`.
    pub fn get_connected_documentation(
        store: &mut dyn GraphStore,
        code_id: &str,
        max_depth: i64,
        database: Option<&str>,
    ) -> OpResult<Vec<Row>> {
        let query = format!(
            r#"
        MATCH (code {{id: $code_id}})
        MATCH (code)-[r:DOCUMENTED_BY|IMPLEMENTS_LOGIC*1..{max_depth}]->(doc)
        WHERE doc:Document OR doc:Paragraph
        RETURN DISTINCT
            doc.id as id,
            labels(doc)[0] as type,
            doc.title as title,
            doc.content as content,
            length(r) as distance
        ORDER BY distance
        "#
        );
        let mut params = BTreeMap::new();
        params.insert("code_id".to_string(), json!(code_id));
        records(store, &query, &params, database)
    }
}
