from __future__ import annotations

import json
import os


def load_harness_config(config_path: str) -> None:
    """Load code.env from a harness dev.json and populate env vars (existing vars take precedence)."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    env = cfg.get("code", {}).get("env", {})
    for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASS", "NEO4J_DB"):
        if key in env and key not in os.environ:
            os.environ[key] = str(env[key])
    if "QDRANT_URL" not in os.environ:
        host = env.get("QDRANT_HOST", "")
        port = env.get("QDRANT_PORT", "")
        if host and port:
            os.environ["QDRANT_URL"] = f"http://{host}:{port}"
    if "CODE_EMBEDDING_MODEL" not in os.environ and "EMBEDDING_MODEL" in env:
        os.environ["CODE_EMBEDDING_MODEL"] = str(env["EMBEDDING_MODEL"])
    if "EMBED_DEVICE" not in os.environ and "device" in env:
        os.environ["EMBED_DEVICE"] = str(env["device"])
    if "EMBED_BATCH_SIZE" not in os.environ and "BATCH_SIZE" in env:
        os.environ["EMBED_BATCH_SIZE"] = str(env["BATCH_SIZE"])
    if "MAX_EMBED_CHARS" not in os.environ and "MAX_EMBED_CHARS" in env:
        os.environ["MAX_EMBED_CHARS"] = str(env["MAX_EMBED_CHARS"])
