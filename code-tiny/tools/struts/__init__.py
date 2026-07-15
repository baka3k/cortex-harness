"""Apache Struts 2 semantic analyzer."""

from tools.struts.models import STRUTS_PARSER_VERSION, StrutsAnalysisResult


def run_struts_analysis(**kwargs) -> StrutsAnalysisResult:
    """Run the deterministic Struts 2 semantic analysis pipeline."""

    from tools.struts.pipeline import run_struts_analysis as run

    return run(**kwargs)


__all__ = ["STRUTS_PARSER_VERSION", "StrutsAnalysisResult", "run_struts_analysis"]
