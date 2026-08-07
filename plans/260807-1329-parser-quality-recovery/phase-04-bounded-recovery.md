# Phase 04: Isolated bounded recovery and candidate selection

## Context

The current libclang fallback runs in-process after Tree-sitter extraction and is
gated only by high explicit-error thresholds. Broadly lowering that threshold
would duplicate work across thousands of files and expose the ingestion process
to native crashes, memory spikes, unsafe compile flags, and path escape.

## Requirements

- Build a persistent priority queue from Phase 03 quality records.
- Run expensive candidates outside the ingestion process.
- Enforce per-file and run-wide resource and retry budgets.
- Parse but never execute compile commands; allowlist safe flags and roots.
- Compare candidates using the common structural/semantic tuple.
- Preserve the first-pass payload when no candidate strictly improves it.

## Architecture

`parse_recovery.py` owns prioritization, budgets, state, and candidate comparison.
`clang_worker.py` is a narrow subprocess entry point that accepts a validated JSON
request and returns a schema-checked JSON result. It has no database clients or
network responsibility. `clang_parser.py` becomes a pure candidate producer over
prevalidated compile context.

## Related files

- `code-tiny/tools/cplus/parse_recovery.py` (new)
- `code-tiny/tools/cplus/clang_worker.py` (new)
- `code-tiny/tools/cplus/clang_parser.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/bootstrap_compile_commands.py`
- `requirements.txt`
- `code-tiny/requirements.txt`
- `tests/test_cplus_clang_worker.py` (new)
- `tests/test_cplus_parse_recovery.py`

## Implementation steps

1. Define queue identity, priority, resumable state, budget counters, circuit
   breakers, and terminal outcomes.
2. Prioritize structural damage, low semantic yield, central headers, lossy
   decoding, and usable compile context over raw error count.
3. Load and index `compile_commands.json` once with file/entry/token caps and
   canonical path validation.
4. Reject executable compile-database discovery for recovery runs: never invoke
   CMake, Make, Bear, `compiledb`, compiler drivers, or repository hooks to create
   compile context.
5. Allowlist language, standard, define, and approved include flags; reject
   response, plugin/load, output, module-cache, PCH, and external-path flags.
6. Derive header context from one bounded representative includer translation
   unit; record when free mode is used.
7. Execute each libclang candidate in a disposable subprocess with timeout,
   memory/CPU caps where supported, no network, bounded temporary storage, and
   process-tree cleanup.
8. Validate worker output and choose the whole-file winner only on strict quality
   or semantic-yield improvement.
9. Enforce run and cohort budgets and expose all stop reasons in the artifact.

## Todo

- [x] Add persistent prioritized recovery queue.
- [x] Add compile database indexing and flag/path filtering.
- [x] Add isolated libclang worker protocol and limits.
- [x] Add deterministic candidate comparison and outcome caching.
- [x] Add crash, timeout, malformed-input, symlink, and budget tests.

## Risks

Platform resource-limit APIs differ. Implement the strongest supported controls,
fail closed on invalid worker configuration, and keep `report` mode independent
of recovery availability.

## Success criteria

A malicious or crashing candidate affects only its file; every budget is enforced
and reported; compile commands are never executed; unsafe flags/paths are
rejected; a fallback never replaces a better recovered Tree-sitter payload.
