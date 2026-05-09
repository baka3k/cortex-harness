"""ApiBridgeAgent — thin facade over ts_api_bridge's API call matching.

This module does not re-implement the matching logic.  It delegates to
``tools.ts.ts_api_bridge`` and exposes a uniform class interface for
pipeline orchestrators.
"""
from __future__ import annotations

from typing import Any, List


class ApiBridgeAgent:
    """Thin facade over ts_api_bridge.match_api_calls."""

    def match_api_calls(self, *args: Any, **kwargs: Any) -> List[Any]:
        from tools.ts.ts_api_bridge import match_api_calls  # lazy import
        return match_api_calls(*args, **kwargs)
