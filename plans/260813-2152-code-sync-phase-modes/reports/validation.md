# Code Sync Phase Modes Validation

## Result

The graph/topology and embedding phases are independently selectable for full scans. The default `both` mode runs primary graph writers, framework graph overlays, and `project_topology` before starting graph-disabled embedding subprocesses.

## Verified commands

- `python -m unittest` across the focused CLI, phase-mode, vector, topology, bootstrap, worktree, non-Git, submodule, framework-routing, and graph-setup modules: **44 tests passed**.
- `python -m py_compile` for changed Python source/tests: passed.
- `git diff --check`: passed.
- `dev sync code --help`: exposes `--sync-mode [both|graph|embedding]`.
- HyperPack graph-only and embedding-only dry runs: both resolved project `hyperpack`, its local FalkorDB path, and the requested mode correctly.

## Isolation evidence

- The graph pass supplies explicit empty Qdrant sentinels so analyzer-side `dev.json` loading cannot restore vector storage.
- The embedding pass supplies `CORTEX_DISABLE_GRAPH=1`, removes graph credentials/paths, and creates no graph journals.
- A simulated embedding failure after topology leaves topology status visible as `success`, vector status as `failed`, and returns non-zero.
- Specialized modes do not advance the shared incremental baseline.

## Broader suite limitation

`unittest discover` ran 435 tests and reported 20 failures plus 28 errors outside the focused scope. The failures are concentrated in missing optional/runtime dependencies and existing environment-sensitive suites: pytest-style module imports, unavailable `dotnet`, COBOL/Dart/Flutter parser fixtures, a real clang worker, active embedded FalkorDB ownership, and framework fixture baselines. No failure named the new phase-mode tests; all focused adjacent suites passed.

