---
kind: logging_system
name: Standard Library logging with per-process file+stdout sinks
category: logging_system
scope:
    - '**'
source_files:
    - code-tiny/mcp/fastmcp_server.py
    - doc-tiny/entity_extractors.py
---

The repository uses Python's built-in `logging` module exclusively — there is no third-party logger (loguru, structlog, etc.) and no centralized logging configuration package. Logging is configured ad-hoc at the entry point of each long-running process.

**Entry-point configuration**
- `code-tiny/mcp/fastmcp_server.py::main()` calls `_logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")` and installs two handlers: a `FileHandler` writing to `mcp_server.log` next to the server script and a `StreamHandler` for stdout. This is the only place in the codebase that configures root-level formatting and sinks.
- `doc-tiny/entity_extractors.py::_setup_llm_debug_logging()` conditionally adds a second `FileHandler` (to `logs/langextract_raw.log`) and sets the root logger to `DEBUG` when `LLM_DEBUG=1`. It guards against double-configuration via a `_llm_debug_configured` flag.

**Logger naming convention**
Every module creates its own logger via `logging.getLogger("project_call_graph.mcp.<area>")` (e.g. `server`, `graph`, `symbols`, `explore`). The name prefix `project_call_graph.mcp` is consistent across all MCP analyzers (android, cplus, java, …) so log lines can be filtered by area.

**Log levels**
- Default level is `INFO` at the root handler.
- The LLM debug path raises the root logger to `DEBUG` only when `LLM_DEBUG` is truthy; otherwise the extra handler is never attached.
- No module overrides the level on its own logger instance — they rely on the root handler level.

**Structured fields / enrichment**
There is no structured-log framework and no custom formatter that injects correlation ids, session ids, or request ids. Log records are plain text produced by the default formatter. Some callers do pass dict-like payloads as part of the message string (e.g. `logger.info("Endpoint: %s", endpoint)`), but these are not machine-parseable structured logs.

**Sinks and routing**
- File sink: one file per process (`mcp_server.log` for the unified MCP server; `logs/langextract_raw.log` for the doc-tiny extractor when debug is enabled).
- Console sink: stdout via `StreamHandler`.
- No rotation, no syslog/HTTP/queue handlers, no separate error-only sink.

**What is NOT present**
- No `logging.config.dictConfig` or external YAML/JSON config file.
- No shared `logging/__init__.py` or `config.py` that centralizes setup.
- No log-level CLI flags beyond the implicit INFO default.
- No test harness that captures or asserts on log output (tests use `unittest` without `caplog` usage found in the grep).

**Developer conventions observed**
- Import `logging` at the top of each module.
- Create a module-level `logger = logging.getLogger(__name__)` or a stable dotted name under `project_call_graph.mcp.*`.
- Use `logger.info(...)` / `logger.exception(...)` rather than `print()` for operational messages inside analyzers and services.
- Do not call `basicConfig` from library modules — only the process entry point should configure handlers.