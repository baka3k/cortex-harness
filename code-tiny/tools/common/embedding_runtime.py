"""Local-first Hugging Face model loading with auditable remote fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlsplit

import requests
from huggingface_hub import configure_http_backend, snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError


_AUDIT_CONFIGURED = False


def _missing_files(snapshot: str) -> List[str]:
    root = Path(snapshot)
    missing: List[str] = []
    if not (root / "config.json").is_file():
        missing.append("config.json")
    if not any(root.glob("*.safetensors")) and not any(root.glob("pytorch_model*.bin")):
        missing.append("model weights (*.safetensors or pytorch_model*.bin)")
    if not any((root / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "spiece.model", "vocab.txt", "vocab.json")):
        missing.append("tokenizer files")
    return missing


def _purpose(method: str, path: str) -> str:
    if method.upper() == "HEAD" and "/resolve/" in path:
        return "verify cached model artifact metadata"
    if method.upper() == "GET" and "/resolve/" in path:
        return "download missing model artifact"
    if "/api/telemetry/" in path:
        return "Hugging Face telemetry"
    return "Hugging Face Hub request"


class _AuditSession(requests.Session):
    def request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        target = urlsplit(str(url))
        purpose = _purpose(str(method), target.path)
        print("[network][outbound][start] purpose=%s method=%s destination=%s://%s%s timeout=%s" % (
            purpose, str(method).upper(), target.scheme, target.netloc, target.path, kwargs.get("timeout")
        ))
        try:
            response = super().request(method, url, *args, **kwargs)
        except Exception as exc:
            print("[network][outbound][failed] purpose=%s method=%s destination=%s://%s%s error=%s" % (
                purpose, str(method).upper(), target.scheme, target.netloc, target.path, exc
            ))
            raise
        for hop in list(getattr(response, "history", ()) or ()) + [response]:
            completed = urlsplit(str(getattr(hop, "url", url)))
            print("[network][outbound][complete] purpose=%s method=%s destination=%s://%s%s status=%s" % (
                purpose, str(method).upper(), completed.scheme, completed.netloc, completed.path,
                getattr(hop, "status_code", "unknown"),
            ))
        return response


def _enable_audit() -> None:
    global _AUDIT_CONFIGURED
    if not _AUDIT_CONFIGURED:
        configure_http_backend(backend_factory=_AuditSession)
        _AUDIT_CONFIGURED = True


def resolve_embedding_cache(model_source: str) -> Tuple[str, bool]:
    """Use a complete local snapshot; audit every request only when fallback is needed."""

    if os.path.isdir(model_source):
        missing = _missing_files(model_source)
        if missing:
            raise ValueError("Local embedding model directory is incomplete: %s (%s)" % (model_source, ", ".join(missing)))
        print(f"[model] local embedding directory complete; network disabled: {model_source}")
        return model_source, True
    try:
        snapshot = snapshot_download(model_source, local_files_only=True)
    except LocalEntryNotFoundError:
        print("[model] local embedding cache miss; remote fallback enabled model=%s purpose=obtain missing model artifacts" % model_source)
        _enable_audit()
        return model_source, False
    missing = _missing_files(snapshot)
    if missing:
        print("[model] local embedding cache incomplete; remote fallback enabled model=%s missing=%s" % (model_source, ", ".join(missing)))
        _enable_audit()
        return model_source, False
    print(f"[model] local embedding cache complete; network disabled: {snapshot}")
    return snapshot, True
