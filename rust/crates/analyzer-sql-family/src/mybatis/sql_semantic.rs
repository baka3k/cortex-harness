//! Port `tools/mybatis/sql_semantic_analyzer.py` — SQL semantic gate:
//! normalize placeholders (#{...} / ${...}) → tree-sitter-sql parse →
//! crud/tables/columns/joins/parameters facts.

use std::collections::BTreeMap;

use regex::Regex;
use tree_sitter::Node;

use crate::mybatis::models::{
    Diagnostic, SourceSpan, SqlColumnFact, SqlJoinFact, SqlParameterFact,
    SqlStatementSemanticFact, SqlTableFact, StatementFact,
};
use crate::sqlfile::new_sql_parser;

fn bound_param_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"#\{\s*([^,}]+?)\s*(?:,\s*([^}]+?))?\s*\}").unwrap())
}

fn text_param_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\{\s*([^}]+?)\s*\}").unwrap())
}

pub struct SqlSemanticAnalysis {
    pub statements: Vec<SqlStatementSemanticFact>,
    pub tables: Vec<SqlTableFact>,
    pub columns: Vec<SqlColumnFact>,
    pub joins: Vec<SqlJoinFact>,
    pub parameters: Vec<SqlParameterFact>,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone)]
struct ParamToken {
    token: String,
    parameter_kind: String,
    name: String,
    options: BTreeMap<String, String>,
    position: i64,
    start_byte: usize,
    end_byte: usize,
}

#[derive(Debug, Clone)]
struct DynamicSegment {
    start_byte: usize,
    end_byte: usize,
    dynamic_node_ids: Vec<String>,
    branch_roles: Vec<String>,
}

/// `analyze_sql_semantics`.
pub fn analyze_sql_semantics(
    statements: &[StatementFact],
    _project_id: &str,
) -> SqlSemanticAnalysis {
    let mut semantic_statements: Vec<SqlStatementSemanticFact> = Vec::new();
    let mut tables: Vec<SqlTableFact> = Vec::new();
    let mut columns: Vec<SqlColumnFact> = Vec::new();
    let mut joins: Vec<SqlJoinFact> = Vec::new();
    let mut parameters: Vec<SqlParameterFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    // `_get_sql_parser` — parser khả dụng trong backend Rust (grammar pinned);
    // fallback parser_unavailable của Python chỉ chạy khi thiếu package.
    let mut parser = new_sql_parser();

    for stmt in statements {
        let (normalized, tokens) = normalize_placeholders(&stmt.expanded_body);
        let source_bytes = normalized.as_bytes();
        let tree = parser.parse(source_bytes, None);
        let Some(tree) = tree else { continue };
        let root = tree.root_node();
        let error_count = count_errors(root);
        let crud = crud_of(root);
        let parser_status = if error_count > 0 {
            "partial"
        } else if crud.is_empty() {
            "unsupported"
        } else {
            "parsed"
        };
        let has_textual = tokens.iter().any(|token| token.parameter_kind == "textual");
        let sql_fact = statement_fact(stmt, &normalized, parser_status, error_count, &crud, has_textual);
        semantic_statements.push(sql_fact.clone());
        let dynamic_segments = dynamic_segments(stmt);
        parameters.extend(parameter_facts(
            &sql_fact.stable_id,
            &stmt.source,
            &stmt.expanded_body,
            &tokens,
            &dynamic_segments,
        ));

        if !crud.is_empty()
            && !stmt.statement_kind.is_empty()
            && crud != stmt.statement_kind
        {
            diagnostics.push(Diagnostic::new(
                "mybatis.sql.crud_mismatch",
                &format!(
                    "XML statement tag {:?} does not match SQL CRUD {:?}",
                    stmt.statement_kind, crud
                ),
            ));
        }

        let cte_names = cte_names(root, source_bytes);
        tables.extend(table_facts(
            &sql_fact.stable_id,
            &stmt.source,
            root,
            source_bytes,
            &cte_names,
            &dynamic_segments,
        ));
        joins.extend(join_facts(
            &sql_fact.stable_id,
            &stmt.source,
            root,
            source_bytes,
            &dynamic_segments,
        ));
        columns.extend(column_facts(
            &sql_fact.stable_id,
            &stmt.source,
            root,
            source_bytes,
            &dynamic_segments,
        ));
    }

    SqlSemanticAnalysis {
        statements: semantic_statements,
        tables,
        columns,
        joins,
        parameters,
        diagnostics,
    }
}

/// `_normalize_placeholders`.
fn normalize_placeholders(sql: &str) -> (String, Vec<ParamToken>) {
    let mut tokens: Vec<ParamToken> = Vec::new();
    let bound = bound_param_re().replace_all(sql, |caps: &regex::Captures<'_>| {
        let whole = caps.get(0).unwrap();
        let token = sized_token("mbp", tokens.len(), whole.as_str().chars().count());
        let replacement = fit_token(&token, whole.as_str());
        let mut options = BTreeMap::new();
        if let Some(opts) = caps.get(2) {
            options = parse_options(opts.as_str());
        }
        let position = tokens.len() as i64;
        tokens.push(ParamToken {
            token: replacement.trim().to_string(),
            parameter_kind: "bound".to_string(),
            name: caps.get(1).unwrap().as_str().trim().to_string(),
            options,
            position,
            start_byte: whole.start(),
            end_byte: whole.end(),
        });
        replacement
    });
    let normalized = text_param_re().replace_all(&bound, |caps: &regex::Captures<'_>| {
        let whole = caps.get(0).unwrap();
        let token = sized_token("mbt", tokens.len(), whole.as_str().chars().count());
        let replacement = fit_token(&token, whole.as_str());
        let position = tokens.len() as i64;
        tokens.push(ParamToken {
            token: replacement.trim().to_string(),
            parameter_kind: "textual".to_string(),
            name: caps.get(1).unwrap().as_str().trim().to_string(),
            options: BTreeMap::new(),
            position,
            start_byte: whole.start(),
            end_byte: whole.end(),
        });
        replacement
    });
    (normalized.into_owned(), tokens)
}

/// `_parse_options`.
fn parse_options(text: &str) -> BTreeMap<String, String> {
    let mut options: BTreeMap<String, String> = BTreeMap::new();
    for item in text.split(',') {
        let item = item.trim();
        if item.is_empty() {
            continue;
        }
        if let Some(eq) = item.find('=') {
            options.insert(item[..eq].trim().to_string(), item[eq + 1..].trim().to_string());
        } else {
            options.insert(item.to_string(), String::new());
        }
    }
    options
}

/// `_statement_fact`.
fn statement_fact(
    stmt: &StatementFact,
    normalized: &str,
    parser_status: &str,
    error_count: i64,
    crud: &str,
    has_textual: bool,
) -> SqlStatementSemanticFact {
    SqlStatementSemanticFact {
        stable_id: format!("mybatis_sql_stmt::{}", stmt.stable_id),
        owner_statement_id: stmt.stable_id.clone(),
        source: stmt.source.clone(),
        crud: crud.to_string(),
        xml_statement_kind: stmt.statement_kind.clone(),
        database_id: stmt.database_id.clone(),
        raw_sql: stmt.expanded_body.clone(),
        normalized_sql: normalized.to_string(),
        parser_status: parser_status.to_string(),
        parser_error_count: error_count,
        has_textual_substitution: has_textual,
        confidence: if parser_status != "parsed" || has_textual {
            0.65
        } else {
            1.0
        },
    }
}

/// `_parameter_facts`.
fn parameter_facts(
    sql_statement_id: &str,
    source: &SourceSpan,
    sql: &str,
    tokens: &[ParamToken],
    dynamic_segments: &[DynamicSegment],
) -> Vec<SqlParameterFact> {
    let mut rows: Vec<SqlParameterFact> = Vec::new();
    for token in tokens {
        let (dynamic_ids, branch_roles) =
            provenance_for_range(token.start_byte, token.end_byte, dynamic_segments);
        rows.push(SqlParameterFact {
            stable_id: format!("mybatis_sql_param::{sql_statement_id}::{}", token.position),
            sql_statement_id: sql_statement_id.to_string(),
            token: token.token.clone(),
            parameter_kind: token.parameter_kind.clone(),
            source: span_from_bytes(source, sql, token.start_byte, token.end_byte),
            name: token.name.clone(),
            options: token.options.clone(),
            position: token.position,
            dynamic_node_ids: dynamic_ids,
            branch_roles,
        });
    }
    rows
}

/// `_table_facts`.
fn table_facts(
    sql_statement_id: &str,
    owner_source: &SourceSpan,
    root: Node<'_>,
    source_bytes: &[u8],
    cte_names: &[String],
    dynamic_segments: &[DynamicSegment],
) -> Vec<SqlTableFact> {
    struct TableAdd<'a> {
        rows: Vec<SqlTableFact>,
        sql_statement_id: &'a str,
        owner_source: &'a SourceSpan,
        source_bytes: &'a [u8],
        cte_names: &'a [String],
        dynamic_segments: &'a [DynamicSegment],
    }
    impl<'a> TableAdd<'a> {
        fn add(&mut self, node: Node<'_>, role: &str, is_cte: bool) {
            let Some((raw_name, alias, source_node)) = relation_parts(node, self.source_bytes)
            else {
                return;
            };
            let normalized = normalize_identifier(&raw_name);
            let dynamic = is_dynamic_token(&raw_name);
            let (dynamic_ids, branch_roles) =
                provenance_for_node(source_node, self.dynamic_segments);
            let (catalog, schema) = catalog_schema(&raw_name);
            let ordinal = self.rows.len();
            let stable_id = stable_id(
                "mybatis_sql_table",
                self.sql_statement_id,
                ordinal,
                &[role, raw_name.as_str(), alias.as_str()],
            );
            self.rows.push(SqlTableFact {
                stable_id,
                sql_statement_id: self.sql_statement_id.to_string(),
                raw_name,
                normalized_name: normalized.clone(),
                role: role.to_string(),
                source: offset_span(self.owner_source, source_node),
                alias,
                catalog,
                schema,
                is_cte: is_cte || self.cte_names.contains(&normalized),
                is_dynamic: dynamic,
                dynamic_node_ids: dynamic_ids,
                branch_roles,
                resolution_status: if dynamic {
                    "dynamic".to_string()
                } else {
                    "resolved".to_string()
                },
            });
        }
    }
    let mut ctx = TableAdd {
        rows: Vec::new(),
        sql_statement_id,
        owner_source,
        source_bytes,
        cte_names,
        dynamic_segments,
    };

    let delete_from_nodes = delete_from_nodes(root);

    for cte in nodes_multi(root, &["cte"]) {
        if let Some(name_node) = first_child_multi(cte, &["identifier"]) {
            ctx.add(name_node, "cte_definition", true);
        }
    }
    for insert in nodes_multi(root, &["insert"]) {
        if let Some(target) = first_child_multi(insert, &["object_reference"]) {
            ctx.add(target, "write", false);
        }
    }
    for update in nodes_multi(root, &["update"]) {
        if let Some(target) = first_child_multi(update, &["relation", "object_reference"]) {
            ctx.add(target, "write", false);
        }
    }
    for delete_from in &delete_from_nodes {
        if let Some(target) = first_child_multi(*delete_from, &["object_reference", "relation"]) {
            ctx.add(target, "write", false);
        }
    }
    for from_node in nodes_multi(root, &["from"]) {
        if delete_from_nodes
            .iter()
            .any(|item| item.id() == from_node.id())
        {
            continue;
        }
        for target in direct_children_multi(from_node, &["relation", "object_reference"]) {
            ctx.add(target, "read", false);
        }
        let mut cursor = from_node.walk();
        for join in from_node.children(&mut cursor).filter(|join| join.kind() == "join") {
            if let Some(relation) = first_child_multi(join, &["relation", "object_reference"]) {
                ctx.add(relation, "read", false);
            }
        }
    }
    ctx.rows
}

/// `_join_facts`.
fn join_facts(
    sql_statement_id: &str,
    owner_source: &SourceSpan,
    root: Node<'_>,
    source_bytes: &[u8],
    dynamic_segments: &[DynamicSegment],
) -> Vec<SqlJoinFact> {
    let mut rows: Vec<SqlJoinFact> = Vec::new();
    for join in nodes_multi(root, &["join"]) {
        let relation = first_child_multi(join, &["relation", "object_reference"]);
        let (raw_name, alias) = match relation.and_then(|rel| relation_parts(rel, source_bytes)) {
            Some((raw_name, alias, _)) => (raw_name, alias),
            None => (String::new(), String::new()),
        };
        let condition = join_condition(join, source_bytes);
        let join_type = join_type_of(join, source_bytes);
        let (dynamic_ids, branch_roles) = provenance_for_node(join, dynamic_segments);
        let ordinal = rows.len();
        rows.push(SqlJoinFact {
            stable_id: stable_id(
                "mybatis_sql_join",
                sql_statement_id,
                ordinal,
                &[join_type.as_str(), raw_name.as_str(), alias.as_str()],
            ),
            sql_statement_id: sql_statement_id.to_string(),
            source: offset_span(owner_source, join),
            join_type,
            right_table: raw_name.clone(),
            right_alias: alias,
            condition,
            dynamic_node_ids: dynamic_ids,
            branch_roles,
            resolution_status: if is_dynamic_token(&raw_name) {
                "dynamic".to_string()
            } else {
                "resolved".to_string()
            },
        });
    }
    rows
}

/// `_column_facts`.
fn column_facts(
    sql_statement_id: &str,
    owner_source: &SourceSpan,
    root: Node<'_>,
    source_bytes: &[u8],
    dynamic_segments: &[DynamicSegment],
) -> Vec<SqlColumnFact> {
    struct ColumnCtx<'a> {
        rows: Vec<SqlColumnFact>,
        sql_statement_id: &'a str,
        owner_source: &'a SourceSpan,
        dynamic_segments: &'a [DynamicSegment],
    }
    impl<'a> ColumnCtx<'a> {
        fn add(&mut self, raw: &str, role: &str, node: Node<'_>, expression: &str) {
            let raw = raw.trim();
            if raw.is_empty() || skip_identifier(raw) {
                return;
            }
            let (qualifier, name) = split_qualifier(raw);
            let (dynamic_ids, branch_roles) =
                provenance_for_node(node, self.dynamic_segments);
            let ordinal = self.rows.len();
            self.rows.push(SqlColumnFact {
                stable_id: stable_id(
                    "mybatis_sql_column",
                    self.sql_statement_id,
                    ordinal,
                    &[role, raw],
                ),
                sql_statement_id: self.sql_statement_id.to_string(),
                raw_name: raw.to_string(),
                normalized_name: normalize_identifier(&name),
                role: role.to_string(),
                source: offset_span(self.owner_source, node),
                qualifier: qualifier.clone(),
                table_ref: qualifier,
                expression: if expression.is_empty() {
                    raw.to_string()
                } else {
                    expression.to_string()
                },
                dynamic_node_ids: dynamic_ids,
                branch_roles,
                resolution_status: if is_dynamic_token(raw) {
                    "dynamic".to_string()
                } else {
                    "unresolved".to_string()
                },
            });
        }
    }
    let mut ctx = ColumnCtx {
        rows: Vec::new(),
        sql_statement_id,
        owner_source,
        dynamic_segments,
    };

    for select in nodes_multi(root, &["select"]) {
        let mut cursor = select.walk();
        for expr in select.children(&mut cursor) {
            if expr.kind() != "select_expression" {
                continue;
            }
            let mut expr_cursor = expr.walk();
            for term in expr.children(&mut expr_cursor) {
                if !term.is_named()
                    || !["term", "field", "column", "object_reference"]
                        .contains(&term.kind())
                {
                    continue;
                }
                for col in column_like_nodes(term) {
                    ctx.add(&text_of(Some(col), source_bytes), "projection", col, &text_of(Some(term), source_bytes));
                }
            }
        }
    }
    for insert in nodes_multi(root, &["insert"]) {
        let mut cursor = insert.walk();
        let lists: Vec<Node<'_>> = insert
            .children(&mut cursor)
            .filter(|child| child.kind() == "list")
            .collect();
        if !lists.is_empty() {
            for col in nodes_multi(lists[0], &["column"]) {
                ctx.add(&text_of(Some(col), source_bytes), "insert", col, "");
            }
        }
    }
    for assignment in nodes_multi(root, &["assignment"]) {
        if let Some(target) = first_child_multi(assignment, &["field", "column", "identifier"]) {
            ctx.add(
                &text_of(Some(target), source_bytes),
                "assignment",
                target,
                &text_of(Some(assignment), source_bytes),
            );
        }
    }
    for where_node in nodes_multi(root, &["where"]) {
        for col in column_like_nodes(where_node) {
            ctx.add(
                &text_of(Some(col), source_bytes),
                "predicate",
                col,
                &text_of(Some(where_node), source_bytes),
            );
        }
    }
    for join in nodes_multi(root, &["join"]) {
        let condition = join_condition(join, source_bytes);
        for col in column_like_nodes(join) {
            if node_is_under_type(col, "relation") {
                continue;
            }
            ctx.add(&text_of(Some(col), source_bytes), "join", col, &condition);
        }
    }
    for order in nodes_multi(root, &["order_by"]) {
        for col in column_like_nodes(order) {
            ctx.add(
                &text_of(Some(col), source_bytes),
                "ordering",
                col,
                &text_of(Some(order), source_bytes),
            );
        }
    }
    for group in nodes_multi(root, &["group_by"]) {
        for col in column_like_nodes(group) {
            ctx.add(
                &text_of(Some(col), source_bytes),
                "grouping",
                col,
                &text_of(Some(group), source_bytes),
            );
        }
    }
    ctx.rows
}

/// `_crud`.
fn crud_of(root: Node<'_>) -> String {
    for node in main_statement_children(root) {
        if ["select", "insert", "update", "delete"].contains(&node.kind()) {
            return node.kind().to_string();
        }
    }
    if let Some(node) = nodes_multi(root, &["select", "insert", "update", "delete"]).first() {
        return node.kind().to_string();
    }
    String::new()
}

/// `_cte_names`.
fn cte_names(root: Node<'_>, source_bytes: &[u8]) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    for cte in nodes_multi(root, &["cte"]) {
        if let Some(name) = first_child_multi(cte, &["identifier"]) {
            rows.push(normalize_identifier(&text_of(Some(name), source_bytes)));
        }
    }
    rows
}

/// `_delete_from_nodes`.
fn delete_from_nodes(root: Node<'_>) -> Vec<Node<'_>> {
    let mut rows: Vec<Node<'_>> = Vec::new();
    for stmt in nodes_multi(root, &["statement"]) {
        let mut cursor = stmt.walk();
        let children: Vec<Node<'_>> = stmt.children(&mut cursor).collect();
        for (index, child) in children.iter().enumerate() {
            if child.kind() == "from"
                && children[..index].iter().any(|prev| prev.kind() == "delete")
            {
                rows.push(*child);
            }
        }
    }
    rows
}

/// `_main_statement_children`.
fn main_statement_children(root: Node<'_>) -> Vec<Node<'_>> {
    let Some(statement) = first_child_multi(root, &["statement"]) else {
        return Vec::new();
    };
    let mut cursor = statement.walk();
    statement
        .children(&mut cursor)
        .filter(|child| child.is_named() && child.kind() != "cte")
        .collect()
}

/// `_relation_parts` → (raw_name, alias, object_ref node).
fn relation_parts<'a>(node: Node<'a>, source_bytes: &[u8]) -> Option<(String, String, Node<'a>)> {
    if node.kind() == "identifier" || node.kind() == "object_reference" {
        return Some((text_of(Some(node), source_bytes), String::new(), node));
    }
    let object_ref = first_child_multi(node, &["object_reference"])?;
    let mut alias = String::new();
    let mut seen_ref = false;
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !child.is_named() {
            continue;
        }
        if child.id() == object_ref.id() {
            seen_ref = true;
            continue;
        }
        if seen_ref && child.kind() == "identifier" {
            alias = text_of(Some(child), source_bytes);
            break;
        }
    }
    Some((text_of(Some(object_ref), source_bytes), alias, object_ref))
}

/// `_join_condition`.
fn join_condition(join: Node<'_>, source_bytes: &[u8]) -> String {
    let mut seen_on = false;
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = join.walk();
    for child in join.children(&mut cursor) {
        if child.kind() == "keyword_on" {
            seen_on = true;
            continue;
        }
        if seen_on {
            parts.push(text_of(Some(child), source_bytes));
        }
    }
    normalize_ws(&parts.join(" "))
}

/// `_join_type`.
fn join_type_of(join: Node<'_>, source_bytes: &[u8]) -> String {
    let text = text_of(Some(join), source_bytes).to_lowercase();
    let before_join = text.split("join").next().unwrap_or("").trim().to_string();
    if before_join.is_empty() {
        "join".to_string()
    } else {
        normalize_ws(&format!("{before_join} join"))
    }
}

/// `_catalog_schema`.
fn catalog_schema(raw_name: &str) -> (String, String) {
    let parts: Vec<String> = raw_name
        .split('.')
        .map(strip_quotes)
        .collect();
    if parts.len() >= 3 {
        return (parts[parts.len() - 3].clone(), parts[parts.len() - 2].clone());
    }
    if parts.len() == 2 {
        return (String::new(), parts[0].clone());
    }
    (String::new(), String::new())
}

/// `_split_qualifier`.
fn split_qualifier(raw: &str) -> (String, String) {
    let parts: Vec<&str> = raw.split('.').collect();
    if parts.len() > 1 {
        (
            parts[..parts.len() - 1].join("."),
            parts[parts.len() - 1].to_string(),
        )
    } else {
        (String::new(), raw.to_string())
    }
}

/// `_column_like_nodes`.
fn column_like_nodes(node: Node<'_>) -> Vec<Node<'_>> {
    let mut rows: Vec<Node<'_>> = Vec::new();
    for child in nodes_multi(node, &["column", "field", "object_reference"]) {
        if (child.kind() == "field" || child.kind() == "object_reference")
            && is_relation_object_reference(child)
        {
            continue;
        }
        if node_is_under_type(child, "invocation") {
            continue;
        }
        if child.kind() == "field" && text_node_parent_type(child) == "list" {
            continue;
        }
        rows.push(child);
    }
    let mut deduped: Vec<Node<'_>> = Vec::new();
    let mut seen: std::collections::BTreeSet<(usize, usize)> = std::collections::BTreeSet::new();
    for row in rows {
        let key = (row.start_byte(), row.end_byte());
        if seen.insert(key) {
            deduped.push(row);
        }
    }
    deduped
}

/// `_is_relation_object_reference`.
fn is_relation_object_reference(node: Node<'_>) -> bool {
    let mut parent = node.parent();
    while let Some(current) = parent {
        if ["relation", "insert", "update", "from", "join", "cte"].contains(&current.kind()) {
            return true;
        }
        if [
            "where",
            "select",
            "assignment",
            "order_by",
            "group_by",
            "binary_expression",
        ]
        .contains(&current.kind())
        {
            return false;
        }
        parent = current.parent();
    }
    false
}

fn text_node_parent_type(node: Node<'_>) -> String {
    node.parent().map(|p| p.kind().to_string()).unwrap_or_default()
}

/// `_node_is_under_type`.
fn node_is_under_type(node: Node<'_>, node_type: &str) -> bool {
    let mut parent = node.parent();
    while let Some(current) = parent {
        if current.kind() == node_type {
            return true;
        }
        parent = current.parent();
    }
    false
}

/// `_skip_identifier`.
fn skip_identifier(value: &str) -> bool {
    let lower = value.to_lowercase();
    lower.starts_with("mbp") || lower.starts_with("mbt")
}

/// `_is_dynamic_token`.
fn is_dynamic_token(value: &str) -> bool {
    value.to_lowercase().starts_with("mbt")
}

/// `_normalize_identifier`.
fn normalize_identifier(value: &str) -> String {
    strip_quotes(value).to_lowercase()
}

/// `_strip_quotes`.
fn strip_quotes(value: &str) -> String {
    let text = value.trim();
    if text.len() >= 2
        && ((text.starts_with('"') && text.ends_with('"'))
            || (text.starts_with('\'') && text.ends_with('\''))
            || (text.starts_with('`') && text.ends_with('`'))
            || (text.starts_with('[') && text.ends_with(']')))
    {
        return text[1..text.len() - 1].to_string();
    }
    text.to_string()
}

/// `_normalize_ws`.
fn normalize_ws(text: &str) -> String {
    Regex::new(r"\s+")
        .unwrap()
        .replace_all(text, " ")
        .trim()
        .to_string()
}

/// `_fit_token`.
fn fit_token(token: &str, original: &str) -> String {
    let token_len = token.chars().count();
    let original_len = original.chars().count();
    if token_len >= original_len {
        token.chars().take(original_len).collect()
    } else {
        format!("{token}{}", " ".repeat(original_len - token_len))
    }
}

/// `_sized_token`.
fn sized_token(prefix: &str, ordinal: usize, width: usize) -> String {
    let base = format!("{prefix}{ordinal}");
    let base_len = base.chars().count();
    if base_len >= width {
        base.chars().take(width).collect()
    } else {
        format!("{base}{}", "x".repeat(width - base_len))
    }
}

/// `_dynamic_segments`.
fn dynamic_segments(stmt: &StatementFact) -> Vec<DynamicSegment> {
    let mut segments: Vec<DynamicSegment> = Vec::new();
    let controls: Vec<&crate::mybatis::models::DynamicNodeFact> = stmt
        .dynamic_nodes
        .iter()
        .filter(|item| item.node_kind == "control")
        .collect();
    let mut cursor = 0usize;
    let expanded = &stmt.expanded_body;
    for node in &stmt.dynamic_nodes {
        if node.tag != "#text" || node.text.is_empty() {
            continue;
        }
        let mut index = find_from(expanded, &node.text, cursor);
        if index.is_none() {
            index = expanded.find(&node.text);
        }
        let Some(index) = index else { continue };
        let start_byte = expanded[..index].len();
        let end_byte = start_byte + node.text.len();
        cursor = index + node.text.len();
        let owning_controls: Vec<&&crate::mybatis::models::DynamicNodeFact> = controls
            .iter()
            .filter(|control| span_contains(&control.source, &node.source))
            .collect();
        if owning_controls.is_empty() {
            continue;
        }
        let mut dynamic_node_ids: Vec<String> = Vec::new();
        let mut branch_roles: Vec<String> = Vec::new();
        for control in owning_controls {
            if !dynamic_node_ids.contains(&control.stable_id) {
                dynamic_node_ids.push(control.stable_id.clone());
            }
            if !control.branch_role.is_empty() && !branch_roles.contains(&control.branch_role) {
                branch_roles.push(control.branch_role.clone());
            }
        }
        segments.push(DynamicSegment {
            start_byte,
            end_byte,
            dynamic_node_ids,
            branch_roles,
        });
    }
    segments
}

fn find_from(haystack: &str, needle: &str, from: usize) -> Option<usize> {
    if from >= haystack.len() {
        return None;
    }
    haystack[from..].find(needle).map(|rel| rel + from)
}

/// `_provenance_for_node`.
fn provenance_for_node(
    node: Node<'_>,
    dynamic_segments: &[DynamicSegment],
) -> (Vec<String>, Vec<String>) {
    provenance_for_range(node.start_byte(), node.end_byte(), dynamic_segments)
}

/// `_provenance_for_range`.
fn provenance_for_range(
    start_byte: usize,
    end_byte: usize,
    dynamic_segments: &[DynamicSegment],
) -> (Vec<String>, Vec<String>) {
    let mut dynamic_ids: Vec<String> = Vec::new();
    let mut branch_roles: Vec<String> = Vec::new();
    for segment in dynamic_segments {
        if start_byte < segment.end_byte && end_byte > segment.start_byte {
            for item in &segment.dynamic_node_ids {
                if !dynamic_ids.contains(item) {
                    dynamic_ids.push(item.clone());
                }
            }
            for item in &segment.branch_roles {
                if !branch_roles.contains(item) {
                    branch_roles.push(item.clone());
                }
            }
        }
    }
    (dynamic_ids, branch_roles)
}

/// `_span_contains`.
fn span_contains(outer: &SourceSpan, inner: &SourceSpan) -> bool {
    if outer.file_path != inner.file_path {
        return false;
    }
    let outer_start = (outer.start_line, outer.start_column);
    let outer_end = (outer.end_line, outer.end_column);
    let inner_start = (inner.start_line, inner.start_column);
    let inner_end = (inner.end_line, inner.end_column);
    outer_start <= inner_start && inner_end <= outer_end
}

/// `_nodes` — recursive collect (pre-order, ALL children incl anonymous).
fn nodes_multi<'tree>(node: Node<'tree>, kinds: &[&str]) -> Vec<Node<'tree>> {
    let mut out = Vec::new();
    if kinds.contains(&node.kind()) {
        out.push(node);
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        out.extend(nodes_multi(child, kinds));
    }
    out
}

/// `_first_child` — named children.
fn first_child_multi<'tree>(node: Node<'tree>, kinds: &[&str]) -> Option<Node<'tree>> {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .find(|child| child.is_named() && kinds.contains(&child.kind()))
}

/// `_direct_children` — named children.
fn direct_children_multi<'tree>(node: Node<'tree>, kinds: &[&str]) -> Vec<Node<'tree>> {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .filter(|child| child.is_named() && kinds.contains(&child.kind()))
        .collect()
}

/// `_count_errors` — ERROR nodes + is_error.
fn count_errors(node: Node<'_>) -> i64 {
    let mut total = if node.kind() == "ERROR" || node.is_error() { 1 } else { 0 };
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        total += count_errors(child);
    }
    total
}

/// `_offset_span`.
fn offset_span(owner_source: &SourceSpan, node: Node<'_>) -> SourceSpan {
    SourceSpan::with_points(
        &owner_source.file_path,
        owner_source.start_line + node.start_position().row as i64,
        owner_source.start_line + node.end_position().row as i64,
        node.start_position().column as i64 + 1,
        node.end_position().column as i64 + 1,
    )
}

/// `_span_from_bytes`.
fn span_from_bytes(owner_source: &SourceSpan, text: &str, start_byte: usize, end_byte: usize) -> SourceSpan {
    let source_bytes = text.as_bytes();
    let before_start = safe_slice(source_bytes, start_byte);
    let through_end = safe_slice(source_bytes, end_byte);
    let start_line_offset = before_start.matches('\n').count();
    let end_line_offset = through_end.matches('\n').count();
    let start_line = owner_source.start_line + start_line_offset as i64;
    let end_line = owner_source.start_line + end_line_offset as i64;
    let start_column = if start_line_offset == 0 {
        owner_source.start_column + before_start.rsplit('\n').next().unwrap_or("").chars().count() as i64
    } else {
        before_start.rsplit('\n').next().unwrap_or("").chars().count() as i64 + 1
    };
    let end_column = if end_line_offset == 0 {
        owner_source.start_column + through_end.rsplit('\n').next().unwrap_or("").chars().count() as i64
    } else {
        through_end.rsplit('\n').next().unwrap_or("").chars().count() as i64 + 1
    };
    SourceSpan::with_points(
        &owner_source.file_path,
        start_line,
        end_line,
        start_column,
        end_column,
    )
}

fn safe_slice(bytes: &[u8], upto: usize) -> &str {
    let upto = upto.min(bytes.len());
    let mut bound = upto;
    while bound > 0 && std::str::from_utf8(&bytes[..bound]).is_err() {
        bound -= 1;
    }
    std::str::from_utf8(&bytes[..bound]).unwrap_or("")
}

/// `_stable`.
fn stable_id(prefix: &str, owner_id: &str, ordinal: usize, parts: &[&str]) -> String {
    let mut items: Vec<String> = vec![owner_id.to_string(), ordinal.to_string()];
    items.extend(parts.iter().map(|item| item.to_string()));
    let digest =
        crate::pyutil::sha1_hex(items.join("|").as_bytes())[..16].to_string();
    format!("{prefix}::{owner_id}::{ordinal}::{digest}")
}

fn text_of(node: Option<Node<'_>>, source_bytes: &[u8]) -> String {
    match node {
        None => String::new(),
        Some(node) => {
            cortex_analyzer_framework::ts::decode_ignore(&source_bytes[node.start_byte()..node.end_byte()])
        }
    }
}
