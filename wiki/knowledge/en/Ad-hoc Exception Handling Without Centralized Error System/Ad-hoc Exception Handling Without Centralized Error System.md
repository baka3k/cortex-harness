---
kind: error_handling
name: Ad-hoc Exception Handling Without Centralized Error System
category: error_handling
scope:
    - '**'
---

This repository does not implement a centralized error handling system. There are no dedicated error type classes, sentinel errors, or shared exception hierarchy files anywhere in the codebase. Instead, error handling is scattered and ad-hoc across modules:

- **Standard library exceptions**: Most code catches `Exception`, `RuntimeError`, `json.JSONDecodeError`, `urllib.error.HTTPError`, and `ModuleNotFoundError` directly with bare `except` clauses.
- **No custom error types**: No `class *Error` definitions were found in any core module (analyzers, MCP server, harness scripts, installers).
- **No structured logging**: The grep search for `logging.error` / `logger.error` returned zero matches in the main analyzer and MCP code; the only logging hits appear in the `livingdoc/` utility scripts which use Python's standard `logging` module sparingly.
- **No middleware or wrapper layer**: The FastMCP server (`code-tiny/mcp/unified_mcp.py`) and individual analyzers propagate raw exceptions rather than wrapping them into domain-specific error objects.
- **Inconsistent patterns**: Some places swallow exceptions silently (`except Exception:`), others re-raise with minimal context, and some log at the call site without a unified format.

Consequently, there is no repository-wide convention for error codes, error propagation boundaries, or user-facing error messages. Errors bubble up as untyped Python exceptions, making cross-module error diagnosis and consistent user feedback difficult.