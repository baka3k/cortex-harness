# Phase 04: Error Reporting & Tracing

## Context

`_run_tool` catches `MCPError` and generic `Exception`, then prints only
`str(exc)` — one line, no context. When an MCP tool fails, the operator must
open server logs to understand why. This phase upgrades error reporting so
the failure detail is visible inline (single-run) and structured in the log
(run-all), making "trace MCP" a single glance instead of a log dive.

This phase is co-designed with Phase 03 (run-all consumes the same error
capture), but the single-run upgrade is independently valuable.

## Requirements

- Introduce a `_ToolRunResult` dataclass capturing everything needed for
  both inline display and log serialization:
  ```python
  @dataclass
  class _ToolRunResult:
      tool: str
      status: str            # "pass" | "empty" | "fail"
      elapsed: float
      payload: Dict[str, Any]
      result: Any = None     # on pass/empty
      error: Optional[str] = None
      traceback: Optional[str] = None
      response_snippet: Optional[str] = None  # first 500 chars of raw response on fail
  ```
- Refactor the call path into a single `_execute_call(client, tool_name,
  payload) -> _ToolRunResult` that both single-run and run-all use. It:
  1. Records `t0`.
  2. Calls `client.call_tool(...)` inside `try/except`.
  3. On success, classifies `PASS` vs `EMPTY` (Phase 03 heuristic).
  4. On `MCPError`/`Exception`, captures `error = str(exc)`,
     `traceback = traceback.format_exc()`, status `FAIL`.
  5. Records `elapsed = time.time() - t0`.
  6. Returns the result.
- **Single-run** (`_run_tool` replacement): after `_execute_call`, if `FAIL`,
  print a failure block:
  ```
  ──────────────────────────────────────────────────────────────
  FAIL  get_symbol  (0.12s)
  ──────────────────────────────────────────────────────────────
  payload: {"node_id": "YOUR_NODE_ID", "project_id": "hyper_graph"}
  error:   MCPError: HTTP 400: {"error":"node not found"}
  trace:
    Traceback (most recent call last):
      File "mcp_tester.py", line ..., in _execute_call
        result = client.call_tool(tool_name, payload)
      File "mcp_client.py", line 112, in call_tool
        ...
      mcp_client.MCPError: HTTP 400: ...
  ──────────────────────────────────────────────────────────────
  ```
  On `PASS`/`EMPTY`, print the result as today (existing `_pprint` path).
- **Run-all** (Phase 03): the summary table + per-failure detail block reuse
  the same `_ToolRunResult` fields. The log file serializes the dataclass
  directly (with `dataclasses.asdict`).
- `response_snippet`: on failure, if the raw HTTP response is accessible
  (extend `MCPClient.call_tool` to attach `.raw_text` or return it on error),
  capture the first 500 chars. If not accessible without a larger client
  refactor, skip this field (the `error` string usually carries the server
  message already). This is a best-effort enhancement, not a blocker.
- Color coding (existing `_USE_COLOUR`): `PASS` green, `EMPTY` yellow,
  `FAIL` red, `SKIP` dim. Keeps the summary scannable.

## Implementation notes

- `import traceback` at the top of `mcp_tester.py` (stdlib).
- `_ToolRunResult` uses `@dataclass` (Python 3.7+, already required).
- Keep the existing post-run actions (`s` save result) for single-run `PASS`;
  failures offer `r` retry / `Enter` continue instead.
- Do not change `MCPClient`'s public API unless `response_snippet` is trivial.
  The `error` string from `MCPError` already carries the server message in
  most cases — that is the primary trace signal.
- The traceback capture (`traceback.format_exc()`) is cheap and always
  available; no dependency on the server cooperating.

## Related Files

- `code-tiny/testtool/mcp_tester.py` (add `_ToolRunResult`, `_execute_call`,
  refactor `_run_tool`, summary/detail rendering)
- `code-tiny/testtool/mcp_client.py` (optional: expose raw response text on
  error for `response_snippet` — best effort only)

## Todo

- [ ] Add `_ToolRunResult` dataclass.
- [ ] Add `_execute_call(client, tool_name, payload) -> _ToolRunResult`.
- [ ] Refactor `_run_tool` to use `_execute_call` + render failure block.
- [ ] Wire `_ToolRunResult` into run-all summary + log serialization.
- [ ] Add color-coded status labels (PASS green / EMPTY yellow / FAIL red).
- [ ] Smoke-test: force a failure (bad `node_id`) and confirm the inline
      block shows payload + error + trace.

## Success Criteria

- A single-run failure prints the payload, error message, and full traceback
  inline.
- A run-all failure produces the same detail in the summary block and the
  saved log file.
- The `error` string carries the server's message verbatim so the operator
  can trace the MCP failure without opening server logs.
- `PASS`/`EMPTY`/`FAIL` are color-coded and scannable in the summary.
