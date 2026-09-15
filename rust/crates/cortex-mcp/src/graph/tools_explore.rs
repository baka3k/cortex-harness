// Faithful port của Python bodies: giữ cấu trúc nguồn để đối chiếu parity;
// các lint style dưới đây được allow có chủ đích ở module graph.
#![allow(
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::unnecessary_filter_map,
    clippy::needless_borrow,
    clippy::map_clone,
    clippy::obfuscated_if_else,
    clippy::unnecessary_lazy_evaluations,
    clippy::let_and_return,
    clippy::useless_format,
    clippy::manual_strip,
    clippy::unwrap_or_default
)]
//! `explore_graph` — port của `services/explore_service.py` +
//! `tools/common/intelligent_retrieval.py` (network IO: graph keyword search,
//! graph expansion qua embedded FalkorDB; Qdrant local-store lane trả rỗng
//! như Python khi scope không khớp collection — embedder sentence-transformers
//! là Python-plane, documented) + `result_packager.py` (per-node reason).

use std::collections::{BTreeMap, BTreeSet};

use cortex_retrieval::bm25::{Bm25Ranker, Document};
use cortex_retrieval::fusion::{Candidate, FusionEngine, SeedInputs};
use cortex_retrieval::query_understanding::QueryUnderstanding;
use serde_json::{json, Map, Value};

use super::{
    is_database_not_found_error, lookup_key, resolve_db_candidates,
};
use crate::framework_registry::{capability_for_parser, text_search_properties};
use crate::project_registry::{
    list_registered_projects, resolve_project_scope_candidates, resolve_project_targets,
};
use super::runtime;

const MODE_SEMANTIC: &str = "semantic";
const MODE_HYBRID: &str = "hybrid";
const MODE_GRAPH_EXPANDED: &str = "graph_expanded";

fn expand_graph_for_mode(mode: &str) -> bool {
    mode == MODE_GRAPH_EXPANDED
}

fn mode_weight_overrides(mode: &str) -> Vec<(&'static str, f64)> {
    match mode {
        MODE_SEMANTIC => vec![("semantic", 0.70), ("keyword", 0.05), ("graph", 0.05)],
        MODE_GRAPH_EXPANDED => vec![("graph", 0.30)],
        _ => Vec::new(),
    }
}

/// `_hop_proximity` (graph_expander).
fn hop_proximity(hops: i64) -> f64 {
    match hops {
        0 => 1.00,
        1 => 0.80,
        2 => 0.60,
        3 => 0.40,
        4 => 0.20,
        _ => (1.0 - hops as f64 * 0.20).max(0.0),
    }
}

pub struct SearchTargets(pub Vec<(String, String, String)>);

/// `ExploreService._resolve_search_targets`.
pub fn resolve_search_targets(
    db: Option<&str>,
    collection: Option<&str>,
    project_id: Option<&str>,
) -> Result<SearchTargets, String> {
    if let Some(project_id) = project_id.map(str::trim).filter(|text| !text.is_empty()) {
        let candidates = resolve_project_scope_candidates(Some(project_id), None)
            .map_err(|error| error.to_string())?;
        if !candidates.is_empty() {
            let targets = candidates
                .into_iter()
                .map(|target| {
                    (
                        target.project_id_normalized.clone(),
                        db.map(str::to_string).unwrap_or_else(|| target.code_graph.clone()),
                        collection
                            .map(str::to_string)
                            .unwrap_or_else(|| target.code_qdrant_collection.clone()),
                    )
                })
                .collect();
            return Ok(SearchTargets(targets));
        }
        let normalized = project_id.trim().to_lowercase();
        return Ok(SearchTargets(vec![(
            normalized.clone(),
            db.map(str::to_string).unwrap_or(project_id.to_string()),
            collection
                .map(str::to_string)
                .unwrap_or(project_id.to_string()),
        )]));
    }
    if db.map(|value| !value.trim().is_empty()).unwrap_or(false)
        || collection.map(|value| !value.trim().is_empty()).unwrap_or(false)
    {
        let default_db = runtime_default_db();
        return Ok(SearchTargets(vec![(
            String::new(),
            db.filter(|value| !value.trim().is_empty())
                .map(str::to_string)
                .unwrap_or(default_db),
            collection
                .filter(|value| !value.trim().is_empty())
                .map(str::to_string)
                .unwrap_or_default(),
        )]));
    }
    let mut resolved: Vec<(String, String, String)> = Vec::new();
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let registered = list_registered_projects(None).map_err(|error| error.to_string())?;
    for project in &registered {
        let target = resolve_project_targets(Some(project), None).map_err(|error| error.to_string())?;
        let key = (target.code_graph.clone(), target.code_qdrant_collection.clone());
        if seen.insert(key) {
            resolved.push((
                target.project_id_normalized.clone(),
                target.code_graph.clone(),
                target.code_qdrant_collection.clone(),
            ));
        }
    }
    if resolved.is_empty() {
        resolved.push((String::new(), runtime_default_db(), String::new()));
    }
    Ok(SearchTargets(resolved))
}

fn runtime_default_db() -> String {
    std::env::var("FALKORDB_GRAPH")
        .or_else(|_| std::env::var("FALKORDB_DATABASE"))
        .unwrap_or_else(|_| "hyper_graph".to_string())
}

// ---------------------------------------------------------------------------
// Graph keyword search (intelligent_retrieval._graph_keyword_search)
// ---------------------------------------------------------------------------

#[allow(clippy::too_many_arguments)]
fn graph_keyword_search(
    runtime: &mut runtime::GraphRuntime,
    query: &str,
    database: &str,
    limit: i64,
    labels: Option<&[String]>,
    properties: Option<&[String]>,
    project_id: Option<&str>,
) -> Vec<Map<String, Value>> {
    let tokens: Vec<String> = query
        .split_whitespace()
        .map(|token| token.trim().to_lowercase())
        .filter(|token| !token.is_empty())
        .collect();
    if tokens.is_empty() {
        return Vec::new();
    }
    let safe_labels: Vec<String> = labels
        .unwrap_or(&[])
        .iter()
        .filter(|label| label.replace('_', "").chars().all(|ch| ch.is_alphanumeric()))
        .cloned()
        .collect();
    let default_properties = ["name", "qualified_name", "comment"];
    let safe_properties: Vec<String> = properties
        .unwrap_or(&default_properties.iter().map(|value| value.to_string()).collect::<Vec<_>>())
        .iter()
        .filter(|property| property.replace('_', "").chars().all(|ch| ch.is_alphanumeric()))
        .cloned()
        .collect();
    let label_clause = if !safe_labels.is_empty() {
        format!(
            "({}) AND ",
            safe_labels
                .iter()
                .map(|label| format!("n:{label}"))
                .collect::<Vec<_>>()
                .join(" OR ")
        )
    } else {
        String::new()
    };
    let property_clause = safe_properties
        .iter()
        .map(|property| format!("toLower(coalesce(n.{property}, '')) CONTAINS q"))
        .collect::<Vec<_>>()
        .join(" OR ");
    let cypher = format!(
        "MATCH (n) WHERE ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) AND \
         {label_clause}any(q IN $qs WHERE {property_clause}) \
         RETURN n LIMIT $limit"
    );
    let mut params: BTreeMap<String, Value> = BTreeMap::new();
    params.insert("qs".to_string(), json!(tokens));
    params.insert("limit".to_string(), json!(limit));
    params.insert(
        "project_id".to_string(),
        match lookup_key(project_id) {
            Some(key) => json!(key),
            None => Value::Null,
        },
    );
    let prepared = super::prepare_params_map_btree(&params);
    match runtime.execute_query(&cypher, &prepared, Some(database)) {
        Ok(rows) => rows
            .iter()
            .filter_map(|row| row.get("n").and_then(Value::as_object).cloned())
            .collect(),
        Err(_) => Vec::new(),
    }
}

// ---------------------------------------------------------------------------
// Graph expansion (graph_expander.GraphExpander.expand, include_seeds=False)
// ---------------------------------------------------------------------------

struct ExpandedNode {
    candidate: Candidate,
    hop_distance: i64,
    seed_id: String,
    seed_ids: Vec<String>,
    graph_proximity: f64,
}

fn expand_seeds(
    runtime: &mut runtime::GraphRuntime,
    seed_ids: &[String],
    depth: i64,
    rel_types: Option<&[String]>,
    limit: i64,
    project_id: Option<&str>,
) -> Vec<ExpandedNode> {
    let rels: Vec<String> = rel_types
        .map(<[String]>::to_vec)
        .unwrap_or_else(|| {
            [
                "CALLS", "USES_TYPE", "REFERENCES", "INHERITS", "ALIASES", "ALIAS_OF",
            ]
            .iter()
            .map(|value| value.to_string())
            .collect()
        });
    if seed_ids.is_empty() {
        return Vec::new();
    }
    let rel_token = rels.join("|");
    let cypher = format!(
        "\nUNWIND $seed_ids AS sid\nMATCH (seed {{id: sid}})\nWHERE ($project_id IS NULL OR seed.project_id_normalized STARTS WITH $project_id_normalized)\n    MATCH p = (seed)-[:{rel_token}*1..{depth}]-(neighbor)\n    WHERE neighbor.id <> sid\n      AND ($project_id IS NULL OR neighbor.project_id_normalized STARTS WITH $project_id_normalized)\n    WITH neighbor, min(length(p)) AS hops, collect(DISTINCT sid) AS seed_ids\n    RETURN\n        seed_ids,\n        seed_ids[0]             AS seed_id,\n        hops,\n        neighbor.id            AS node_id,\n    neighbor.name          AS name,\n    coalesce(neighbor.qualified_name, neighbor.name) AS qualified_name,\n    coalesce(labels(neighbor)[0], 'Node') AS kind,\n    coalesce(neighbor.file_path, '')       AS file_path,\n    neighbor.doc_confidence                AS doc_confidence,\n    neighbor.intent                        AS intent,\n    neighbor.exported                      AS exported,\n        neighbor.side_effect                   AS side_effect,\n        neighbor.project_id                    AS project_id,\n        neighbor.target_name                   AS target_name,\n        neighbor.signature                     AS signature,\n        neighbor.language                      AS language,\n        neighbor.framework                     AS framework,\n        neighbor.resolution_status             AS resolution_status,\n        neighbor.start_line                    AS start_line,\n        neighbor.end_line                      AS end_line\n    ORDER BY hops ASC\nLIMIT $limit\n"
    );
    let mut params: BTreeMap<String, Value> = BTreeMap::new();
    params.insert("seed_ids".to_string(), json!(seed_ids));
    params.insert("limit".to_string(), json!(limit));
    params.insert(
        "project_id".to_string(),
        match lookup_key(project_id) {
            Some(key) => json!(key),
            None => Value::Null,
        },
    );
    let prepared = super::prepare_params_map_btree(&params);
    let rows = match runtime.execute_query(&cypher, &prepared, None) {
        Ok(rows) => rows,
        Err(_) => return Vec::new(),
    };
    let mut nodes: Vec<ExpandedNode> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for row in &rows {
        let node_id = row
            .get("node_id")
            .map(value_to_string_or_empty)
            .unwrap_or_default();
        if node_id.is_empty() || seen.contains(&node_id) {
            continue;
        }
        seen.insert(node_id.clone());
        let hops = row
            .get("hops")
            .and_then(Value::as_i64)
            .map(|hops| hops.max(1))
            .unwrap_or(depth);
        let seed_ids_value: Vec<String> = row
            .get("seed_ids")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(value_to_string_or_empty).collect())
            .unwrap_or_default();
        let seed_id = row
            .get("seed_id")
            .map(value_to_string_or_empty)
            .unwrap_or_default();
        let props = candidate_from_expansion_row(row, node_id.clone(), hops, &seed_id, seed_ids_value);
        nodes.push(props);
    }
    nodes
}

fn candidate_from_expansion_row(
    row: &Map<String, Value>,
    node_id: String,
    hops: i64,
    seed_id: &str,
    seed_ids: Vec<String>,
) -> ExpandedNode {
    let get_str = |key: &str| {
        row.get(key)
            .map(value_to_string_or_empty)
            .unwrap_or_default()
    };
    let doc_confidence = row
        .get("doc_confidence")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let proximity = hop_proximity(hops);
    ExpandedNode {
        graph_proximity: proximity,
        hop_distance: hops,
        seed_id: seed_id.to_string(),
        seed_ids,
        candidate: Candidate {
            node_id,
            name: get_str("name"),
            qualified_name: get_str("qualified_name"),
            kind: get_str("kind"),
            file_path: get_str("file_path"),
            semantic: 0.0,
            keyword: 0.0,
            graph: proximity,
            freshness: 0.0,
            confidence: doc_confidence.clamp(0.0, 1.0),
            usage: 0.0,
            bm25: 0.0,
            intent: get_str("intent"),
            exported: row.get("exported").and_then(Value::as_bool).unwrap_or(false),
            side_effect: row.get("side_effect").and_then(Value::as_bool).unwrap_or(false),
            return_type: String::new(),
            project_id: get_str("project_id"),
            language: String::new(),
            source: "graph_expansion".to_string(),
        },
    }
}

fn value_to_string_or_empty(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// explore_graph tool
// ---------------------------------------------------------------------------

pub fn tool_explore_graph(
    runtime: &mut runtime::GraphRuntime,
    arguments: Value,
) -> Result<Value, String> {
    let query = arguments
        .get("query")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    let mode_argument = arguments.get("mode").and_then(Value::as_str).unwrap_or("");
    let requested_mode = if mode_argument.is_empty() { "hybrid" } else { mode_argument };
    let top_k_value = arguments.get("top_k").cloned().unwrap_or(Value::Null);
    if !top_k_value.is_null() {
        // `_parse_positive_int` gate: reject non-int values.
        let parsed = parse_positive_int(&top_k_value);
        match parsed {
            Ok(None) => {}
            Ok(Some(value)) => {
                if !(1..=100).contains(&value) {
                    return Err(format!(
                        "top_k must be an integer between 1 and 100 (accepted_limit: 100, requested_top_k: {value})"
                    ));
                }
            }
            Err(message) => {
                return Err(message);
            }
        }
    }
    let top_k = parse_positive_int(&top_k_value)
        .ok()
        .flatten()
        .unwrap_or(10)
        .clamp(1, 100) as usize;
    if query.is_empty() {
        return Ok(json!({
            "matched_nodes": [],
            "entry_points": [],
            "related_paths": [],
            "explanation": "No query provided.",
            "confidence": 0.0,
            "query_analysis": {},
            "mode": requested_mode,
        }));
    }
    let collection_argument = payload_optional_str(&arguments, "collection");
    let project_id = payload_optional_str(&arguments, "project_id");
    let debug = arguments.get("debug").and_then(Value::as_bool).unwrap_or(false);
    let selected_parser = crate::dispatch::normalize_parser_type(payload_optional_str(&arguments, "parser_type").as_deref());
    if let Some(parser) = &selected_parser
        && capability_for_parser(Some(parser)).is_none()
    {
        return Err(format!("Parser '{parser}' is not registered."));
    }
    let capability = capability_for_parser(selected_parser.as_deref());

    let targets = resolve_search_targets(
        None,
        collection_argument.as_deref(),
        project_id.as_deref(),
    )?;

    let understanding = if query.contains('\n') || query.chars().count() > 200 {
        QueryUnderstanding::from_paragraph(&query, 400)
    } else {
        QueryUnderstanding::from_text(&query)
    };

    let mut mode = requested_mode.to_string();
    if ![
        MODE_SEMANTIC.to_string(),
        MODE_HYBRID.to_string(),
        MODE_GRAPH_EXPANDED.to_string(),
    ]
    .contains(&mode)
    {
        mode = MODE_HYBRID.to_string();
    }

    // Relationship resolution cho non-semantic modes (capability defaults).
    let mut relationship_types: Option<Vec<String>> = None;
    let mut effective_mode = mode.clone();
    let mut capability_diagnostics: Option<Value> = None;
    if capability.is_some() && mode != MODE_SEMANTIC {
        let defaults = crate::framework_registry::default_relationships(
            capability.map(|item| item.name.as_str()),
            Some("explore_graph"),
        );
        let db_candidates = resolve_db_candidates(project_id.as_deref())?;
        let resolved = super::resolve_rel_types_with_diagnostics(
            runtime,
            Some(&json!(defaults)),
            selected_parser.as_deref(),
            &db_candidates,
            false,
        )?;
        if resolved.applied.is_empty() {
            effective_mode = MODE_SEMANTIC.to_string();
        } else {
            relationship_types = Some(resolved.applied);
        }
        capability_diagnostics = Some(resolved.diagnostics);
    }

    let searchable_labels: Option<Vec<String>> = capability.map(|capability| {
        let mut labels: Vec<String> = capability
            .labels
            .iter()
            .cloned()
            .collect();
        labels.sort();
        labels
    });
    let searchable_properties: Option<Vec<String>> =
        capability.map(|capability| text_search_properties(Some(capability.name.as_str())));

    // Per-target retrieval + global merge (`_merge_target_results`).
    let mut target_results: Vec<(String, Vec<ScoredOwned>)> = Vec::new();
    for (target_project_id, target_db, target_collection) in &targets.0 {
        let scored = run_retrieval(
            runtime,
            &understanding,
            target_db,
            target_collection,
            top_k,
            &mode,
            debug,
            relationship_types.as_deref(),
            searchable_labels.as_deref(),
            searchable_properties.as_deref(),
            project_id.as_deref(),
        );
        target_results.push((target_project_id.clone(), scored));
    }
    let scored_results = merge_target_results(target_results, top_k);

    let mut packed = pack_results(&scored_results, &understanding, &effective_mode);
    let graph_requested = effective_mode != MODE_SEMANTIC;
    packed["retrieval"] = json!({
        "graph_provider": "falkordb",
        "graph_database": if targets.0.len() == 1 { json!(targets.0[0].1.clone()) } else { Value::Null },
        "graph_databases": targets.0.iter().map(|target| json!(target.1.clone())).collect::<Vec<_>>(),
        "qdrant_collections": targets.0.iter().map(|target| json!(target.2.clone())).collect::<Vec<_>>(),
        "graph_requested": graph_requested,
        "graph_connected": graph_requested,
        "graph_expansion_requested": expand_graph_for_mode(&effective_mode),
        "semantic_enabled": false,
        "degraded": false,
    });
    packed["query_engine"] = json!(crate::framework_registry::query_engine_for_backend(
        if selected_parser.is_some() {
            Some(crate::framework_registry::capability_for_parser(selected_parser.as_deref()).map(|item| item.backend.as_str()).unwrap_or("cplus"))
        } else {
            None
        }
        .or(Some("cplus")),
    ));
    packed["capability"] = crate::dispatch::capability_summary(selected_parser.as_deref());
    if let Some(diagnostics) = capability_diagnostics {
        packed["capability_diagnostics"] = diagnostics;
    }
    if effective_mode != requested_mode {
        packed["requested_mode"] = json!(requested_mode);
        packed["graph_expansion"] = json!({
            "requested": true,
            "enabled": false,
            "outcome": "unavailable",
            "reason": "No requested relationships are available in the active graph provider.",
            "relationship_types": [],
        });
    }
    Ok(packed)
}

fn payload_optional_str(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|text| !text.is_empty())
}

fn parse_positive_int(raw: &Value) -> Result<Option<i64>, String> {
    match raw {
        Value::Null => Ok(None),
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                Ok(Some(int))
            } else {
                Err("top_k must be an integer (invalid_parameters)".to_string())
            }
        }
        Value::String(text) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                return Ok(None);
            }
            trimmed
                .parse::<i64>()
                .map(Some)
                .map_err(|_| format!("top_k must be an integer, got {trimmed:?} (invalid_parameters)"))
        }
        Value::Bool(_) => Err("top_k must be an integer (invalid_parameters)".to_string()),
        other => Err(format!("top_k must be an integer, got {other} (invalid_parameters)")),
    }
}

// ---------------------------------------------------------------------------
// Retrieval engine (offline fusion + IO lanes)
// ---------------------------------------------------------------------------

pub struct ScoredOwned {
    pub node_id: String,
    pub score: f64,
    pub candidate: Candidate,
    pub hop_distance: i64,
    pub seed_id: String,
    pub seed_ids: Vec<String>,
}

#[allow(clippy::too_many_arguments)]
fn run_retrieval(
    runtime: &mut runtime::GraphRuntime,
    understanding: &QueryUnderstanding,
    database: &str,
    collection: &str,
    top_k: usize,
    mode: &str,
    debug: bool,
    graph_rel_types: Option<&[String]>,
    searchable_labels: Option<&[String]>,
    searchable_properties: Option<&[String]>,
    project_id: Option<&str>,
) -> Vec<ScoredOwned> {
    let expand_graph = expand_graph_for_mode(mode);
    let weight_override = mode_weight_overrides(mode);
    let embed_query = if understanding.embedding_text.is_empty() {
        understanding.raw_query.clone()
    } else {
        understanding.embedding_text.clone()
    };
    let active_project_id = lookup_key(project_id);

    // 1. Qdrant lane — embed qua `cortex-embed` rồi search store (local
    // sidecar | remote REST); mọi lỗi → seeds rỗng đúng như python
    // `_retrieve_qdrant` (try/except → []).
    let seeds_qdrant: Vec<Candidate> =
        qdrant_seeds(&embed_query, collection, top_k, project_id);

    // 2. Keyword lane.
    let keyword_nodes = graph_keyword_search(
        runtime,
        &embed_query,
        database,
        20,
        searchable_labels,
        searchable_properties,
        active_project_id.as_deref(),
    );
    let seeds_kw: Vec<Candidate> = keyword_nodes
        .iter()
        .map(|node| {
            let get_str = |key: &str| {
                node.get(key)
                    .map(value_to_string_or_empty)
                    .unwrap_or_default()
            };
            Candidate {
                node_id: get_str("id"),
                name: get_str("name"),
                qualified_name: get_str("qualified_name"),
                kind: get_str("kind"),
                file_path: get_str("file_path"),
                semantic: 0.0,
                keyword: 1.0,
                graph: 0.0,
                freshness: 0.0,
                confidence: node.get("doc_confidence").and_then(Value::as_f64).unwrap_or(0.0),
                usage: 0.0,
                bm25: 0.0,
                intent: get_str("intent"),
                exported: node.get("exported").and_then(Value::as_bool).unwrap_or(false),
                side_effect: node.get("side_effect").and_then(Value::as_bool).unwrap_or(false),
                return_type: get_str("return_type"),
                project_id: get_str("project_id"),
                language: get_str("language"),
                source: "graph_keyword".to_string(),
            }
        })
        .collect();

    // 3. Merge + BM25 auto-corpus + expansion qua FusionEngine.
    let mut order: Vec<String> = Vec::new();
    let mut candidates: BTreeMap<String, Candidate> = BTreeMap::new();
    let push_candidate = |order: &mut Vec<String>,
                              candidates: &mut BTreeMap<String, Candidate>,
                              candidate: Candidate| {
        let node_id = candidate.node_id.clone();
        if node_id.is_empty() || candidates.contains_key(&node_id) {
            return;
        }
        order.push(node_id.clone());
        candidates.insert(node_id, candidate);
    };
    for candidate in seeds_qdrant {
        push_candidate(&mut order, &mut candidates, candidate);
    }
    for candidate in seeds_kw {
        let node_id = candidate.node_id.clone();
        if node_id.is_empty() {
            continue;
        }
        if let Some(existing) = candidates.get_mut(&node_id) {
            existing.keyword = existing.keyword.max(1.0);
        } else {
            push_candidate(&mut order, &mut candidates, candidate);
        }
    }

    // Auto-corpus BM25 (CORTEX_BM25_AUTO default ON).
    let mut ranker = Bm25Ranker::new();
    let mut documents: Vec<Document> = Vec::new();
    for node_id in &order {
        let candidate = &candidates[node_id];
        documents.push(Document::new(
            node_id.clone(),
            bm25_corpus_text(candidate),
        ));
    }
    ranker.build_index(&documents);
    let bm25_scores = ranker.score(&embed_query);
    for (node_id, score) in &bm25_scores {
        if let Some(candidate) = candidates.get_mut(node_id) {
            candidate.bm25 = *score;
        }
    }

    // 4. Graph expansion (graph_expanded mode only).
    let mut expansion_nodes: Vec<ExpandedNode> = Vec::new();
    // Seeds keep hop_distance 0 (python `graph_expander` line 529): only
    // nodes first discovered BY expansion take hop >= 1.
    let seed_set: std::collections::HashSet<String> = order.iter().cloned().collect();
    if expand_graph && !order.is_empty() {
        let seeds: Vec<String> = order.iter().take(order.len().min(10)).cloned().collect();
        expansion_nodes = expand_seeds(
            runtime,
            &seeds,
            2,
            graph_rel_types,
            50,
            active_project_id.as_deref(),
        );
        for expanded in &expansion_nodes {
            let node_id = expanded.candidate.node_id.clone();
            if node_id.is_empty() {
                continue;
            }
            if let Some(existing) = candidates.get_mut(&node_id) {
                existing.graph = existing.graph.max(expanded.graph_proximity);
            } else if !order.contains(&node_id) {
                order.push(node_id.clone());
                candidates.insert(node_id, expanded.candidate.clone());
            }
        }
    }

    // 5. Fusion search (normalize + score + rank).
    let engine = FusionEngine::default();
    let now_epoch_s = 0.0; // freshness_map rỗng → decay không đổi (0.3 fallback).
    let scored = engine.search(
        &embed_query,
        &SeedInputs {
            qdrant: order
                .iter()
                .filter_map(|node_id| candidates.get(node_id).cloned())
                .collect(),
            keyword: Vec::new(),
        },
        top_k,
        debug,
        &weight_override,
        now_epoch_s,
    );
    scored
        .into_iter()
        .map(|result| {
            let expansion = expansion_nodes
                .iter()
                .find(|node| node.candidate.node_id == result.node_id);
            let is_seed = seed_set.contains(&result.node_id);
            ScoredOwned {
                node_id: result.node_id.clone(),
                score: result.score,
                hop_distance: if is_seed {
                    0
                } else {
                    expansion.map(|node| node.hop_distance).unwrap_or(0)
                },
                seed_id: expansion.map(|node| node.seed_id.clone()).unwrap_or_default(),
                seed_ids: expansion
                    .map(|node| node.seed_ids.clone())
                    .unwrap_or_default(),
                candidate: result.node,
            }
        })
        .collect()
}

fn bm25_corpus_text(candidate: &Candidate) -> String {
    [
        candidate.qualified_name.as_str(),
        candidate.name.as_str(),
        candidate.kind.as_str(),
        candidate.file_path.as_str(),
    ]
    .iter()
    .filter(|part| !part.is_empty())
    .map(|part| part.to_string())
    .collect::<Vec<_>>()
    .join(" ")
}

/// `_merge_target_results`.
fn merge_target_results(
    target_results: Vec<(String, Vec<ScoredOwned>)>,
    top_k: usize,
) -> Vec<ScoredOwned> {
    let mut best: BTreeMap<(String, String), (usize, ScoredOwned)> = BTreeMap::new();
    let mut ordinal: usize = 0;
    for (target_project_id, results) in target_results {
        for result in results {
            let result_project = result
                .candidate
                .project_id
                .to_lowercase();
            let project_key = if result_project.is_empty() {
                target_project_id.to_lowercase()
            } else {
                result_project
            };
            let key = (project_key, result.node_id.clone());
            match best.get(&key) {
                None => {
                    best.insert(key, (ordinal, result));
                }
                Some((existing_ordinal, existing)) => {
                    if result.score > existing.score {
                        let existing_ordinal = *existing_ordinal;
                        best.insert(key, (existing_ordinal, result));
                    }
                }
            }
            ordinal += 1;
        }
    }
    let mut ranked: Vec<(usize, ScoredOwned)> = best.into_values().collect();
    ranked.sort_by(|left, right| {
        right
            .1
            .score
            .partial_cmp(&left.1.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(left.0.cmp(&right.0))
    });
    ranked.into_iter().map(|(_, result)| result).take(top_k).collect()
}

// ---------------------------------------------------------------------------
// Result packager (result_packager.py)
// ---------------------------------------------------------------------------

const SIGNAL_THRESHOLD: f64 = 0.15;
const ENTRY_POINT_PROXIMITY_THRESHOLD: f64 = 0.60;
const SIGNAL_PROXIMITY_HIGH: f64 = 0.50;

const SIGNAL_LABELS: [(&str, &str); 6] = [
    ("semantic", "semantic similarity"),
    ("keyword", "keyword match"),
    ("graph", "graph proximity"),
    ("confidence", "semantic confidence"),
    ("usage", "usage importance"),
    ("freshness", "recent modification"),
];

fn signal_value(candidate: &Candidate, key: &str) -> f64 {
    match key {
        "semantic" => candidate.semantic,
        "keyword" => candidate.keyword,
        "graph" => candidate.graph,
        "confidence" => candidate.confidence,
        "usage" => candidate.usage,
        "freshness" => candidate.freshness,
        _ => 0.0,
    }
}

fn build_node_reason(candidate: &Candidate, node_id: &str, hop_distance: Option<i64>) -> String {
    let mut active: Vec<String> = Vec::new();
    for (signal_key, label) in SIGNAL_LABELS {
        let value = signal_value(candidate, signal_key);
        if value >= SIGNAL_THRESHOLD {
            active.push(format!("{label} ({value:.2})"));
        }
    }
    let name = if candidate.name.is_empty() {
        if candidate.qualified_name.is_empty() {
            node_id.to_string()
        } else {
            candidate.qualified_name.clone()
        }
    } else {
        candidate.name.clone()
    };
    let _ = name;
    let mut parts: Vec<String> = Vec::new();
    if !active.is_empty() {
        if active.len() == 1 {
            parts.push(format!("Retrieved via {}.", active[0]));
        } else {
            let last = active.pop().unwrap_or_default();
            parts.push(format!("Retrieved via {} and {last}.", active.join(", ")));
        }
    } else {
        parts.push("Retrieved as a related graph neighbor.".to_string());
    }
    // Raw-node hop parity: matched_nodes in the python flow never carry a
    // hop_distance on the raw node (packager defaults the OUTPUT to 0), so
    // the "direct seed" phrase never fires; only expansion-derived hops do.
    match hop_distance {
        Some(1) => parts.push("This is a 1-hop neighbor of a seed node.".to_string()),
        Some(2) => {
            parts.push("This is a 2-hop neighbor discovered via graph expansion.".to_string())
        }
        _ => {
            if candidate.graph >= SIGNAL_PROXIMITY_HIGH {
                parts.push("This node is closely connected to the matched cluster.".to_string());
            }
        }
    }
    if !candidate.file_path.is_empty() {
        parts.push(format!("Located in {}.", candidate.file_path));
    }
    parts.join(" ")
}

fn is_entry_point(candidate: &Candidate, hop_distance: Option<i64>) -> bool {
    let _ = hop_distance; // raw matched nodes never carry hop in python
    if candidate.graph >= ENTRY_POINT_PROXIMITY_THRESHOLD {
        return true;
    }
    if candidate.exported {
        return true;
    }
    if candidate.usage >= 0.70 {
        return true;
    }
    let kind = candidate.kind.to_lowercase();
    matches!(
        kind.as_str(),
        "entrypoint" | "public_method" | "exported_function" | "api_handler"
    )
}

fn build_related_path(result: &ScoredOwned) -> Option<Value> {
    if result.hop_distance <= 0 {
        return None;
    }
    Some(json!({
        "seed_id": if result.seed_id.is_empty() { Value::Null } else { json!(result.seed_id) },
        "seed_ids": if result.seed_ids.is_empty() {
            if result.seed_id.is_empty() { json!([]) } else { json!([result.seed_id]) }
        } else {
            json!(result.seed_ids)
        },
        "node_id": result.node_id,
        "name": if result.candidate.name.is_empty() { json!(result.node_id) } else { json!(result.candidate.name) },
        "qualified_name": result.candidate.qualified_name,
        "file_path": result.candidate.file_path,
        "hop_distance": result.hop_distance,
        "via": "graph_expansion",
        "score": round4(result.score),
    }))
}

fn round4(value: f64) -> f64 {
    format!("{value:.4}").parse().unwrap_or(value)
}

fn pack_results(scored_results: &[ScoredOwned], understanding: &QueryUnderstanding, mode: &str) -> Value {
    let mut matched_nodes: Vec<Value> = Vec::new();
    let mut entry_points: Vec<Value> = Vec::new();
    let mut related_paths: Vec<Value> = Vec::new();
    for result in scored_results {
        let mut signals: Map<String, Value> = Map::new();
        for (signal_key, _) in SIGNAL_LABELS {
            let value = signal_value(&result.candidate, signal_key);
            if value != 0.0 {
                signals.insert(signal_key.to_string(), json!(round4(value)));
            }
        }
        let raw_hop = if result.hop_distance == 0 { None } else { Some(result.hop_distance) };
        let reason = build_node_reason(&result.candidate, &result.node_id, raw_hop);
        let is_entry = is_entry_point(&result.candidate, raw_hop);
        let name = if result.candidate.name.is_empty() {
            result.node_id.clone()
        } else {
            result.candidate.name.clone()
        };
        let mut properties: Map<String, Value> = Map::new();
        properties.insert("node_id".to_string(), json!(result.candidate.node_id));
        properties.insert("intent".to_string(), json!(result.candidate.intent));
        properties.insert("exported".to_string(), json!(result.candidate.exported));
        properties.insert("side_effect".to_string(), json!(result.candidate.side_effect));
        properties.insert("return_type".to_string(), json!(result.candidate.return_type));
        properties.insert("project_id".to_string(), json!(result.candidate.project_id));
        properties.insert("language".to_string(), json!(result.candidate.language));
        properties.insert("bm25".to_string(), json!(result.candidate.bm25));
        // python `_bm25_corpus_text` được ghi ngược vào candidate trước khi
        // packager sao chép vào properties.
        properties.insert(
            "_bm25_text".to_string(),
            json!(bm25_corpus_text(&result.candidate)),
        );
        properties.insert("_source".to_string(), json!(result.candidate.source));
        let node = json!({
            "node_id": result.node_id,
            "name": name,
            "qualified_name": result.candidate.qualified_name,
            "kind": result.candidate.kind,
            "file_path": result.candidate.file_path,
            "score": round4(result.score),
            "reason": reason,
            "is_entry_point": is_entry,
            "hop_distance": result.hop_distance,
            "signals": Value::Object(signals),
            "properties": Value::Object(properties),
        });
        matched_nodes.push(node.clone());
        if is_entry {
            entry_points.push(node);
        }
        if let Some(path) = build_related_path(result) {
            related_paths.push(path);
        }
    }
    let confidence = compute_confidence(scored_results);
    let query_analysis = json!({
        "intent": understanding.intent,
        "entities": understanding.entities,
        "keywords": understanding.keywords,
        "actions": understanding.actions,
        "domain_signals": understanding.domain_signals,
        "embedding_text": understanding.embedding_text,
        "raw_query": understanding.raw_query,
    });
    let explanation = build_explanation_summary(scored_results, &query_analysis, mode);
    json!({
        "matched_nodes": matched_nodes,
        "entry_points": entry_points,
        "related_paths": related_paths,
        "explanation": explanation,
        "confidence": confidence,
        "query_analysis": query_analysis,
        "mode": mode,
    })
}

fn compute_confidence(scored_results: &[ScoredOwned]) -> f64 {
    if scored_results.is_empty() {
        return 0.0;
    }
    let top: Vec<f64> = scored_results.iter().take(5).map(|item| item.score).collect();
    if top.is_empty() {
        return 0.0;
    }
    let mean = top.iter().sum::<f64>() / top.len() as f64;
    (round3(mean)).clamp(0.0, 1.0)
}

fn round3(value: f64) -> f64 {
    format!("{value:.3}").parse().unwrap_or(value)
}

fn build_explanation_summary(
    scored_results: &[ScoredOwned],
    query_analysis: &Value,
    mode: &str,
) -> String {
    if scored_results.is_empty() {
        return "No matching nodes found for the given query.".to_string();
    }
    let n_total = scored_results.len();
    let n_entry = scored_results
        .iter()
        .filter(|item| {
            let raw_hop = if item.hop_distance == 0 { None } else { Some(item.hop_distance) };
            is_entry_point(&item.candidate, raw_hop)
        })
        .count();
    let n_expanded = scored_results.iter().filter(|item| item.hop_distance > 0).count();
    let confidence = compute_confidence(scored_results);
    let top_name = scored_results
        .first()
        .map(|item| {
            if item.candidate.name.is_empty() {
                item.node_id.clone()
            } else {
                item.candidate.name.clone()
            }
        })
        .unwrap_or_default();
    let mut parts: Vec<String> = Vec::new();
    let mode_desc = match mode {
        "semantic" => "semantic vector search",
        "hybrid" => "hybrid (semantic + keyword) search",
        "graph_expanded" => "graph-expanded search (semantic + keyword + neighbor traversal)",
        _ => "multi-strategy search",
    };
    parts.push(format!("Found {n_total} matching node(s) using {mode_desc}."));
    if let Some(signals) = query_analysis
        .get("domain_signals")
        .and_then(Value::as_array)
        .filter(|items| !items.is_empty())
    {
        let joined = signals
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!("Query involves domain signals: {joined}."));
    }
    if let Some(entities) = query_analysis
        .get("entities")
        .and_then(Value::as_array)
        .filter(|items| !items.is_empty())
    {
        let joined = entities
            .iter()
            .take(5)
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!("Key entities identified: {joined}."));
    }
    parts.push(format!(
        "Top match: '{top_name}' (confidence {:.0}%).",
        confidence * 100.0
    ));
    if n_entry > 0 {
        parts.push(format!("{n_entry} node(s) identified as entry points."));
    }
    if n_expanded > 0 {
        parts.push(format!(
            "{n_expanded} additional node(s) discovered via call-graph expansion."
        ));
    }
    parts.join(" ")
}

// Keep imports referenced.
#[allow(unused_imports)]
use is_database_not_found_error as _is_db_not_found;

// ---------------------------------------------------------------------------
// Qdrant seed lane (`_retrieve_qdrant`, vector-lane phase-03)
// ---------------------------------------------------------------------------

fn is_project_scope_collection(scope: &str, collection: &str) -> bool {
    let scope = scope.trim();
    let collection = collection.trim();
    if scope.is_empty() || collection.is_empty() {
        return false;
    }
    if collection == format!("{scope}_mess") {
        return true;
    }
    collection.starts_with(&format!("{scope}_"))
        && collection.contains("__")
        && collection.ends_with("_functions")
}

fn hit_node_id(hit: &Value, payload: &Value) -> String {
    payload
        .get("symbol_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| hit.get("id").and_then(Value::as_str).map(str::to_string))
        .unwrap_or_default()
}

fn hit_to_candidate(hit: &Value, payload: &Value) -> Candidate {
    let get_str = |key: &str| {
        payload
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    Candidate {
        node_id: hit_node_id(hit, payload),
        name: get_str("name"),
        qualified_name: get_str("qualified_name"),
        kind: get_str("kind"),
        file_path: get_str("file_path"),
        semantic: hit.get("score").and_then(Value::as_f64).unwrap_or(0.0),
        keyword: 0.0,
        graph: 0.0,
        freshness: 0.0,
        confidence: payload
            .get("doc_confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        usage: payload
            .get("signals")
            .and_then(|signals| signals.get("usage"))
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        bm25: 0.0,
        intent: get_str("intent"),
        exported: payload.get("exported").and_then(Value::as_bool).unwrap_or(false),
        side_effect: payload
            .get("side_effect")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        return_type: get_str("return_type"),
        project_id: get_str("project_id"),
        language: get_str("language"),
        source: "qdrant".to_string(),
    }
}

/// `_retrieve_qdrant` — embed query, search every resolved collection, dedupe
/// by node_id keeping the higher score, cut to `top_k`. Every failure mode
/// collapses to empty seeds exactly like the python `try/except → []`.
fn qdrant_seeds(
    embed_query: &str,
    collection: &str,
    top_k: usize,
    project_id: Option<&str>,
) -> Vec<Candidate> {
    let vector = match super::vector_lane::embed_query(embed_query) {
        Ok(vector) => vector,
        Err(_) => return Vec::new(),
    };
    let store = match super::vector_lane::resolve_store(project_id) {
        Ok(store) => store,
        Err(_) => return Vec::new(),
    };
    let available = match super::vector_lane::list_collection_names(&store) {
        Ok(names) => names,
        Err(_) => return Vec::new(),
    };
    // `_resolve_qdrant_collections`: token → scope resolve; no token → all.
    let tokens: Vec<String> = collection
        .split(',')
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
        .collect();
    let collections: Vec<String> = if tokens.is_empty() {
        available
    } else if available.is_empty() {
        tokens
    } else {
        let mut resolved: Vec<String> = Vec::new();
        for token in &tokens {
            if available.iter().any(|name| name == token) {
                if !resolved.contains(token) {
                    resolved.push(token.clone());
                }
            } else {
                for name in &available {
                    if is_project_scope_collection(token, name) && !resolved.contains(name) {
                        resolved.push(name.clone());
                    }
                }
            }
        }
        if resolved.is_empty() { tokens } else { resolved }
    };
    let filter = if project_id
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_some()
    {
        super::vector_lane::project_scope_filter(project_id)
    } else {
        None
    };
    let mut merged: Vec<(String, Value)> = Vec::new();
    for name in &collections {
        let hits = match super::vector_lane::search_collection(
            &store,
            name,
            &vector,
            None,
            top_k,
            filter.as_ref(),
        ) {
            Ok(hits) => hits,
            Err(_) => continue,
        };
        for hit in hits {
            let payload = hit
                .get("payload")
                .cloned()
                .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
            let node_id = hit_node_id(&hit, &payload);
            if node_id.is_empty() {
                continue;
            }
            let score = hit.get("score").and_then(Value::as_f64).unwrap_or(0.0);
            match merged.iter_mut().find(|(id, _)| id == &node_id) {
                Some((_, existing)) => {
                    if score > existing.get("score").and_then(Value::as_f64).unwrap_or(0.0) {
                        *existing = hit;
                    }
                }
                None => merged.push((node_id, hit)),
            }
        }
    }
    merged.sort_by(|a, b| {
        let score = |item: &(String, Value)| item.1.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        score(b).partial_cmp(&score(a)).unwrap_or(std::cmp::Ordering::Equal)
    });
    merged.truncate(top_k);
    merged
        .into_iter()
        .map(|(_, hit)| {
            let payload = hit
                .get("payload")
                .cloned()
                .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
            hit_to_candidate(&hit, &payload)
        })
        .collect()
}
