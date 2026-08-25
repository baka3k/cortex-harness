from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.core.base import GraphProvider  # noqa: E402
from tools.graph.core.shared_runtime import _driver_key  # noqa: E402


def test_remote_falkordb_cache_key_includes_endpoint():
    first = _driver_key(
        GraphProvider.FALKORDB,
        {"uri": "redis://graph-a:6379", "path": None, "owner_id": "code"},
    )
    second = _driver_key(
        GraphProvider.FALKORDB,
        {"uri": "redis://graph-b:6379", "path": None, "owner_id": "code"},
    )

    assert first != second
