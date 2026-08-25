"""Portable Tree-sitter COBOL discovery and preflight."""

from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
from typing import Any
import warnings


MINIMAL_PROGRAM = b"       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PREFLIGHT.\n       PROCEDURE DIVISION.\n       STOP RUN.\n"


class CobolRuntimeError(RuntimeError):
    """Raised when no compatible COBOL grammar can be loaded."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimeInfo:
    provider: str
    platform: str
    architecture: str
    tree_sitter_version: str
    grammar_abi: int
    library_path: str = ""
    library_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _grammar_abi(language) -> int:
    value = getattr(language, "abi_version", None)
    if value is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            value = getattr(language, "version", 0)
    return int(value)


def _bundled_candidates() -> list[Path]:
    library_dir = Path(__file__).resolve().parent / "lib"
    if not library_dir.is_dir():
        return []
    suffixes = {
        "Windows": (".dll", ".pyd"),
        "Darwin": (".dylib", ".so"),
        "Linux": (".so",),
    }.get(platform.system(), (".so", ".dll", ".dylib"))
    return sorted(path for path in library_dir.iterdir() if path.suffix.lower() in suffixes)


def resolve_language_library(override: str | None = None) -> Path | None:
    requested = override or os.environ.get("COBOL_LANGUAGE_LIBRARY")
    if requested:
        path = Path(requested).expanduser().resolve()
        if not path.is_file():
            raise CobolRuntimeError("COBOL_RUNTIME_LIBRARY_NOT_FOUND", f"grammar library not found: {path}")
        return path
    candidates = _bundled_candidates()
    return candidates[0] if candidates else None


def _native_parser(path: Path):
    try:
        library = ctypes.CDLL(str(path))
    except OSError as exc:
        raise CobolRuntimeError("COBOL_RUNTIME_LOAD_FAILED", f"could not load {path}: {exc}") from exc
    try:
        language_fn = library.tree_sitter_cobol
    except AttributeError as exc:
        raise CobolRuntimeError(
            "COBOL_RUNTIME_SYMBOL_MISSING",
            f"{path} does not export tree_sitter_cobol",
        ) from exc
    language_fn.restype = ctypes.c_void_p
    pointer = language_fn()
    if not pointer:
        raise CobolRuntimeError("COBOL_RUNTIME_NULL_LANGUAGE", f"{path} returned a null grammar pointer")
    try:
        from tree_sitter import Language, Parser

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            language = Language(pointer)
        parser = Parser(language)
        return parser, language
    except (ImportError, TypeError, ValueError) as exc:
        raise CobolRuntimeError(
            "COBOL_RUNTIME_ABI_INCOMPATIBLE",
            f"native grammar is incompatible with the installed tree-sitter binding: {exc}",
        ) from exc


def load_parser(language_library: str | None = None):
    """Load an override/bundled native grammar, otherwise the portable language pack."""
    requested = language_library or os.environ.get("COBOL_LANGUAGE_LIBRARY")
    bundled_error: CobolRuntimeError | None = None
    path = resolve_language_library(language_library)
    if path is not None:
        try:
            parser, language = _native_parser(path)
        except CobolRuntimeError as exc:
            if requested:
                raise
            bundled_error = exc
        else:
            info = RuntimeInfo(
                provider="native-library",
                platform=platform.system(),
                architecture=platform.machine(),
                tree_sitter_version=_package_version("tree-sitter"),
                grammar_abi=_grammar_abi(language),
                library_path=str(path),
                library_sha256=_sha256(path),
            )
            return parser, info
    try:
        from tree_sitter_language_pack import get_language, get_parser

        language = get_language("cobol")
        parser = get_parser("cobol")
    except (ImportError, LookupError, RuntimeError) as exc:
        detail = "install tree-sitter-language-pack or pass --cobol-language-library"
        if bundled_error is not None:
            detail = f"bundled grammar failed ({bundled_error.code}); {detail}"
        raise CobolRuntimeError(
            "COBOL_RUNTIME_UNAVAILABLE",
            detail,
        ) from exc
    info = RuntimeInfo(
        provider="tree-sitter-language-pack",
        platform=platform.system(),
        architecture=platform.machine(),
        tree_sitter_version=_package_version("tree-sitter"),
        grammar_abi=_grammar_abi(language),
    )
    return parser, info


def preflight(language_library: str | None = None) -> RuntimeInfo:
    parser, info = load_parser(language_library)
    tree = parser.parse(MINIMAL_PROGRAM)
    root = tree.root_node
    if (
        root.type not in {"start", "source_file"}
        or not root.named_child_count
        or root.has_error
    ):
        raise CobolRuntimeError("COBOL_RUNTIME_PARSE_FAILED", "minimal COBOL parse produced no program tree")
    return info
