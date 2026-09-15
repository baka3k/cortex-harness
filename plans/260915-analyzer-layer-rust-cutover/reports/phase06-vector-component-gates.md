# Phase 06 — Vector-plane embedding at orchestrator level via cortex-embed

**Verdict: GO for the shared-7 vector lineage (component gates G1–G8 PASS).**
The spike `260914-1706-onnx-embedding-spike` NO-GO (`reports/phase05-sync-decision.md`)
was about porting `primary_vector_sync` — the three silent hazards it named
(deterministic uuid5 join order, 3-tier redaction, `_hash_vector`) plus
`delete_by_filter`/`vectors_config` byte-match — and here each is met with a
measured component gate. The legacy `CodeEmbedder`/`QdrantWriter` lineage and
the local embedded-store lane are **explicit carve-outs** (fail-closed to
Python children), not silent losses.

> Worktree note: built and measured in an isolated git worktree off `d70588a`
> (phase-01 artifact-contract freeze — the only hard dependency of phase-06).
> Merged back to `feat/change-db` by hand after phase-02/03/05 land.

Environment: macOS arm64 (Apple Silicon), CPU-only, live Qdrant server
(`cortex-qdrant`, HTTP 127.0.0.1:6333) + FalkorDB. Fixture set digest
sha256 `747a3239a35bee1d…` (`scripts/rust_parity/gen_vector_plane_fixtures.py`).

---

## G1 — deterministic uuid5 point id (red-team A5 / F7, spike reason #2)

`vector_sync::deterministic_point_id` — custom namespace
`6694a056-5f64-5e1a-b5fc-b1ef4ec630db`, separator U+0000, **join order
`(parser, project_id, root_scope, symbol_id)`** which is swapped against the
`(parser, project_id, symbol_id, root_scope)` signature — the exact trap the
spike flagged. `parser` is `strip().lower()`; the other three are `strip()`
only. Whitespace set is CPython `Py_UNICODE_ISSPACE` (adds U+001C..U+001F that
Rust's `char::is_whitespace` excludes).

- **Fixture gate** `g1_point_id_fixture_parity`: 14 id cases (incl. trailing
  U+001C/U+001F strips, NBSP, Turkish dotted-capital parser, Greek final-sigma,
  500-char fields) + 4 empty-field error cases — **ids exact, Python (CPython
  uuid5) ↔ Rust (uuid::new_v5) byte-identical.** PASS.
- **Live**: native run produced point ids byte-identical to the Python baseline
  (G5 `shared_ids=80/80`, join by id succeeded with `text_field_mismatches=0`).

## G2 — 3-layer redaction on corpus real (red-team F7)

`vector_sync::redact_text` (fancy-regex, because tier 2 uses a named
backreference `(?:P=quote)` the plain `regex` crate cannot express): private-key
block → quoted assignment → unquoted assignment, in that order, matching
Python's chained `re.sub` (tier 3 re-scans tier-2 output: `key="x"` → `key=[REDACTED]`,
quotes collapse). Python `\s` extended with U+001C..U+001F in every pattern.
Applied in `_bounded_text`: join parts `\n` → **redact** → truncate to
`min(max_chars, 16000)` **code points** (after redaction, matching Python).

- **Fixture gate** `g2_redaction_fixture_parity`: 27 adversarial secret shapes
  (multi-line quoted values, `TOKENIZE`/`tokenizer` must-not-fire, `secret=`
  idempotence, U+001C value stop) + 5 `_bounded_text` cases — **diff=0 vs
  Python.** PASS.
- **Live**: `creds.go` (PEM block + api_key/password/token assignments) → stored
  payload text is `[REDACTED PRIVATE KEY]` / `api_key = [REDACTED]`, and the
  native text byte-matches the Python text (G5 `text_field_mismatches=0`).

## G3 — `_hash_vector` port (spike reason #3)

Correction to the plan text: `_hash_vector` is not "change-detection for vector
skip" — in the reference (`message_scan.py:393`) it is the **message-lane
fallback vectorizer** (used when the neural embedder is absent/fails). Ported
faithfully: token regex `[A-Za-z0-9_:.]+` over Python full-Unicode
`str.lower()`, per-token SHA-1 as a 160-bit big-endian int `% size` (exact
modular fold, no bignum), L2-normalise over f64.

- **Fixture gate** `g3_hash_vector_fixture_parity`: 36 texts × sizes {1024,8,1},
  incl. Turkish `İ`, `ẞ`, Greek `ΑΣ/Σ` final-sigma context, the U+001C..U+001F
  separator class, zero-norm. Compared **bit-exact (f64 to_bits)** — **0
  divergent vectors.** PASS.
- Message-vector lane itself (`cortex-embed` swap for phase-05 sidecar pin) is
  **not exercised here** — phase-05 lands in a separate session; G3 delivers the
  bit-exact primitive it needs.

## G4 — stale `delete_by_filter` fields + `vectors_config` byte-match (red-team F8)

**Fixture**: `g4_stale_filter_fixture_parity` + `g4_collection_bodies_parity` —
the stale filter (`project_id_normalized` casefold + `parser` + `root_scope` +
`file_path:any` sorted-deduped-slashes, `must_not:has_id`, full_replace drops
the file_path clause, early-out when nothing to delete) and the
`{"vectors":{"size":1024,"distance":"Cosine"}}` create body + env-gated
`_tuning_kwargs` + 4 `SCOPE_INDEX_FIELDS` in tuple order + the size-drift error
text — **byte-identical to the captured Python reference calls.** PASS.

**Live (collection written by Python, then native run):**
| check | result |
|---|---|
| baseline (Python, 10 files) | 80 points, vectors `{size:1024, distance:Cosine}` |
| native embedding on same collection, 2 files deleted | **71 points** (the 9 points of `deprecated.go`+`todelete.go` gone; 8 survivor files intact) |
| deleted-file identification | exactly `deprecated.go`, `todelete.go` |
| `vectors` config before/after native | **unchanged → collection NOT recreated** ✓ |
| exit code of native embedding-only run | 0 |

## G5 — cortex-embed jina fp32 vs Python children on real corpus

int8 note: **no official int8 artifact exists** for jina-v3 or bge-m3 (spike
`findings.md` C3; only fp32/fp16; self-quantised int8 never measured and GLiNER
int8 was a hard NO-GO). Per the plan's "int8 nếu artifact có", G5 runs **fp32**,
gate cosine ≥ 0.999.

Two independent measurements, both PASS at ≥0.999:
1. **Unit corpus (fixture)** — `embed_golden.rs --ignored` gate re-run this
   session (release, 259s): **840 cases** across code+doc planes, token-id
   `drift=0`, cosine `mean=1.0000001 worst=0.9999994 below_gate=0`. 3/3 tests
   PASS. (jina-v3 code plane worst 0.9999995 ≫ gate 0.999, the spike's
   "5 decimal orders of margin" holds.)
2. **Live sync corpus (native ONNX pass vs Python child, per point joined by the
   deterministic uuid5 id):** 80 docs (10-file corpus incl. secrets/non-ASCII) →
   **mean 0.999999999998, worst 0.999999999992, points-below-gate 0,
   text_field_mismatches 0, verdict PASS.** Survivor-only recount 71/71 same.

Count parity (per collection): Python 80 ↔ native 80 (full), 80→71 after the
2-file delete on both planes.

## G6 — sync-time wall-time baseline (red-team A7; "same machine, measured")

Live A/B on the **same machine, same 8-file corpus (71 docs), same HTTP Qdrant**,
`--sync-mode embedding --full-scan`:
| lane | wall time | embedding-pass duration |
|---|---|---|
| Python children (`CORTEX_EMBED_ORCHESTRATOR` pin) | 18.31 s | 18.17 s |
| **Orchestrator ONNX (native)** | **9.41 s** | **9.14 s** |

Native is **1.95× faster**, not a ≥20 % regression (gate: no regression >20 %).
One-time ONNX session load is amortised across all parsers by the orchestrator
(single model load) versus a fresh SentenceTransformer cold start per Python
child. Batch size kept at production default **8** (`EMBED_BATCH_SIZE`), matching
the spike finding that ort ties torch at batch 8 and the corpus here.

## G7 — artifact provenance (red-team S5)

- `export_jina_onnx.py` / `fetch_bge_onnx.py` now pin the exact HF snapshot
  revision as a `MODEL_REVISION` constant and pass it to `snapshot_download`;
  the `trust_remote_code` surface (jina's XLMRobertaLoRA modeling file) is
  reviewed and time-boxed to that revision in-file.
- `cortex-embed/src/model.rs`: `model_pin()` records the exported graph + weights
  sha256 in-repo; `verify_provenance()` **hashes the graph file on every load**
  and **fails closed on mismatch** (escape hatch `CORTEX_EMBED_VERIFY_SHA256=0`
  warns loudly). Negative test `pinned_graph_with_wrong_digest_fails_closed`
  PASS. The 2.2 GB weights file is digested by the `#[ignore]`d
  `provenance_pins_match_disk` build/repro gate, not per-load.
- `cortex-dev/src/ensure_ort.rs`: `pick_asset` now **fails closed when the PyPI
  wheel digest is missing or malformed** (was `if let Some(expected)` — silently
  downloaded unverified). Negative tests `pick_asset_fails_closed_when_digest_missing`
  / `_on_malformed_digest` PASS.

## G8 — embedding-input artifact 0600 (red-team S6)

`cortex-analyzer-framework/src/embedding_artifact.rs` implements the phase-01
frozen contract (schema_version, generated_at, ordered categories minus
relations/calls, cleanup file sets). Written **atomically at mode 0600**
(tmp+chmod+rename). Fixture gate `writes_artifact_at_mode_0600_and_roundtrips`
asserts `perm & 0o777 == 0o600`. **Live artifact** from `analyzer-go`: 53485
bytes, `-rw-------` (0600), 5 categories / 80 documents. PASS.

---

## Findings corrected during implementation (plan-text ≠ reference code)

1. **G3 `_hash_vector`** is the message-lane *fallback vectorizer*, not
   change-detection (see G3). Ported to its true semantics.
2. **`upsert` was POST, qdrant upsert is PUT** (`/points` POST is the
   retrieve-by-ids endpoint). The phase-06 native pass caught this against a
   live server; `cortex-storage::qdrant_remote::upsert_wait` fixed to `PUT`.
3. **`delete` filter body shape**: was `{"points":{"filter":…}}` (invalid
   `PointsSelector`); now `{"filter":…}`, matching the Python qdrant_client.
   No prior filter-delete caller existed on the remote store.
4. **`pick_graph` could not discover a script-named export dir** (jina →
   `jina-v3-onnx-fp32`, not the HF slug) and would silently fall to the
   official task_id graph; only the G7 pin digest exposed it. Fixed with
   `metadata_exported_graph` (scans `.cache/embed/*/metadata.json`).
5. **Local embedded-store lane stays on Python children**: the Rust JSON engine
   (`cortex-local-store.json`) is not file-compatible with the Python
   `qdrant_client(path=…)` embedded engine. The native pass **fails closed to
   `NativeStore::Unsupported`** (loud, per parser) for anything that is not an
   HTTP Qdrant server — so the default/local dogfood path is unchanged until an
   explicit store-format decision. This is the primary carve-out.

## Exit criteria status

- ✅ Component gates G1–G8 all PASS (unit + fixture + live measurements above).
- ✅ End-to-end sync smoke with Qdrant on (final rebuild): `--sync-mode embedding
  --full-scan` native run exit=0, `vector_status=success`, `vector_count=71`,
  `embedding_backend=orchestrator`; export compare vs Python-written baseline:
  shared_ids 71/71, worst cosine 0.999999999992, below-gate 0, text mismatches 0;
  **query sanity PASS** (top-1 = the query symbol at score 1.000000, sibling
  `Handle` methods ranked 2–5).
- ✅ `cargo test` green across every touched crate (cortex-sync 43, framework 8,
  cortex-embed 27 + 4 ignored gates incl. `provenance_pins_match_disk`,
  cortex-storage, graph-writer, 6 analyzer children, cortex-dev 27); re-run of
  `embed_golden --ignored` 3/3: 840 cases token drift 0, cosine worst 0.9999994,
  bench batch8=22.0 code / 9.6 doc texts/s (batch 8 best at both planes ⇒ keep
  `EMBED_BATCH_SIZE=8`).
- ✅ `cargo clippy --all-targets -- -D warnings` exit 0 across all 11
  phase-06-touched crates. **Note**: whole-workspace `-D warnings` still fails on
  `cortex-dev` (16 warnings) — pre-existing lints surfaced by clippy 1.97.1 in
  files phase-06 did not author; not expanded here.
- ⚠ **Repo-hygiene finding** (not caused by phase-06): `analyzer-jp1`'s
  `tests/parser_golden.rs` reads `tests/fixtures/jp1-analyzer/` which is **not
  tracked in git** → the test fails in any fresh clone/worktree until the
  fixtures are committed or the test self-skips. Reproduced by copying the
  fixtures in; recommend committing them in a follow-up.

## Scope NOT done here (honest)

- **Legacy `CodeEmbedder`/`QdrantWriter` lineage** (~17 parsers: sql, java,
  cplus, ts, js, python, csharp, plsql, delphi, kotlin, android, php, vb*,
  cobol): different point-id scheme (`uuid5(NAMESPACE_URL, symbol_id)`), no
  redaction, 512-token mean-pool, own QdrantWriter. Porting it is a separate
  program; those children keep the Python embedding path. Consistent with
  phase-06's fallback ("carve-out tường minh") and the "Không xoá khi vector
  plane chưa native hoặc chưa carve-out tường minh" constraint — phase-08 delete
  list must be narrowed to the shared-7 lineage accordingly.
- **Message-vector lane cortex-embed swap** (plan scope bullet, conditional on
  phase-05 pinning the sidecar): deferred to phase-05's session; G3 delivers the
  bit-exact primitive.

## Child wiring status (all six shared-7 lineage parsers)

All six wired to emit the embedding-input artifact and compiling clippy-clean:
`go` (verified **live end-to-end** above), `swift`, `rust`, `perl`, `shell`
(verified standalone artifact: mode 0600, `parser`/`root_scope`/categories/
cleanup sets correct), `jp1`. `dart` (the 7th) auto-inherits the framework hook
when phase-02 lands its binary; the native pass keeps it on the Python child
(loud log) until then. A missing binary for any shared parser degrades to the
Python child, so child wiring never gates the component evidence.

### Parity notes from the child-wiring review (accepted, not defects)

1. **Category ORDER in the artifact differs from Python's dict order** for the
   `WriteAllPayload`-derived children (Rust emits `namespaces, files, …`,
   Python `files, namespaces, …`). Harmless: point ids are per-document
   deterministic (`uuid5`), so upsert/stale results are order-invariant; only
   the batch grouping changes (G5 join-by-id proved 80/80 and 71/71 anyway).
   The shell child deliberately does NOT wrap through `WriteAllPayload` (it has
   no `scripts`/`invocations`/`programs` slots) and emits Python's exact category
   keys — verified against `shell_analyzer.py:144-150`.
2. **`scanned_directory: true` is constant** in the children. Python's
   `full_replace = not incremental and _scanned_directory`; that conjunct can
   only be false on a single-FILE embedding run, which the orchestrator never
   issues (it always points a child at the repo directory). shell/jp1 Python
   don't even carry the conjunct (shell:323, jp1:95), so `true` is literal
   parity there.
3. **`hash_vector` now exists twice** in cortex-sync after the phase-05 merge:
   `message_scan::record::hash_vector` (f32, ASCII-only lower, digest truncated
   to 64 bits before `% size`) vs the G3 bit-exact `crate::hash_vector` (f64,
   full-Unicode lower, exact 160-bit modulo, fixture-gated). The G3 one is
   canonical — migrate the message-lane caller onto it in a phase-05 follow-up;
   do not add a third variant.
