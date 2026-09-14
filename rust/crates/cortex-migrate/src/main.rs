//! `cortex-migrate` — chuyển local harness instance từ legacy embedded
//! FalkorDBLite `.rdb` sang Ladybug (phase 14 scope B của
//! `plans/260913-2130-rust-full-migration`).
//!
//! * Đọc layout instance theo `cortex-storage/src/layout.rs`:
//!   `<data_root>/v1/instances/<instance>/falkordb/<owner>/data.rdb`
//!   → `<instance>/ladybug/<owner>/<owner>.lbug/<graph>` (1 graph = 1 file).
//! * Boot `.rdb` đúng cơ chế `cortex-mcp/src/graph/runtime.rs` (redislite +
//!   `falkordb.so`, read-only, `SHUTDOWN NOSAVE` — không bao giờ đè nguồn).
//! * `--dry-run` chỉ liệt kê graph + counts, không ghi. Mặc định ghi vào store
//!   file MỚI — file đã tồn tại → lỗi, trừ `--overwrite`.
//! * Verify: mở lại target qua `cortex_graph_writer::store::LadybugStore`,
//!   so node/rel count từng graph (và từng label/rel-type), in bảng report.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use cortex_falkordb::client::{FalkorDbClient, Param};
use cortex_graph_writer::store::ladybug_store::LadybugStore;
use cortex_graph_writer::store::GraphStore;
use serde_json::Value;

use crate::falkor_boot::{graph_list, launch_embedded_falkordb};
use crate::falkor_values::{parse_node_row, parse_rel_row, primary_label, rel_type_of};
use crate::ladybug_writer::{GraphSchemaPlan, LadybugWriter, NodeRow, RelRow, TableSchema};

mod falkor_boot;
mod falkor_values;
mod ladybug_writer;

const NODE_PAGE: &str = "MATCH (n) RETURN id(n) AS _fid, labels(n) AS _labels, \
     properties(n) AS _props ORDER BY id(n) SKIP $skip LIMIT $limit";
const REL_PAGE: &str = "MATCH (a)-[r]->(b) RETURN id(r) AS _fid, type(r) AS _type, \
     id(a) AS _src, id(b) AS _dst, labels(a) AS _sl, labels(b) AS _dl, \
     properties(r) AS _props ORDER BY id(r) SKIP $skip LIMIT $limit";

#[derive(Debug)]
struct Cli {
    data_root: Option<PathBuf>,
    instance: Option<String>,
    owner: String,
    graphs: Vec<String>,
    dry_run: bool,
    overwrite: bool,
    node_batch: usize,
    rel_batch: usize,
}

fn usage() -> &'static str {
    "cortex-migrate — migrate a local harness instance from FalkorDBLite .rdb to Ladybug\n\
     \n\
     USAGE:\n    \
     cortex-migrate [--data-root PATH] [--instance NAME] [--owner code|doc|both]\n    \
                    [--graph NAME]... [--dry-run] [--overwrite]\n    \
                    [--node-batch N] [--rel-batch N]\n\
     \n\
     OPTIONS:\n    \
     --data-root PATH   Harness data root (default: $CORTEX_DATA_HOME or ~/.cortext-harness)\n    \
     --instance NAME    Instance id (default: $CORTEX_STORAGE_INSTANCE or \"default\")\n    \
     --owner WHICH      Which lane to migrate: code, doc, or both (default: both)\n    \
     --graph NAME       Migrate only the named graphs (repeatable; default: all)\n    \
     --dry-run          List what would migrate (graphs + counts); writes nothing\n    \
     --overwrite        Replace an existing Ladybug store file instead of erroring\n    \
     --node-batch N     Nodes per CREATE statement (default 200)\n    \
     --rel-batch N      Relationships per MATCH/CREATE statement (default 50)\n\
     \n\
     SAFETY: the source .rdb is opened read-only (SHUTDOWN NOSAVE) and never\n\
     deleted or modified. Migration targets a NEW Ladybug store file.\n"
}

fn parse_args() -> Result<Cli, String> {
    let mut cli = Cli {
        data_root: None,
        instance: None,
        owner: "both".to_string(),
        graphs: Vec::new(),
        dry_run: false,
        overwrite: false,
        node_batch: 200,
        rel_batch: 50,
    };
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => {
                print!("{}", usage());
                std::process::exit(0);
            }
            "--dry-run" => cli.dry_run = true,
            "--overwrite" => cli.overwrite = true,
            "--data-root" => {
                cli.data_root = Some(PathBuf::from(
                    args.next().ok_or("--data-root requires a value")?,
                ))
            }
            "--instance" => {
                cli.instance = Some(args.next().ok_or("--instance requires a value")?)
            }
            "--owner" => {
                cli.owner = args.next().ok_or("--owner requires a value")?;
                if !matches!(cli.owner.as_str(), "code" | "doc" | "both") {
                    return Err(format!(
                        "--owner must be code|doc|both, got {:?}",
                        cli.owner
                    ));
                }
            }
            "--graph" => cli
                .graphs
                .push(args.next().ok_or("--graph requires a value")?),
            "--node-batch" => {
                cli.node_batch = args
                    .next()
                    .ok_or("--node-batch requires a value")?
                    .parse()
                    .map_err(|_| "--node-batch must be a positive integer")?;
            }
            "--rel-batch" => {
                cli.rel_batch = args
                    .next()
                    .ok_or("--rel-batch requires a value")?
                    .parse()
                    .map_err(|_| "--rel-batch must be a positive integer")?;
            }
            other => return Err(format!("unknown argument {other:?} (try --help)")),
        }
    }
    Ok(cli)
}

/// `data_root()` của runtime.rs: $CORTEX_DATA_HOME hoặc ~/.cortext-harness.
fn default_data_root() -> PathBuf {
    if let Ok(home) = std::env::var("CORTEX_DATA_HOME") {
        let trimmed = home.trim();
        if !trimmed.is_empty() {
            let expanded = if let Some(stripped) = trimmed.strip_prefix('~') {
                std::env::var_os("HOME")
                    .map(|home| Path::new(&home).join(stripped))
                    .unwrap_or_else(|| PathBuf::from(trimmed))
            } else {
                PathBuf::from(trimmed)
            };
            return expanded;
        }
    }
    match std::env::var("HOME") {
        Ok(home) => Path::new(&home).join(".cortext-harness"),
        Err(_) => PathBuf::from(".cortext-harness"),
    }
}

fn owners_requested(owner: &str) -> Vec<&'static str> {
    match owner {
        "code" => vec!["code"],
        "doc" => vec!["doc"],
        _ => vec!["code", "doc"],
    }
}

// ---------------------------------------------------------------------------
// Source reads
// ---------------------------------------------------------------------------

fn scalar_count(client: &mut FalkorDbClient, graph: &str, query: &str) -> Result<i64, String> {
    let result = client
        .ro_query(graph, query, &BTreeMap::new())
        .map_err(|error| format!("{query} — {error}"))?;
    match result.records.first().and_then(|row| row.first()) {
        Some(cortex_falkordb::value::FalkorValue::Int(count)) => Ok(*count),
        _ => Err(format!("{query} — unexpected result shape")),
    }
}

fn count_nodes(client: &mut FalkorDbClient, graph: &str) -> Result<i64, String> {
    scalar_count(client, graph, "MATCH (n) RETURN count(n)")
}

fn count_rels(client: &mut FalkorDbClient, graph: &str) -> Result<i64, String> {
    scalar_count(client, graph, "MATCH ()-[r]->() RETURN count(r)")
}

fn db_labels(client: &mut FalkorDbClient, graph: &str) -> Result<Vec<String>, String> {
    let result = client
        .ro_query(
            graph,
            "CALL db.labels() YIELD label RETURN label",
            &BTreeMap::new(),
        )
        .map_err(|error| error.to_string())?;
    Ok(result
        .records
        .iter()
        .filter_map(|row| row.first())
        .filter_map(|value| match value {
            cortex_falkordb::value::FalkorValue::String(text) => Some(text.clone()),
            _ => None,
        })
        .collect())
}

fn db_rel_types(client: &mut FalkorDbClient, graph: &str) -> Result<Vec<String>, String> {
    let result = client
        .ro_query(
            graph,
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
            &BTreeMap::new(),
        )
        .map_err(|error| error.to_string())?;
    Ok(result
        .records
        .iter()
        .filter_map(|row| row.first())
        .filter_map(|value| match value {
            cortex_falkordb::value::FalkorValue::String(text) => Some(text.clone()),
            _ => None,
        })
        .collect())
}

fn page(
    client: &mut FalkorDbClient,
    graph: &str,
    query: &str,
    skip: i64,
    limit: i64,
) -> Result<Vec<Vec<cortex_falkordb::value::FalkorValue>>, String> {
    let params: BTreeMap<String, Param> = BTreeMap::from([
        ("skip".to_string(), Param::Int(skip)),
        ("limit".to_string(), Param::Int(limit)),
    ]);
    let result = client
        .ro_query(graph, query, &params)
        .map_err(|error| error.to_string())?;
    Ok(result.records)
}

fn stream_all(
    client: &mut FalkorDbClient,
    graph: &str,
    query: &str,
    page_size: i64,
    mut visit: impl FnMut(Vec<cortex_falkordb::value::FalkorValue>) -> Result<(), String>,
) -> Result<(), String> {
    let mut skip: i64 = 0;
    loop {
        let records = page(client, graph, query, skip, page_size)?;
        let received = records.len() as i64;
        for record in records {
            visit(record)?;
        }
        skip += received;
        if received < page_size {
            return Ok(());
        }
    }
}

// ---------------------------------------------------------------------------
// Migration of one graph
// ---------------------------------------------------------------------------

struct GraphOutcome {
    owner: String,
    graph: String,
    target: PathBuf,
    src_nodes: i64,
    src_rels: i64,
    dst_nodes: Option<i64>,
    dst_rels: Option<i64>,
    label_detail: Vec<(String, i64, i64)>,
    rel_detail: Vec<(String, i64, i64)>,
    status: String,
    ok: bool,
}

fn migrate_graph(
    client: &mut FalkorDbClient,
    owner: &str,
    graph: &str,
    target: &Path,
    options: &MigrateOptions,
) -> GraphOutcome {
    let mut outcome = GraphOutcome {
        owner: owner.to_string(),
        graph: graph.to_string(),
        target: target.to_path_buf(),
        src_nodes: 0,
        src_rels: 0,
        dst_nodes: None,
        dst_rels: None,
        label_detail: Vec::new(),
        rel_detail: Vec::new(),
        status: String::new(),
        ok: false,
    };
    if let Err(error) = run_migration(client, graph, target, options, &mut outcome) {
        outcome.status = format!("ERROR: {error}");
        outcome.ok = false;
    }
    outcome
}

/// Batch sizes cho statement inserts (CLI `--node-batch`/`--rel-batch`).
struct MigrateOptions {
    dry_run: bool,
    overwrite: bool,
    node_batch: usize,
    rel_batch: usize,
}

fn run_migration(
    client: &mut FalkorDbClient,
    graph: &str,
    target: &Path,
    options: &MigrateOptions,
    outcome: &mut GraphOutcome,
) -> Result<(), String> {
    let MigrateOptions {
        dry_run,
        overwrite,
        node_batch,
        rel_batch,
    } = *options;
    outcome.src_nodes = count_nodes(client, graph)?;
    outcome.src_rels = count_rels(client, graph)?;

    if dry_run {
        let labels = db_labels(client, graph)?;
        let rel_types = db_rel_types(client, graph)?;
        outcome.status = format!("dry-run (labels: {labels:?}, rel types: {rel_types:?})");
        outcome.ok = true;
        return Ok(());
    }

    if target.exists() && !overwrite {
        return Err(format!(
            "target store already exists: {} (use --overwrite to replace)",
            target.display()
        ));
    }
    if overwrite && target.exists() {
        std::fs::remove_file(target)
            .map_err(|error| format!("cannot remove {}: {error}", target.display()))?;
    }

    // ── Pass A: schema discovery + per-label/per-type source counts ────────
    let mut plan = GraphSchemaPlan::default();
    let mut src_label_counts: BTreeMap<String, i64> = BTreeMap::new();
    let mut src_rel_counts: BTreeMap<String, i64> = BTreeMap::new();

    stream_all(client, graph, NODE_PAGE, 500, |record| {
        let node = parse_node_row(&record)?;
        let label = primary_label(&node.labels);
        // Node nhiều label: chỉ label đầu được first-class (docstring module).
        plan.ensure_label(&label);
        let table = plan.node_registry.get(&label).cloned().unwrap_or_default();
        for (prop, value) in &node.props {
            plan.observe_node_prop(&label, &table, prop, value);
        }
        *src_label_counts.entry(label).or_insert(0) += 1;
        Ok(())
    })?;

    stream_all(client, graph, REL_PAGE, 500, |record| {
        let rel_type = rel_type_of(&record)?;
        plan.ensure_rel_type(&rel_type);
        let row = parse_rel_row(&record)?;
        plan.observe_rel_pair(&rel_type, &row.endpoints.0, &row.endpoints.1);
        let table = plan
            .rel_registry
            .get(&rel_type)
            .cloned()
            .unwrap_or_default();
        for (prop, value) in &row.props {
            plan.observe_rel_prop(&rel_type, &table, prop, value);
        }
        *src_rel_counts.entry(rel_type).or_insert(0) += 1;
        Ok(())
    })?;

    // ── Schema DDL ─────────────────────────────────────────────────────────
    let writer = LadybugWriter::open(target)?;
    writer.create_schema(&plan)?;

    // ── Pass B: data inserts (batched) ─────────────────────────────────────
    let mut node_buffers: HashMap<String, Vec<NodeRow>> = HashMap::new();
    let flush_nodes = |plan: &GraphSchemaPlan,
                       writer: &LadybugWriter,
                       buffers: &mut HashMap<String, Vec<NodeRow>>,
                       label: &str|
     -> Result<(), String> {
        let Some(rows) = buffers.get_mut(label) else {
            return Ok(());
        };
        if rows.is_empty() {
            return Ok(());
        }
        let schema: &TableSchema = plan
            .nodes
            .get(label)
            .ok_or_else(|| format!("missing node schema for label {label}"))?;
        // Chunk manually to keep the buffer semantics simple.
        let total = rows.len();
        for chunk_start in (0..total).step_by(node_batch.max(1)) {
            let chunk_end = (chunk_start + node_batch.max(1)).min(total);
            writer.insert_nodes(schema, &rows[chunk_start..chunk_end])?;
        }
        rows.clear();
        Ok(())
    };

    stream_all(client, graph, NODE_PAGE, 500, |record| {
        let node = parse_node_row(&record)?;
        let label = primary_label(&node.labels);
        plan.ensure_label(&label);
        let entry = node_buffers.entry(label.clone()).or_default();
        entry.push(node);
        if entry.len() >= node_batch.max(1) {
            flush_nodes(&plan, &writer, &mut node_buffers, &label)?;
        }
        Ok(())
    })?;
    let labels: Vec<String> = node_buffers.keys().cloned().collect();
    for label in labels {
        flush_nodes(&plan, &writer, &mut node_buffers, &label)?;
    }

    let mut rel_buffers: HashMap<String, Vec<RelRow>> = HashMap::new();
    let flush_rels = |plan: &GraphSchemaPlan,
                      writer: &LadybugWriter,
                      buffers: &mut HashMap<String, Vec<RelRow>>,
                      rel_type: &str|
     -> Result<(), String> {
        let Some(rows) = buffers.get_mut(rel_type) else {
            return Ok(());
        };
        if rows.is_empty() {
            return Ok(());
        }
        let schema = &plan
            .rels
            .get(rel_type)
            .ok_or_else(|| format!("missing rel schema for type {rel_type}"))?
            .0;
        let total = rows.len();
        for chunk_start in (0..total).step_by(rel_batch.max(1)) {
            let chunk_end = (chunk_start + rel_batch.max(1)).min(total);
            writer.insert_rels(plan, schema, &rows[chunk_start..chunk_end])?;
        }
        rows.clear();
        Ok(())
    };

    stream_all(client, graph, REL_PAGE, 500, |record| {
        let rel_type = rel_type_of(&record)?;
        plan.ensure_rel_type(&rel_type);
        let row = parse_rel_row(&record)?;
        let entry = rel_buffers.entry(rel_type.clone()).or_default();
        entry.push(row);
        if entry.len() >= rel_batch.max(1) {
            flush_rels(&plan, &writer, &mut rel_buffers, &rel_type)?;
        }
        Ok(())
    })?;
    let rel_types: Vec<String> = rel_buffers.keys().cloned().collect();
    for rel_type in rel_types {
        flush_rels(&plan, &writer, &mut rel_buffers, &rel_type)?;
    }

    // Writer phải drop trước khi verify mở lại file (embedded single-owner).
    drop(writer);

    // ── Verify: đọc lại qua cortex-graph-writer LadybugStore ───────────────
    let mut store = LadybugStore::open(target, graph)
        .map_err(|error| format!("verify: cannot open target store: {error}"))?;
    let empty: BTreeMap<String, Value> = BTreeMap::new();
    let read_count = |store: &mut LadybugStore, query: &str| -> Result<i64, String> {
        let records = store
            .execute_query(query, &empty, None)
            .map_err(|error| format!("verify {query}: {error}"))?;
        let value = records
            .first()
            .and_then(|row| row.values().next())
            .cloned()
            .unwrap_or(Value::Null);
        value
            .as_i64()
            .or_else(|| {
                value.as_str().and_then(|text| text.parse::<i64>().ok())
            })
            .ok_or_else(|| format!("verify {query}: unexpected value {value}"))
    };
    outcome.dst_nodes = Some(read_count(
        &mut store,
        "MATCH (n) RETURN count(n) AS c",
    )?);
    outcome.dst_rels = Some(read_count(
        &mut store,
        "MATCH ()-[r]->() RETURN count(r) AS c",
    )?);

    let mut ok = outcome.dst_nodes == Some(outcome.src_nodes)
        && outcome.dst_rels == Some(outcome.src_rels);
    for (label, src_count) in &src_label_counts {
        let table = plan
            .nodes
            .get(label)
            .map(|schema| schema.table.clone())
            .unwrap_or_default();
        let dst_count = read_count(
            &mut store,
            &format!("MATCH (n:`{table}`) RETURN count(n) AS c"),
        )
        .unwrap_or(-1);
        outcome.label_detail.push((table, *src_count, dst_count));
        if dst_count != *src_count {
            ok = false;
        }
    }
    for (rel_type, src_count) in &src_rel_counts {
        let table = plan
            .rels
            .get(rel_type)
            .map(|(schema, _)| schema.table.clone())
            .unwrap_or_default();
        let dst_count = read_count(
            &mut store,
            &format!("MATCH ()-[r:`{table}`]->() RETURN count(r) AS c"),
        )
        .unwrap_or(-1);
        outcome.rel_detail.push((table, *src_count, dst_count));
        if dst_count != *src_count {
            ok = false;
        }
    }
    outcome.label_detail.sort();
    outcome.rel_detail.sort();
    outcome.status = if ok {
        "OK".to_string()
    } else {
        "COUNT MISMATCH".to_string()
    };
    outcome.ok = ok;
    Ok(())
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

fn print_report(outcomes: &[GraphOutcome], dry_run: bool) {
    let mode = if dry_run { "dry-run" } else { "full migrate" };
    println!();
    println!("=== cortex migrate report ({mode}) ===");
    const HEAD: &str = "status";
    println!(
        "{:<38} {:<22} {:>16} {:>16}  {}",
        "graph (owner)", "target store", "nodes src/dst", "rels src/dst", HEAD
    );
    println!("{}", "-".repeat(130));
    for item in outcomes {
        let dst_nodes = item.dst_nodes.map(|c| c.to_string()).unwrap_or_else(|| "-".into());
        let dst_rels = item.dst_rels.map(|c| c.to_string()).unwrap_or_else(|| "-".into());
        println!(
            "{:<38} {:<22} {:>7}/{:<8} {:>7}/{:<8}  {}",
            format!("{} ({})", item.graph, item.owner),
            short_path(&item.target),
            item.src_nodes,
            dst_nodes,
            item.src_rels,
            dst_rels,
            item.status,
        );
        for (table, src, dst) in &item.label_detail {
            let mark = if src == dst { " " } else { "!" };
            println!("    {mark} label {:<28} src={src:<8} dst={dst}", table);
        }
        for (table, src, dst) in &item.rel_detail {
            let mark = if src == dst { " " } else { "!" };
            println!("    {mark} rel   {:<28} src={src:<8} dst={dst}", table);
        }
    }
    println!("{}", "-".repeat(130));
    let ok = outcomes.iter().filter(|item| item.ok).count();
    println!(
        "{}/{} graphs {} — source .rdb left untouched (read-only, SHUTDOWN NOSAVE)",
        ok,
        outcomes.len(),
        if dry_run { "inspected" } else { "migrated" }
    );
}

fn short_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    if text.len() <= 22 {
        text.to_string()
    } else {
        format!("…{}", &text[text.len() - 21..])
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn main() -> ExitCode {
    let cli = match parse_args() {
        Ok(cli) => cli,
        Err(error) => {
            eprintln!("error: {error}\n\n{}", usage());
            return ExitCode::from(2);
        }
    };
    let data_root = cli.data_root.clone().unwrap_or_else(default_data_root);
    let instance = cli
        .instance
        .clone()
        .or_else(|| std::env::var("CORTEX_STORAGE_INSTANCE").ok())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "default".to_string());
    let instance_root = data_root.join("v1").join("instances").join(&instance);
    if !instance_root.is_dir() {
        eprintln!(
            "error: instance root does not exist: {}",
            instance_root.display()
        );
        return ExitCode::from(1);
    }

    println!(
        "cortex migrate: instance={instance} root={} owners={} mode={}",
        instance_root.display(),
        cli.owner,
        if cli.dry_run { "dry-run" } else { "full" }
    );

    let mut outcomes: Vec<GraphOutcome> = Vec::new();
    let mut hard_error: Option<String> = None;

    for owner in owners_requested(&cli.owner) {
        let source = instance_root.join("falkordb").join(owner).join("data.rdb");
        if !source.is_file() {
            println!(
                "  [skip] {owner}: no legacy .rdb at {}",
                source.display()
            );
            continue;
        }
        println!(
            "  [boot] {owner}: {} (redislite + falkordb.so, read-only)",
            source.display()
        );
        let embedded = match launch_embedded_falkordb(&source) {
            Ok(embedded) => embedded,
            Err(error) => {
                hard_error = Some(format!("boot {}: {error}", source.display()));
                break;
            }
        };
        let mut client = match embedded.connect() {
            Ok(client) => client,
            Err(error) => {
                hard_error = Some(format!("connect {}: {error}", source.display()));
                break;
            }
        };
        let graphs = match graph_list(&mut client) {
            Ok(graphs) => graphs,
            Err(error) => {
                hard_error = Some(format!("GRAPH.LIST {}: {error}", source.display()));
                break;
            }
        };
        if graphs.is_empty() {
            println!("  [skip] {owner}: no graphs in {}", source.display());
            continue;
        }
        for graph in &graphs {
            if !cli.graphs.is_empty() && !cli.graphs.contains(graph) {
                continue;
            }
            let target = match cortex_storage::layout::ladybug_graph_path(
                instance_root.join("ladybug").join(owner),
                owner,
                graph,
            ) {
                Ok(path) => path,
                Err(error) => {
                    outcomes.push(GraphOutcome {
                        owner: owner.to_string(),
                        graph: graph.clone(),
                        target: PathBuf::new(),
                        src_nodes: 0,
                        src_rels: 0,
                        dst_nodes: None,
                        dst_rels: None,
                        label_detail: Vec::new(),
                        rel_detail: Vec::new(),
                        status: format!("ERROR: invalid graph name: {error}"),
                        ok: false,
                    });
                    continue;
                }
            };
            print!("  [migrate] {owner}/{graph} → {} … ", target.display());
            let options = MigrateOptions {
                dry_run: cli.dry_run,
                overwrite: cli.overwrite,
                node_batch: cli.node_batch,
                rel_batch: cli.rel_batch,
            };
            let outcome = migrate_graph(&mut client, owner, graph, &target, &options);
            println!("{}", outcome.status);
            outcomes.push(outcome);
        }
        // embedded drop ⇒ SHUTDOWN NOSAVE.
    }

    if let Some(error) = &hard_error {
        eprintln!("error: {error}");
    }

    print_report(&outcomes, cli.dry_run);
    if hard_error.is_some() || outcomes.iter().any(|item| !item.ok) {
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}
