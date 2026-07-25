"""Provider-neutral project topology extraction and graph contracts."""

from .models import (
    AnalysisDiagnostic,
    DependencyFact,
    DescriptorFact,
    EndpointFact,
    FrameworkInstanceFact,
    ModuleFact,
    PublicApiFact,
    SpecialFileFact,
    TopologyAnalysisResult,
)
from .pipeline import analyze_project

__all__ = [
    "AnalysisDiagnostic",
    "DependencyFact",
    "DescriptorFact",
    "EndpointFact",
    "FrameworkInstanceFact",
    "ModuleFact",
    "PublicApiFact",
    "SpecialFileFact",
    "TopologyAnalysisResult",
    "analyze_project",
]
