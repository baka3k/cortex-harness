# Phase 1: Rust Crate Skeleton + cplus Pilot

## Goal

Build the minimal Rust native extension (`cortex_extract`) that can parse ONE C++ file and return the exact same payload dict as `parse_c_family_file()`. This proves the architecture end-to-end before committing to batch/multithread.

## Steps

### 1.1 — Cargo workspace setup

Create `rust-analyzer-core/` at repo root:

```
rust-analyzer-core/
├── Cargo.toml
├── pyproject.toml          (maturin config)
├── src/
│   ├── lib.rs              (PyO3 module init)
│   ├── parser.rs           (tree-sitter Parser factory)
│   ├── walker.rs           (iterative AST DFS — port of _walk_tree)
│   ├── symbols/
│   │   ├── mod.rs
│   │   ├── function.rs     (extract_function — port of _walk_tree function_definition branch)
│   │   ├── type_def.rs     (extract_type — class_specifier/struct_specifier/enum_specifier/union_specifier)
│   │   ├── namespace.rs    (extract_namespace)
│   │   ├── field.rs        (extract_field)
│   │   ├── alias.rs        (extract_alias — type_alias/alias_declaration)
│   │   └── template.rs     (extract_template)
│   ├── calls.rs            (extract_call — call_expression/method_call_expression)
│   ├── relations.rs        (EXTENDS, DECLARES, USES_TYPE, etc.)
│   ├── text.rs             (node_text, extract_comment, normalize_ws — zero-copy &str)
│   └── payload.rs          (build PyDict from Rust structs)
└── tests/
    └── fixtures/           (small .cpp/.h files + expected JSON payloads)
```

**Cargo.toml dependencies:**
```toml
[package]
name = "cortex-extract"
version = "0.1.0"
edition = "2021"

[lib]
name = "cortex_extract"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
tree-sitter = "0.22"
tree-sitter-cpp = "0.22"
tree-sitter-c = "0.22"
rayon = "1.10"           # Phase 2 (not used yet)

[build-dependencies]
maturin = "1.5"          # optional: for wheel building
```

### 1.2 — Port `_walk_tree` to Rust

The Python `_walk_tree` (cplus_analyzer.py:1114) is an **iterative DFS** using `collections.deque`. Port directly:

```rust
// walker.rs
use std::collections::VecDeque;

pub struct WalkContext<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    // output collections:
    pub functions: Vec<FunctionDef>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub calls: Vec<CallEdge>,
    pub relations: Vec<RelationEdge>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    // per-branch state:
    // namespace_stack, type_stack, using_namespaces, using_imports
    // — carried in work frames
}

pub fn walk_tree(root: tree_sitter::Node, source: &[u8], rel_path: &str) -> WalkContext {
    let mut ctx = WalkContext::new(source, rel_path);
    let mut work = VecDeque::new();
    work.push_back(WalkFrame::new(root, vec![], vec![]));

    while let Some(frame) = work.pop_front() {
        let node = frame.node;
        match node.kind() {
            "function_definition" | "function_declaration" => {
                ctx.extract_function(&node, &frame);
            }
            "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier" => {
                ctx.extract_type(&node, &frame);
            }
            "namespace_definition" => {
                ctx.extract_namespace(&node, &frame);
            }
            "call_expression" | "method_call_expression" => {
                ctx.extract_call(&node);
            }
            // ... all node types from _walk_tree ...
            _ => {} // push children
        }
        // push children to work queue
        for child in node.children(&mut node.walk()) {
            work.push_back(frame.for_child(child));
        }
    }
    ctx
}
```

**Key difference from Python:** `node_text()` returns `&str` slicing into `source` bytes — zero copy, zero allocation. Python does `source_bytes[start:end].decode("utf-8")` per call.

### 1.3 — Port symbol extractors

Port each `_walk_tree` branch into a dedicated function. Reference the Python implementation line-by-line:

| Python (cplus_analyzer.py) | Rust module | Key logic |
|---|---|---|
| `function_definition` (line ~1180) | `symbols/function.rs` | declarator → name, scope, arity; params extraction |
| `class_specifier` (line ~1300) | `symbols/type_def.rs` | name, kind (class/struct/union/enum); base classes → EXTENDS |
| `namespace_definition` (line ~1400) | `symbols/namespace.rs` | qualified_name from stack |
| `field_declaration` (line ~1450) | `symbols/field.rs` | declarator iteration, type_signature |
| `alias_declaration` (line ~1500) | `symbols/alias.rs` | typedef, target_name |
| `template_declaration` (line ~1160) | `symbols/template.rs` | template params |
| `call_expression` (line ~644) | `calls.rs` | callee name, arity, control context |

### 1.4 — Build PyDict payload (`payload.rs`)

```rust
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

pub fn build_payload(py: Python, ctx: WalkContext) -> PyResult<PyObject> {
    let dict = PyDict::new(py);

    // file_def
    let file_def = PyDict::new(py);
    file_def.set_item("file_path", ctx.file_path)?;
    file_def.set_item("start_line", ctx.start_line)?;
    // ... all FileDef fields ...
    dict.set_item("file_def", file_def)?;

    // functions
    let funcs = PyList::new(py, ctx.functions.iter().map(|f| {
        let d = PyDict::new(py);
        d.set_item("symbol_id", &f.symbol_id).unwrap();
        d.set_item("name", &f.name).unwrap();
        // ... all FunctionDef fields ...
        d.into()
    }));
    dict.set_item("functions", funcs)?;

    // ... calls, types, namespaces, relations, fields, aliases, templates ...

    Ok(dict.into())
}
```

### 1.5 — PyO3 module registration (`lib.rs`)

```rust
use pyo3::prelude::*;

#[pyfunction]
fn extract_cplus(path: &str, root: &str) -> PyResult<PyObject> {
    let py = Python::acquire_gil();  // GIL held only during PyDict build
    let py = py.python();

    let source = std::fs::read(path)?;
    let rel_path = path.strip_prefix(root).unwrap_or(path);
    let is_cpp = is_cpp_file(path);
    let parser = get_parser(is_cpp);  // thread_local singleton
    let tree = parser.parse(&source, None).ok_or(...)?;

    let ctx = walk_tree(tree.root_node(), &source, rel_path);
    let payload = build_payload(py, ctx)?;

    Ok(payload)
}

#[pymodule]
fn cortex_extract(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_cplus, m)?)?;
    Ok(())
}
```

### 1.6 — Fixture-based differential testing

Create test fixtures with expected payloads:

```
tests/fixtures/
├── simple_class.cpp          → simple_class.expected.json
├── template_function.cpp     → template_function.expected.json
├── namespace_nested.cpp      → namespace_nested.expected.json
├── call_chains.cpp           → call_chains.expected.json
├── header_only.h             → header_only.expected.json
└── macro_heavy.h             → macro_heavy.expected.json
```

**Test procedure:**
1. Run Python `parse_c_family_file()` on each fixture → save as `.expected.json`
2. Run Rust `extract_cplus()` → compare dict equality
3. Any diff = bug in Rust port

### 1.7 — Python fallback wrapper

In `cplus_analyzer.py`, add at top:

```python
try:
    import cortex_extract as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
```

In `_load_or_parse_payload()`:
```python
if _RUST_AVAILABLE:
    payload = _rust.extract_cplus(file_path, root)
else:
    # existing Python path
    payload = _python_parse_and_serialize(file_path, root, ...)
```

## Validation

```bash
# Build Rust extension
cd rust-analyzer-core && maturin develop --release

# Run differential tests
python -m pytest tests/test_rust_vs_python_parity.py

# Profile with Rust
python profile_analyzer.py --target /path/to/cpp --language cplus --rust

# Expected: Phase 2b drops from 92.8s to ~15s (single-thread ~40s, but proves concept)
```

## Deliverables

- [ ] `rust-analyzer-core/` Cargo workspace
- [ ] `cortex_extract.extract_cplus()` PyO3 function
- [ ] 6+ fixture files with expected JSON
- [ ] Differential test: Rust payload == Python payload (100% field match)
- [ ] Python fallback path when Rust unavailable
