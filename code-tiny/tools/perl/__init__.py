"""Perl 5 structural analyzer public API."""

from .models import ANALYZER_VERSION, AnalysisResult

__all__ = ["ANALYZER_VERSION", "AnalysisResult", "run_perl_analysis"]


def run_perl_analysis(*args, **kwargs):
    """Load the pipeline lazily so importing contracts needs no graph services."""
    from .pipeline import run_perl_analysis as _run_perl_analysis

    return _run_perl_analysis(*args, **kwargs)
