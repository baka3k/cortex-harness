//! `cortex_extract` — PyO3 native extension exposing the Rust extraction layer.
//!
//! Public functions:
//! - `extract_cplus(path, root)` — single-file parse returning a `dict` matching
//!   the Python `ParseResult` schema.
//! - `extract_cplus_batch(paths, root, threads)` — parallel batch returning a
//!   `list[dict]`.
//! - `extract_batch(paths, root, language, threads)` — language-parametric API
//!   used by Phase 6.

mod calls;
mod csharp;
mod go;
mod grammar;
mod java;
mod js_lang;
mod parser;
mod payload;
mod php;
mod profile;
mod relations;
mod resolver;
mod semantic;
mod symbols;
mod text;
mod walker;

use std::path::Path;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule};

use crate::parser::{default_is_cpp, is_cpp_path, parse_source};
use crate::walker::walk_tree;
use crate::symbols::ParseOutput;

fn parse_path_to_output(path: &str, root: &str, force_is_cpp: Option<bool>) -> Option<ParseOutput> {
    let is_cpp = force_is_cpp.unwrap_or_else(|| default_is_cpp(path));
    let source = std::fs::read(path).ok()?;
    let tree = parse_source(&source, is_cpp)?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());

    // Leak the source into static lifetime so walk_tree's `'static` bound holds.
    let source_static: &'static [u8] = Box::leak(source.into_boxed_slice());
    let rel_static: &'static str = Box::leak(rel_path.into_boxed_str());

    let out = walk_tree(tree.root_node(), source_static, rel_static);
    Some(out)
}

/// Parse a single Go file and return the `GoParseOutput`.
fn parse_go_path_to_output(path: &str, root: &str) -> Option<go::GoParseOutput> {
    let source = std::fs::read(path).ok()?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    go::parse_go_source(&source, &rel_path)
}

/// Parse a single Java file and return the `JavaParseOutput`.
fn parse_java_path_to_output(path: &str, root: &str) -> Option<java::JavaParseOutput> {
    let source = std::fs::read(path).ok()?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    java::parse_java_source(&source, &rel_path)
}

/// Parse a single JavaScript file and return the `JsParseOutput`.
fn parse_js_path_to_output(path: &str, root: &str) -> Option<js_lang::JsParseOutput> {
    let source = std::fs::read(path).ok()?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    js_lang::parse_js_source(&source, &rel_path)
}

/// Parse a single C# file and return the `CSharpParseOutput`.
fn parse_csharp_path_to_output(path: &str, root: &str) -> Option<csharp::CSharpParseOutput> {
    let source = std::fs::read(path).ok()?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    csharp::parse_csharp_source(&source, &rel_path)
}

/// Parse a single PHP file and return the `PhpParseOutput`.
fn parse_php_path_to_output(path: &str, root: &str) -> Option<php::PhpParseOutput> {
    let source = std::fs::read(path).ok()?;
    let rel_path = Path::new(path)
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    php::parse_php_source(&source, &rel_path)
}

/// Parse a single C/C++ file and return the Python `dict` payload.
#[pyfunction]
fn extract_cplus(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_path_to_output(path, root, None)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("parse failed"))?;
        payload::build_payload(py, &out)
    })
}

/// Force the C++ parser (skip header-retry heuristic).
#[pyfunction]
fn extract_cplus_force_cpp(path: &str, root: &str, force_cpp: bool) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_path_to_output(path, root, Some(force_cpp))
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("parse failed"))?;
        payload::build_payload(py, &out)
    })
}

/// Parse a single Go file and return the Python `dict` payload (Phase 2).
#[pyfunction]
fn extract_go(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_go_path_to_output(path, root)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("go parse failed"))?;
        payload::build_go_payload(py, &out)
    })
}

/// Parse a single Java file and return the Python `dict` payload (Phase 2 Tier 2).
#[pyfunction]
fn extract_java(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_java_path_to_output(path, root)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("java parse failed"))?;
        payload::build_java_payload(py, &out)
    })
}

/// Parse a single JavaScript file and return the Python `dict` payload (Phase 2 Tier 2).
#[pyfunction]
fn extract_javascript(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_js_path_to_output(path, root)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("js parse failed"))?;
        payload::build_js_payload(py, &out)
    })
}

/// Parse a single C# file and return the Python `dict` payload (Phase 2 Tier 2).
#[pyfunction]
fn extract_csharp(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_csharp_path_to_output(path, root)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("csharp parse failed"))?;
        payload::build_csharp_payload(py, &out)
    })
}

/// Parse a single PHP file and return the Python `dict` payload (Phase 2 Tier 2).
#[pyfunction]
fn extract_php(path: &str, root: &str) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let out = parse_php_path_to_output(path, root)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("php parse failed"))?;
        payload::build_php_payload(py, &out)
    })
}

/// Parse many Go files in parallel using rayon (Phase 2).
///
/// `threads` of 0 → use the rayon default (logical CPUs).
#[pyfunction]
fn extract_go_batch(paths: Vec<String>, root: String, threads: usize) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let pool = if threads == 0 {
            rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
            })?
        } else {
            rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
        };

        let results: Vec<Option<go::GoParseOutput>> = pool.install(|| {
            use rayon::prelude::*;
            paths
                .par_iter()
                .map(|p| parse_go_path_to_output(p, &root))
                .collect()
        });

        let list = PyList::empty(py);
        for (idx, res) in results.into_iter().enumerate() {
            match res {
                Some(out) => {
                    let dict = payload::build_go_payload(py, &out)?;
                    list.append(dict)?;
                }
                None => {
                    let err_dict = PyDict::new(py);
                    err_dict.set_item("error", "parse_failed")?;
                    err_dict.set_item("path", &paths[idx])?;
                    list.append(err_dict)?;
                }
            }
        }
        Ok(list.into())
    })
}

/// Parse many Java files in parallel using rayon (Phase 2 Tier 2).
///
/// `threads` of 0 → use the rayon default (logical CPUs).
#[pyfunction]
fn extract_java_batch(paths: Vec<String>, root: String, threads: usize) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let pool = if threads == 0 {
            rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
            })?
        } else {
            rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
        };

        let results: Vec<Option<java::JavaParseOutput>> = pool.install(|| {
            use rayon::prelude::*;
            paths
                .par_iter()
                .map(|p| parse_java_path_to_output(p, &root))
                .collect()
        });

        let list = PyList::empty(py);
        for (idx, res) in results.into_iter().enumerate() {
            match res {
                Some(out) => {
                    let dict = payload::build_java_payload(py, &out)?;
                    list.append(dict)?;
                }
                None => {
                    let err_dict = PyDict::new(py);
                    err_dict.set_item("error", "parse_failed")?;
                    err_dict.set_item("path", &paths[idx])?;
                    list.append(err_dict)?;
                }
            }
        }
        Ok(list.into())
    })
}

/// Parse many files in parallel using rayon.
///
/// `threads` of 0 → use the rayon default (logical CPUs).
#[pyfunction]
fn extract_cplus_batch(paths: Vec<String>, root: String, threads: usize) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        let pool = if threads == 0 {
            rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
            })?
        } else {
            rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
        };

        let results: Vec<Result<Option<ParseOutput>, String>> = pool.install(|| {
            use rayon::prelude::*;
            paths
                .par_iter()
                .map(|p| Ok(parse_path_to_output(p, &root, None)))
                .collect()
        });

        let list = PyList::empty(py);
        for (idx, res) in results.into_iter().enumerate() {
            match res {
                Ok(Some(out)) => {
                    let dict = payload::build_payload(py, &out)?;
                    list.append(dict)?;
                }
                Ok(None) => {
                    let err_dict = PyDict::new(py);
                    err_dict.set_item("error", "parse_failed")?;
                    err_dict.set_item("path", &paths[idx])?;
                    list.append(err_dict)?;
                }
                Err(e) => return Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
            }
        }
        Ok(list.into())
    })
}

/// Language-parametric batch (Phase 6).
#[pyfunction]
fn extract_batch(
    paths: Vec<String>,
    root: String,
    language: String,
    threads: usize,
) -> PyResult<PyObject> {
    Python::with_gil(|py| {
        // Route Go to the Go pipeline (different payload schema).
        if language == "go" {
            let pool = if threads == 0 {
                rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
            } else {
                rayon::ThreadPoolBuilder::new()
                    .num_threads(threads)
                    .build()
                    .map_err(|e| {
                        pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                    })?
            };

            let results: Vec<Option<go::GoParseOutput>> = pool.install(|| {
                use rayon::prelude::*;
                paths
                    .par_iter()
                    .map(|p| parse_go_path_to_output(p, &root))
                    .collect()
            });

            let list = PyList::empty(py);
            for out in results.into_iter().flatten() {
                let dict = payload::build_go_payload(py, &out)?;
                list.append(dict)?;
            }
            return Ok(list.into());
        }

        // Route Java to the Java pipeline (Family A — 9-tuple payload).
        if language == "java" {
            let pool = if threads == 0 {
                rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
            } else {
                rayon::ThreadPoolBuilder::new()
                    .num_threads(threads)
                    .build()
                    .map_err(|e| {
                        pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                    })?
            };

            let results: Vec<Option<java::JavaParseOutput>> = pool.install(|| {
                use rayon::prelude::*;
                paths
                    .par_iter()
                    .map(|p| parse_java_path_to_output(p, &root))
                    .collect()
            });

            let list = PyList::empty(py);
            for out in results.into_iter().flatten() {
                let dict = payload::build_java_payload(py, &out)?;
                list.append(dict)?;
            }
            return Ok(list.into());
        }

        let force_is_cpp = match language.as_str() {
            "c" => Some(false),
            "cpp" | "cplus" => Some(true),
            _ => None,
        };
        let pool = if threads == 0 {
            rayon::ThreadPoolBuilder::new().build().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
            })?
        } else {
            rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("rayon init: {}", e))
                })?
        };

        let results: Vec<Option<ParseOutput>> = pool.install(|| {
            use rayon::prelude::*;
            paths
                .par_iter()
                .map(|p| parse_path_to_output(p, &root, force_is_cpp))
                .collect()
        });

        let list = PyList::empty(py);
        for out in results.into_iter().flatten() {
            let dict = payload::build_payload(py, &out)?;
            list.append(dict)?;
        }
        Ok(list.into())
    })
}

/// Cheap probe so Python can detect language availability.
#[pyfunction]
fn is_cpp_file(path: &str) -> PyResult<bool> {
    Ok(is_cpp_path(path))
}

/// Phase 3 — resolve `callee_id` for every call across a batch of payloads.
#[pyfunction]
fn resolve_batch(py: Python, payloads: &PyList) -> PyResult<()> {
    resolver::resolve_batch(py, payloads)
}

/// Phase 4 — enrich functions with intent / summary / signals.
#[pyfunction]
fn enrich_corpus(py: Python, functions: &PyList, calls: &PyList) -> PyResult<()> {
    semantic::enrich_corpus_py(py, functions, calls)
}

/// Phase 6 — list the languages the extension can dispatch.
#[pyfunction]
fn supported_languages() -> PyResult<Vec<String>> {
    Ok(grammar::registry().iter().map(|g| g.id().to_string()).collect())
}

/// Phase 6 — resolve which grammar owns a given file path.
#[pyfunction]
fn detect_language(path: &str) -> PyResult<Option<String>> {
    Ok(grammar::by_path(path).map(|g| g.id().to_string()))
}

/// Phase 6 — parse a source string with the requested grammar; return the
/// tree-sitter root node kind. Useful as a sanity probe while the per-
/// language walker is being implemented.
#[pyfunction]
fn parse_root_kind(language: &str, source: Vec<u8>) -> PyResult<Option<String>> {
    Ok(grammar::parse_root_kind(language, &source))
}

#[pymodule]
fn cortex_extract(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_cplus, m)?)?;
    m.add_function(wrap_pyfunction!(extract_cplus_force_cpp, m)?)?;
    m.add_function(wrap_pyfunction!(extract_cplus_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_go, m)?)?;
    m.add_function(wrap_pyfunction!(extract_go_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_java, m)?)?;
    m.add_function(wrap_pyfunction!(extract_java_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_csharp, m)?)?;
    m.add_function(wrap_pyfunction!(extract_php, m)?)?;
    m.add_function(wrap_pyfunction!(extract_batch, m)?)?;
    m.add_function(wrap_pyfunction!(is_cpp_file, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_batch, m)?)?;
    m.add_function(wrap_pyfunction!(enrich_corpus, m)?)?;
    m.add_function(wrap_pyfunction!(supported_languages, m)?)?;
    m.add_function(wrap_pyfunction!(detect_language, m)?)?;
    m.add_function(wrap_pyfunction!(parse_root_kind, m)?)?;
    Ok(())
}
