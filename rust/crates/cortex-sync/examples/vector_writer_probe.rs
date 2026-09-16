//! Writer-seam probe for the native local vector lane (phase-04 matrix +
//! phase-05 drill of plans/260916-1154-native-vector-ingest-local). Drives
//! the real `sync_vector_documents` contract over the local JSON engine with
//! a deterministic embedder. Not a user tool.

use std::path::Path;

use cortex_embed::{EmbedError, Embedder};
use cortex_storage::config::{resolve_storage, ResolveOverrides, StorageRole};
use cortex_storage::qdrant::LocalQdrantStore;
use cortex_sync::vector_store::sync_vector_documents;
use cortex_sync::vector_sync::VectorDocument;
use serde_json::{json, Value};

/// Deterministic dim-N embedder: text bytes → stable pseudo-vector. Same
// shape the unit tests use, so matrix expectations are stable across runs.
struct FixedEmbedder {
    dim: usize,
}

impl Embedder for FixedEmbedder {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
        Ok(texts
            .iter()
            .map(|text| {
                let seed = text
                    .bytes()
                    .fold(0u64, |acc, byte| acc.wrapping_mul(31).wrapping_add(byte as u64));
                (0..self.dim)
                    .map(|dim| ((seed >> (dim % 13)) & 0xff) as f32 / 255.0 + 0.01)
                    .collect::<Vec<f32>>()
            })
            .collect())
    }
    fn dimension(&self) -> Option<usize> {
        None
    }
    fn backend_name(&self) -> &'static str {
        "fixed"
    }
}

fn open_store(store_dir: &Path) -> LocalQdrantStore {
    let root = store_dir.join(".probe-root");
    let resolved = resolve_storage(
        &root,
        None,
        &ResolveOverrides {
            qdrant_code_path: Some(store_dir.to_string_lossy().into_owned()),
            data_home: Some(root.to_string_lossy().into_owned()),
            ..Default::default()
        },
    )
    .expect("resolve storage");
    resolved.ensure_directories().expect("ensure dirs");
    LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).expect("open store")
}

fn to_documents(entries: &[Value]) -> Vec<VectorDocument> {
    entries
        .iter()
        .map(|entry| VectorDocument {
            id: entry["id"].as_str().expect("id").to_string(),
            text: entry["text"].as_str().expect("text").to_string(),
            payload: entry["payload"].as_object().expect("payload object").clone(),
        })
        .collect()
}

fn stored_ids(store: &LocalQdrantStore, collection: &str) -> Vec<String> {
    let Ok((page, _)) = store.scroll(collection, None, 100_000, false, false, None) else {
        return Vec::new();
    };
    let mut ids: Vec<String> = page
        .iter()
        .map(|hit| hit["id"].as_str().expect("string id").to_string())
        .collect();
    ids.sort();
    ids
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("run") => {
            // run <store_dir> <scenario.json>
            // scenario: {"collection", "dim", "documents": [...],
            //            "cleanup_paths": [...], "full_replace": bool}
            let store = open_store(Path::new(&args[2]));
            let scenario: Value =
                serde_json::from_str(&std::fs::read_to_string(&args[3]).expect("read scenario"))
                    .expect("parse scenario");
            let collection = scenario["collection"].as_str().expect("collection").to_string();
            let dim = scenario.get("dim").and_then(Value::as_u64).unwrap_or(8) as usize;
            let project_id = scenario.get("project_id").and_then(Value::as_str).unwrap_or("proj").to_string();
            let parser = scenario.get("parser").and_then(Value::as_str).unwrap_or("go").to_string();
            let root_scope = scenario.get("root_scope").and_then(Value::as_str).unwrap_or("/r").to_string();
            let documents = to_documents(scenario["documents"].as_array().expect("documents"));
            let cleanup: Vec<String> = scenario
                .get("cleanup_paths")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .map(|v| v.as_str().expect("path str").to_string())
                        .collect()
                })
                .unwrap_or_default();
            let full_replace = scenario.get("full_replace").and_then(Value::as_bool).unwrap_or(false);
            let result = sync_vector_documents(
                &store,
                &FixedEmbedder { dim },
                &collection,
                &documents,
                &parser,
                &project_id,
                &root_scope,
                &cleanup,
                full_replace,
            );
            match result {
                Ok(count) => print!(
                    "{}",
                    json!({
                        "ok": true,
                        "count": count,
                        "ids": stored_ids(&store, &collection),
                        "collections": store.list_collection_names().unwrap(),
                    })
                ),
                Err(error) => print!("{}", json!({"ok": false, "error": error, "count": 0, "ids": stored_ids(&store, &collection)})),
            }
        }
        Some("drift") => {
            // drift <store_dir> <existing_size> <embed_dim> — pre-create a
            // collection, then let the contract's drift guard fire.
            let store_dir = Path::new(&args[2]);
            let existing_size: usize = args[3].parse().expect("existing size");
            let embed_dim: usize = args[4].parse().expect("embed dim");
            let store = open_store(store_dir);
            store
                .create_collection(
                    "code",
                    &json!({"vectors": {"size": existing_size, "distance": "Cosine"}}),
                )
                .expect("pre-create");
            let documents = vec![VectorDocument {
                id: "id-a".into(),
                text: "seed".into(),
                payload: {
                    let mut payload = serde_json::Map::new();
                    payload.insert("project_id".into(), json!("proj"));
                    payload.insert("project_id_normalized".into(), json!("proj"));
                    payload.insert("parser".into(), json!("go"));
                    payload.insert("root_scope".into(), json!("/r"));
                    payload.insert("file_path".into(), json!("/r/a.go"));
                    payload
                },
            }];
            let error = sync_vector_documents(
                &store,
                &FixedEmbedder { dim: embed_dim },
                "code",
                &documents,
                "go",
                "proj",
                "/r",
                &[],
                false,
            )
            .expect_err("drift must error");
            print!("{}", json!({"ok": false, "error": error}));
        }
        Some("resolve") => {
            // resolve [qdrant_url] — prints the open_native_store outcome
            // (the CORTEX_VECTOR_BACKEND hatch decision) for the drill.
            use cortex_sync::vector_store::{open_native_store, NativeStore};
            let locator = args.get(2).map(String::as_str);
            match open_native_store(locator) {
                NativeStore::Remote(_) => print!("{}", json!({"kind": "remote"})),
                NativeStore::Local(store) => {
                    let path = store.path().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
                    store.close();
                    print!("{}", json!({"kind": "local", "store_root": path}))
                }
                NativeStore::Unsupported(reason) => {
                    print!("{}", json!({"kind": "unsupported", "reason": reason}))
                }
            }
        }
        other => {
            eprintln!("unknown subcommand {other:?} — expected run|drift|resolve");
            std::process::exit(2);
        }
    }
}