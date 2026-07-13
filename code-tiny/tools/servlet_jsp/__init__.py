"""Servlet/JSP semantic overlay analyzer."""

from tools.servlet_jsp.models import SERVLET_JSP_PARSER_VERSION


def run_servlet_jsp_analysis(**kwargs):
    """Lazy public entry point that keeps parser-only imports lightweight."""

    from tools.servlet_jsp.pipeline import run_servlet_jsp_analysis as run

    return run(**kwargs)


__all__ = ["SERVLET_JSP_PARSER_VERSION", "run_servlet_jsp_analysis"]
