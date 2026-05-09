"""BackendPipeline — orchestrates the TypeScript Express/NestJS backend analysis.

Delegates to BackendAgent (which wraps ts_backend_analyzer) and optionally
runs the API bridge matching via ApiBridgeAgent.
"""
from __future__ import annotations

from typing import Any, List, Optional

from tools.ts.agents.backend_agent import BackendAgent
from tools.ts.agents.api_bridge_agent import ApiBridgeAgent


class BackendPipeline:
    """Wire BackendAgent → optional ApiBridgeAgent."""

    def __init__(self) -> None:
        self.backend = BackendAgent()
        self.api_bridge = ApiBridgeAgent()

    async def build_backend_graph(
        self,
        root: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await self.backend.build_backend_graph(root, *args, **kwargs)

    def match_api_calls(self, *args: Any, **kwargs: Any) -> List[Any]:
        return self.api_bridge.match_api_calls(*args, **kwargs)

    async def run(
        self,
        root: str,
        frontend_api_calls: Optional[List[Any]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Optional[List[Any]]:
        """Run backend graph build and optionally match frontend API calls."""
        await self.build_backend_graph(root, *args, **kwargs)
        if frontend_api_calls is not None:
            return self.match_api_calls(frontend_api_calls)
        return None
