//! Port of the doc-tiny graph-write + graph-read layer:
//! - `ingest_to_graph_batch` / `ingest_to_graph` (Cypher kept byte-identical)
//! - `graphrag_query_langextract.fetch_related_graph` / `format_graph_context`
//!   / `build_generation_prompt`
//!
//! Backed by `cortex-falkordb` (same wire contract as the Python
//! `FalkorDBDriver`: RESP2 + `GRAPH.QUERY`/`GRAPH.RO_QUERY --compact`).

use std::collections::{BTreeMap, BTreeSet};

use cortex_falkordb::{FalkorDbClient, Param, QueryResult};
use serde_json::Value;

use crate::entity::select_primary_mention;

/// One paragraph batch item — mirrors the dicts appended to `graph_batch` in
/// `process_text`.
pub struct BatchItem {
    pub source_id: String,
    pub paragraph_id: i64,
    pub paragraph_text: Option<String>,
    pub is_short: bool,
    /// Ordered `(merge_key, node_dict)` pairs.
    pub nodes: Vec<(String, Value)>,
    pub relations: Vec<Value>,
    pub paragraph_props: Value,
}

/// FalkorDB client bound to one graph name (like `FalkorDBGraphStore`).
pub struct DocGraphStore {
    client: FalkorDbClient,
    graph: String,
}

fn value_to_param(value: &Value) -> Param {
    match value {
        Value::Null => Param::Null,
        Value::Bool(b) => Param::Bool(*b),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Param::Int(i)
            } else {
                Param::Float(n.as_f64().unwrap_or(0.0))
            }
        }
        Value::String(s) => Param::Str(s.clone()),
        Value::Array(items) => Param::List(items.iter().map(value_to_param).collect()),
        Value::Object(map) => Param::Map(
            map.iter()
                .map(|(k, v)| (k.clone(), value_to_param(v)))
                .collect(),
        ),
    }
}

fn opt_str_param(value: &Value) -> Param {
    match value {
        Value::Null => Param::Null,
        Value::String(s) => Param::Str(s.clone()),
        other => Param::Str(other.to_string()),
    }
}

/// Convert the JSON node dict payload (`paragraph_props` / `props`) to a
/// param map. `None` values are preserved as `null` (mirrors
/// `_stringify_values`' None passthrough for raw_values).
fn props_param(props: &Value) -> Param {
    match props {
        Value::Object(map) => Param::Map(
            map.iter()
                .map(|(k, v)| (k.clone(), value_to_param(v)))
                .collect(),
        ),
        _ => Param::Map(Vec::new()),
    }
}

impl DocGraphStore {
    /// Connect to a FalkorDB server (`redis://host:port`, `host:port`, `host`).
    pub fn connect(uri: &str, graph: &str) -> Result<Self, String> {
        let (host, port) = parse_uri(uri);
        let client = FalkorDbClient::connect_verified(&host, port)
            .map_err(|e| format!("connect {host}:{port}: {e}"))?;
        Ok(Self {
            client,
            graph: graph.to_string(),
        })
    }

    pub fn graph(&self) -> &str {
        &self.graph
    }

    /// `GRAPH.QUERY` write with params.
    pub fn run(
        &mut self,
        cypher: &str,
        params: BTreeMap<String, Param>,
    ) -> Result<QueryResult, String> {
        self.client
            .query(&self.graph, cypher, &params, None)
            .map_err(|e| e.to_string())
    }

    /// `GRAPH.RO_QUERY` read with params.
    pub fn run_read(
        &mut self,
        cypher: &str,
        params: BTreeMap<String, Param>,
    ) -> Result<QueryResult, String> {
        self.client
            .ro_query(&self.graph, cypher, &params)
            .map_err(|e| e.to_string())
    }

    /// Port of `ingest_to_graph_batch` — four UNWIND/MERGE write queries with
    /// the exact Cypher text of the Python reference.
    pub fn ingest_to_graph_batch(
        &mut self,
        items: &[BatchItem],
        project_id: Option<&str>,
        project_id_normalized: Option<&str>,
    ) -> Result<(), String> {
        let mut paragraphs: Vec<Param> = Vec::new();
        let mut entities: Vec<Param> = Vec::new();
        let mut relations: Vec<Param> = Vec::new();

        for item in items {
            if let Some(text) = &item.paragraph_text {
                paragraphs.push(Param::Map(vec![
                    ("doc_id".into(), Param::Str(item.source_id.clone())),
                    ("doc_name".into(), Param::Str(item.source_id.clone())),
                    ("source_id".into(), Param::Str(item.source_id.clone())),
                    ("paragraph_id".into(), Param::Int(item.paragraph_id)),
                    ("text".into(), Param::Str(text.clone())),
                    ("is_short".into(), Param::Bool(item.is_short)),
                    ("props".into(), props_param(&item.paragraph_props)),
                ]));
            }
            for (_, node) in &item.nodes {
                let primary = select_primary_mention(node.get("mentions").unwrap_or(&Value::Null));
                entities.push(Param::Map(vec![
                    ("id".into(), opt_str_param(&node["id"])),
                    ("name".into(), opt_str_param(&node["name"])),
                    ("type".into(), opt_str_param(&node["type"])),
                    ("name_norm".into(), opt_str_param(&node["name_norm"])),
                    ("source_id".into(), Param::Str(item.source_id.clone())),
                    ("paragraph_id".into(), Param::Int(item.paragraph_id)),
                    (
                        "confidence".into(),
                        value_to_param(node.get("confidence").unwrap_or(&Value::Null)),
                    ),
                    (
                        "start_char".into(),
                        value_to_param(primary.get("start_char").unwrap_or(&Value::Null)),
                    ),
                    (
                        "end_char".into(),
                        value_to_param(primary.get("end_char").unwrap_or(&Value::Null)),
                    ),
                    (
                        "surface".into(),
                        opt_str_param(primary.get("surface").unwrap_or(&Value::Null)),
                    ),
                ]));
            }
            for rel in &item.relations {
                relations.push(Param::Map(vec![
                    ("source_id".into(), opt_str_param(&rel["source_id"])),
                    ("target_id".into(), opt_str_param(&rel["target_id"])),
                    ("type".into(), opt_str_param(&rel["type"])),
                    ("source_doc".into(), Param::Str(item.source_id.clone())),
                    ("paragraph_id".into(), Param::Int(item.paragraph_id)),
                ]));
            }
        }

        let mut params: BTreeMap<String, Param> = BTreeMap::new();
        params.insert(
            "project_id".into(),
            project_id
                .map(|value| Param::Str(value.to_string()))
                .unwrap_or(Param::Null),
        );
        params.insert(
            "project_id_normalized".into(),
            project_id_normalized
                .map(|value| Param::Str(value.to_string()))
                .unwrap_or(Param::Null),
        );

        if !paragraphs.is_empty() {
            params.insert("paragraphs".into(), Param::List(paragraphs));
            self.run(PARAGRAPH_BATCH_CYPHER, params.clone())?;
        }
        if !entities.is_empty() {
            params.insert("entities".into(), Param::List(entities));
            self.run(ENTITY_MERGE_CYPHER, params.clone())?;
            self.run(HAS_ENTITY_CYPHER, params.clone())?;
        }
        if !relations.is_empty() {
            params.insert("relations".into(), Param::List(relations));
            self.run(RELATED_CYPHER, params)?;
        }
        Ok(())
    }

    /// Port of `graphrag_query_langextract.fetch_related_graph`.
    pub fn fetch_related_graph(&mut self, entity_ids: &[String]) -> Result<Vec<GraphRow>, String> {
        if entity_ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut params: BTreeMap<String, Param> = BTreeMap::new();
        params.insert(
            "entity_ids".into(),
            Param::List(entity_ids.iter().map(|id| Param::Str(id.clone())).collect()),
        );
        let result = self.run_read(FETCH_RELATED_CYPHER, params)?;
        let mut rows = Vec::new();
        for record in &result.records {
            if record.len() < 3 {
                continue;
            }
            rows.push(GraphRow {
                entity: node_props(&record[0]),
                relation: edge_props(&record[1]),
                related: node_props(&record[2]),
            });
        }
        Ok(rows)
    }
}

/// The three cypher statements of `ingest_to_graph_batch`, byte-identical to
/// the Python module.
pub const PARAGRAPH_BATCH_CYPHER: &str = r#"
                UNWIND $paragraphs AS row
                MERGE (d:Document {id: row.doc_id})
                SET d.name                 = row.doc_name,
                    d.project_id           = $project_id,
                    d.project_id_normalized = $project_id_normalized
                MERGE (p:Paragraph {source_id: row.source_id, paragraph_id: row.paragraph_id})
                SET p.text                 = row.text,
                    p.short                = row.is_short,
                    p.project_id           = $project_id,
                    p.project_id_normalized = $project_id_normalized
                SET p += row.props
                MERGE (d)-[:HAS_PARAGRAPH]->(p)
                "#;

pub const ENTITY_MERGE_CYPHER: &str = r#"
                UNWIND $entities AS row
                MERGE (e:Entity {id: row.id})
                SET e.name                 = coalesce(e.name, row.name),
                    e.type                 = row.type,
                    e.name_norm            = row.name_norm,
                    e.project_id           = $project_id,
                    e.project_id_normalized = $project_id_normalized
                "#;

pub const HAS_ENTITY_CYPHER: &str = r#"
                UNWIND $entities AS row
                MATCH (p:Paragraph {source_id: row.source_id, paragraph_id: row.paragraph_id})
                MATCH (e:Entity {id: row.id})
                MERGE (p)-[r:HAS_ENTITY]->(e)
                SET r.source_id            = row.source_id,
                    r.paragraph_id         = row.paragraph_id,
                    r.confidence           = row.confidence,
                    r.start_char           = row.start_char,
                    r.end_char             = row.end_char,
                    r.surface              = row.surface,
                    r.project_id           = $project_id,
                    r.project_id_normalized = $project_id_normalized
                "#;

pub const RELATED_CYPHER: &str = r#"
                UNWIND $relations AS row
                MATCH (s:Entity {id: row.source_id})
                MATCH (t:Entity {id: row.target_id})
                MERGE (s)-[r:RELATED {type: row.type, source_id: row.source_doc, paragraph_id: row.paragraph_id}]->(t)
                SET r.project_id           = $project_id,
                    r.project_id_normalized = $project_id_normalized
                "#;

/// `MATCH (e:Entity)-[r]-(related) WHERE e.id IN $entity_ids RETURN e, r, related`
pub const FETCH_RELATED_CYPHER: &str = r#"
    MATCH (e:Entity)-[r]-(related)
    WHERE e.id IN $entity_ids
    RETURN e, r, related
    "#;

/// One `fetch_related_graph` record (`e`, `r`, `related` property dicts).
pub struct GraphRow {
    pub entity: BTreeMap<String, String>,
    pub relation: BTreeMap<String, String>,
    pub related: BTreeMap<String, String>,
}

fn node_props(value: &cortex_falkordb::FalkorValue) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    if let cortex_falkordb::FalkorValue::Node { properties, .. } = value {
        for (key, val) in properties {
            out.insert(key.clone(), falkor_string(val));
        }
    }
    out
}

fn edge_props(value: &cortex_falkordb::FalkorValue) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    if let cortex_falkordb::FalkorValue::Edge { properties, .. } = value {
        for (key, val) in properties {
            out.insert(key.clone(), falkor_string(val));
        }
    }
    out
}

fn falkor_string(value: &cortex_falkordb::FalkorValue) -> String {
    match value {
        cortex_falkordb::FalkorValue::String(s) => s.clone(),
        cortex_falkordb::FalkorValue::Int(i) => i.to_string(),
        cortex_falkordb::FalkorValue::Double(d) => d.to_string(),
        cortex_falkordb::FalkorValue::Bool(b) => b.to_string(),
        cortex_falkordb::FalkorValue::Null => String::new(),
        other => format!("{other:?}"),
    }
}

/// Port of `format_graph_context(subgraph)` — sorted unique node names plus
/// `"{source} {relation} {target}"` edge strings in result order.
pub fn format_graph_context(subgraph: &[GraphRow]) -> GraphContext {
    let mut nodes: BTreeSet<String> = BTreeSet::new();
    let mut edges: Vec<String> = Vec::new();
    for entry in subgraph {
        // Python: `if not entity or not related or not relation: continue`
        // (falsy = empty property dict on any of the three slots).
        if entry.entity.is_empty() || entry.related.is_empty() || entry.relation.is_empty() {
            continue;
        }
        // Python f-string renders a missing name as `None`; the node set is
        // filtered with `if n` afterwards.
        let entity_name = entry
            .entity
            .get("name")
            .cloned()
            .unwrap_or_else(|| "None".into());
        let related_name = entry
            .related
            .get("name")
            .cloned()
            .unwrap_or_else(|| "None".into());
        if entry.entity.contains_key("name") && !entity_name.is_empty() {
            nodes.insert(entity_name.clone());
        }
        if entry.related.contains_key("name") && !related_name.is_empty() {
            nodes.insert(related_name.clone());
        }
        let rel_type = match entry.relation.get("type") {
            Some(t) => t.clone(),
            None => "RELATED".to_string(),
        };
        edges.push(format!("{entity_name} {rel_type} {related_name}"));
    }
    GraphContext {
        nodes: nodes.into_iter().collect(),
        edges,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct GraphContext {
    pub nodes: Vec<String>,
    pub edges: Vec<String>,
}

/// Port of `textwrap.dedent` (CPython 3.12 regex implementation):
/// 1. whitespace-only lines (only spaces/tabs) are emptied unconditionally;
/// 2. the margin is the reduced common leading whitespace of every line that
///    contains non-whitespace content;
/// 3. a non-empty margin is removed from the start of every line.
pub fn py_dedent(text: &str) -> String {
    // 1. `^[ \t]+$` → '' (multiline).
    let emptied: String = text
        .split('\n')
        .map(|line| {
            if !line.is_empty() && line.chars().all(|c| c == ' ' || c == '\t') {
                ""
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n");

    // 2. margin over content lines (`(^[ \t]*)(?:[^ \t\n])`).
    let mut margin: Option<String> = None;
    for line in emptied.split('\n') {
        let indent_len = line.chars().take_while(|c| *c == ' ' || *c == '\t').count();
        if indent_len >= line.chars().count() {
            continue; // whitespace-only or empty → not a content line
        }
        let indent: String = line.chars().take(indent_len).collect();
        margin = Some(match &margin {
            None => indent,
            Some(current) => {
                if indent.starts_with(current.as_str()) {
                    current.clone()
                } else if current.starts_with(indent.as_str()) {
                    indent
                } else {
                    String::new()
                }
            }
        });
    }

    // 3. strip the margin at line starts.
    match margin.filter(|m| !m.is_empty()) {
        Some(m) => emptied
            .split('\n')
            .map(|line| line.strip_prefix(m.as_str()).unwrap_or(line))
            .collect::<Vec<_>>()
            .join("\n"),
        None => emptied,
    }
}

/// Port of `build_generation_prompt(query, graph_context, passages)`.
///
/// Byte-compatible with the Python `textwrap.dedent`-ed f-string: when the
/// joined passages are multi-line the common margin is empty and the 8-space
/// indentation of the literal SURVIVES into the prompt — this replicates that
/// behavior faithfully.
pub fn build_generation_prompt(query: &str, context: &GraphContext, passages: &[String]) -> String {
    let nodes_str = context.nodes.join(", ");
    let edges_str = context.edges.join("; ");
    let passages_str = passages.join("\n\n");
    // The literal f-string of the Python reference, indented 8 spaces inside
    // its function body (kept on one line so Rust source formatting cannot
    // eat the margin).
    let raw = format!(
        "        You are an assistant with access to a knowledge graph and retrieved passages.\n\n        Nodes: {nodes_str}\n        Edges: {edges_str}\n\n        Passages:\n        {passages_str}\n\n        Answer the user question using the graph context and passages.\n        Question: {query}\n        "
    );
    py_dedent(&raw)
}

/// Parse `redis://host:port`, `host:port` or bare `host`.
pub fn parse_uri(uri: &str) -> (String, u16) {
    let trimmed = uri.trim();
    let without_scheme = trimmed
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(trimmed);
    let without_scheme = without_scheme.split('/').next().unwrap_or(without_scheme);
    match without_scheme.rsplit_once(':') {
        Some((host, port)) => (host.to_string(), port.parse().unwrap_or(6379)),
        None => (without_scheme.to_string(), 6379),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prompt_matches_python_layout() {
        let ctx = GraphContext {
            nodes: vec!["Acme".to_string(), "Berlin".to_string()],
            edges: vec!["Acme LOCATED_IN Berlin".to_string()],
        };
        // Multi-line passages: textwrap.dedent no-ops (margin empty), so the
        // 8-space indentation of the literal survives — verified against the
        // python module by the phase 14 parity harness.
        let prompt = build_generation_prompt(
            "Where is Acme?",
            &ctx,
            &[
                "Acme is in Berlin.".to_string(),
                "Berlin is big.".to_string(),
            ],
        );
        let expected = "        You are an assistant with access to a knowledge graph and retrieved passages.\n\n        Nodes: Acme, Berlin\n        Edges: Acme LOCATED_IN Berlin\n\n        Passages:\n        Acme is in Berlin.\n\nBerlin is big.\n\n        Answer the user question using the graph context and passages.\n        Question: Where is Acme?\n";
        assert_eq!(prompt, expected);
        // Single-line passage: margin is 8 spaces -> fully dedented.
        let single =
            build_generation_prompt("Where is Acme?", &ctx, &["Acme is in Berlin.".to_string()]);
        assert_eq!(
            single,
            "You are an assistant with access to a knowledge graph and retrieved passages.\n\
             \n\
             Nodes: Acme, Berlin\n\
             Edges: Acme LOCATED_IN Berlin\n\
             \n\
             Passages:\n\
             Acme is in Berlin.\n\
             \n\
             Answer the user question using the graph context and passages.\n\
             Question: Where is Acme?\n"
        );
    }

    #[test]
    fn dedent_follows_textwrap() {
        assert_eq!(py_dedent("    a\n    b"), "a\nb");
        assert_eq!(py_dedent("    a\nb"), "    a\nb"); // shorter indent -> margin ""
        assert_eq!(py_dedent("    a\n\n    b"), "a\n\nb"); // blank lines skipped
        assert_eq!(py_dedent("\ta\n\tb"), "a\nb"); // tabs count
        // CPython: whitespace-only lines are emptied even when margin ends ""
        assert_eq!(py_dedent("    a\nb\n    "), "    a\nb\n");
        assert_eq!(py_dedent("    a\n\t\n    b"), "a\n\nb");
    }

    #[test]
    fn uri_parsing() {
        assert_eq!(
            parse_uri("redis://127.0.0.1:6379"),
            ("127.0.0.1".into(), 6379)
        );
        assert_eq!(parse_uri("localhost:6380"), ("localhost".into(), 6380));
        assert_eq!(parse_uri("localhost"), ("localhost".into(), 6379));
    }

    #[test]
    fn graph_context_skips_missing_names() {
        let mut relation = BTreeMap::new();
        relation.insert("type".to_string(), "RELATED".to_string());
        let rows = vec![GraphRow {
            entity: BTreeMap::new(),
            relation,
            related: BTreeMap::new(),
        }];
        let ctx = format_graph_context(&rows);
        assert!(ctx.nodes.is_empty());
        assert!(ctx.edges.is_empty());
    }
}
