---
title: "Rust Extraction Layer (Tier 1)"
status: pending
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 2
scope: Port tree-sitter AST walk + extraction for Rust language analyzer to Rust cortex_extract
priority: 8 (Phase 0: 65.1% parse+extract, but only 17 files in corpus — low absolute ROI)
blockedBy: []
---

# Rust Extraction Layer (Tier 1)

## Overview

Port `rust_analyzer.py`'s `parse_rust_file` → `_walk_tree` → `_resolve_calls` pipeline to the Rust native extension (`rust-analyzer-core/src/rust_lang.rs`). Rust (the language) shares the same payload schema shape as Go: list-typed `using_imports`, `macros`, `includes`.

**Pattern:** Follow the exact Go port pattern (`go.rs` + `build_go_payload`). The walker is self-contained per language — the shared value is the data-model structs and the DFS pattern, not a shared walker.

## Survey Results (from `rust_analyzer.py`)

### Node-type constants

```
_TYPE_NODES = {
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "trait_item": "interface",
}
_FUNCTION_NODES = {"function_item", "function_signature_item"}
_MODULE_NODES = {"mod_item"}
_IMPL_NODES = {"impl_item"}
_ALIAS_NODES = {"type_item"}
_CALL_NODES = {"call_expression", "method_call_expression", "macro_invocation"}
```

### Schema (identical shape to Go)

```python
{
    "functions": [...],     # FunctionDef
    "calls": [...],         # CallEdge
    "types": [...],         # TypeDef
    "namespaces": [...],    # NamespaceDef
    "relations": [...],     # RelationEdge
    "function_types": [],   # always empty
    "fields": [...],        # FieldDef
    "aliases": [...],       # AliasDef
    "templates": [...],     # TemplateDef
    "file_def": {...},      # FileDef with includes/using_namespaces/using_imports/macros
    "using_namespaces": [...],  # list
    "using_imports": [...],     # list (NOT dict like C++)
    "includes": [...],          # list
    "macros": [...],            # list (NOT dict like C++)
    "parse_meta": {...},
}
```

### Unique features vs Go

- **`_MODULE_NODES` (`mod_item`)**: Rust modules create namespace entries. Go uses `package_scope`.
- **`_IMPL_NODES` (`impl_item`)**: impl blocks scope methods to a type — Rust-specific.
- **`trait_item`**: mapped to kind `"interface"`.
- **`macro_invocation`**: in `_CALL_NODES` — macro calls are tracked.
- **Generic type parameters**: Rust generics use `type_parameter` / `generic_type` nodes.
- **`use_declaration`**: imports use `use foo::bar` syntax → `_collect_imports` + `_collect_includes`.

## Phases

### Phase 1 — Grammar + walker skeleton

**Deliverables:**
1. Add `tree-sitter-rust = "0.23"` to `Cargo.toml`
2. Add `RustGrammar` in `grammar.rs` (matches `.rs` files)
3. Write `rust_lang.rs` (avoid name clash with crate name `rust-analyzer-core`):
   - `RustParseOutput` struct (same schema as `GoParseOutput`)
   - `parse_rust_source(source, rel_path)` entry point
   - Full walker porting all node-type handlers
4. Add `build_rust_payload` in `payload.rs` (reuse Go payload builder pattern)
5. Wire `lib.rs`: `extract_rust`, `extract_rust_batch`, `extract_batch` routing for `"rust"`

**Validation:** Rust unit tests on structs, enums, traits, impls, functions, macros, calls.

### Phase 2 — Differential testing

**Deliverables:**
1. Create Rust fixture files in `tests/fixtures/rust-app/`
2. Write `tests/test_rust_differential.py` comparing Rust `extract_rust` vs Python `parse_rust_file`
3. Verify: functions, calls, fields, types, namespaces, imports match

**Validation:** ✅ MATCH on all core fields.

## Validation Criteria

- [ ] Phase 1: `cargo test --release rust_lang::` passes (≥6 tests)
- [ ] Phase 2: differential test passes (Rust output == Python on fixtures)
- [ ] Grammar tests pass (`parse_root_kind("rust", ...)` returns `"source_file"`)
- [ ] `extract_batch(paths, root, "rust", threads)` routes to Rust pipeline

## Files to Create/Modify

| File | Action |
|------|--------|
| `rust-analyzer-core/Cargo.toml` | Add `tree-sitter-rust = "0.23"` |
| `rust-analyzer-core/src/grammar.rs` | Add `RustGrammar` |
| `rust-analyzer-core/src/rust_lang.rs` | **NEW**: full walker + `RustParseOutput` |
| `rust-analyzer-core/src/payload.rs` | Add `build_rust_payload` |
| `rust-analyzer-core/src/lib.rs` | Add `extract_rust`, `extract_rust_batch`, routing |
| `tests/fixtures/rust-app/` | Fixture `.rs` files |
| `tests/test_rust_differential.py` | Differential test |

## Estimated Effort

**Low** — follows the Go port pattern exactly. The walker is ~500 lines, payload builder reuses the Go pattern. ~1-2 hours.
