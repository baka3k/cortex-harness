//! Build Python `dict` payload from a `ParseOutput`.
//!
//! Mirrors the JSON shape produced by `asdict(...)` on the Python dataclasses.
//! Returning PyDict avoids JSON encoding/decoding overhead on the crossing.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::csharp::CSharpParseOutput;
use crate::delphi::DelphiParseOutput;
use crate::go::GoParseOutput;
use crate::java::JavaParseOutput;
use crate::js_lang::JsParseOutput;
use crate::php::PhpParseOutput;
use crate::rust_lang::RustParseOutput;
use crate::sql_lang::SqlParseOutput;
use crate::ts::TsParseOutput;
use crate::ParseOutput;

pub fn build_payload(py: Python, out: &ParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("start_byte", f.start_byte)?;
        d.set_item("end_byte", f.end_byte)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // function_types
    let func_types = PyList::empty(py);
    for ft in &out.function_types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &ft.symbol_id)?;
        d.set_item("type_signature", &ft.type_signature)?;
        d.set_item("file_path", &ft.file_path)?;
        d.set_item("start_line", ft.start_line)?;
        d.set_item("end_line", ft.end_line)?;
        d.set_item("code", &ft.code)?;
        func_types.append(d)?;
    }
    dict.set_item("function_types", func_types)?;

    // fields
    let fields = PyList::empty(py);
    for f in &out.fields {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("type_signature", &f.type_signature)?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("code", &f.code)?;
        fields.append(d)?;
    }
    dict.set_item("fields", fields)?;

    // aliases
    let aliases = PyList::empty(py);
    for a in &out.aliases {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &a.symbol_id)?;
        d.set_item("qualified_name", &a.qualified_name)?;
        d.set_item("name", &a.name)?;
        d.set_item("kind", &a.kind)?;
        d.set_item("target_name", a.target_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &a.file_path)?;
        d.set_item("start_line", a.start_line)?;
        d.set_item("end_line", a.end_line)?;
        d.set_item("code", &a.code)?;
        aliases.append(d)?;
    }
    dict.set_item("aliases", aliases)?;

    // templates
    let templates = PyList::empty(py);
    for t in &out.templates {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("name", &t.name)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        templates.append(d)?;
    }
    dict.set_item("templates", templates)?;

    // scalar + map fields
    let using_ns = PyList::empty(py);
    for ns in &out.using_namespaces {
        using_ns.append(ns)?;
    }
    dict.set_item("using_namespaces", using_ns)?;

    let using_imp = PyDict::new(py);
    for (k, v) in &out.using_imports {
        using_imp.set_item(k, v)?;
    }
    dict.set_item("using_imports", using_imp)?;

    let includes = PyList::empty(py);
    for inc in &out.includes {
        includes.append(inc)?;
    }
    dict.set_item("includes", includes)?;

    let macros = PyDict::new(py);
    for (k, v) in &out.macros {
        macros.set_item(k, v)?;
    }
    dict.set_item("macros", macros)?;

    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `GoParseOutput`.
///
/// Go's schema differs from C++:
/// - `using_imports` is a `Vec<String>` (list), not a dict
/// - `macros` is always an empty list
/// - `file_def` carries `includes`, `using_namespaces`, `using_imports`, `macros`, `parse_meta`
pub fn build_go_payload(py: Python, out: &GoParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def — Go's FileDef includes the parse_meta and container fields
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    let includes = PyList::new(py, out.includes.iter());
    file_def.set_item("includes", includes)?;
    let using_ns = PyList::new(py, out.using_namespaces.iter());
    file_def.set_item("using_namespaces", using_ns)?;
    let using_imp = PyList::new(py, out.using_imports.iter());
    file_def.set_item("using_imports", using_imp)?;
    let macros = PyList::empty(py);
    file_def.set_item("macros", macros)?;
    let parse_meta_fd = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta_fd.set_item(k, val)?;
        }
    }
    file_def.set_item("parse_meta", parse_meta_fd)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("start_byte", f.start_byte)?;
        d.set_item("end_byte", f.end_byte)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // function_types (always empty for Go)
    dict.set_item("function_types", PyList::empty(py))?;

    // fields
    let fields = PyList::empty(py);
    for f in &out.fields {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("type_signature", &f.type_signature)?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("code", &f.code)?;
        fields.append(d)?;
    }
    dict.set_item("fields", fields)?;

    // aliases
    let aliases = PyList::empty(py);
    for a in &out.aliases {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &a.symbol_id)?;
        d.set_item("qualified_name", &a.qualified_name)?;
        d.set_item("name", &a.name)?;
        d.set_item("kind", &a.kind)?;
        d.set_item("target_name", a.target_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &a.file_path)?;
        d.set_item("start_line", a.start_line)?;
        d.set_item("end_line", a.end_line)?;
        d.set_item("code", &a.code)?;
        aliases.append(d)?;
    }
    dict.set_item("aliases", aliases)?;

    // templates
    let templates = PyList::empty(py);
    for t in &out.templates {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("name", &t.name)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        templates.append(d)?;
    }
    dict.set_item("templates", templates)?;

    // scalar list fields
    let using_ns = PyList::new(py, out.using_namespaces.iter());
    dict.set_item("using_namespaces", using_ns)?;
    let using_imp = PyList::new(py, out.using_imports.iter());
    dict.set_item("using_imports", using_imp)?;
    let includes = PyList::new(py, out.includes.iter());
    dict.set_item("includes", includes)?;
    dict.set_item("macros", PyList::empty(py))?;

    // parse_meta (top-level)
    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `RustParseOutput`.
///
/// Rust's schema (like Go) uses list-typed `using_imports` / `macros` /
/// `includes` rather than the C++ HashMap types. Unlike Go, Rust actually
/// populates `macros` (macro_invocations) and `using_namespaces`.
pub fn build_rust_payload(py: Python, out: &RustParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    let includes = PyList::new(py, out.includes.iter());
    file_def.set_item("includes", includes)?;
    let using_ns = PyList::new(py, out.using_namespaces.iter());
    file_def.set_item("using_namespaces", using_ns)?;
    let using_imp = PyList::new(py, out.using_imports.iter());
    file_def.set_item("using_imports", using_imp)?;
    let macros_fd = PyList::new(py, out.macros.iter());
    file_def.set_item("macros", macros_fd)?;
    let parse_meta_fd = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta_fd.set_item(k, val)?;
        }
    }
    file_def.set_item("parse_meta", parse_meta_fd)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("start_byte", f.start_byte)?;
        d.set_item("end_byte", f.end_byte)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // function_types (always empty for Rust)
    dict.set_item("function_types", PyList::empty(py))?;

    // fields
    let fields = PyList::empty(py);
    for f in &out.fields {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("type_signature", &f.type_signature)?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("code", &f.code)?;
        fields.append(d)?;
    }
    dict.set_item("fields", fields)?;

    // aliases
    let aliases = PyList::empty(py);
    for a in &out.aliases {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &a.symbol_id)?;
        d.set_item("qualified_name", &a.qualified_name)?;
        d.set_item("name", &a.name)?;
        d.set_item("kind", &a.kind)?;
        d.set_item("target_name", a.target_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &a.file_path)?;
        d.set_item("start_line", a.start_line)?;
        d.set_item("end_line", a.end_line)?;
        d.set_item("code", &a.code)?;
        aliases.append(d)?;
    }
    dict.set_item("aliases", aliases)?;

    // templates
    let templates = PyList::empty(py);
    for t in &out.templates {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("name", &t.name)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        templates.append(d)?;
    }
    dict.set_item("templates", templates)?;

    // scalar list fields
    let using_ns = PyList::new(py, out.using_namespaces.iter());
    dict.set_item("using_namespaces", using_ns)?;
    let using_imp = PyList::new(py, out.using_imports.iter());
    dict.set_item("using_imports", using_imp)?;
    let includes = PyList::new(py, out.includes.iter());
    dict.set_item("includes", includes)?;
    let macros = PyList::new(py, out.macros.iter());
    dict.set_item("macros", macros)?;

    // parse_meta (top-level)
    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `JsParseOutput`.
///
/// JavaScript is Family B: 7-tuple, `FunctionDef` has `exported: bool`,
/// `CallEdge` has `call_arity`, `FileDef` carries `imports`, `exports`,
/// `jsx_tags`, `jsx_components`.
pub fn build_js_payload(py: Python, out: &JsParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    let imports = PyList::new(py, out.file_def.imports.iter());
    file_def.set_item("imports", imports)?;
    let exports = PyList::new(py, out.file_def.exports.iter());
    file_def.set_item("exports", exports)?;
    let jsx_tags = PyList::new(py, out.file_def.jsx_tags.iter());
    file_def.set_item("jsx_tags", jsx_tags)?;
    let jsx_components = PyList::new(py, out.file_def.jsx_components.iter());
    file_def.set_item("jsx_components", jsx_components)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        d.set_item("exported", f.exported)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // parse_meta
    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

fn pythonize_json(py: Python, v: &serde_json::Value) -> PyResult<PyObject> {
    match v {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok(b.into_py(py)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_py(py))
            } else if let Some(u) = n.as_u64() {
                Ok(u.into_py(py))
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_py(py))
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_py(py)),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(pythonize_json(py, item)?)?;
            }
            Ok(list.into())
        }
        serde_json::Value::Object(obj) => {
            let d = PyDict::new(py);
            for (k, v) in obj {
                d.set_item(k, pythonize_json(py, v)?)?;
            }
            Ok(d.into())
        }
    }
}

/// Build a Python `dict` payload from a `JavaParseOutput`.
///
/// Java's schema (Family A) is the 9-tuple:
/// `(functions, calls, classes, type_edges, function_types, relations,
///    file_def, package_def, parse_meta)`
pub fn build_java_payload(py: Python, out: &JavaParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item(
        "package_name",
        out.file_def.package_name.as_ref().unwrap_or(&String::new()),
    )?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    dict.set_item("file_def", file_def)?;

    // package_def
    let pkg = PyDict::new(py);
    pkg.set_item("name", &out.package_def.name)?;
    pkg.set_item("start_line", out.package_def.start_line)?;
    pkg.set_item("end_line", out.package_def.end_line)?;
    pkg.set_item("code", &out.package_def.code)?;
    pkg.set_item("comment", &out.package_def.comment)?;
    pkg.set_item("summary", &out.package_def.summary)?;
    pkg.set_item("note", &out.package_def.note)?;
    dict.set_item("package_def", pkg)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("class_name", f.class_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("package_name", f.package_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        d.set_item("visibility", &f.visibility)?;
        d.set_item("is_public_api", f.is_public_api)?;
        d.set_item("visibility_source", &f.visibility_source)?;
        d.set_item("export_evidence", &f.export_evidence)?;
        d.set_item("signature", &f.signature)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // classes
    let classes = PyList::empty(py);
    for c in &out.classes {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &c.symbol_id)?;
        d.set_item("qualified_name", &c.qualified_name)?;
        d.set_item("name", &c.name)?;
        d.set_item("kind", &c.kind)?;
        d.set_item("package_name", c.package_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &c.file_path)?;
        d.set_item("start_line", c.start_line)?;
        d.set_item("end_line", c.end_line)?;
        d.set_item("code", &c.code)?;
        d.set_item("comment", &c.comment)?;
        d.set_item("summary", &c.summary)?;
        d.set_item("note", &c.note)?;
        d.set_item("visibility", &c.visibility)?;
        d.set_item("is_public_api", c.is_public_api)?;
        d.set_item("visibility_source", &c.visibility_source)?;
        d.set_item("export_evidence", &c.export_evidence)?;
        d.set_item("signature", &c.signature)?;
        classes.append(d)?;
    }
    dict.set_item("classes", classes)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_package", c.caller_package.as_ref().unwrap_or(&String::new()))?;
        d.set_item("caller_class", c.caller_class.as_ref().unwrap_or(&String::new()))?;
        let imports = PyList::new(py, c.imports.iter());
        d.set_item("imports", imports)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // type_edges
    let type_edges = PyList::empty(py);
    for t in &out.type_edges {
        let d = PyDict::new(py);
        d.set_item("source_id", &t.source_id)?;
        d.set_item(
            "source_package",
            t.source_package.as_ref().unwrap_or(&String::new()),
        )?;
        d.set_item("target_name", &t.target_name)?;
        d.set_item("rel_type", &t.rel_type)?;
        d.set_item("target_id", t.target_id.as_ref().unwrap_or(&String::new()))?;
        type_edges.append(d)?;
    }
    dict.set_item("type_edges", type_edges)?;

    // function_types
    let func_types = PyList::empty(py);
    for ft in &out.function_types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &ft.symbol_id)?;
        d.set_item("type_signature", &ft.type_signature)?;
        d.set_item("file_path", &ft.file_path)?;
        d.set_item("start_line", ft.start_line)?;
        d.set_item("end_line", ft.end_line)?;
        d.set_item("code", &ft.code)?;
        func_types.append(d)?;
    }
    dict.set_item("function_types", func_types)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // imports (top-level list)
    let imports = PyList::new(py, out.imports.iter());
    dict.set_item("imports", imports)?;

    // parse_meta
    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `CSharpParseOutput`.
///
/// C# is Family B: 7-tuple, simplest `FunctionDef` (no `exported`, no visibility).
pub fn build_csharp_payload(py: Python, out: &CSharpParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // parse_meta
    let parse_meta = PyDict::new(py);
    let meta_json = out.parse_meta.to_json();
    if let Some(obj) = meta_json.as_object() {
        for (k, v) in obj {
            let val = pythonize_json(py, v)?;
            parse_meta.set_item(k, val)?;
        }
    }
    dict.set_item("parse_meta", parse_meta)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `PhpParseOutput`.
///
/// PHP is Family B with 6-tuple (no `parse_meta` at top level in payload).
pub fn build_php_payload(py: Python, out: &PhpParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    let imports = PyList::new(py, out.file_def.imports.iter());
    file_def.set_item("imports", imports)?;
    let exports = PyList::new(py, out.file_def.exports.iter());
    file_def.set_item("exports", exports)?;
    let jsx_tags = PyList::new(py, out.file_def.jsx_tags.iter());
    file_def.set_item("jsx_tags", jsx_tags)?;
    let jsx_components = PyList::new(py, out.file_def.jsx_components.iter());
    file_def.set_item("jsx_components", jsx_components)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        d.set_item("exported", f.exported)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("call_column", c.call_column)?;
        d.set_item("call_start_byte", c.call_start_byte)?;
        d.set_item("call_branch_kind", &c.call_branch_kind)?;
        d.set_item("call_loop_depth", c.call_loop_depth)?;
        d.set_item("call_control_frames_json", &c.call_control_frames_json)?;
        d.set_item("call_type", &c.call_type)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // No parse_meta at top level for PHP (it's Family B 6-tuple).

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `TsParseOutput` (TypeScript/TSX Tier 2).
///
/// TS has a **12-tuple** payload schema — completely different from C++/Go:
/// functions, calls, types, namespaces, relations, renders, navigates,
/// file_def, parse_meta, api_calls, navigators, param_lists.
pub fn build_ts_payload(py: Python, out: &TsParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        d.set_item("exported", f.exported)?;
        d.set_item("return_type", &f.return_type)?;
        let pt = PyList::new(py, f.param_types.iter());
        d.set_item("param_types", pt)?;
        d.set_item("react_role", &f.react_role)?;
        d.set_item("middleware_kind", &f.middleware_kind)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        d.set_item("callee_arity", c.callee_arity.unwrap_or(0))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        d.set_item("exported", t.exported)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // renders
    let renders = PyList::empty(py);
    for r in &out.renders {
        let d = PyDict::new(py);
        d.set_item("renderer_id", &r.renderer_id)?;
        d.set_item("rendered_name", &r.rendered_name)?;
        d.set_item("rendered_id", r.rendered_id.as_ref().unwrap_or(&String::new()))?;
        renders.append(d)?;
    }
    dict.set_item("renders", renders)?;

    // navigates
    let navigates = PyList::empty(py);
    for n in &out.navigates {
        let d = PyDict::new(py);
        d.set_item("source_id", &n.source_id)?;
        d.set_item("target_name", &n.target_name)?;
        d.set_item("nav_method", &n.nav_method)?;
        d.set_item("target_id", n.target_id.as_ref().unwrap_or(&String::new()))?;
        d.set_item("via", &n.via)?;
        d.set_item("trigger_type", &n.trigger_type)?;
        d.set_item("guard", n.guard.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_depth", n.call_depth)?;
        let trace = PyList::new(py, n.source_trace.iter());
        d.set_item("source_trace", trace)?;
        d.set_item("confidence", n.confidence)?;
        navigates.append(d)?;
    }
    dict.set_item("navigates", navigates)?;

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    let imports = PyList::new(py, out.file_def.imports.iter());
    file_def.set_item("imports", imports)?;
    let exports = PyList::new(py, out.file_def.exports.iter());
    file_def.set_item("exports", exports)?;
    let jsx_tags = PyList::new(py, out.file_def.jsx_tags.iter());
    file_def.set_item("jsx_tags", jsx_tags)?;
    let jsx_comp = PyList::new(py, out.file_def.jsx_components.iter());
    file_def.set_item("jsx_components", jsx_comp)?;
    dict.set_item("file_def", file_def)?;

    // parse_meta
    let meta = PyDict::new(py);
    meta.set_item("parser_language", "typescript_tree_sitter")?;
    meta.set_item("parser_available", true)?;
    meta.set_item("has_error", out.has_error)?;
    meta.set_item("error_nodes", out.error_nodes)?;
    dict.set_item("parse_meta", meta)?;

    // api_calls
    let api_calls = PyList::empty(py);
    for a in &out.api_calls {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &a.symbol_id)?;
        d.set_item("caller_function_id", &a.caller_function_id)?;
        d.set_item("url_pattern", &a.url_pattern)?;
        d.set_item("raw_url", &a.raw_url)?;
        d.set_item("http_method", &a.http_method)?;
        d.set_item("base_url_ref", &a.base_url_ref)?;
        d.set_item("file_path", &a.file_path)?;
        d.set_item("start_line", a.start_line)?;
        d.set_item("confidence", a.confidence)?;
        api_calls.append(d)?;
    }
    dict.set_item("api_calls", api_calls)?;

    // navigators
    let navigators = PyList::empty(py);
    for n in &out.navigators {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("var_name", &n.var_name)?;
        d.set_item("factory", &n.factory)?;
        d.set_item("nav_type", &n.nav_type)?;
        d.set_item("param_list_ref", &n.param_list_ref)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        let routes = PyList::empty(py);
        for (name, comp) in &n.routes {
            let pair = PyList::new(py, [name, comp].into_iter());
            routes.append(pair)?;
        }
        d.set_item("routes", routes)?;
        navigators.append(d)?;
    }
    dict.set_item("navigators", navigators)?;

    // param_lists
    let param_lists = PyList::empty(py);
    for p in &out.param_lists {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &p.symbol_id)?;
        d.set_item("name", &p.name)?;
        d.set_item("file_path", &p.file_path)?;
        let routes = PyDict::new(py);
        for (k, v) in &p.routes {
            routes.set_item(k, v)?;
        }
        d.set_item("routes", routes)?;
        param_lists.append(d)?;
    }
    dict.set_item("param_lists", param_lists)?;

    Ok(dict.into())
}

/// Build a Python `dict` payload from a `DelphiParseOutput` (Tier 3).
///
/// Delphi is Family B 9-tuple: `functions, calls, types, namespaces, fields,
/// relations, file_def, uses_units, parse_meta`. Note that `uses_units` lives
/// at the top level (not nested inside `file_def`).
pub fn build_delphi_payload(py: Python, out: &DelphiParseOutput) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", &out.file_def.file_path)?;
    file_def.set_item("start_line", out.file_def.start_line)?;
    file_def.set_item("end_line", out.file_def.end_line)?;
    file_def.set_item("code", &out.file_def.code)?;
    file_def.set_item("comment", &out.file_def.comment)?;
    file_def.set_item("summary", &out.file_def.summary)?;
    file_def.set_item("note", &out.file_def.note)?;
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::empty(py);
    for f in &out.functions {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("kind", &f.kind)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("start_byte", f.start_byte)?;
        d.set_item("end_byte", f.end_byte)?;
        d.set_item("arity", f.arity)?;
        d.set_item("code", &f.code)?;
        d.set_item("comment", &f.comment)?;
        d.set_item("summary", &f.summary)?;
        d.set_item("note", &f.note)?;
        funcs.append(d)?;
    }
    dict.set_item("functions", funcs)?;

    // calls
    let calls = PyList::empty(py);
    for c in &out.calls {
        let d = PyDict::new(py);
        d.set_item("caller_id", &c.caller_id)?;
        d.set_item("caller_file", &c.caller_file)?;
        d.set_item("caller_scope", c.caller_scope.as_ref().unwrap_or(&String::new()))?;
        d.set_item("call_line", c.call_line)?;
        d.set_item("callee_raw", &c.callee_name)?;
        d.set_item("callee_name", &c.callee_name)?;
        d.set_item("call_arity", c.call_arity)?;
        d.set_item("callee_id", c.callee_id.as_ref().unwrap_or(&String::new()))?;
        calls.append(d)?;
    }
    dict.set_item("calls", calls)?;

    // types
    let types = PyList::empty(py);
    for t in &out.types {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &t.symbol_id)?;
        d.set_item("qualified_name", &t.qualified_name)?;
        d.set_item("name", &t.name)?;
        d.set_item("kind", &t.kind)?;
        d.set_item("file_path", &t.file_path)?;
        d.set_item("start_line", t.start_line)?;
        d.set_item("end_line", t.end_line)?;
        d.set_item("code", &t.code)?;
        d.set_item("comment", &t.comment)?;
        d.set_item("summary", &t.summary)?;
        d.set_item("note", &t.note)?;
        types.append(d)?;
    }
    dict.set_item("types", types)?;

    // namespaces
    let namespaces = PyList::empty(py);
    for n in &out.namespaces {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &n.symbol_id)?;
        d.set_item("qualified_name", &n.qualified_name)?;
        d.set_item("name", &n.name)?;
        d.set_item("file_path", &n.file_path)?;
        d.set_item("start_line", n.start_line)?;
        d.set_item("end_line", n.end_line)?;
        d.set_item("code", &n.code)?;
        d.set_item("comment", &n.comment)?;
        d.set_item("summary", &n.summary)?;
        d.set_item("note", &n.note)?;
        namespaces.append(d)?;
    }
    dict.set_item("namespaces", namespaces)?;

    // fields
    let fields = PyList::empty(py);
    for f in &out.fields {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id)?;
        d.set_item("qualified_name", &f.qualified_name)?;
        d.set_item("name", &f.name)?;
        d.set_item("scope_name", f.scope_name.as_ref().unwrap_or(&String::new()))?;
        d.set_item("type_signature", &f.type_signature)?;
        d.set_item("file_path", &f.file_path)?;
        d.set_item("start_line", f.start_line)?;
        d.set_item("end_line", f.end_line)?;
        d.set_item("code", &f.code)?;
        fields.append(d)?;
    }
    dict.set_item("fields", fields)?;

    // relations
    let relations = PyList::empty(py);
    for r in &out.relations {
        let d = PyDict::new(py);
        d.set_item("source_id", &r.source_id)?;
        d.set_item("source_label", &r.source_label)?;
        d.set_item("target_id", &r.target_id)?;
        d.set_item("target_label", &r.target_label)?;
        d.set_item("rel_type", &r.rel_type)?;
        let props = PyDict::new(py);
        for (k, v) in &r.properties {
            props.set_item(k, v)?;
        }
        d.set_item("properties", props)?;
        relations.append(d)?;
    }
    dict.set_item("relations", relations)?;

    // uses_units (top-level, not in file_def)
    let uses = PyList::new(py, out.uses_units.iter());
    dict.set_item("uses_units", uses)?;

    // parse_meta
    let pm = PyDict::new(py);
    pm.set_item("parser_language", &out.parse_meta.parser_language)?;
    pm.set_item("parser_available", out.parse_meta.parser_language != "regex_fallback")?;
    pm.set_item("has_error", out.parse_meta.has_error)?;
    pm.set_item("error_nodes", out.parse_meta.error_nodes)?;
    pm.set_item("range_guided_by_tree", false)?;
    pm.set_item("interface_ranges", PyList::empty(py))?;
    pm.set_item("implementation_ranges", PyList::empty(py))?;
    dict.set_item("parse_meta", pm)?;

    Ok(dict.into())
}
