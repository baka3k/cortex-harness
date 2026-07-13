from __future__ import annotations

import importlib.metadata
from typing import List, Tuple

from tools.mybatis.models import Diagnostic, ParserCapability


_SAMPLES = {
    "java": b"package com.acme; public interface UserMapper { User findById(long id); }\n",
    "sql": b"select u.id, u.name from users u where u.id = ?\n",
    "xml": (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" '
        b'"http://mybatis.org/dtd/mybatis-3-mapper.dtd">\n'
        b'<mapper namespace="com.acme.UserMapper">\n'
        b'  <!-- comment -->\n'
        b'  <select id="find" resultType="User" databaseId="h2">\n'
        b'    <![CDATA[select * from users where id = #{id}]]>\n'
        b"  </select>\n"
        b"</mapper>\n"
    ),
}


def load_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except Exception as exc:  # pragma: no cover - exercised when deps are missing.
        raise RuntimeError("tree-sitter-language-pack is required for MyBatis parser gates") from exc
    try:
        return get_parser(language)
    except Exception as exc:
        raise RuntimeError(f"Unable to load Tree-sitter parser for {language!r}") from exc


def check_parser_capabilities() -> Tuple[List[ParserCapability], List[Diagnostic]]:
    capabilities: List[ParserCapability] = []
    diagnostics: List[Diagnostic] = []
    for language in ("java", "sql", "xml"):
        try:
            parser = load_parser(language)
            tree = parser.parse(_SAMPLES[language])
            root = tree.root_node
            available = not root.has_error and root.start_point[0] == 0 and root.end_point[0] >= 0
            status = "ok" if available else "parse_error"
            message = "" if available else f"{language} sample parsed with syntax errors"
            capabilities.append(
                ParserCapability(
                    language=language,
                    available=available,
                    parser="tree_sitter_language_pack.get_parser",
                    package="tree-sitter-language-pack",
                    package_version=_package_version("tree-sitter-language-pack"),
                    abi_version=_abi_version(),
                    status=status,
                    message=message,
                )
            )
            if not available:
                diagnostics.append(Diagnostic(f"mybatis.parser.{language}.parse_error", message, "error"))
        except Exception as exc:
            capabilities.append(
                ParserCapability(
                    language=language,
                    available=False,
                    parser="tree_sitter_language_pack.get_parser",
                    package="tree-sitter-language-pack",
                    package_version=_package_version("tree-sitter-language-pack"),
                    abi_version=_abi_version(),
                    status="unavailable",
                    message=str(exc),
                )
            )
            diagnostics.append(Diagnostic(f"mybatis.parser.{language}.unavailable", str(exc), "error"))
    return capabilities, diagnostics


def parse_xml_bytes(source: bytes):
    parser = load_parser("xml")
    return parser.parse(source)


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
