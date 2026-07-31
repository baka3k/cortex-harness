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
mod parser;
mod payload;
mod relations;
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

#[pymodule]
fn cortex_extract(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_cplus, m)?)?;
    m.add_function(wrap_pyfunction!(extract_cplus_force_cpp, m)?)?;
    m.add_function(wrap_pyfunction!(extract_cplus_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_batch, m)?)?;
    m.add_function(wrap_pyfunction!(is_cpp_file, m)?)?;
    Ok(())
}
