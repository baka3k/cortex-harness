# Phase 03 — full symbol universe: resultset pagination + Field label

## Goal

Make `read_code_symbols` see **every** symbol regardless of the graph
server's `resultset_size` (observed: FalkorDB default 10000), and add the
missing `Field` label — designer controls, the code artifacts screen /
business-rule documents actually name.

## Evidence (live deployment @ localhost:6379, 2026-10-19)

- `GRAPH.CONFIG GET resultset_size` → `10000` (server-wide, silent cut).
- Per-label engine reads return **exactly 10000** for Function (real
  ~50437), Variable (real ~38619), Field (real ~27941); the reported
  `symbol_universe` 28992 = 10000 Function + 10000 Variable + 4237
  Property + 2732 Constant + 2011 Type + tail labels — i.e. ~78% of
  symbols never enter the match index.
- Concrete miss: doc token `grid_unit_col` (paragraph 21 of
  `specs__order_summary_business_rule_spec.md`)
  vs `Variable` node in `OrderSummaryForm.Designer.vb` + `Field`
  node in the `.Designer.cs` — token matches the symbol key, the nodes
  pass every filter, but sit beyond the 10k cut → 0 links.
- `Field` is absent from `CODE_SYMBOL_LABELS` entirely.

## Tasks

1. **Paginated reads** — `_read_labeled_symbols` loops deterministic
   keyset batches (`ORDER BY id(n) LIMIT $batch SKIP $offset`, batch
   ≈ 5000, stop at a short page) so the server's `resultset_size` never
   silently drops rows. No server config writes — the engine stays
   read-only (`GRAPH.CONFIG SET` is out of contract).
2. **Add `Field` to `CODE_SYMBOL_LABELS`** (`target_type="symbol"`,
   `file_path` identity column, same as `Variable`). Endpoint labels
   untouched. Update the LINK_CONTRACT.md symbol-label list and the
   Phase-01 contract section (full-universe guarantee, pagination
   invariant: rows read == server count per label; a short-fall is a
   loud warning, never a silent cut).
3. **Regression tests** (the live incident, as fixtures):
   - a fake graph whose `run()` serves >resultset_size rows per label →
     engine still indexes all of them (pagination proof);
   - a server capping at 10k mid-label → every row present exactly once,
     no duplicates across batch boundaries;
   - `Field` symbol `grid_unit_col` + the para-21 token pair →
     link produced (red today, green after);
   - universe counters: `symbol_universe` must equal the sum of per-label
     server counts (fixture with known sizes).
4. **Noise guard** — designer Field names can be generic (`label1`,
   `textBox2`); confirm the existing stoplist/minimum-length rules in
   `normalizers` still apply on the doc-token side (no engine-side
   filtering added in this phase; measure pair growth in Phase 04).

## Files

- Touched: `link-tiny/link_engine.py` (pagination + label list),
  `docs/LINK_CONTRACT.md` (labels + universe invariant)
- Tests: `link-tiny/tests/test_link_engine.py` (pagination + Field),
  `link-tiny/tests/test_sha_join.py` stays green

## Test scope

Mapped to acceptance "full-universe gate" + the cap regression.
Expected size **M**: ~8 tests (pagination correctness, cap edge, Field
inclusion, universe accounting) on fake graphs; no live DB required.
