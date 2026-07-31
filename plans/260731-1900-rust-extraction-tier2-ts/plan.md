---
title: "TypeScript Extraction Layer (Tier 2)"
status: done
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 3
scope: Port tree-sitter AST walk + extraction + React/navigation/API intelligence for TypeScript analyzer to Rust cortex_extract
priority: 1 (Phase 0: 78.7% parse+extract, 665 files, 9.25s end-to-end — HIGHEST ROI)
blockedBy: []
---

# TypeScript Extraction Layer (Tier 2)

## Overview

Port the TypeScript analyzer's full extraction pipeline to the Rust native extension. TS is **the highest-ROI port** in the entire plan: 665 files, 78.7% CPU-bound, and 9.25s end-to-end. But it is also **the most complex**: the extraction code spans 8+ source files across `tools/ts/agents/`, `tools/ts/utils/`, and `tools/ts/types/`.

**⚠️ Complexity warning:** Unlike Go (single file, ~500 lines), TS extraction involves ~40 compiled regex patterns, React component classification, navigation intelligence with BFS screen-owner attribution, API call extraction, and a 12-tuple payload schema that is completely different from C++.

## Survey Results

### Architecture (NOT a single file)

| File | Role | Lines |
|------|------|-------|
| `ts_analyzer.py` | Orchestrator + call resolution + graph/vector writers | ~1600 |
| `agents/traversal_agent.py` | `_walk_tree` + `_record_function` + node-kind dispatch | ~700 |
| `agents/parser_agent.py` | Parser, AST helpers, JSX/import/export, name extraction | ~800 |
| `agents/symbol_agent.py` | React-role, API-call, navigation, navigator extraction | ~900 |
| `agents/dependency_agent.py` | Import graph | ~300 |
| `utils/regex_patterns.py` | ~40 compiled regexes + factory maps | ~400 |
| `utils/id_utils.py` | Symbol-ID construction | ~150 |
| `utils/file_utils.py` | File scanning, screen/service detection | ~200 |
| `types/ast_types.py` | TS-specific dataclasses | ~250 |
| `types/graph_types.py` | Graph dataclasses | ~150 |

### 12-tuple payload schema (NOT a dict)

`parse_ts_file` returns a **tuple**, not a dict:
```
(functions, calls, types, namespaces, relations, renders, navigates,
 file_def, meta, api_calls, navigators, param_lists)
```

### TS-unique extraction (no C++/Go equivalent)

1. **`react_role`** — screen | component | hook | middleware (heuristic classification)
2. **`middleware_kind`** — api | query | redux | service
3. **`ApiCallDef`** — outgoing HTTP calls (fetch/axios, URL normalization, method)
4. **`RenderEdge`** — renderer → rendered PascalCase JSX component
5. **`NavigateEdge`** — source → target with method/guard/confidence/trigger_type
6. **`NavigatorDef`** — RN navigator factories (createStackNavigator etc.)
7. **`ParamListDef`** — `*ParamList` type aliases with route→type map
8. **`RouteConfigEntry`** — `<Stack.Screen name=... component={...}>` extraction
9. **Factory classification** — `_CALL_EXPR_KIND_MAP` (60+ entries: createSlice→redux_slice, etc.)

### Node-type dispatch sets

```
_TYPE_NODE_KINDS:
  class_declaration→class, abstract_class_declaration→class,
  interface_declaration→interface, type_alias_declaration→type_alias,
  enum_declaration→enum

_FUNCTION_NODE_KINDS:
  function_declaration→function, generator_function_declaration→generator_function,
  method_definition→method

_NAMESPACE_NODE_TYPES:
  namespace_declaration, internal_module, module_declaration, module

_JSX_NODE_TYPES:
  jsx_element, jsx_fragment, jsx_text, jsx_opening_element, jsx_self_closing_element

_BARE_FUNC_TYPES (inside export):
  arrow_function, function, generator_function, function_expression

_CALL_NODES:
  call_expression, new_expression
```

### ~40 Regex patterns (in `utils/regex_patterns.py`)

Organized by domain:
- **React role:** HOC factory (`^with[A-Z]`), screen hooks (useNavigation/useRoute...), screen nav calls
- **Middleware:** API (fetch/axios), Query (useQuery/useSWR), Redux (createSlice), Service (prisma/mongoose...)
- **API extraction:** fetch calls, axios shorthand, http_client, named_client, env vars
- **Navigation:** useNavigation/useRouter/useNavigate destructuring, nav-prop calls, router calls, nav-ref, `<Link to>`, `<Navigate to>`
- **Navigation V2:** user/system/async triggers, auth/permission guards, Screen element/name/comp attrs
- **Factory classification:** `_RE_NAVIGATOR_FACTORY`, `_FACTORY_TO_NAV_TYPE`, `_CALL_EXPR_KIND_MAP`

### Call resolution (NOT `_resolve_calls`)

Resolution happens at graph-write time in `ts_analyzer.py` via `resolve_callee_id(call)`:
1. Index by `(name, arity)` then by `name`
2. Prefer arity match, fall back to name-only
3. Filter by scope if multiple candidates
4. **Post-resolution:** BFS over reverse CALLS+RENDERS graph to attribute navigate calls to owning screens (`_find_screen_owners`, max_depth=6)

### Symbol-ID formats (load-bearing for Qdrant UUID5)

```
function: "{scope}::{name}/{arity}@{rel_path}"
type: identity (qualified name)
namespace: "namespace::{name}"
```

## Phases

### Phase 1 — Grammar + base walker + payload schema

**Goal:** Get a working TS tree-sitter walk producing the basic payload (functions, calls, types, namespaces, relations). No React/navigation/API yet.

**Deliverables:**
1. Add `tree-sitter-typescript = "0.23"` to `Cargo.toml`
2. Add `TsGrammar` + `TsxGrammar` in `grammar.rs` (tsx vs ts by extension)
3. Write `ts.rs`:
   - `TsParseOutput` struct (12-tuple equivalent)
   - `parse_ts_source(source, rel_path, is_tsx)` entry point
   - Port `_walk_tree` from `traversal_agent.py` — all dispatch sets
   - Port AST helpers from `parser_agent.py` (name extraction, call extraction, JSX collection)
   - Port ID construction from `id_utils.py`
4. Add `build_ts_payload` in `payload.rs` — 12-field output
5. Wire `lib.rs`: `extract_ts`, `extract_ts_batch`, `extract_batch` routing for `"ts"`/`"tsx"`

**Validation:** Rust unit tests on functions, classes, interfaces, calls, imports.

### Phase 2 — React role + middleware classification

**Goal:** Port `react_role` and `middleware_kind` heuristic classification.

**Deliverables:**
1. Port ~15 regex patterns from `utils/regex_patterns.py` (React role + middleware)
2. Port `_detect_react_role` and `_detect_middleware_kind` from `symbol_agent.py`
3. Integrate into `_record_function` flow

**Validation:** Fixture with React components, hooks, middleware — verify role/kind match.

### Phase 3 — Renders + JSX extraction

**Goal:** Port JSX component rendering extraction.

**Deliverables:**
1. Port `_collect_jsx_tags` + `_collect_rendered_components` from `symbol_agent.py`
2. Port `_has_jsx_in_subtree` helper
3. Emit `RenderEdge` entries during walk

### Phase 4 — Navigation intelligence

**Goal:** Port the full navigation extraction pipeline.

**Deliverables:**
1. Port ~20 navigation regex patterns
2. Port `_collect_navigate_calls` + `_classify_nav_context` + `_detect_nav_guard`
3. Port `_collect_route_configs` + `_extract_navigator_declarations`
4. Port `_extract_param_lists`
5. Emit `NavigateEdge`, `NavigatorDef`, `ParamListDef` entries

**Validation:** Fixture with RN navigator setup, screen navigation, route configs.

### Phase 5 — API call extraction

**Goal:** Port API call extraction.

**Deliverables:**
1. Port API regex patterns (fetch, axios, http_client)
2. Port `_extract_api_calls` + `normalize_url_pattern` + `merge_base_url`
3. Emit `ApiCallDef` entries

### Phase 6 — Factory classification

**Goal:** Port `_CALL_EXPR_KIND_MAP` factory classification.

**Deliverables:**
1. Port the 60+ entry factory map
2. Integrate into `_walk_tree` export handling

### Phase 7 — Call resolution + screen-owner BFS

**Goal:** Port call resolution and the BFS screen-owner attribution.

**Deliverables:**
1. Port `resolve_callee_id` (arity + scope matching)
2. Port `_find_screen_owners` BFS algorithm
3. Port RENDERS edge filtering (Screen→Screen suppressed → NAVIGATE)

### Phase 8 — Differential testing

**Goal:** Verify Rust output matches Python on real TS/TSX files.

**Deliverables:**
1. Create TS fixtures (React components, hooks, RN screens, API calls)
2. Write `tests/test_ts_differential.py`
3. Verify all 12 payload fields match

## Validation Criteria

- [ ] Phase 1: `cargo test --release ts::` passes (≥8 tests)
- [ ] Phase 2-6: each phase has dedicated unit tests
- [ ] Phase 7: call resolution + BFS parity
- [ ] Phase 8: differential test passes on fixtures
- [ ] `extract_batch(paths, root, "ts", threads)` routes to Rust pipeline
- [ ] 12-tuple payload schema exactly matches Python (symbol-ID formats preserved)

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| ~40 regex ports — regex crate syntax differs from Python | High | Test each regex individually against known inputs |
| BFS screen-owner algorithm complexity | High | Port faithfully; test on multi-screen RN fixture |
| 12-tuple payload — graph-write side depends on exact shape | High | Differential test against real TSX from payslip project |
| `_CALL_EXPR_KIND_MAP` — 60+ entries | Medium | Port as static HashMap; test classification |
| tsx vs ts grammar selection | Medium | Grammar dispatch by `.tsx` extension |

## Files to Create/Modify

| File | Action |
|------|--------|
| `rust-analyzer-core/Cargo.toml` | Add `tree-sitter-typescript` |
| `rust-analyzer-core/src/grammar.rs` | Add `TsGrammar`, `TsxGrammar` |
| `rust-analyzer-core/src/ts.rs` | **NEW**: ~1500+ lines — full walker + all helpers |
| `rust-analyzer-core/src/ts_regex.rs` | **NEW**: ~40 compiled regex patterns + factory maps |
| `rust-analyzer-core/src/payload.rs` | Add `build_ts_payload` (12-field) |
| `rust-analyzer-core/src/lib.rs` | Add `extract_ts`, `extract_ts_batch`, routing |
| `tests/fixtures/ts-app/` | TS/TSX fixtures |
| `tests/test_ts_differential.py` | Differential test |

## Estimated Effort

**High** — this is the largest single-language port. 8 phases, ~1500+ lines of Rust, ~40 regex patterns. The React/navigation/API intelligence is the bulk of the work, not the basic AST walk. Estimate: 2-3 focused sessions.
