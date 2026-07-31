# Phase 3: Call Resolution + Relation Building (Rust)

## Goal

Move cross-file call resolution (`_resolve_calls`) and relation inference from Python to Rust. This eliminates the Python-side index-building pass (Pass 1, ~3038) and reduces Python orchestration to graph-write + embedding only.

## Current Python flow (what we're replacing)

```
Pass 1 (cplus_analyzer.py:3038):
  for payload in iter_payloads():
      for func in payload["functions"]:
          # Build 8+ function indexes for call resolution
          function_index_by_name[name] = [entry, ...]
          function_index_by_name_arity[(name, arity)] = [entry, ...]
          function_index_by_scope_name[(scope, name)] = [entry, ...]
          function_index_by_scope_name_arity[(scope, name, arity)] = [...]
          function_index_by_qualified[qname] = entry
          function_index_by_qualified_arity[(qname, arity)] = entry
          function_index_by_file_name[(file, name)] = [...]
          class_methods[scope] = [entry, ...]

      # Also: using_namespaces_by_file, includes_by_file, macros_by_file,
      #       alias_targets_by_name, base_relations, event_nodes

_resolve_calls(functions, calls):  # line 2086
  for call in calls:
      # Try to match callee_name → callee_id using indexes
      # Priority: scope+name+arity > scope+name > name+arity > name
      # Also resolves via using_namespaces, aliases, class_methods
```

## Target Rust flow

```
Rust: extract_and_resolve_batch(paths, root, threads)
    │
    ├── Phase A: parallel parse + extract (Phase 2)
    │   → Vec<ExtractedPayload> (functions, calls, types, etc.)
    │
    ├── Phase B: build indexes (single-threaded, fast in Rust)
    │   ├── function_index_by_name: HashMap<String, Vec<FuncEntry>>
    │   ├── function_index_by_scope_name: HashMap<(Option<str>, String), Vec<FuncEntry>>
    │   ├── using_namespaces_by_file: HashMap<String, Vec<String>>
    │   ├── includes_by_file, macros_by_file, alias_targets_by_name
    │   └── class_methods: HashMap<String, Vec<FuncEntry>>
    │
    ├── Phase C: parallel call resolution
    │   for call in calls.par_iter_mut():
    │       call.callee_id = resolve_callee(
    │           call.callee_name, call.caller_scope, call.call_arity,
    │           call.caller_file,
    │           &function_index_by_name, &function_index_by_scope_name,
    │           &using_namespaces_by_file, &alias_targets_by_name,
    │           &class_methods,
    │       )
    │
    ├── Phase D: build relations
    │   ├── EXTENDS (from base_relations)
    │   ├── DECLARES, CONTAINS
    │   ├── USES_TYPE, POINTER_TO
    │   └── POSSIBLE_CALLS (function pointer analysis)
    │
    └── Return: Vec<ResolvedPayload> — ready for Python graph-write
```

## Rust index structures

```rust
// resolver.rs
use std::collections::HashMap;
use rayon::prelude::*;

#[derive(Clone)]
pub struct FuncEntry {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub arity: usize,
    pub file_path: String,
}

pub struct CallIndex {
    pub by_name: HashMap<String, Vec<FuncEntry>>,
    pub by_name_arity: HashMap<(String, usize), Vec<FuncEntry>>,
    pub by_scope_name: HashMap<(Option<String>, String), Vec<FuncEntry>>,
    pub by_scope_name_arity: HashMap<(Option<String>, String, usize), Vec<FuncEntry>>,
    pub by_qualified: HashMap<String, FuncEntry>,
    pub by_qualified_arity: HashMap<(String, usize), FuncEntry>,
    pub by_file_name: HashMap<(String, String), Vec<FuncEntry>>,
    pub class_methods: HashMap<String, Vec<FuncEntry>>,
}

impl CallIndex {
    pub fn from_payloads(payloads: &[ExtractedPayload]) -> Self {
        let mut idx = Self::default();
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry { ... };
                idx.by_name.entry(func.name.clone()).or_default().push(entry.clone());
                idx.by_name_arity.entry((func.name.clone(), func.arity)).or_default().push(entry.clone());
                // ... all index variants
            }
        }
        idx
    }

    pub fn resolve_callee(
        &self,
        callee_name: &str,
        caller_scope: Option<&str>,
        caller_file: &str,
        arity: usize,
        using_namespaces: &[String],
        aliases: &HashMap<String, String>,
    ) -> Option<String> {
        // Resolution priority (port of _resolve_calls logic):
        // 1. Scope-qualified name + arity (exact match)
        // 2. Scope-qualified name (arity-agnostic)
        // 3. Using-namespace qualified resolution
        // 4. Simple name + arity
        // 5. Simple name (first candidate)
        // 6. Alias resolution

        // Try scope-qualified first
        if let Some(scope) = caller_scope {
            let key = (Some(scope.to_string()), callee_name.to_string());
            if let Some(candidates) = self.by_scope_name.get(&key) {
                return Some(candidates[0].symbol_id.clone());
            }
        }

        // Try with using-namespaces
        for ns in using_namespaces {
            let qualified = format!("{}::{}", ns, callee_name);
            if let Some(entry) = self.by_qualified.get(&qualified) {
                return Some(entry.symbol_id.clone());
            }
        }

        // Try alias
        if let Some(aliased) = aliases.get(callee_name) {
            if let Some(entry) = self.by_qualified.get(aliased) {
                return Some(entry.symbol_id.clone());
            }
        }

        // Fallback: simple name + arity
        let key = (callee_name.to_string(), arity);
        if let Some(candidates) = self.by_name_arity.get(&key) {
            return Some(candidates[0].symbol_id.clone());
        }

        // Last resort: simple name
        if let Some(candidates) = self.by_name.get(callee_name) {
            return Some(candidates[0].symbol_id.clone());
        }

        None
    }
}
```

## Parallel resolution

```rust
pub fn resolve_calls(
    payloads: &mut [ExtractedPayload],
    index: &CallIndex,
) {
    // Flatten all calls across payloads for parallel processing
    // Then write resolved callee_id back

    payloads.par_iter_mut().for_each(|payload| {
        let using_ns = &payload.using_namespaces;
        let aliases = &payload.alias_map;  // pre-built
        let caller_file = &payload.file_path;

        for call in &mut payload.calls {
            if call.callee_id.is_none() {
                call.callee_id = index.resolve_callee(
                    &call.callee_name,
                    call.caller_scope.as_deref(),
                    caller_file,
                    call.call_arity,
                    using_ns,
                    aliases,
                );
            }
        }
    });
}
```

## Python integration

```python
# cplus_analyzer.py — simplified build_call_graph()

def build_call_graph(...):
    if _RUST_AVAILABLE:
        # Rust does parse + extract + resolve in one call
        payloads = _rust.extract_and_resolve_batch(
            all_file_paths, root, threads=8
        )
        # Python only needs to:
        # 1. Write to Neo4j (streaming)
        # 2. Embed + write to Qdrant
        # No more Pass 1 index building, no more _resolve_calls()

    else:
        # Existing Python 3-pass flow
        ...
```

## Performance impact

| Step | Python (current) | Rust (Phase 3) |
|------|-----------------|----------------|
| Pass 1: parse + index | 92.8s | Included in batch |
| _resolve_calls | ~5-10s | ~0.5s (parallel) |
| Relation building | ~3-5s | ~0.3s (parallel) |
| **Total** | ~100s | ~12s |

## Deliverables

- [ ] `CallIndex` struct with all resolution strategies
- [ ] `resolve_calls()` parallel function
- [ ] `build_relations()` for EXTENDS/DECLARES/USES_TYPE
- [ ] `extract_and_resolve_batch()` unified API
- [ ] Differential test: resolved callee_id matches Python output on 2493 files
