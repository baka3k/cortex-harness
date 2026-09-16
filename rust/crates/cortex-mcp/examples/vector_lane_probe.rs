//! Lane-level probe for the parity matrix (review fix: the explore-seeds leg
//! must drive the REAL `search_collection` + `merge_hits` from
//! `cortex-mcp/src/graph/vector_lane.rs`, not a re-implementation).
//!
//! Usage: vector_lane_probe <store_root> <collection1,collection2,…> <limit>
//! Reads the query vector from stdin as a JSON array. `CORTEX_VECTOR_BACKEND`
//! is inherited from the caller (set `=rust` to take the native arms).

use std::path::PathBuf;

use cortex_mcp::graph::vector_lane::{
    collection_vector_sizes, list_collection_names, merge_hits, search_collection, VectorStore,
};
use serde_json::Value;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let store_root = PathBuf::from(&args[1]);
    let collections: Vec<String> = args[2].split(',').map(str::to_string).collect();
    let limit: usize = args[3].parse().expect("limit");
    let vector: Vec<f64> = serde_json::from_str(
        &std::io::stdin().lines().next().expect("stdin").expect("line"),
    )
    .expect("query vector");

    let store = VectorStore::Local { store_root };
    let mut per_collection: Vec<Vec<Value>> = Vec::new();
    let mut errors: Vec<Value> = Vec::new();
    for collection in &collections {
        match search_collection(&store, collection, &vector, None, limit, None) {
            Ok(mut hits) => {
                for hit in hits.iter_mut() {
                    if let Some(object) = hit.as_object_mut() {
                        object.insert("_collection".into(), Value::String(collection.clone()));
                    }
                }
                per_collection.push(hits);
            }
            Err(error) => errors.push(serde_json::json!({"collection": collection, "error": error})),
        }
    }
    let merged = merge_hits(per_collection, limit);
    print!(
        "{}",
        serde_json::json!({
            "ok": true,
            "merged": merged,
            "sizes": {
                "seed_a": collection_vector_sizes(&store, "seed_a").ok(),
                "seed_b": collection_vector_sizes(&store, "seed_b").ok(),
            },
            "list": list_collection_names(&store).unwrap_or_default(),
            "errors": errors,
        })
    );
}
