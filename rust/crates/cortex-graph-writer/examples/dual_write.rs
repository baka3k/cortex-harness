//! Dual-write parity driver (phase 03 gate) — đọc fixture `writer_rows.json`,
//! ghi vào 1 graph FalkorDB bằng writer Rust, cùng thứ tự calls với script
//! Python (`scripts/rust_parity/dual_write_diff.py`).
//!
//! Usage:
//!   cargo run --release -p cortex-graph-writer --example dual_write -- \
//!     --fixture tests/fixtures/writer_rows.json --host 127.0.0.1 --port 6379 \
//!     --graph stock_rw

use std::collections::BTreeMap;

use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::json_row::Row;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::FalkorDbStore;
use cortex_graph_writer::topology::ProjectTopologyWriter;
use cortex_graph_writer::upserts;
use serde_json::Value;

fn arg(flag: &str, default: &str) -> String {
    let args: Vec<String> = std::env::args().collect();
    args.iter()
        .position(|a| a == flag)
        .and_then(|index| args.get(index + 1))
        .cloned()
        .unwrap_or_else(|| default.to_string())
}

fn rows_of(fixture: &Value, key: &str) -> Vec<Row> {
    fixture
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_object)
                .cloned()
                .collect::<Vec<Row>>()
        })
        .unwrap_or_default()
}

fn main() {
    let fixture_path = arg("--fixture", "tests/fixtures/writer_rows.json");
    let host = arg("--host", "127.0.0.1");
    let port: u16 = arg("--port", "6379").parse().expect("port");
    let graph = arg("--graph", "stock_rw");

    let raw = std::fs::read_to_string(&fixture_path).expect("fixture readable");
    let fixture: Value = serde_json::from_str(&raw).expect("fixture json");

    let client = FalkorDbClient::connect_verified(&host, port).expect("connect falkordb");
    let store = FalkorDbStore::new(client, graph.clone());
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 1000, true);
    let database: Option<&str> = None;

    // 0. cleanup — DETACH DELETE toàn bộ graph (khớp script Python).
    let deleted = writer
        .store
        .execute_query("MATCH (n) DETACH DELETE n", &BTreeMap::new(), database)
        .expect("cleanup");
    println!("cleanup deleted={:?}", deleted.first().cloned().unwrap_or_default());

    // 1. ensure_schema (preflight indexes — query contract gọi ở lần write đầu
    // nhưng gọi tường minh để khớp script Python).
    writer.ensure_schema().expect("ensure_schema");

    // 2. projects — fixture "project" là một dict (khớp script Python wrap [dict]).
    let projects: Vec<Row> = fixture
        .get("project")
        .and_then(Value::as_object)
        .map(|row| vec![row.clone()])
        .unwrap_or_default();
    if !projects.is_empty() {
        let count = writer.write_projects(&projects).expect("write_projects");
        println!("projects={count}");
    }

    // 3. repository setup (trusted node contracts + HAS_REPOSITORY evidence).
    if let Some(setup) = fixture.get("repository_setup") {
        let counts = writer
            .write_project_repository_setup(
                setup["project_id"].as_str().expect("project_id"),
                setup["project_name"].as_str().expect("project_name"),
                setup["project_slug"].as_str().expect("project_slug"),
                setup["repository_name"].as_str().expect("repository_name"),
            )
            .expect("repository_setup");
        println!("repository_setup={counts:?}");
    }

    // 4. packages + namespaces (full variants).
    let packages = rows_of(&fixture, "packages");
    if !packages.is_empty() {
        println!("packages={}", writer.write_packages_full(&packages).expect("packages"));
    }
    let namespaces = rows_of(&fixture, "namespaces");
    if !namespaces.is_empty() {
        println!(
            "namespaces={}",
            writer.write_namespaces_full(&namespaces).expect("namespaces")
        );
    }

    // 5. files + repo_file_edges.
    let files = rows_of(&fixture, "files");
    if !files.is_empty() {
        println!("files={}", writer.write_files(&files).expect("files"));
        println!(
            "repo_file_edges={}",
            writer.write_repo_file_edges(&files).expect("repo_file_edges")
        );
    }

    // 6. classes + types (2 passes cho CASE min-lex) + functions.
    let classes = rows_of(&fixture, "classes");
    if !classes.is_empty() {
        println!("classes={}", writer.write_classes_full(&classes).expect("classes"));
    }
    let types1 = rows_of(&fixture, "types_pass1");
    if !types1.is_empty() {
        println!("types_pass1={}", writer.write_types_full(&types1).expect("types1"));
    }
    let types2 = rows_of(&fixture, "types_pass2");
    if !types2.is_empty() {
        println!("types_pass2={}", writer.write_types_full(&types2).expect("types2"));
    }
    let functions = rows_of(&fixture, "functions");
    if !functions.is_empty() {
        println!(
            "functions={}",
            writer.write_functions_full(&functions).expect("functions")
        );
    }

    // 7. navigators + has_routes + param_lists.
    let navigators = rows_of(&fixture, "navigators");
    if !navigators.is_empty() {
        println!(
            "navigators={}",
            writer.write_navigators(&navigators).expect("navigators")
        );
    }
    let has_routes = rows_of(&fixture, "has_routes");
    if !has_routes.is_empty() {
        println!(
            "has_routes={}",
            writer.write_has_routes(&has_routes).expect("has_routes")
        );
    }
    let param_lists = rows_of(&fixture, "param_lists");
    if !param_lists.is_empty() {
        println!(
            "param_lists={}",
            writer.write_param_lists(&param_lists).expect("param_lists")
        );
    }

    // 8. typed relations.
    let relations = rows_of(&fixture, "relations");
    if !relations.is_empty() {
        println!(
            "relations={}",
            writer
                .write_relations_typed(&relations, Some("stock"))
                .expect("relations")
        );
    }

    // 9. calls + calls_with_site.
    let calls = rows_of(&fixture, "calls");
    if !calls.is_empty() {
        println!("calls={}", writer.write_calls(&calls).expect("calls"));
    }
    let calls_with_site = rows_of(&fixture, "calls_with_site");
    if !calls_with_site.is_empty() {
        println!(
            "calls_with_site={}",
            writer.write_calls_with_site(&calls_with_site).expect("cws")
        );
    }

    // 10. evidence plane.
    let sites = rows_of(&fixture, "call_evidence_sites");
    if !sites.is_empty() {
        println!(
            "call_evidence_sites={}",
            writer.write_call_evidence_sites(&sites).expect("sites")
        );
    }
    let observations = rows_of(&fixture, "call_evidence_observations");
    if !observations.is_empty() {
        println!(
            "call_evidence_observations={}",
            writer
                .write_call_evidence_observations(&observations)
                .expect("observations")
        );
    }
    let configurations = rows_of(&fixture, "build_configurations");
    if !configurations.is_empty() {
        println!(
            "build_configurations={}",
            writer
                .write_build_configurations(&configurations)
                .expect("configurations")
        );
    }
    let coverage = rows_of(&fixture, "semantic_coverage");
    if !coverage.is_empty() {
        println!(
            "semantic_coverage={}",
            writer.write_semantic_coverage(&coverage).expect("coverage")
        );
    }
    let proc_joins = rows_of(&fixture, "proc_function_joins");
    let proc_hosts = rows_of(&fixture, "proc_host_declarations");
    if !proc_joins.is_empty() || !proc_hosts.is_empty() {
        println!(
            "proc_evidence_joins={}",
            writer
                .write_proc_evidence_joins(&proc_joins, &proc_hosts)
                .expect("proc_joins")
        );
    }

    // 11. workflows + steps.
    let workflows = rows_of(&fixture, "workflows");
    if !workflows.is_empty() {
        println!(
            "workflows={}",
            writer.write_workflows(&workflows).expect("workflows")
        );
    }
    let workflow_steps = rows_of(&fixture, "workflow_steps");
    if !workflow_steps.is_empty() {
        println!(
            "workflow_steps={}",
            writer.write_workflow_steps(&workflow_steps).expect("wf_steps")
        );
    }

    // 12. topology.
    if let Some(topology_result) = fixture.get("topology") {
        let mut topology = ProjectTopologyWriter::new(
            Box::new(FalkorDbStore::new(
                FalkorDbClient::connect_verified(&host, port).expect("connect topology"),
                graph,
            )),
            None,
            500,
        );
        let counts = topology.write(topology_result).expect("topology write");
        println!("topology={counts:?}");
    }

    // Kiểm Tra: không còn symbol nào tham chiếu upserts (giữ import sống).
    let _ = upserts::CPLUS_FILE_OWNED_NODE_LABELS.len();
}
