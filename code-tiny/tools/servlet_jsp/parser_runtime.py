from __future__ import annotations

import importlib.metadata
from typing import List, Tuple

from tools.servlet_jsp.models import Diagnostic, ParserCapability


_SAMPLES = {
    "java": b"package demo; class Example extends jakarta.servlet.http.HttpServlet {}\n",
    "xml": (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE web-app PUBLIC "-//Sun Microsystems, Inc.//DTD Web Application 2.3//EN" '
        b'"http://java.sun.com/dtd/web-app_2_3.dtd">\n'
        b'<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee"><servlet/></web-app>\n'
    ),
    "html": b"<html><body><form action='/login'></form></body></html>\n",
}


def load_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("tree-sitter-language-pack is required for Servlet/JSP parser gates") from exc
    try:
        return get_parser(language)
    except Exception as exc:
        raise RuntimeError(f"Unable to load Tree-sitter parser for {language!r}") from exc


def check_parser_capabilities() -> Tuple[List[ParserCapability], List[Diagnostic]]:
    capabilities: List[ParserCapability] = []
    diagnostics: List[Diagnostic] = []
    for language in ("java", "xml", "html"):
        mandatory = language in {"java", "xml"}
        try:
            root = load_parser(language).parse(_SAMPLES[language]).root_node
            available = not root.has_error
            status = "ok" if available else "parse_error"
            message = "" if available else f"{language} sample parsed with syntax errors"
        except Exception as exc:  # noqa: BLE001
            available = False
            status = "unavailable"
            message = str(exc)
        capabilities.append(
            ParserCapability(
                language=language,
                available=available,
                mandatory=mandatory,
                parser="tree_sitter_language_pack.get_parser",
                package="tree-sitter-language-pack",
                package_version=_package_version("tree-sitter-language-pack"),
                abi_version=_abi_version(),
                status=status,
                message=message,
            )
        )
        if not available:
            diagnostics.append(
                Diagnostic(
                    f"servlet_jsp.parser.{language}.{status}",
                    message,
                    "error" if mandatory else "warning",
                    hint=f"Install a tree-sitter-language-pack build with {language} support.",
                )
            )
    return capabilities, diagnostics


def parse_xml_bytes(source: bytes):
    return load_parser("xml").parse(source)


def parse_java_bytes(source: bytes):
    return load_parser("java").parse(source)


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _abi_version() -> str:
    try:
        import tree_sitter

        return str(getattr(tree_sitter, "LANGUAGE_VERSION", ""))
    except Exception:
        return ""

