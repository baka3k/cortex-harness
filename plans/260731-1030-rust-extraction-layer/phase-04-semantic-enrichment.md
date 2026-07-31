# Phase 4: Semantic Enrichment (Rust)

## Goal

Port `SemanticInferenceEngine.enrich_corpus()` (common/semantic_inference.py:434) to Rust. This eliminates the second bottleneck: 80.5s of regex heuristic scoring.

## Current Python flow

```
SemanticInferenceEngine.enrich_corpus(functions, calls):
    usage_index = build_usage_index(functions, calls)   # call_graph_builder.py
    for func in functions:
        result = engine.analyze(func, usage_index)
        func["intent"] = result.intent
        func["summary"] = result.summary
        func["doc_confidence"] = result.confidence
        func["signals"] = result.signals
        func["side_effect"] = result.side_effect
        func["note"] = _build_semantic_note(func)

analyze(func, usage_index):
    signals = {
        "naming": _naming_signal(func["name"]),       # 0.40 weight
        "usage":  _usage_signal(func, usage_index),    # 0.30 weight
        "type":   _type_signal(func),                  # 0.20 weight
        "body":   _body_signal(func["code"]),          # 0.10 weight
    }
    intent = highest_priority_signal(signals)
    confidence = weighted_sum(signals)
```

## Target Rust implementation

```rust
// semantic.rs
use regex::Regex;
use std::sync::Arc;

pub struct SemanticEngine {
    // Pre-compiled regex patterns (compiled once, used across all functions)
    naming_patterns: NamingPatterns,
    body_patterns: BodyPatterns,
}

struct NamingPatterns {
    get:     Regex,  // ^(get|fetch|find|query|select|read|load)
    set:     Regex,  // ^(set|update|save|write|create|insert|add|put)
    delete:  Regex,  // ^(delete|remove|clear|drop|purge)
    is_has:  Regex,  // ^(is|has|can|should|will|exists)
    validate: Regex, // ^(validate|check|verify|ensure|assert)
    // ... all 12 intent categories
}

struct BodyPatterns {
    return_db:   Regex,   // return.*(select|query|find|cursor|result)
    mutation:    Regex,   // (insert|update|delete|save)\(
    io_read:     Regex,   // (read|fetch|get|open)\(
    io_write:    Regex,   // (write|flush|close|send|dispatch)\(
    side_effect: Regex,   // (System\.out|print|log|emit|notify)
    // ...
}

impl SemanticEngine {
    pub fn new() -> Self {
        Self {
            naming_patterns: NamingPatterns {
                get: Regex::new(r"^(?i)(get|fetch|find|query|select|read|load)").unwrap(),
                set: Regex::new(r"^(?i)(set|update|save|write|create|insert|add|put)").unwrap(),
                // ... compile all patterns once
            },
            body_patterns: BodyPatterns { ... },
        }
    }

    pub fn enrich_corpus(
        &self,
        payloads: &mut [ExtractedPayload],
    ) {
        // Build usage index from all calls (parallel)
        let usage_index = Arc::new(UsageIndex::from_payloads(payloads));

        // Enrich functions in parallel
        payloads.par_iter_mut().for_each(|payload| {
            let engine = self;  // &Self is Sync (regex is Sync)
            for func in &mut payload.functions {
                let result = engine.analyze(func, &usage_index);
                func.intent = result.intent;
                func.summary = result.summary;
                func.doc_confidence = result.confidence;
                func.signals = result.signals;
                func.side_effect = result.side_effect;
                func.note = build_semantic_note(func);
            }
        });
    }

    fn analyze(&self, func: &FunctionDef, usage_index: &UsageIndex) -> SemanticResult {
        let naming = self.naming_signal(&func.name);
        let usage = self.usage_signal(func, usage_index);
        let type_sig = self.type_signal(&func);
        let body = self.body_signal(&func.code);

        let signals = Signals { naming, usage, type_sig, body };
        let intent = resolve_intent(&signals);
        let confidence = weighted_sum(&signals);

        SemanticResult { intent, confidence, signals }
    }
}
```

## Usage index (port of call_graph_builder.py)

```rust
// usage_index.rs
use std::collections::HashMap;

pub struct UsageIndex {
    pub by_id: HashMap<String, Vec<CallSiteContext>>,
    pub by_name: HashMap<String, Vec<CallSiteContext>>,
}

pub struct CallSiteContext {
    pub pattern: CallPattern,  // Assignment, Condition, Await, Standalone
    pub caller_id: String,
}

impl UsageIndex {
    pub fn from_payloads(payloads: &[ExtractedPayload]) -> Self {
        let mut by_id = HashMap::new();
        let mut by_name = HashMap::new();

        for payload in payloads {
            for call in &payload.calls {
                let caller_code = payload.functions.iter()
                    .find(|f| f.symbol_id == call.caller_id)
                    .map(|f| f.code.as_str())
                    .unwrap_or("");

                let ctx = classify_call_context(&call.callee_name, caller_code, &call.caller_id);

                if !call.callee_id.is_empty() {
                    by_id.entry(call.callee_id.clone()).or_default().push(ctx.clone());
                }
                by_name.entry(call.callee_name.clone()).or_default().push(ctx);
            }
        }
        Self { by_id, by_name }
    }
}
```

## PyO3 API

```rust
#[pyfunction]
fn enrich_semantics(
    py: Python,
    functions: &PyAny,  // PyList of PyDict
    calls: &PyAny,      // PyList of PyDict
) -> PyResult<()> {
    // Option A: Convert PyDicts → Rust structs, enrich, convert back
    // Option B: Mutate PyDicts in place via PyO3 (slower per-item but no conversion)

    // Recommended: Option A (batch conversion is fast, parallel enrichment is fast)
    let mut funcs: Vec<FunctionDef> = functions.extract()?;
    let calls: Vec<CallEdge> = calls.extract()?;

    let engine = SemanticEngine::new();
    engine.enrich_corpus(&mut funcs, &calls);

    // Write back to PyDicts
    for (i, func) in funcs.iter().enumerate() {
        let dict = functions.get_item(i)?;
        dict.set_item("intent", &func.intent)?;
        dict.set_item("summary", &func.summary)?;
        dict.set_item("doc_confidence", func.doc_confidence)?;
        dict.set_item("side_effect", func.side_effect)?;
        // ...
    }
    Ok(())
}
```

## Alternative: integrate into Phase 3 batch

Instead of a separate API, semantic enrichment can be part of the unified pipeline:

```rust
#[pyfunction]
fn extract_resolve_enrich_batch(
    paths: Vec<String>,
    root: &str,
    threads: usize,
) -> PyResult<Vec<PyObject>> {
    // Phase A: parallel parse + extract
    let mut payloads = parallel_extract(&paths, root, threads)?;

    // Phase B: build call index
    let index = CallIndex::from_payloads(&payloads);

    // Phase C: parallel call resolution
    resolve_calls(&mut payloads, &index);

    // Phase D: parallel semantic enrichment
    let engine = SemanticEngine::new();
    engine.enrich_corpus(&mut payloads);

    // Phase E: build PyDicts (GIL required)
    let py = Python::acquire_gil();
    build_payloads(py.python(), &payloads)
}
```

## Performance projection

| Step | Python (current) | Rust (Phase 4) |
|------|-----------------|----------------|
| build_usage_index | ~5s | ~0.3s (HashMap, parallel) |
| naming regex per func | ~10s | ~0.5s (pre-compiled regex, &str) |
| usage classification per call | ~25s | ~1s (parallel, no Python string ops) |
| type/body regex per func | ~15s | ~0.5s |
| note building per func | ~25s | ~0.5s |
| **Total semantic** | **80.5s** | **~3s** |

## Validation

- **Differential test:** Run Rust `enrich_semantics()` on same function/call dicts; compare `intent`, `confidence`, `signals`, `note` against Python output for all 2928 functions in 2493-file corpus
- **Performance:** Profile with `profile_analyzer.py --full --rust`; semantic phase should drop from 80.5s to <5s

## Deliverables

- [ ] `SemanticEngine` Rust struct with pre-compiled regex patterns
- [ ] `UsageIndex` port of `call_graph_builder.py`
- [ ] `enrich_semantics()` PyO3 function
- [ ] Differential test: 100% match with Python on test corpus
- [ ] Semantic phase < 5s on 2493 files
