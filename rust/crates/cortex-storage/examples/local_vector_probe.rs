//! Engine-level probe for the native local vector lane (phase-03/04 harness
//! of plans/260916-1154-native-vector-ingest-local). Not a user tool — the
//! parity matrix, RSS gate, and drill scripts drive this binary.

use std::path::{Path, PathBuf};

use cortex_storage::config::{resolve_storage, ResolveOverrides, StorageRole};
use cortex_storage::qdrant::{LocalQdrantReader, LocalQdrantStore};

fn resolved_for(store_dir: &Path) -> (cortex_storage::ResolvedStorage, PathBuf) {
    let root = store_dir.join(".probe-root");
    let resolved = resolve_storage(
        &root,
        None,
        &ResolveOverrides {
            // Pin the code-store path so env (QDRANT_CODE_PATH etc.) can
            // never redirect the probe.
            qdrant_code_path: Some(store_dir.to_string_lossy().into_owned()),
            data_home: Some(root.to_string_lossy().into_owned()),
            ..Default::default()
        },
    )
    .expect("resolve storage");
    resolved.ensure_directories().expect("ensure dirs");
    let path = resolved
        .path_for_role(StorageRole::Code.as_str())
        .expect("code path")
        .to_path_buf();
    (resolved, path)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("init") => {
            // init <store_dir> <points.json> <collection> <dim>
            let store_dir = Path::new(&args[2]);
            let points: Vec<serde_json::Value> =
                serde_json::from_str(&std::fs::read_to_string(&args[3]).expect("read points"))
                    .expect("parse points");
            let collection = args[4].clone();
            let dim: usize = args[5].parse().expect("dim");
            let (resolved, _path) = resolved_for(store_dir);
            let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).expect("open");
            if !store.collection_exists(&collection) {
                store
                    .create_collection(
                        &collection,
                        &serde_json::json!({"size": dim, "distance": "Cosine"}),
                    )
                    .expect("create collection");
            }
            store.upsert(&collection, &points).expect("upsert");
            print!(
                "{}",
                serde_json::json!({
                    "ok": true,
                    "collections": store.list_collection_names().unwrap(),
                    "count": store.count(&collection, None).unwrap(),
                })
            );
        }
        Some("search") => {
            // search <store_dir> <query.json>  — reader-side (lock-free),
            // exactly what the MCP lane arm does.
            let store_dir = Path::new(&args[2]);
            let query: serde_json::Value =
                serde_json::from_str(&std::fs::read_to_string(&args[3]).expect("read query"))
                    .expect("parse query");
            let collection = query["collection"].as_str().expect("collection").to_string();
            let vector: Vec<f64> = query["vector"]
                .as_array()
                .map(|items| items.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect())
                .unwrap_or_default();
            let limit = query.get("limit").and_then(|v| v.as_u64()).unwrap_or(10) as usize;
            let filter = query.get("filter").filter(|v| !v.is_null());
            let using = query.get("using").and_then(|v| v.as_str());
            let reader = LocalQdrantReader::open(store_dir).expect("open reader");
            let hits = reader
                .search(&collection, &vector, limit, filter, true, false, using)
                .expect("search");
            let sizes = reader
                .get_collection_info(&collection)
                .ok()
                .and_then(|info| {
                    info.pointer("/result/config/params/vectors/size")
                        .map(|size| serde_json::json!({"default": size}))
                })
                .unwrap_or(serde_json::json!({}));
            print!(
                "{}",
                serde_json::json!({"ok": true, "hits": hits, "sizes": sizes, "collections": reader.list_collection_names().unwrap()})
            );
        }
        Some("info") => {
            // info <store_dir> <collection>
            let store_dir = Path::new(&args[2]);
            let collection = args[3].clone();
            let (resolved, _path) = resolved_for(store_dir);
            let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).expect("open");
            print!(
                "{}",
                serde_json::json!({
                    "ok": true,
                    "sizes": {
                        "default": store
                            .get_collection_info(&collection)
                            .ok()
                            .and_then(|info| info.pointer("/result/config/params/vectors/size").cloned())
                            .unwrap_or(serde_json::Value::Null),
                    },
                    "payload_indexes": store
                        .get_collection_info(&collection)
                        .ok()
                        .and_then(|info| info.pointer("/result/payload_indexes").cloned())
                        .unwrap_or(serde_json::Value::Null),
                })
            );
        }
        Some("gen-big") => {
            // gen-big <store_dir> <points> <dim> — bulk deferred ingest of a
            // store sized for the RSS gate (~132MB JSON).
            let store_dir = Path::new(&args[2]);
            let total: usize = args[3].parse().expect("points");
            let dim: usize = args[4].parse().expect("dim");
            let (resolved, _path) = resolved_for(store_dir);
            let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).expect("open");
            store
                .create_collection("code", &serde_json::json!({"size": dim, "distance": "Cosine"}))
                .or_else(|e| {
                    if store.collection_exists("code") { Ok(()) } else { Err(e) }
                })
                .expect("create big collection");
            let batch_size = 512;
            let mut batch = Vec::with_capacity(batch_size);
            for index in 0..total {
                let seed = (index as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15);
                let vector: Vec<f64> = (0..dim)
                    .map(|d| ((seed >> (d % 31)) & 0xff) as f64 / 255.0 + 0.001)
                    .collect();
                batch.push(serde_json::json!({
                    "id": format!("pt-{index:08}"),
                    "vector": vector,
                    "payload": {
                        "project_id_normalized": "rssproj",
                        "parser": "go",
                        "root_scope": "/r",
                        "file_path": format!("/r/file{}.go", index % 997),
                        "symbol": format!("sym_{index}"),
                    },
                }));
                if batch.len() == batch_size {
                    store.upsert_deferred("code", &batch).expect("bulk upsert");
                    batch.clear();
                }
            }
            if !batch.is_empty() {
                store.upsert_deferred("code", &batch).expect("bulk upsert");
            }
            store.flush().expect("flush");
            let store_file = store_dir.join("cortex-local-store.json");
            let bytes = std::fs::metadata(&store_file).expect("store file").len();
            eprintln!("gen-big: {total} points × {dim} dims → {bytes} bytes");
        }
        Some("read-big") => {
            // read-big <store_dir> <iters> — burst searches through the
            // lock-free reader (snapshot cached across iterations, revalidated
            // by mtime+size). Peak RSS is measured by the outer /usr/bin/time.
            let store_dir = Path::new(&args[2]);
            let iters: usize = args[3].parse().expect("iters");
            let (_resolved, path) = resolved_for(store_dir);
            let reader = LocalQdrantReader::open(&path).expect("open reader");
            let mut checksum = 0.0f64;
            for iteration in 0..iters {
                let vector: Vec<f64> = (0..512)
                    .map(|d| (((iteration * 31 + d) % 97) as f64) / 97.0)
                    .collect();
                let hits = reader
                    .search("code", &vector, 10, None, true, false, None)
                    .expect("search");
                checksum += hits.first().and_then(|h| h["score"].as_f64()).unwrap_or(0.0);
            }
            println!("read-big ok iters={iters} checksum={checksum:.6}");
        }
        other => {
            eprintln!("unknown subcommand {other:?} — expected init|search|info|gen-big|read-big");
            std::process::exit(2);
        }
    }
}