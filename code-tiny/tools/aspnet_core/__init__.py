"""ASP.NET Core semantic overlay."""

from .pipeline import run_aspnet_core_analysis

PARSER_VERSION = "aspnet-core-v2026-07-15-1"

__all__ = ["PARSER_VERSION", "run_aspnet_core_analysis"]
