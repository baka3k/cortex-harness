from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _read_config(config_path: str) -> Dict[str, Any]:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def load_harness_config(config_path: str) -> None:
    """Load code.env + doc.env from a harness dev.json and populate env vars.

    Per Phase 06 of the unified ingest/query contract plan, the loader is
    provider-neutral: it now reads both ``code.env`` and ``doc.env`` and
    stamps FalkorDB variables alongside the legacy Neo4j ones. Existing env
    vars take precedence over config values so callers can override at the
    shell level.
    """
    cfg = _read_config(config_path)
    if not cfg:
        return

    code_env = dict(cfg.get("code", {}).get("env") or {})
    doc_env = dict(cfg.get("doc", {}).get("env") or {})

    # Neo4j credentials — set only when the value is provided.
    for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASS", "NEO4J_DB"):
        if key in code_env and key not in os.environ:
            os.environ[key] = str(code_env[key])

    # FalkorDB graph/database — preferred over NEO4J_DB when both are set.
    for key in ("FALKORDB_GRAPH", "FALKORDB_DATABASE"):
        value = code_env.get(key) or doc_env.get(key)
        if value and key not in os.environ:
            os.environ[key] = str(value)

    # Resolve one canonical local-storage overlay for all child processes.
    from cortex_harness.storage import resolve_storage, storage_overlay

    local_keys = {
        key: value
        for key, value in {**doc_env, **code_env}.items()
        if key
        in {
            "CORTEX_DATA_HOME",
            "CORTEX_STORAGE_INSTANCE",
            "CORTEX_CODE_STORAGE_OWNER",
            "CORTEX_DOC_STORAGE_OWNER",
            "QDRANT_PATH",
            "QDRANT_CODE_PATH",
            "QDRANT_DOC_PATH",
            "FALKORDB_PATH",
            "FALKORDB_CODE_PATH",
            "FALKORDB_DOC_PATH",
        }
    }
    config_file = Path(config_path).resolve()
    project_root = (
        config_file.parents[2]
        if config_file.parent.name == "config"
        and config_file.parent.parent.name == ".cortext-harness"
        else config_file.parent
    )
    resolved = resolve_storage(project_root, config=local_keys)
    for key, value in storage_overlay(resolved, owner="code").items():
        os.environ.setdefault(key, value)

    # Qdrant collection defaults — code side reads code.env, doc side reads
    # doc.env, both fall back to ``project.code`` then the naming rule.
    project_code = (cfg.get("project") or {}).get("code")
    if "QDRANT_COLLECTION" not in os.environ:
        collection = (
            code_env.get("QDRANT_COLLECTION")
            or (project_code if project_code else None)
        )
        if collection:
            os.environ["QDRANT_COLLECTION"] = str(collection)
    if "QDRANT_COLLECTION_DOC" not in os.environ:
        doc_collection = (
            doc_env.get("QDRANT_COLLECTION")
            or (f"{project_code}_doc" if project_code else None)
        )
        if doc_collection:
            os.environ["QDRANT_COLLECTION_DOC"] = str(doc_collection)
    if "QDRANT_COLLECTION_CODE" not in os.environ and "QDRANT_COLLECTION" in os.environ:
        os.environ["QDRANT_COLLECTION_CODE"] = os.environ["QDRANT_COLLECTION"]

    # Embedding model + device + batch — apply to whichever env side
    # declared them. Both code and doc servers consume these.
    merged_env: Dict[str, Any] = {}
    merged_env.update(doc_env)
    merged_env.update(code_env)
    if "CODE_EMBEDDING_MODEL" not in os.environ and "EMBEDDING_MODEL" in merged_env:
        os.environ["CODE_EMBEDDING_MODEL"] = str(merged_env["EMBEDDING_MODEL"])
    if "EMBED_MODEL" not in os.environ and "EMBEDDING_MODEL" in merged_env:
        os.environ["EMBED_MODEL"] = str(merged_env["EMBEDDING_MODEL"])
    if "EMBED_DEVICE" not in os.environ and "device" in merged_env:
        os.environ["EMBED_DEVICE"] = str(merged_env["device"])
    if "EMBED_BATCH_SIZE" not in os.environ and "BATCH_SIZE" in merged_env:
        os.environ["EMBED_BATCH_SIZE"] = str(merged_env["BATCH_SIZE"])
    if "MAX_EMBED_CHARS" not in os.environ and "MAX_EMBED_CHARS" in merged_env:
        os.environ["MAX_EMBED_CHARS"] = str(merged_env["MAX_EMBED_CHARS"])

    # GRAPH_PROVIDER — first non-empty wins.
    provider = (
        code_env.get("GRAPH_PROVIDER")
        or doc_env.get("GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
    )
    if provider and "GRAPH_PROVIDER" not in os.environ:
        os.environ["GRAPH_PROVIDER"] = str(provider)


def load_harness_targets(
    config_path: str, project_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return a dict with both ``code`` and ``doc`` target names for the
    configured project. Used by launchers that need byte-identical env on
    both servers.

    When ``project_id`` is given, the targets are resolved through the
    shared :mod:`project_registry`. When omitted, the registry is consulted
    against every project file in the directory. Returns ``None`` when no
    project can be resolved.
    """
    cfg = _read_config(config_path)
    if not cfg:
        return None
    raw_project_id = project_id or (cfg.get("project") or {}).get("code")
    if not raw_project_id:
        return None
    try:
        from tools.common.project_registry import resolve_project_targets

        targets = resolve_project_targets(raw_project_id)
    except Exception:
        return None
    return {
        "project_id": targets.project_id,
        "project_id_normalized": targets.project_id_normalized,
        "code_graph": targets.code_graph,
        "code_qdrant_collection": targets.code_qdrant_collection,
        "doc_graph": targets.doc_graph,
        "doc_qdrant_collection": targets.doc_qdrant_collection,
    }
