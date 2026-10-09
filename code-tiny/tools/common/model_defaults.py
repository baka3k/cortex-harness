"""Single source of the code-side default embedding model.

Every literal default in the code tree (dev.py launchers, MCP
``DEFAULT_MODEL`` chains, analyzer entrypoints, delegating analyzers)
resolves through this constant so a model swap is a one-line change plus a
re-index. Keep the doc-tiny twin
(``doc-tiny/embedding_utils.DEFAULT_DOC_EMBEDDING_MODEL``) at the same
value — the flat doc tree cannot import from here.
"""

DEFAULT_CODE_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"

__all__ = ["DEFAULT_CODE_EMBEDDING_MODEL"]
