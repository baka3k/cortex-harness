# Coverage telemetry checkpoint — Phase-3 gate input (plan 261019-1126)

Date: 2026-10-19. Command: `link_worker.py --project-id cortext` against the
live remote FalkorDB (`localhost:6379`, graphs `cortext` / `cortext_doc`).

## Results

| Metric | Value |
| --- | --- |
| Doc universe (Paragraph documents) | 502 |
| Symbol universe (Function/Class/… + endpoint labels) | 9,655 |
| Docs with ≥1 active link | 414 (**82.47%**) |
| Symbols referenced by ≥1 link | 2,115 (**21.91%**) |
| Active links | 12,882 |
| Provenance distribution | `exact-name: 12882` (100%) |
| First full build wall time | 7.8 s |
| Second run (delta) | 0 new, 12,882 unchanged, 0 staled (14.9 s incl. baseline load) |

Environment notes captured during the run:

1. **Remote backend fix.** The worker must pass the active config mapping
   into `resolve_storage` — without it a `storage_backend: remote` project
   silently resolved to the local embedded instance (empty store).
2. **redis-py 8 `CLIENT MAINT_NOTIFICATIONS` incompatibility.** The probe
   fails against the local OSS/compat FalkorDB server and its broken
   handshake poisons every client created after a `close()` in the same
   process. `read_only_opener._falkordb_remote_client` now builds its
   connection pool with `MaintNotificationsConfig(enabled=False)`.
3. **Match index.** Per-token linear scans over ~9.7k symbols made full
   builds unbounded; `normalizers.build_match_index` +
   `match_index_lookup` (identical semantics, dict lookup) brought the
   full build to seconds.

## Reading (Phase-3 fuzzy gate input, not a decision)

- **Doc-side coverage is already high (82%)** — most documents mention at
  least one real identifier, so the exact-name pass connects the doc graph
  to code broadly. The sparse dimension is **symbol coverage (22%)**: most
  code symbols are never named in prose.
- Implication for the Phase-3 gate: fuzzy/semantic matching would mostly
  add *symbol-side* recall (linking unmentioned symbols to related docs),
  not doc-side coverage. Any Phase-3 design should weight precision risk
  (R3 noise) against that specific gap, and should re-read
  `state.json.coverage` from this file's command rather than trusting
  these static numbers.
