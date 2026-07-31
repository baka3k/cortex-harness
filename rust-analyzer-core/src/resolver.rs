//! Phase 3 — Call resolution + relation building.
//!
//! Direct port of `_resolve_calls` from `cplus_analyzer.py`. Given a batch of
//! `ParseOutput`s, builds the eight function indexes and resolves
//! `call.callee_id` for every call, mirroring the Python priority order:
//!
//!   1. qualified name + arity            (score 120)
//!   2. qualified name                     (score 110)
//!   3. file-local name + arity            (score 115)
//!   4. file-local name                    (score 105)
//!   5. scope-chain (closest first) + arity (score 100 - min(depth, 25))
//!   6. scope-chain + name                 (score 90 - min(depth, 25))
//!   7. global name + arity                (score 70)
//!   8. global name                        (score 50)
//!
//! Ties break on (score ASC, distance ASC, qualified_name ASC).

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::symbols::{CallEdge, FunctionDef, ParseOutput};

/// Minimal copy of a function used for indexing.
#[derive(Debug, Clone)]
struct FuncEntry {
    symbol_id: String,
    qualified_name: String,
    name: String,
    scope_name: Option<String>,
    arity: u32,
    file_path: String,
}

impl FuncEntry {
    fn from_def(func: &FunctionDef) -> Self {
        Self {
            symbol_id: func.symbol_id.clone(),
            qualified_name: func.qualified_name.clone(),
            name: func.name.clone(),
            scope_name: func.scope_name.clone(),
            arity: func.arity,
            file_path: func.file_path.clone(),
        }
    }
}

/// Aggregate of every function-level lookup table.
pub struct CallIndex {
    by_name: HashMap<String, Vec<FuncEntry>>,
    by_name_arity: HashMap<(String, u32), Vec<FuncEntry>>,
    by_scope_name: HashMap<(Option<String>, String), Vec<FuncEntry>>,
    by_scope_name_arity: HashMap<(Option<String>, String, u32), Vec<FuncEntry>>,
    by_file_name: HashMap<(String, String), Vec<FuncEntry>>,
    by_file_name_arity: HashMap<(String, String, u32), Vec<FuncEntry>>,
    by_qualified: HashMap<String, FuncEntry>,
    by_qualified_arity: HashMap<(String, u32), FuncEntry>,
}

impl CallIndex {
    pub fn from_payloads(payloads: &[ParseOutput]) -> Self {
        let mut idx = CallIndex {
            by_name: HashMap::new(),
            by_name_arity: HashMap::new(),
            by_scope_name: HashMap::new(),
            by_scope_name_arity: HashMap::new(),
            by_file_name: HashMap::new(),
            by_file_name_arity: HashMap::new(),
            by_qualified: HashMap::new(),
            by_qualified_arity: HashMap::new(),
        };
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry::from_def(func);
                idx.by_name.entry(entry.name.clone()).or_default().push(entry.clone());
                idx.by_name_arity
                    .entry((entry.name.clone(), entry.arity))
                    .or_default()
                    .push(entry.clone());
                idx.by_scope_name
                    .entry((entry.scope_name.clone(), entry.name.clone()))
                    .or_default()
                    .push(entry.clone());
                idx.by_scope_name_arity
                    .entry((entry.scope_name.clone(), entry.name.clone(), entry.arity))
                    .or_default()
                    .push(entry.clone());
                idx.by_file_name
                    .entry((entry.file_path.clone(), entry.name.clone()))
                    .or_default()
                    .push(entry.clone());
                idx.by_file_name_arity
                    .entry((entry.file_path.clone(), entry.name.clone(), entry.arity))
                    .or_default()
                    .push(entry.clone());
                idx.by_qualified.insert(entry.qualified_name.clone(), entry.clone());
                idx.by_qualified_arity
                    .insert((entry.qualified_name.clone(), entry.arity), entry);
            }
        }
        idx
    }
}

/// Build the same scope-chain lookup order the Python implementation uses.
fn scope_chain(scope: Option<&str>) -> Vec<Option<String>> {
    let Some(scope) = scope else {
        return vec![None];
    };
    let parts: Vec<&str> = scope.split("::").collect();
    let mut chain: Vec<Option<String>> = parts
        .iter()
        .enumerate()
        .map(|(idx, _)| {
            let take = parts.len() - idx;
            Some(parts[..take].join("::"))
        })
        .collect();
    chain.push(None);
    chain
}

/// Resolve every call's `callee_id` in-place using the scoring algorithm.
pub fn resolve_calls(payloads: &mut [ParseOutput], index: &CallIndex) {
    for payload in payloads {
        for call in &mut payload.calls {
            if call.callee_id.is_some() {
                continue;
            }
            let resolved = resolve_one(
                call,
                &index.by_name,
                &index.by_name_arity,
                &index.by_scope_name,
                &index.by_scope_name_arity,
                &index.by_file_name,
                &index.by_file_name_arity,
                &index.by_qualified,
                &index.by_qualified_arity,
            );
            if let Some(symbol_id) = resolved {
                call.callee_id = Some(symbol_id);
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_one(
    call: &CallEdge,
    by_name: &HashMap<String, Vec<FuncEntry>>,
    by_name_arity: &HashMap<(String, u32), Vec<FuncEntry>>,
    by_scope_name: &HashMap<(Option<String>, String), Vec<FuncEntry>>,
    by_scope_name_arity: &HashMap<(Option<String>, String, u32), Vec<FuncEntry>>,
    by_file_name: &HashMap<(String, String), Vec<FuncEntry>>,
    by_file_name_arity: &HashMap<(String, String, u32), Vec<FuncEntry>>,
    by_qualified: &HashMap<String, FuncEntry>,
    by_qualified_arity: &HashMap<(String, u32), FuncEntry>,
) -> Option<String> {
    let callee_name = call.callee_name.clone();
    let call_arity = call.call_arity;
    let caller_file = call.caller_file.clone();
    let caller_scope = call.caller_scope.clone();

    let mut best_by_symbol: HashMap<String, (i32, i32, String)> = HashMap::new();

    let short_name = callee_name
        .split("::")
        .last()
        .unwrap_or(&callee_name)
        .to_string();

    if callee_name.contains("::") {
        if let Some(entry) = by_qualified_arity.get(&(callee_name.clone(), call_arity)) {
            consider(&mut best_by_symbol, &entry.symbol_id, 120, 0, &entry.qualified_name);
        }
        if let Some(entry) = by_qualified.get(&callee_name) {
            consider(&mut best_by_symbol, &entry.symbol_id, 110, 0, &entry.qualified_name);
        }
    }

    if let Some(items) =
        by_file_name_arity.get(&(caller_file.clone(), short_name.clone(), call_arity))
    {
        for entry in items {
            consider(&mut best_by_symbol, &entry.symbol_id, 115, 0, &entry.qualified_name);
        }
    }
    if let Some(items) = by_file_name.get(&(caller_file, short_name.clone())) {
        for entry in items {
            consider(&mut best_by_symbol, &entry.symbol_id, 105, 0, &entry.qualified_name);
        }
    }

    for (depth, scope) in scope_chain(caller_scope.as_deref()).into_iter().enumerate() {
        if let Some(items) =
            by_scope_name_arity.get(&(scope.clone(), short_name.clone(), call_arity))
        {
            for entry in items {
                consider(
                    &mut best_by_symbol,
                    &entry.symbol_id,
                    100 - (depth as i32).min(25),
                    depth as i32,
                    &entry.qualified_name,
                );
            }
        }
        if let Some(items) = by_scope_name.get(&(scope, short_name.clone())) {
            for entry in items {
                consider(
                    &mut best_by_symbol,
                    &entry.symbol_id,
                    90 - (depth as i32).min(25),
                    depth as i32,
                    &entry.qualified_name,
                );
            }
        }
    }

    if let Some(items) = by_name_arity.get(&(short_name.clone(), call_arity)) {
        for entry in items {
            consider(&mut best_by_symbol, &entry.symbol_id, 70, 999, &entry.qualified_name);
        }
    }
    if let Some(items) = by_name.get(&short_name) {
        for entry in items {
            consider(&mut best_by_symbol, &entry.symbol_id, 50, 999, &entry.qualified_name);
        }
    }

    best_by_symbol
        .into_iter()
        .max_by(|(id_a, (sa, da, qa)), (id_b, (sb, db, qb))| {
            // Higher score wins; ties → smaller distance; final tie → smaller qname; final → smaller id.
            sa.cmp(sb)
                .then(da.cmp(db))
                .then(qa.cmp(qb))
                .then(id_a.cmp(id_b))
        })
        .map(|(id, _)| id)
}

fn consider(
    best_by_symbol: &mut HashMap<String, (i32, i32, String)>,
    symbol_id: &str,
    score: i32,
    distance: i32,
    qualified_name: &str,
) {
    let current = best_by_symbol.get(symbol_id).cloned();
    let candidate = (score, distance, qualified_name.to_string());
    let better = match current {
        None => true,
        Some((s, d, ref q)) => {
            (score, -(distance), qualified_name) > (s, -(d), q.as_str())
        }
    };
    if better {
        best_by_symbol.insert(symbol_id.to_string(), candidate);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// PyO3 surface
// ─────────────────────────────────────────────────────────────────────────────

/// Resolve `callee_id` for every call in a batch of Python dict payloads.
///
/// Receives a `list[dict]` from `extract_cplus_batch`, runs the same
/// in-Rust resolution algorithm, and writes the resolved `callee_id` back to
/// each call dict in place.
///
/// Phase 3 acceptance criteria:
///   * Differential parity with Python `_resolve_calls`
///   * Single GIL pass — Rust does all the work
#[pyfunction]
pub fn resolve_batch(py: Python, payloads_list: &PyList) -> PyResult<()> {
    // Pass 1: extract all functions and calls from PyDicts into Rust structs.
    let mut payloads: Vec<ParseOutput> = Vec::with_capacity(payloads_list.len());
    for idx in 0..payloads_list.len() {
        let item = payloads_list.get_item(idx)?;
        let dict = item.downcast::<PyDict>()?;
        payloads.push(parse_output_from_pydict(dict)?);
    }

    // Pass 2: build the call index from all functions.
    let index = CallIndex::from_payloads(&payloads);

    // Pass 3: resolve in parallel (independent payloads → safe par_iter_mut).
    use rayon::prelude::*;
    payloads
        .par_iter_mut()
        .for_each(|payload| resolve_calls(std::slice::from_mut(payload), &index));

    // Pass 4: write resolved callee_id back into each call dict.
    for (idx, payload) in payloads.iter().enumerate() {
        let item = payloads_list.get_item(idx)?;
        let dict = item.downcast::<PyDict>()?;
        let calls_any = dict.get_item("calls").ok().flatten().ok_or_else(|| {
            pyo3::exceptions::PyKeyError::new_err("calls key missing from payload dict")
        })?;
        let calls_list = calls_any.downcast::<PyList>()?;
        for (i, call) in payload.calls.iter().enumerate() {
            let call_any = calls_list.get_item(i)?;
            let call_dict = call_any.downcast::<PyDict>()?;
            if let Some(ref cid) = call.callee_id {
                call_dict.set_item("callee_id", cid)?;
            } else {
                call_dict.set_item("callee_id", py.None())?;
            }
        }
    }
    Ok(())
}

/// Hydrate a minimal `ParseOutput` from a Python dict payload.
///
/// Only the fields needed for resolution are extracted — `functions` (for the
/// call index) and `calls` (the resolution targets). Other fields are ignored
/// since the Python dict is the authoritative payload.
fn parse_output_from_pydict(dict: &PyDict) -> PyResult<ParseOutput> {
    let file_path = dict
        .get_item("file_def")
        .ok()
        .flatten()
        .and_then(|fd| fd.downcast::<PyDict>().ok())
        .and_then(|fd| fd.get_item("file_path").ok().flatten())
        .and_then(|fp| fp.extract::<String>().ok())
        .unwrap_or_default();

    let mut out = ParseOutput::default();
    out.file_def.file_path = file_path.clone();

    if let Ok(Some(funcs_any)) = dict.get_item("functions") {
        if let Ok(funcs_list) = funcs_any.downcast::<PyList>() {
            for idx in 0..funcs_list.len() {
                let item = funcs_list.get_item(idx)?;
                let d = item.downcast::<PyDict>()?;
                let f = function_def_from_pydict(d, &file_path)?;
                out.functions.push(f);
            }
        }
    }

    if let Ok(Some(calls_any)) = dict.get_item("calls") {
        if let Ok(calls_list) = calls_any.downcast::<PyList>() {
            for idx in 0..calls_list.len() {
                let item = calls_list.get_item(idx)?;
                let d = item.downcast::<PyDict>()?;
                out.calls.push(call_edge_from_pydict(d, &file_path)?);
            }
        }
    }

    Ok(out)
}

fn function_def_from_pydict(dict: &PyDict, fallback_file: &str) -> PyResult<FunctionDef> {
    let get_str = |key: &str| -> String {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_default()
    };
    let get_opt_str = |key: &str| -> Option<String> {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<String>().ok())
            .filter(|s| !s.is_empty())
    };
    let get_u32 = |key: &str, default: u32| -> u32 {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<u32>().ok())
            .unwrap_or(default)
    };

    Ok(FunctionDef {
        symbol_id: get_str("symbol_id"),
        qualified_name: get_str("qualified_name"),
        name: get_str("name"),
        kind: get_str("kind"),
        scope_name: get_opt_str("scope_name"),
        file_path: get_opt_str("file_path").unwrap_or_else(|| fallback_file.to_string()),
        arity: get_u32("arity", 0),
        ..FunctionDef::default()
    })
}

fn call_edge_from_pydict(dict: &PyDict, fallback_file: &str) -> PyResult<CallEdge> {
    let get_str = |key: &str| -> String {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_default()
    };
    let get_opt_str = |key: &str| -> Option<String> {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<String>().ok())
            .filter(|s| !s.is_empty())
    };
    let get_u32 = |key: &str, default: u32| -> u32 {
        dict.get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<u32>().ok())
            .unwrap_or(default)
    };

    Ok(CallEdge {
        caller_id: get_str("caller_id"),
        caller_file: get_opt_str("caller_file").unwrap_or_else(|| fallback_file.to_string()),
        caller_scope: get_opt_str("caller_scope"),
        call_line: get_u32("call_line", 0),
        call_column: get_u32("call_column", 0),
        call_start_byte: get_u32("call_start_byte", 0),
        call_branch_kind: get_str("call_branch_kind"),
        call_loop_depth: get_u32("call_loop_depth", 0),
        call_control_frames_json: get_str("call_control_frames_json"),
        call_type: get_str("call_type"),
        call_arity: get_u32("call_arity", 0),
        callee_name: get_str("callee_name"),
        callee_id: get_opt_str("callee_id"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_func(
        name: &str,
        qname: &str,
        scope: Option<&str>,
        arity: u32,
        file: &str,
        id: &str,
    ) -> FunctionDef {
        FunctionDef {
            symbol_id: id.to_string(),
            qualified_name: qname.to_string(),
            name: name.to_string(),
            scope_name: scope.map(|s| s.to_string()),
            file_path: file.to_string(),
            arity,
            ..FunctionDef::default()
        }
    }

    fn make_call(callee_name: &str, scope: Option<&str>, file: &str, arity: u32) -> CallEdge {
        CallEdge {
            caller_scope: scope.map(|s| s.to_string()),
            caller_file: file.to_string(),
            call_arity: arity,
            callee_name: callee_name.to_string(),
            ..CallEdge::default()
        }
    }

    fn payload_with_functions_and_call(funcs: Vec<FunctionDef>, call: CallEdge) -> ParseOutput {
        ParseOutput {
            functions: funcs,
            calls: vec![call],
            ..ParseOutput::default()
        }
    }

    #[test]
    fn scope_chain_descends_to_root_then_none() {
        let chain = scope_chain(Some("a::b::c"));
        assert_eq!(
            chain,
            vec![
                Some("a::b::c".to_string()),
                Some("a::b".to_string()),
                Some("a".to_string()),
                None
            ]
        );
    }

    #[test]
    fn scope_chain_none_returns_only_none() {
        assert_eq!(scope_chain(None), vec![None]);
    }

    #[test]
    fn qualified_match_wins_over_global() {
        let f1 = make_func("foo", "ns1::foo", Some("ns1"), 0, "a.cpp", "A");
        let f2 = make_func("foo", "foo", None, 0, "b.cpp", "B");
        let call = make_call("ns1::foo", Some("ns1"), "a.cpp", 0);
        let mut payloads = vec![payload_with_functions_and_call(vec![f1.clone(), f2.clone()], call)];
        let idx = CallIndex::from_payloads(&payloads);
        resolve_calls(&mut payloads, &idx);
        assert_eq!(payloads[0].calls[0].callee_id.as_deref(), Some("A"));
    }

    #[test]
    fn file_local_match_wins_over_global() {
        let f1 = make_func("helper", "ns::helper", Some("ns"), 0, "other.cpp", "OTHER");
        let f2 = make_func("helper", "helper", None, 0, "this.cpp", "HERE");
        let call = make_call("helper", None, "this.cpp", 0);
        let mut payloads = vec![payload_with_functions_and_call(vec![f1, f2.clone()], call)];
        let idx = CallIndex::from_payloads(&payloads);
        resolve_calls(&mut payloads, &idx);
        assert_eq!(payloads[0].calls[0].callee_id.as_deref(), Some("HERE"));
    }

    #[test]
    fn closest_scope_wins_when_no_file_local_tie() {
        // INNER lives in a.cpp (matches caller's file → score 115).
        // OUTER lives in other.cpp (no file-local match → only scope-chain).
        // At score 115 vs 99, OUTER file-local hits 105 (lower than INNER's 115)
        // but INNER wins because file-local + arity=0 has priority.
        let inner = make_func("foo", "Outer::Inner::foo", Some("Outer::Inner"), 0, "a.cpp", "INNER");
        let outer = make_func("foo", "Outer::foo", Some("Outer"), 0, "other.cpp", "OUTER");
        let call = make_call("foo", Some("Outer::Inner::Deeper"), "a.cpp", 0);
        let mut payloads = vec![payload_with_functions_and_call(
            vec![inner.clone(), outer.clone()],
            call,
        )];
        let idx = CallIndex::from_payloads(&payloads);
        resolve_calls(&mut payloads, &idx);
        assert_eq!(payloads[0].calls[0].callee_id.as_deref(), Some("INNER"));
    }

    #[test]
    fn closest_scope_wins_when_only_scope_chain_matches() {
        // Both functions live in the caller's file (a.cpp).
        // File-local candidates both score 115 and tie on lexical order
        // (Outer::foo < Outer::Inner::foo), so OUTER would win under pure
        // file-local priority. This test verifies the scope-chain logic in
        // isolation by using files that only match via scope.
        let inner = make_func("foo", "Outer::Inner::foo", Some("Outer::Inner"), 0, "outer.cpp", "INNER");
        let outer = make_func("foo", "Outer::foo", Some("Outer"), 0, "outer.cpp", "OUTER");
        let call = make_call("foo", Some("Outer::Inner::Deeper"), "inner.cpp", 0);
        let mut payloads = vec![payload_with_functions_and_call(
            vec![inner.clone(), outer.clone()],
            call,
        )];
        let idx = CallIndex::from_payloads(&payloads);
        resolve_calls(&mut payloads, &idx);
        // Neither function is file-local to inner.cpp → only scope-chain
        // matches → INNER at depth 1 (score 99) wins over OUTER at depth 2
        // (score 98).
        assert_eq!(payloads[0].calls[0].callee_id.as_deref(), Some("INNER"));
    }
}