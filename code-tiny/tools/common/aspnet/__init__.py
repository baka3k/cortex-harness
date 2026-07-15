"""Shared contracts for ASP.NET semantic overlay analyzers."""

from .models import (
    ASPNET_MODEL_VERSION,
    ASPNET_PROTOCOL_VERSION,
    AnalysisModule,
    AnalysisResult,
    Diagnostic,
    ParserCapability,
    SemanticFact,
    SemanticRelationship,
    SourceSpan,
)

__all__ = [
    "ASPNET_MODEL_VERSION",
    "ASPNET_PROTOCOL_VERSION",
    "AnalysisModule",
    "AnalysisResult",
    "Diagnostic",
    "ParserCapability",
    "SemanticFact",
    "SemanticRelationship",
    "SourceSpan",
]
