"""Framework-agnostic semantic extractors for the C# primary analyzer.

ASP.NET overlays already own DI registration, middleware, minimal API, and
configuration binding. The primary C# analyzer only extracts the
framework-agnostic items in this package.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from ..models import SemanticFrameworkItem
from .auth import AuthPolicyExtractor
from .background import BackgroundServiceExtractor
from .base import FrameworkExtractor, run_extractors
from .ef import EfEntityMappingExtractor
from .grpc import GrpcServiceExtractor
from .logging import LoggingTelemetryExtractor
from .nuget import NuGetDependencyExtractor
from .signalr import SignalRHubExtractor


def default_extractors() -> List[FrameworkExtractor]:
    """All framework-agnostic extractors that the primary C# analyzer runs."""
    return [
        EfEntityMappingExtractor(),
        GrpcServiceExtractor(),
        SignalRHubExtractor(),
        BackgroundServiceExtractor(),
        AuthPolicyExtractor(),
        LoggingTelemetryExtractor(),
        NuGetDependencyExtractor(),
    ]


def extract_framework_items(
    files: List[Tuple[str, Dict[str, Any]]],
    project_metadata: Dict[str, Any],
    extractors: Iterable[FrameworkExtractor] = (),
) -> List[SemanticFrameworkItem]:
    """Run extractors across the file evidence and project metadata.

    `files` is a list of (file_path, evidence_payload) pairs. Each extractor
    walks the payloads looking for known patterns. NuGet gets a special
    treatment because its source is the project file (one level above file
    evidence).
    """
    chosen = list(extractors) or default_extractors()
    base_payloads = [payload for _, payload in files]
    if project_metadata:
        # Inject project metadata into a synthetic "payload" so the NuGet
        # extractor can iterate the same shape as the others.
        base_payloads.append({**project_metadata, "_project_metadata": project_metadata})
    items: List[SemanticFrameworkItem] = []
    for extractor in chosen:
        items.extend(extractor.extract(base_payloads))
    return items


__all__ = [
    "AuthPolicyExtractor",
    "BackgroundServiceExtractor",
    "EfEntityMappingExtractor",
    "FrameworkExtractor",
    "GrpcServiceExtractor",
    "LoggingTelemetryExtractor",
    "NuGetDependencyExtractor",
    "SignalRHubExtractor",
    "default_extractors",
    "extract_framework_items",
    "run_extractors",
]
