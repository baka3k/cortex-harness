# sync-plane golden fixtures (phase-01, plan 260915-2300)

Captured 2026-09-16 from the live Python-leg (`dev sync code` → delegation →
`incremental_sync.py` with Rust analyzer children) on a scratch instance.

## Re-create procedure

```bash
# 1. scratch project from fixture (git baseline commit required for incremental legs)
rm -rf /tmp/sp1-golden /tmp/sp1-golden-data && mkdir -p /tmp/sp1-golden
cp -R tests/fixtures/web-overlays/fastapi_django/. /tmp/sp1-golden/
cd /tmp/sp1-golden && git init -q && git add -A \
  && git -c user.email=sp1@example.com -c user.name=sp1 commit -qm baseline

# 2. scratch config (mirror of the user's ladybug shape, scratch instance)
mkdir -p .cortext-harness/config && cat > .cortext-harness/config/dev.json <<'EOF'
{
  "active": true,
  "project": {"code": "sp1golden", "name": "sp1golden"},
  "storage_backend": "local",
  "code": {
    "env": {
      "CORTEX_STORAGE_INSTANCE": "sp1-golden",
      "CORTEX_DATA_HOME": "/tmp/sp1-golden-data",
      "GRAPH_PROVIDER": "ladybug",
      "CODE_GRAPH_PROVIDER": "ladybug",
      "LADYBUG_GRAPH": "sp1golden"
    },
    "source": {"projects": [{"git": "", "folder": ["."]}]}
  }
}
EOF

# 3. full sync (delegation fires; Python orchestrator + Rust children)
cd <repo> && rust/target/release/cortex-dev sync code \
  --project-dir /tmp/sp1-golden --full-scan all
```

Artifacts then live under `/tmp/sp1-golden/.cache/` (summary, state,
manifests) and
`/tmp/sp1-golden-data/v1/instances/sp1-golden/ladybug/code/code.lbug/hyper_graph`
(store file — copy only after the run exits; no `.wal` remains).

## Files

- `ladybug/summary-full-run1.json` — full-scan summary as-found. NOTE: this
  run carries two PRE-EXISTING ladybug store dialect bugs (see
  `plans/260915-2300-sync-plane-rust-cutover/reports/phase01-probes.md` §1):
  `SET += row` unsupported by ladybug 0.20.4 and the `ProjectModule(id)`
  ART/PK index collision. `outcome=failed` is the faithful as-found record;
  a HEALTHY golden is re-captured after the phase-02 store dialect fixes and
  is the parity reference for phase-04.
- `ladybug/state-after-full-run1.json` — sync state v2 (dirty=true as-found).
- `ladybug/manifests-run1/` — per-parser changed/deleted manifests
  (`<snapshot12>_<pid>_<hex8>` token is volatile — masked in parity).
- `journal/cplus-legacy.sqlite3` — REAL legacy graph-write journal (39
  batches: 33 done / 4 pending / 2 blocked; fingerprint
  `8613fc08894a26c2…` == current canonical schema) — byte-compat replay
  fixture for the phase-03 Rust drain. Heavy copies (store dump, journal
  working copy) live in `<repo>/.cache/sp1-golden/`.

## Mask list (phase-04 harness)

Mask: `run_id, correlation_id, started_at, finished_at, duration_seconds,
wait_seconds, updated_at, detector_evidence` + per-run tokens (cache dir,
lock path/owner, manifest/summary/artifact paths, `*_output_dir`, manifest
filename pid/hex tokens).

Never mask: graph name (assert == project_id-derived), `summary.backend`
(assert per leg), `status`/`outcome`/`error`.
