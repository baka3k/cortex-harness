# Phase 01 — Scaffold link-tiny/ + registry facade + normalizers

## Goal

Create the `link-tiny/` tree with its isolation rules written down
(`CLAUDE.md`), and the two pure-logic foundations: project resolution
(delegating to the code-side registry) and identifier normalization. No
graph access, no I/O, no MCP yet.

## Tasks

1. Create the tree: `link-tiny/` with `CLAUDE.md`, `README.md`,
   `requirements.txt` (fastmcp only), `tests/`.
2. `CLAUDE.md` hard rules (mirrors plan invariants): engine never imports
   the server module and vice-versa; no tree outside link-tiny may import
   link-tiny; links are append-only; every record carries provenance+score;
   identity is `(path, name, lines, source_sha)` — never `_graph_id`.
3. `registry_facade.py` — copy the delegation pattern of
   `doc-tiny/project_contract.py` (digest: exact import mechanism, see
   research-digest.md §7): resolve `project_id` → `{code_graph, doc_graph,
   code_qdrant, doc_qdrant, parser_type, provider}`. The facade keeps
   link-tiny runnable standalone while name rules stay owned by
   `code-tiny/tools/common/project_registry.py`.
4. `normalizers.py` — pure functions: `casefold_name`, 
   `qualified_segments("a.b.C")` → `["a.b.C", "b.C", "C"]`,
   `extract_identifier_tokens(text)` (identifier-like tokens from prose:
   snake_case / camelCase / dotted paths / route fragments), 
   `match_candidates(doc_token, code_symbols) → ranked pairs`.
5. `pyproject.toml` / packaging decision: follow how code-tiny and doc-tiny
   are packaged today (digest §10) — likely no separate package in v1; the
   tree runs via `python -m` / direct path like the existing servers.

## Files

- New: `link-tiny/CLAUDE.md`, `link-tiny/README.md`,
  `link-tiny/requirements.txt`, `link-tiny/registry_facade.py`,
  `link-tiny/normalizers.py`, `link-tiny/tests/test_normalizers.py`
- Existing files touched: none.

## Boundaries (do NOT)

- Do not touch `code-tiny/tools/common/project_registry.py` (consume only).
- Do not add link-tiny to root `pyproject.toml` workspaces unless the
  digest shows the repo already packages trees that way.

## Test scope

Mapped acceptance criteria + size: **S** (~5 test functions, within budget).

| Behavior (map) | Test |
| --- | --- |
| Facade returns targets identical to `resolve_project_targets` for a registry fixture (typical) | `test_facade_matches_code_registry` |
| Facade works when registry file missing → env+defaults fallback (boundary) | `test_facade_env_fallback` |
| `qualified_segments`: dotted / plain / empty (typical, boundary, invalid) | `test_qualified_segments` (parameterized) |
| `extract_identifier_tokens`: snake/camel/dotted/route from prose; none in plain prose (typical + boundary) | `test_identifier_tokens` (parameterized) |
