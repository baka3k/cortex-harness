//! Build Python `dict` payload from a `ParseOutput`.
//!
//! Mirrors the JSON shape produced by `asdict(...)` on the Python dataclasses.
//! Returning PyDict avoids JSON encoding/decoding overhead on the crossing.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::go::GoParseOutput;
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
