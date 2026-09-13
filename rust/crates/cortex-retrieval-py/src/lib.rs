//! PyO3 bindings cho retrieval brain — cho phép Python MCP servers gọi thẳng
//! phần Rust (BM25/intent/query-understanding) không cần subprocess.
//!
//! Build:
//!   bash scripts/rust_parity/build_pyo3.sh

// pyo3 0.22 macro-generated wrappers dùng unsafe fn nội bộ — edition 2024
// strictness + clippy flag chúng; đây không phải unsafe do ta viết.
#![allow(unsafe_op_in_unsafe_fn)]
#![allow(clippy::useless_conversion)]
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// `tools.common.query_intent_classifier.classify_query`.
#[pyfunction]
fn classify_query(query: &str) -> String {
    cortex_retrieval::intent::classify_query(query).to_string()
}

/// `classify_query_explain` → dict {intent, matched}.
#[pyfunction]
fn classify_query_explain(query: &str) -> PyResult<(String, String)> {
    let explain = cortex_retrieval::intent::classify_query_explain(query);
    Ok((explain.intent.to_string(), explain.matched))
}

/// `get_weight_profile(intent)` → dict {signal: weight}.
#[pyfunction]
fn get_weight_profile(intent: &str) -> Vec<(String, f64)> {
    cortex_retrieval::intent::get_weight_profile(intent).into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

/// `BM25Ranker.build_index + score` stateless trong 1 call.
/// `documents_json`: JSON array [{"id": ..., "text": ...}]; trả JSON dict
/// {symbol_id: score} giống `BM25Ranker.score(query)`.
#[pyfunction]
#[pyo3(signature = (documents_json, query, text_field = "text", id_field = "id"))]
fn bm25_score(
    documents_json: &str,
    query: &str,
    text_field: &str,
    id_field: &str,
) -> PyResult<String> {
    let rows: Vec<serde_json::Value> = serde_json::from_str(documents_json)
        .map_err(|e| PyValueError::new_err(format!("documents_json lỗi: {e}")))?;
    let documents: Vec<cortex_retrieval::bm25::Document> = rows
        .into_iter()
        .map(|row| {
            let id = row
                .get(id_field)
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            let text = row
                .get(text_field)
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            cortex_retrieval::bm25::Document::new(id, text)
        })
        .collect();
    let mut ranker = cortex_retrieval::bm25::Bm25Ranker::new();
    ranker.build_index(&documents);
    let scores = ranker.score(query);
    serde_json::to_string(&scores)
        .map_err(|e| PyValueError::new_err(format!("serialize lỗi: {e}")))
}

/// `QueryUnderstanding.from_text` → JSON dict cùng shape `to_dict()`.
#[pyfunction]
fn query_understanding(text: &str) -> PyResult<String> {
    let u = cortex_retrieval::query_understanding::QueryUnderstanding::from_text(text);
    let payload = serde_json::json!({
        "intent": u.intent,
        "entities": u.entities,
        "keywords": u.keywords,
        "actions": u.actions,
        "domain_signals": u.domain_signals,
        "embedding_text": u.embedding_text,
        "raw_query": u.raw_query,
    });
    serde_json::to_string(&payload).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pymodule]
fn cortex_retrieval_py(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(pyo3::wrap_pyfunction!(classify_query, module)?)?;
    module.add_function(pyo3::wrap_pyfunction!(classify_query_explain, module)?)?;
    module.add_function(pyo3::wrap_pyfunction!(get_weight_profile, module)?)?;
    module.add_function(pyo3::wrap_pyfunction!(bm25_score, module)?)?;
    module.add_function(pyo3::wrap_pyfunction!(query_understanding, module)?)?;
    Ok(())
}
