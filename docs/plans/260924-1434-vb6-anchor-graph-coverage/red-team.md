# Red-Team Review — 260924-1434-vb6-anchor-graph-coverage

- date: 2026-09-24 · lens: failure-modes + assumptions · reviewer: delegated spawn (role=reviewer)
- verdict: 2 Critical, 4 High, 5 Medium — **toàn bộ đã áp vào plan/phases** (bản plan hiện tại là bản post-fix)

## Disposition

| # | Severity | Finding (tóm tắt) | Fix áp vào |
|---|---|---|---|
| C1 | Critical | `Class` endpoint sai — form VB6 đi types lane `:Type` (`vb_analyzer_base.py:1118-1134`, pin `test_vb6_graph_contract.py:194-199`); typed-rel upsert MATCH `:Class` → 0 row → RuntimeError fail-closed | AD-01/AD-02 retype: HAS_CONTROL (Type,Control), INSTANTIATES (Function,Type); bỏ pair USES (Function,Class); P4 "form Class node" → Type node |
| C2 | Critical | `Control` không có write lane + chưa đăng ký id-index: `group_typed_relations` ValueError (`query_contract.py:27-31`), `write_all` không có generic node plane (`language_writer.py:2757-2836`); fingerprint change → journal `INCOMPATIBLE_SCHEMA` | P1 1.2 mới: manifest `_id_indexes` + `write_controls_full`; plan scope/M7 chấp nhận writer surgical + full resync |
| H1 | High | `HANDLES` đã tồn tại (Endpoint→Function, `ladybug_schema.py:394-396`, MCP đọc `framework_registry.py:84`) — assignment cũ là destructive overwrite + đảo chiều | Đổi sang rel mới `WIRED_TO`; cấm đụng HANDLES (P1 1.1) |
| H2 | High | `Variable.is_static` dead weight: thiếu `VariableDef.is_static` + `asdict_variable` + SET list `write_variables_full` (`language_writer.py:1631-1653`) | P1 1.4 end-to-end; M7 ghi rõ writer surgical |
| H3 | High | Planes mới bị `_hydrate_payload` drop silently (`vb_analyzer_base.py:320-337`) | P2 2.3: whitelist hydration + test bridge adapter→analyzer |
| H4 | High | Planes thiếu owner `proc` / `block_end_line` → cạnh P3/P4 underivable; nested With ambiguous | P2 2.2: mọi plane mang `proc`; with_targets có `block_end_line`; P3 3.3 resolve innermost block |
| M1 | Medium | Incremental resync mất cạnh mới (filter :1332-1349 chỉ calls/possible) | P4 4.1: mở rộng predicate source-or-target cho rel mới |
| M2 | Medium | Fallback `Global → Public Dim` lỗi grammar (`variableStmt` g4:600-601); GLOBAL là visibility token riêng (g4:824-828) | P2 S1: check `VisibilityEnum.GLOBAL`; rewrite `Global → Public` (cấm Public Dim) |
| M3 | Medium | MCP `visual_basic` profile không thấy label/rel mới (`framework_registry.py:614-616`) | Restate M2/M5 driver-level; MCP registration → Follow-ups |
| M4 | Medium | Row trỏ target thiếu abort cả batch (`_OPTIONAL_EXTERNAL_RELATION_TYPES` không chứa USES/WIRED_TO); pseudo-control `Form` không có Control node | P1 1.5 builders guarded target-in-batch; P4: lifecycle handler WIRED_TO Type node; STRIP_DESIGNER co về 0 rows |
| M5 | Medium | Pin đỏ có sẵn `test_vb6_engine_dispatch.py:38` (09-17-4 vs 09-18-1); worker-contract constant không pin thật; benchmark path sai | P1 1.6 đổi assert-equality; P2 sửa pin đỏ (exclusion pre-existing red); P4 4.3 chỉ `tests/benchmark_vb6_parse_quality.py` |

## Cleared (đã verify, không cần fix)

- Generic relations path: KHÔNG có rel-type whitelist — `write_relations_typed` group by (source,target,rel_type) MERGE upsert (`language_writer.py:786-791,1717+`); rel `properties` persist qua spill (`ladybug_driver.py:1065-1083`).
- LadybugDB migration additive an toàn: ALTER-heal node columns (:1467-1499), rel pairs (:1553-1587), pre-create tables (:1700-1712).
- `exported` P1 không cần bump cache: `is_private` hydrate sẵn 2 engine, writer đã SET.
- `symbol_id` `Module.Name/arity@rel`; New filter; `match_event_handlers` return int contained (1 caller).
- Corpus counts: ReDim Preserve 26, With 6, 0 Global/WithEvents, 27 frm/3 bas.
