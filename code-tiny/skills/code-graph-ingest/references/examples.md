# Command templates (Rust analyzer binaries — phase-08 cutover)

The Python analyzer entries (`python tools/<lang>/<lang>_analyzer.py …`)
were retired at the phase-08 cutover. Use the corresponding Rust binary
in `rust/target/release/` directly, or invoke the orchestrator
(`incremental_sync.py` / `cortex-sync`) for the managed path.

## Dry run (any language)
```bash
./rust/target/release/analyzer-<lang> --root /path/to/src --dry-run
```

## Kotlin
```bash
./rust/target/release/analyzer-kotlin \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379 \
  --falkordb-graph my-project
```

## Java
```bash
./rust/target/release/analyzer-java \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## TypeScript
```bash
./rust/target/release/analyzer-ts \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## JavaScript
```bash
./rust/target/release/analyzer-js \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## PHP
```bash
./rust/target/release/analyzer-php \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## SQL
```bash
./rust/target/release/analyzer-sql \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## PL/SQL
```bash
./rust/target/release/analyzer-plsql \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## C#
```bash
./rust/target/release/analyzer-csharp \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379
```

## C/C++
```bash
./rust/target/release/analyzer-cplus \
  --root /path/to/src \
  --falkordb-uri falkor://localhost:6379 \
  --repo my-project/project \
  --parse-quality report
```

## Orchestrator (managed path)
```bash
CORTEX_RUST_ANALYZER=unset ./cortex-harness/dev.sh sync code
# or, equivalent:
.venv/bin/python code-tiny/tools/sync/incremental_sync.py --root /path/to/src sync code
```

The orchestrator honors `CORTEX_RUST_ANALYZER` (`unset` → Rust auto-flip,
`rust` → Rust with hard error if missing, anything else → loud "retired"
error per the phase-08 cutover).