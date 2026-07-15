"""Tree-sitter Perl grammar loading and capability validation."""

from __future__ import annotations

import importlib.metadata as metadata
import warnings
from functools import lru_cache
from typing import Any, Tuple

from .models import ANALYZER_VERSION, ParserCapabilities, SUPPORTED_EXTENSIONS


GRAMMAR_PACKAGE = "tree-sitter-perl"
MIN_GRAMMAR_VERSION = (1, 2, 1)
MAX_GRAMMAR_VERSION = (1, 3, 0)
REQUIRED_NODE_TYPES = (
    "source_file",
    "package_statement",
    "subroutine_declaration_statement",
    "variable_declaration",
    "localization_expression",
    "use_statement",
    "require_expression",
    "function_call_expression",
    "method_call_expression",
    "coderef_call_expression",
    "comment",
    "pod",
    "data_section",
)


class ParserCapabilityError(RuntimeError):
    """Raised when the installed Perl grammar cannot satisfy the contract."""


def _version_tuple(value: str) -> Tuple[int, int, int]:
    parts = []
    for token in value.split(".")[:3]:
        digits = "".join(char for char in token if char.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


@lru_cache(maxsize=1)
def load_language() -> Any:
    try:
        from tree_sitter import Language
        import tree_sitter_perl
    except Exception as exc:  # pragma: no cover - exercised by import smoke tests
        raise ParserCapabilityError(
            "Perl parser unavailable; install tree-sitter-perl==1.2.1 and tree-sitter."
        ) from exc

    grammar_version = metadata.version(GRAMMAR_PACKAGE)
    parsed_version = _version_tuple(grammar_version)
    if not (MIN_GRAMMAR_VERSION <= parsed_version < MAX_GRAMMAR_VERSION):
        raise ParserCapabilityError(
            f"Unsupported tree-sitter-perl version {grammar_version}; expected >=1.2.1,<1.3."
        )
    try:
        capsule = tree_sitter_perl.language()
        if isinstance(capsule, Language):
            return capsule
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return Language(capsule)
    except Exception as exc:
        raise ParserCapabilityError("Unable to load the tree-sitter Perl language capsule.") from exc


def new_parser() -> Any:
    from tree_sitter import Parser

    language = load_language()
    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(language)
        else:
            parser.language = language
        return parser


@lru_cache(maxsize=1)
def capabilities() -> ParserCapabilities:
    language = load_language()
    runtime_version = metadata.version("tree-sitter")
    grammar_version = metadata.version(GRAMMAR_PACKAGE)
    semantic = getattr(language, "semantic_version", None)
    semantic_text = ".".join(str(item) for item in semantic) if semantic else "unknown"
    abi_value = getattr(language, "abi_version", None)
    if abi_value is None:  # pragma: no cover - compatibility with old runtimes
        abi_value = getattr(language, "version", 0)
    abi = int(abi_value)
    if abi < 14:
        raise ParserCapabilityError(f"Unsupported Perl grammar ABI {abi}; expected ABI 14 or newer.")
    return ParserCapabilities(
        analyzer_version=ANALYZER_VERSION,
        runtime_package="tree-sitter",
        runtime_version=runtime_version,
        grammar_package=GRAMMAR_PACKAGE,
        grammar_version=grammar_version,
        grammar_abi=abi,
        grammar_semantic_version=semantic_text,
        language_name=str(getattr(language, "name", "perl") or "perl"),
        extensions=SUPPORTED_EXTENSIONS,
        supported_nodes=REQUIRED_NODE_TYPES,
    )


def error_nodes(root: Any) -> Tuple[Any, ...]:
    found = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_error or node.is_missing:
            found.append(node)
        stack.extend(reversed(node.children))
    return tuple(found)
