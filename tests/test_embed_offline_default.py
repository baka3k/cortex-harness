"""Cached-model offline guarantee for the embedding runtime.

A complete HF snapshot must load with zero network traffic — no HEAD
pings to huggingface.co, no retry backoff — while a cache miss or a
partially-interrupted prefetch keeps the normal online fallback.
Regression coverage for the "huggingface.co unreachable / Retrying in
2s" storm observed on already-cached models.

Covers every load chokepoint:
- ``tools.common.embed_runtime`` (get_sentence_transformer + AutoModel branch)
- ``doc-tiny/embedding_utils.resolve_embedding_model`` (repo-id cache probe)
- a residual gate: no other module may construct ``SentenceTransformer``
  directly (all loads go through the offline-first runtime).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from huggingface_hub.errors import LocalEntryNotFoundError

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for _path in (str(CODE_TINY), str(DOC_TINY)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

from tools.common import embed_runtime  # noqa: E402
import embedding_utils as doc_embedding_utils  # noqa: E402

CACHED_MODEL = "example-org/test-embedder"
JINA = "jinaai/jina-embeddings-v3"

_ENV_VARS = (
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "EMBEDDING_LOCAL_ONLY",
    "EMBEDDING_MODEL_PATH",
    "EMBEDDING_MODEL",
)


def _make_snapshot(
    tmp: Path,
    *,
    config: bool = True,
    weights: bool = True,
    tokenizer: bool = True,
) -> str:
    snap = tmp / "snapshots" / "rev1"
    snap.mkdir(parents=True, exist_ok=True)
    if config:
        (snap / "config.json").write_text("{}")
    if weights:
        (snap / "model.safetensors").write_bytes(b"x")
    if tokenizer:
        (snap / "tokenizer.json").write_text("{}")
    return str(snap)


def _restore_env() -> None:
    """Undo the offline flips so tests stay order-independent."""
    for var in _ENV_VARS:
        os.environ.pop(var, None)
    try:
        import huggingface_hub.constants as hub_constants

        hub_constants.HF_HUB_OFFLINE = False
    except Exception:
        pass
    try:
        import transformers.utils.hub as transformers_hub

        transformers_hub._is_offline_mode = False
    except Exception:
        pass


class SnapshotCompletenessTests(unittest.TestCase):
    def test_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(
                embed_runtime._snapshot_complete(_make_snapshot(Path(tmp)))
            )

    def test_missing_weights_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp), weights=False)
            self.assertFalse(embed_runtime._snapshot_complete(snap))

    def test_missing_tokenizer_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp), tokenizer=False)
            self.assertFalse(embed_runtime._snapshot_complete(snap))

    def test_missing_config_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp), config=False)
            self.assertFalse(embed_runtime._snapshot_complete(snap))


class LocalFilesOnlyProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        _restore_env()

    def tearDown(self) -> None:
        _restore_env()

    def test_complete_cache_probes_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp))
            with patch("huggingface_hub.snapshot_download", return_value=snap):
                self.assertTrue(embed_runtime._st_local_files_only(CACHED_MODEL))

    def test_partial_cache_probes_false(self):
        # Interrupted prefetch (weights missing) → online fallback, so the
        # loader downloads the rest instead of dying mid-constructor.
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp), weights=False)
            with patch("huggingface_hub.snapshot_download", return_value=snap):
                self.assertFalse(embed_runtime._st_local_files_only(CACHED_MODEL))

    def test_cache_miss_probes_false(self):
        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=LocalEntryNotFoundError("miss"),
        ):
            self.assertFalse(embed_runtime._st_local_files_only(CACHED_MODEL))

    def test_local_directory_probes_true_without_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("huggingface_hub.snapshot_download") as download:
                self.assertTrue(embed_runtime._st_local_files_only(str(tmp)))
            download.assert_not_called()


class OfflineDefaultTests(unittest.TestCase):
    """``local_files_only`` omitted → the probe result reaches the loader."""

    def setUp(self) -> None:
        _restore_env()
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()
        _restore_env()

    def test_get_sentence_transformer_defaults_offline_when_cached(self):
        import sentence_transformers

        captured: dict = {}

        class _ST:
            def __init__(self, model_name, device=None, **kwargs):
                captured.update(kwargs)

        with patch.object(
            sentence_transformers, "SentenceTransformer", _ST
        ), patch.object(
            embed_runtime, "_st_local_files_only", return_value=True
        ) as probe:
            embed_runtime.get_sentence_transformer(CACHED_MODEL, device="cpu")
        probe.assert_called_once_with(CACHED_MODEL)
        self.assertIs(captured["local_files_only"], True)

    def test_get_sentence_transformer_stays_online_when_probe_false(self):
        import sentence_transformers

        captured: dict = {}

        class _ST:
            def __init__(self, model_name, device=None, **kwargs):
                captured.update(kwargs)

        with patch.object(
            sentence_transformers, "SentenceTransformer", _ST
        ), patch.object(embed_runtime, "_st_local_files_only", return_value=False):
            embed_runtime.get_sentence_transformer(CACHED_MODEL, device="cpu")
        self.assertIs(captured["local_files_only"], False)

    def test_explicit_local_files_only_wins(self):
        import sentence_transformers

        captured: dict = {}

        class _ST:
            def __init__(self, model_name, device=None, **kwargs):
                captured.update(kwargs)

        with patch.object(
            sentence_transformers, "SentenceTransformer", _ST
        ), patch.object(
            embed_runtime, "_st_local_files_only", return_value=True
        ) as probe:
            embed_runtime.get_sentence_transformer(
                CACHED_MODEL, device="cpu", local_files_only=False
            )
        probe.assert_not_called()
        self.assertIs(captured["local_files_only"], False)

    def test_get_embedder_automodel_passes_probe(self):
        import torch
        import transformers

        with patch.object(
            embed_runtime, "resolve_device", return_value=torch.device("cpu")
        ), patch.object(transformers, "AutoTokenizer"), patch.object(
            transformers, "AutoModel"
        ) as model_cls, patch.object(
            embed_runtime, "_st_local_files_only", return_value=True
        ):
            embed_runtime.get_embedder(JINA, device_name="cpu")
        self.assertTrue(model_cls.from_pretrained.call_args.kwargs["local_files_only"])


class OfflineHubGuardTests(unittest.TestCase):
    """The load-time guard flips + restores both libraries' offline flags."""

    def test_active_guard_flips_flags_and_restores(self):
        import huggingface_hub.constants as hub_constants
        import transformers.utils.hub as transformers_hub

        hub_before = hub_constants.HF_HUB_OFFLINE
        tr_before = transformers_hub._is_offline_mode
        seen: list = []
        with embed_runtime._offline_hub_load_guard(True):
            seen.append(hub_constants.HF_HUB_OFFLINE)
            seen.append(transformers_hub._is_offline_mode)
        self.assertEqual(seen, [True, True])
        self.assertIs(hub_constants.HF_HUB_OFFLINE, hub_before)
        self.assertIs(transformers_hub._is_offline_mode, tr_before)

    def test_inactive_guard_touches_nothing(self):
        import huggingface_hub.constants as hub_constants
        import transformers.utils.hub as transformers_hub

        hub_before = hub_constants.HF_HUB_OFFLINE
        tr_before = transformers_hub._is_offline_mode
        with embed_runtime._offline_hub_load_guard(False):
            self.assertIs(hub_constants.HF_HUB_OFFLINE, hub_before)
            self.assertIs(transformers_hub._is_offline_mode, tr_before)
        self.assertIs(hub_constants.HF_HUB_OFFLINE, hub_before)
        self.assertIs(transformers_hub._is_offline_mode, tr_before)

    def test_guard_restores_flags_after_load_failure(self):
        import huggingface_hub.constants as hub_constants
        import transformers.utils.hub as transformers_hub

        hub_before = hub_constants.HF_HUB_OFFLINE
        tr_before = transformers_hub._is_offline_mode
        with self.assertRaises(RuntimeError):
            with embed_runtime._offline_hub_load_guard(True):
                raise RuntimeError("load blew up")
        self.assertIs(hub_constants.HF_HUB_OFFLINE, hub_before)
        self.assertIs(transformers_hub._is_offline_mode, tr_before)


class DocSideResolveTests(unittest.TestCase):
    """``resolve_embedding_model`` must resolve a cached repo id offline."""

    def setUp(self) -> None:
        _restore_env()

    def tearDown(self) -> None:
        _restore_env()

    def test_cached_repo_id_resolves_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp))
            with patch("huggingface_hub.snapshot_download", return_value=snap):
                selected, local_only = doc_embedding_utils.resolve_embedding_model(
                    CACHED_MODEL, "fallback-model"
                )
        self.assertEqual(selected, CACHED_MODEL)
        self.assertIs(local_only, True)
        self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), "1")

    def test_partial_snapshot_stays_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp), weights=False)
            with patch("huggingface_hub.snapshot_download", return_value=snap):
                _, local_only = doc_embedding_utils.resolve_embedding_model(
                    CACHED_MODEL, "fallback-model"
                )
        self.assertIs(local_only, False)
        self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_cache_miss_stays_online(self):
        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=LocalEntryNotFoundError("miss"),
        ):
            _, local_only = doc_embedding_utils.resolve_embedding_model(
                CACHED_MODEL, "fallback-model"
            )
        self.assertIs(local_only, False)

    def test_local_dir_offline_without_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("huggingface_hub.snapshot_download") as download:
                _, local_only = doc_embedding_utils.resolve_embedding_model(
                    str(tmp), "fallback-model"
                )
            download.assert_not_called()
        self.assertIs(local_only, True)

    def test_offline_flips_captured_hub_flags(self):
        import huggingface_hub.constants as hub_constants

        self.assertFalse(hub_constants.HF_HUB_OFFLINE)
        with tempfile.TemporaryDirectory() as tmp:
            snap = _make_snapshot(Path(tmp))
            with patch("huggingface_hub.snapshot_download", return_value=snap):
                doc_embedding_utils.resolve_embedding_model(
                    CACHED_MODEL, "fallback-model"
                )
        self.assertTrue(hub_constants.HF_HUB_OFFLINE)


class NoDirectSentenceTransformerConstructionTests(unittest.TestCase):
    """Residual gate: only the offline-first runtimes may construct ST.

    Every other module must go through ``embed_runtime.get_sentence_transformer``
    (code side) or ``resolve_embedding_model`` + explicit
    ``local_files_only=`` (doc side) so a cached model can never ping the hub.
    """

    ALLOWED = {
        CODE_TINY / "tools" / "common" / "embed_runtime.py",
        DOC_TINY / "graphrag_query_langextract.py",
        DOC_TINY / "graphrag_ingest_langextract.py",
        DOC_TINY / "mcp_graph_rag.py",
    }

    def test_no_direct_construction_outside_allowlist(self):
        offenders: list = []
        for base in (CODE_TINY, DOC_TINY):
            for path in base.rglob("*.py"):
                rel = path.relative_to(ROOT)
                if "tests" in rel.parts or path.name.startswith("test_"):
                    continue
                if path in self.ALLOWED:
                    continue
                if "SentenceTransformer(" in path.read_text(encoding="utf-8", errors="ignore"):
                    offenders.append(str(rel))
        self.assertEqual(offenders, [])


class RealCacheOfflineProbeTests(unittest.TestCase):
    """Against the machine's real snapshot with an unreachable HF endpoint.

    If any probe went online for any reason, the dead endpoint would turn
    its result into False (or hang in retries) and the assert would fail.
    """

    def test_probe_offline_on_real_cached_model(self):
        from huggingface_hub import snapshot_download

        try:
            snapshot_download(JINA, local_files_only=True)
        except Exception:
            self.skipTest("jina-embeddings-v3 snapshot not cached on this machine")
        code = (
            "import sys; "
            "sys.path.insert(0, {code_tiny!r}); "
            "sys.path.insert(0, {doc_tiny!r}); "
            "from tools.common import embed_runtime; "
            "import embedding_utils; "
            "assert embed_runtime._st_local_files_only({model!r}) is True; "
            "selected, local_only = embedding_utils.resolve_embedding_model({model!r}, 'x'); "
            "assert local_only is True, (selected, local_only)"
        ).format(code_tiny=str(CODE_TINY), doc_tiny=str(DOC_TINY), model=JINA)
        subprocess.run(
            [sys.executable, "-c", code],
            env=dict(os.environ, HF_ENDPOINT="http://127.0.0.1:9"),
            check=True,
            timeout=120,
        )


if __name__ == "__main__":
    unittest.main()
