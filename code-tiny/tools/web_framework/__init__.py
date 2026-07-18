"""Provider-neutral semantic overlays for common web frameworks."""

from .pipeline import WebAnalysisResult, analyze_project

__all__ = ["WebAnalysisResult", "analyze_project"]
