---
title: "Swift Extraction Layer (Tier 1)"
status: pending
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 2
scope: Port tree-sitter AST walk + extraction for Swift language analyzer to Rust cortex_extract
priority: 7 (Phase 0: 48.2% parse+extract, borderline — defer until ≥500 file corpus)
blockedBy: []
---

# Swift Extraction Layer (Tier 1)

## Overview

Port `swift_analyzer.py`'s `parse_swift_file` → `_walk_tree` → `_resolve_calls` pipeline to the Rust native extension. Swift shares the Go/Rust-lang payload schema shape (list-typed `using_imports`, `macros`, `includes`).

**⚠️ Phase 0 verdict: BORDERLINE** — 48.2% parse+extract on only 10 files. Defer until a real ≥500 file iOS corpus is available to confirm the ratio. If semantic dominates on larger corpora, skip the port.

## Survey Results (from `swift_analyzer.py`)

### Node-type constants

```
_TYPE_NODES = {"class_declaration", "protocol_declaration"}
_FUNCTION_NODES = {
    "function_declaration",
    "protocol_function_declaration",
    "init_declaration",
    "deinit_declaration",
    "subscript_declaration",
}
_ALIAS_NODES = {"typealias_declaration", "associatedtype_declaration"}
_CALL_NODES = {"call_expression", "constructor_expression", "macro_invocation"}
_BRANCH_NODES = {
    "if_statement": "if", "guard_statement": "guard",
    "switch_statement": "switch", "catch_block": "catch", "do_statement": "do",
}
_LOOP_NODES = {"for_statement", "while_statement", "repeat_while_statement", "repeat"}
```

### Unique features vs Go/Rust

- **`protocol_declaration`**: Swift protocols mapped to kind `"protocol"`.
- **`init_declaration` / `deinit_declaration`**: constructors/destructors — kind `"constructor"` / `"destructor"`.
- **`subscript_declaration`**: Swift subscripts — kind `"subscript"`.
- **`constructor_expression` in `_CALL_NODES`**: constructor calls tracked.
- **`property_declaration` / `protocol_property_declaration`**: fields extracted outside active_function scope.
- **`guard_statement`**: Swift-specific branch type.
- **`associatedtype_declaration`**: protocol associated types.
- **Extensions (`extension_declaration`)**: Swift extends existing types — may need handling.

### Schema (identical to Go/Rust-lang)

Same list-typed `using_imports`/`macros`/`includes` shape.

## Phases

### Phase 1 — Grammar + walker

**Deliverables:**
1. Add `tree-sitter-swift = "0.23"` (check ABI compatibility) to `Cargo.toml`
2. Add `SwiftGrammar` in `grammar.rs`
3. Write `swift_lang.rs`:
   - `SwiftParseOutput` struct
   - `parse_swift_source(source, rel_path)`
   - Full walker with all Swift node-type handlers
4. Add `build_swift_payload` in `payload.rs`
5. Wire `lib.rs`: `extract_swift`, `extract_swift_batch`, routing

**⚠️ tree-sitter-swift crate version:** `0.7.3` is latest — check if it's compatible with `tree-sitter = "0.23"` ABI. May need `tree-sitter-swift = "0.6"` or fork.

### Phase 2 — Differential testing

**Deliverables:**
1. Create Swift fixtures in `tests/fixtures/swift-app/`
2. Write `tests/test_swift_differential.py`
3. Verify all core fields match

## Validation Criteria

- [ ] Phase 0 re-confirmed with ≥500 file corpus (defer if not available)
- [ ] `cargo test --release swift_lang::` passes
- [ ] Differential test passes
- [ ] `tree-sitter-swift` crate ABI-compatible with `tree-sitter = "0.23"`

## Files to Create/Modify

| File | Action |
|------|--------|
| `rust-analyzer-core/Cargo.toml` | Add `tree-sitter-swift` |
| `rust-analyzer-core/src/grammar.rs` | Add `SwiftGrammar` |
| `rust-analyzer-core/src/swift_lang.rs` | **NEW**: walker + `SwiftParseOutput` |
| `rust-analyzer-core/src/payload.rs` | Add `build_swift_payload` |
| `rust-analyzer-core/src/lib.rs` | Add `extract_swift`, routing |
| `tests/fixtures/swift-app/` | Fixture `.swift` files |
| `tests/test_swift_differential.py` | Differential test |

## Estimated Effort

**Medium** — Swift has more node types than Go/Rust (init, deinit, subscript, guard, property). Walker ~600 lines. tree-sitter-swift ABI compatibility is the main risk.
