# Phase 05 — link_mcp :8790 read-only facade

## Goal

The third MCP server: FastMCP app exposing `find_code_for_doc`,
`find_doc_for_code`, `trace_status`. Reads `links.jsonl` from disk, emits
the `cortex.mcp.tool-result` v2 envelope, scopes every call through the
registry, delegates content to `graph_mcp`/`mind_mcp` over HTTP. Never
writes; never imports the engine.

## Tasks

1. `mcp_server.py`:
   - FastMCP app + `main()` with `--host/--port/--path` mirroring the code
     server's launch signature (digest §5/§6); default port 8790. Tool
     registration copies the doc server's `_standard_tool` decorator
     pattern (`doc-tiny/mcp_graph_rag.py:1372-1433`).
   - `find_code_for_doc(project_id, doc_id|query, top_k, min_score)` —
     loads links (in-memory per project), filters status=active, returns
     ranked code refs. Envelope `data` carries refs + score + provenance +
     short snippet — not full doc bodies (R2). Snippets pass a
     **sensitive-terms filter implemented in link-tiny** (digest: `.sensitive-terms`
     has no Python consumer today — read the term list, casefold match,
     replace with `▮`).
   - `find_doc_for_code(project_id, symbol_id|path+name, ...)` — symmetric
     reverse lookup; accepts the code symbol identity
     (`file_path` + `name` / `qualified_name`, digest §2).
   - `trace_status(project_id)` — state.json summary: built_at, counts,
     coverage, stale count, and staleness verdict vs sync-state timestamps.
2. Envelope: **import `result_envelope_json` from `cortex_harness.mcp_contract`**
   (digest §5: single v2 producer; the harness package is shared ground, so
   this is not a sibling-tree import). No copied envelope code.
3. Content delegation: thin HTTP MCP client calls to `graph_mcp`/`mind_mcp`
   URLs (config keys `graph_mcp_url` / `mind_mcp_url`, the `dev.py:~4695`
   pattern). Degrade rule: upstream down → return links with
   `note: "content unavailable"`; links missing → empty data + hint
   `"run 'dev sync link'"`. Never raise to the caller for upstream
   failures (plan isolation contract).
4. Staleness annotation: if `state.json` older than available sync state →
   envelope message notes stale + command hint (scope challenge #3).
5. Registration (completes RT-4): `dev.py` `MCP_SERVICES` entry
   (~196–215) + `--server` Choice `{all, code, doc, link}` (`all` still =
   code+doc), `scripts/mcp-lifecycle.py` `SERVERS` entry + `link-tiny/mcp.sh`.
6. Tests follow repo style (digest §8): unittest/pytest with sys.path
   injection and **direct envelope calls** — no in-memory FastMCP client
   exists in this repo; the boot smoke test exercises the lifecycle script
   path.

## Files

- New: `link-tiny/mcp_server.py`, `link-tiny/tests/test_mcp_tools.py`
- Existing files touched: `cortex_harness/dev.py` — `link` entry in the
  server table (~lines 195–215) + `--server` Choice extended to
  `{all, code, doc, link}` with `all` still = code+doc (opt-in launch).

## Boundaries (do NOT)

- No write operations on any tool (facade is read-only by construction —
  no tool may accept a payload that mutates links).
- No direct graph/Qdrant access in the server process (disk links + HTTP
  delegation only).
- `all` must not start launching link_mcp by default in Phase 1.

## Test scope

Mapped acceptance criteria + size: **M** (~9 test functions; public API
contract — Always tier).

| Behavior (map) | Test |
| --- | --- |
| `find_code_for_doc` returns ranked active links for a fixture (typical) | `test_find_code_for_doc` |
| `find_doc_for_code` reverse lookup by symbol identity (typical) | `test_find_doc_for_code` |
| Stale links excluded from default results, visible via trace_status (rule) | `test_stale_excluded` |
| Envelope shape: ok/data/message/truncation present, contractVersion 2.0 (contract) | `test_envelope_v2` |
| Upstream server down → links + `content unavailable` note, ok=true (degrade) | `test_upstream_down_degrade` |
| No links yet → empty data + sync hint, not an error (degrade) | `test_no_links_hint` |
| Stale state → message annotation present (staleness rule) | `test_stale_annotation` |
| Truncated results carry truncation hint (boundary, top_k cap) | `test_truncation_hint` |
| `dev start --server link` boots :8790 and responds (e2e smoke) | `test_server_boots` |
