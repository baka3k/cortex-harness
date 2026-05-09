"""tools/ts — TypeScript / React-Native / Next.js code analysis toolkit.

This package exposes the same public symbols as ``ts_analyzer.py`` so that
existing callers (``ts_backend_analyzer``, tests, MCP tools) continue to work
without modification.

New callers should import from the sub-packages directly:

    from tools.ts.agents import GraphAgent, ParserAgent
    from tools.ts.pipeline import FrontendPipeline
"""

# ── Backward-compatible re-exports from ts_analyzer ───────────────────────────
# Import everything that ts_backend_analyzer and other callers rely on.
from tools.ts.ts_analyzer import (  # noqa: F401
    # Parser / AST helpers
    _get_ts_parser,
    _parse_file,
    _node_text,
    _find_nodes_by_type,
    _line_from_byte,
    _node_snippet,
    _extract_leading_comment,
    _first_identifier,
    _extract_name_field,
    _normalize_ws,
    # Import / export collection
    _collect_imports,
    _collect_exports,
    # Import graph
    _collect_ts_import_graph,
    _expand_impacted_files_by_imports,
    # Payload helpers
    _load_or_parse_payload,
    # Qdrant / embeddings
    QdrantWriter,
    CodeEmbedder,
    _func_qdrant_payload,
    _stable_point_id,
    # Dataclasses
    FileDef,
    # Constants
    _SCAN_SKIP_DIRS,
    _PARSE_CACHE_VERSION,
    # Entry-points
    build_call_graph,
    parse_ts_file,
    parse_args,
    main,
)
