---
title: "Tier 2 Extraction Layer — Java, JS, C#, PHP"
status: pending
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 3
scope: Port tree-sitter AST walk + extraction for 4 schema-divergent language analyzers (SQL moved to Tier 3 — pure regex)
priority: 3-9 (varies per language — see Phase 0 matrix)
blockedBy: []
relatedPlans: [260731-1900-rust-extraction-tier2-ts, 260731-2100-rust-extraction-tier3-delphi-plsql]
---

# Tier 2 Extraction Layer — Java, JS, C#, PHP

## Overview

Port 4 language analyzers to the Rust native extension. Each has a schema-divergent payload but follows the same per-language walker pattern. SQL was originally in this group but research revealed its tree-sitter code is dead — it's pure regex and has been moved to the Tier 3 plan.

**⚠️ SQL moved to Tier 3** — `sql_analyzer.py`'s tree-sitter code is unreachable dead code. `parse_sql_file` uses pure regex. See plan `260731-2100-rust-extraction-tier3-delphi-plsql`.

| Language | parse+extract% | Priority | Family | Effort |
|----------|:--------------|:---------|:-------|:-------|
| Java | 60.4% | 6 | A (Package/Class) | Medium |
| JS | 62.6% | 9 | B (Namespace/Type) | Low-Medium |
| C# | 53.6% | 5 | B | Medium |
| PHP | 89.0% | 9 | B | Low-Medium |

**Skip:** Python (22%), Kotlin (24%) — semantic-dominated. SQL — moved to Tier 3 (pure regex).

## Per-Language Survey

**⚠️ ARCHITECTURAL INSIGHT:** These 5 languages split into **two families**:

- **Family A (Java):** Package/Class model — 9-tuple with `ClassDef`, `TypeEdge`, `PackageDef`, `FunctionTypeDef`, visibility fields. Richer than C++ in semantic fields.
- **Family B (JS, C#, PHP, SQL):** Namespace/Type model — 6-7 tuple with generic `TypeDef`, `callee_arity` on calls, `exported` flag. Simpler than C++.

**Note:** SQL is moved to the Tier 3 plan (`260731-2100`) after research revealed it's pure-regex (tree-sitter code is dead). Kotlin is skipped per Phase 0.

### Java (`java_analyzer.py` — 2678 lines) — Family A

**Returns 9-tuple:**
`(functions, calls, classes, type_edges, function_types, relations, file_def, package_def, parse_meta)`

**Dataclasses:** `FunctionDef, PackageDef, ClassDef, TypeEdge, FunctionTypeDef, RelationEdge, CallEdge`

**`FunctionDef` (unique fields vs C++):** `class_name`, `package_name` (not `scope_name`), `visibility`, `is_public_api`, `visibility_source`, `export_evidence`, `signature`

**`CallEdge` (unique):** `caller_id, caller_file, caller_package, caller_class, imports, callee_name, callee_id` — NO `callee_arity`; carries imports list for resolution

**Node types:**
- Types: `class_declaration`, `interface_declaration`, `enum_declaration`, `record_declaration`
- Functions: `method_declaration`, `constructor_declaration`
- Type refs: `type_identifier`, `scoped_type_identifier`, `identifier`, `generic_type`, `annotated_type`, `array_type`
- Calls: `method_invocation`, `object_creation_expression`, `explicit_constructor_invocation`, `method_reference`
- Inheritance: `super_interfaces`, `extends_interfaces`, `superclass`

**Call resolution:** Two-stage — per-file `_resolve_calls` (4 indexes: by_name, by_qualified, by_class_and_name, by_package_and_name) + project-level `resolve_callee_id`.

### JavaScript (`js_analyzer.py` — 2116 lines) — Family B

**Returns 7-tuple:**
`(functions, calls, types, namespaces, relations, file_def, parse_meta)`

**`FunctionDef`:** `scope_name`, `exported: bool` — no visibility/byte-offset fields
**`FileDef`:** `imports`, `exports`, `jsx_tags`, `jsx_components` (JS-only lists)
**`CallEdge`:** `caller_id, caller_scope, callee_name, callee_id, callee_arity`

**Node types (shares TS dispatch sets without React/navigation):**
- Types: `_TYPE_NODE_KINDS`: `class_declaration→class`
- Functions: `_FUNCTION_NODE_KINDS`: `function_declaration→function`, `generator_function_declaration`, `method_definition`
- Variables: `lexical_declaration`, `variable_declaration` (when init is `arrow_function`/`function` → `function_variable`)
- Exports: `export_statement`, `export_default_declaration`
- Calls: `call_expression`, `new_expression`

**Call resolution:** Single-stage — no per-file resolver; `callee_id=None` at parse time, resolved at project level.

### C# (`csharp_analyzer.py` — 1930 lines) — Family B

**Returns 7-tuple:** Same shape as JS.
**`FunctionDef`:** Simplest of all — no `exported`, no visibility, no byte-offsets. Just `scope_name`.
**`CallEdge`:** `caller_id, caller_scope, callee_name, callee_id, callee_arity`

**Node types:**
- Namespaces: `namespace_declaration`, `file_scoped_namespace_declaration` (C# 10)
- Types: `class_declaration`, `struct_declaration`, `interface_declaration`, `enum_declaration`
- Functions: `method_declaration`, `constructor_declaration`, `local_function_statement` (nested functions)
- Calls: `invocation_expression`, `object_creation_expression`
- Root: `compilation_unit`

**Call resolution:** Two-stage — per-file `_resolve_calls` (by_name + by_name_arity) + project-level.

### PHP (`php_analyzer.py` — 1930 lines) — Family B (sibling to JS)

**Returns 6-tuple:** `(functions, calls, types, namespaces, relations, file_def)` — NO parse_meta dict!

**`FunctionDef`:** Same as JS — `scope_name`, `exported: bool`
**`FileDef`:** Same as JS (imports/exports/jsx — mostly stubs)
**`CallEdge`:** Same as JS/C# — `callee_arity`

**Node types:**
- Namespaces: `_NAMESPACE_NODE_TYPES`: `namespace_definition`, `namespace_declaration`
- Types: `_TYPE_NODE_KINDS`: `class_declaration→class`, `interface_declaration→interface`, `trait_declaration→trait`, `enum_declaration→enum`
- Functions: `_FUNCTION_NODE_KINDS`: `function_definition→function`, `method_declaration→method`
- Anonymous: `_ANON_FUNCTION_NODE_TYPES`: `arrow_function`, `anonymous_function`
- Calls (5 kinds — richest in Family B): `function_call_expression`, `method_call_expression`, `scoped_call_expression`, `call_expression`, `object_creation_expression`
- Imports: also `include_expression`, `require_expression` (PHP include/require)

**Unique:** Anonymous functions (arrow + closure), `skip_function_ranges` dedup, `trait` kind, include/require tracking.

**Call resolution:** Single-stage (like JS) — no per-file resolver.

## Implementation Pattern (per language)

Each language follows the **Go port template**:

```
1. Add tree-sitter-<lang> dep to Cargo.toml
2. Add <Lang>Grammar in grammar.rs
3. Write <lang>.rs:
   - <Lang>ParseOutput struct
   - parse_<lang>_source(source, rel_path)
   - Walker with per-language node-type dispatch
4. Add build_<lang>_payload in payload.rs
5. Wire lib.rs: extract_<lang>, extract_<lang>_batch, routing
6. Create fixtures
7. Differential test
```

## Phases (one per language — independent)

### Phase J — Java port

**Deliverables:**
- `tree-sitter-java = "0.23"` (already in Cargo.toml!)
- `JavaGrammar` (already in grammar.rs — only needs walker)
- `java.rs`: walker with `class_declaration`, `interface_declaration`, `method_declaration`, `constructor_declaration`, `object_creation_expression`
- `build_java_payload`: `classes` + `type_edges` + `package_def` schema
- Fixtures + differential test

### Phase JS — JavaScript port

**Deliverables:**
- `tree-sitter-javascript = "0.23"` (already in Cargo.toml!)
- `JsGrammar` (already in grammar.rs — only needs walker)
- `js_lang.rs`: walker with JS dispatch sets
- `build_js_payload`: minimal schema
- Fixtures + differential test

### Phase CS — C# port

**Deliverables:**
- Add `tree-sitter-c-sharp = "0.23"` to Cargo.toml
- Add `CSharpGrammar` in grammar.rs
- `csharp.rs`: walker with C# node types
- `build_csharp_payload`
- Fixtures + differential test

### Phase PHP — PHP port

**Deliverables:**
- Add `tree-sitter-php = "0.23"` to Cargo.toml
- Add `PhpGrammar` in grammar.rs
- `php.rs`: walker with PHP node types + anonymous functions + 5 call kinds
- `build_php_payload`: 6-tuple (no parse_meta)
- Fixtures + differential test

## Validation Criteria

Per language:
- [ ] `cargo test --release <lang>::` passes (≥5 tests)
- [ ] Differential test: Rust output == Python `parse_<lang>_file` on fixtures
- [ ] `extract_batch(paths, root, "<lang>", threads)` routes correctly
- [ ] Payload schema matches Python (key names, types, symbol-ID formats)

## Grammar Crate Status

| Language | Crate in Cargo.toml | Grammar in grammar.rs | Walker needed |
|----------|:-------------------:|:---------------------:|:-------------:|
| Java | ✅ already | ✅ already | **YES** |
| JS | ✅ already | ✅ already | **YES** |
| C# | ❌ need to add | ❌ need to add | **YES** |
| PHP | ❌ need to add | ❌ need to add | **YES** |
| ~~SQL~~ | ~~moved to Tier 3 (pure regex)~~ | — | — |

## Estimated Effort

- **Java:** Medium-High (~700 lines — Family A, visibility, ClassDef/TypeEdge/PackageDef, 4-index resolver)
- **JS:** Low-Medium (~400 lines — shares TS dispatch sets without React/navigation)
- **C#:** Medium (~500 lines — namespaces, local functions, simplest FunctionDef)
- **PHP:** Low-Medium (~450 lines — anonymous functions, 5 call kinds, include/require)

Total: ~2050 lines across 4 modules. Each language is an independent session.
