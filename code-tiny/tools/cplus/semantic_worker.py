"""Isolated semantic callsite-evidence worker contract (Phase 02).

This module owns the versioned JSON-in/JSON-out semantic worker protocol that
is separate from graph payload construction.  A request identifies one
translation unit (or one mapped Pro*C generated artifact) plus normalized
validated compile arguments, root, fingerprints, limits, and the requested
schema.  A response carries classified callsite evidence, coverage,
redacted diagnostics/dependencies, resource usage, and a typed terminal
outcome.

Provider-neutral: the output contract must not depend on which Clang
interface (libclang/CIndex or a LibTooling sidecar) produced it.  The
selected production backend is recorded in the Phase 02 backend-selection
report; today it is CIndex over the pinned ``libclang`` wheel.

The worker never publishes consumer-visible ``CALLS`` edges; it only produces
evidence that later phases may merge under the guarded publication gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.common.call_evidence import (
    CALL_EVIDENCE_SCHEMA_VERSION,
    RESOLUTION_CLASS_DIRECT_RESOLVED,
)
from tools.cplus.function_identity import build_function_identity, normalize_syntax

# Protocol "1" is the whole-payload recovery contract owned by
# parse_recovery.WORKER_PROTOCOL_VERSION.  Protocol "2" adds the semantic
# callsite-evidence schema used here.
SEMANTIC_WORKER_PROTOCOL_VERSION = "2"
SEMANTIC_REQUEST_SCHEMA = "call_evidence"
SEMANTIC_BACKEND_ID = "cindex-libclang"
PINNED_LIBCLANG_VERSION = "18.1.1"

MAX_CALLSITES_PER_TU = 200_000
MAX_DIAGNOSTICS = 200
MAX_DEPENDENCIES = 20_000

_PROC_SOURCE_EXTENSIONS = (".pc", ".pcc")
# Credential-bearing precompiler/compile option shapes that must never reach
# the worker or appear in persisted evidence.  Matched against every request
# token (arguments, bundle fields) before any parsing happens.
_CREDENTIAL_TOKEN_PATTERN = re.compile(
    r"(?i)\b(user(id)?|password|passwd|pwd|secret|token)\s*[=:]\s*\S"
)
_CREDENTIAL_FLAG_PREFIXES = ("-DUSER", "-DPASS", "-DUID", "-DPWD")


# ---------------------------------------------------------------------------
# Readiness probe
# ---------------------------------------------------------------------------


def probe_clang_runtime(expected_version: str = PINNED_LIBCLANG_VERSION) -> Dict[str, Any]:
    """Typed readiness probe for the pinned Clang backend.

    Returns a dict with ``ready`` and a typed ``reason`` so an unavailable or
    version-mismatched runtime is a readiness failure, never a silent skip.
    """

    result: Dict[str, Any] = {
        "ready": False,
        "backend": SEMANTIC_BACKEND_ID,
        "expected_libclang_version": expected_version,
        "libclang_version": None,
        "native_library": None,
        "reason": "",
    }
    try:
        import clang.cindex as ci  # noqa: PLC0415
    except ImportError as exc:
        result["reason"] = f"clang_runtime_unavailable:{exc}"
        return result
    try:
        import importlib.metadata  # noqa: PLC0415

        version = importlib.metadata.version("libclang")
    except Exception:  # pragma: no cover - metadata should ship with the wheel
        version = "unknown"
    result["libclang_version"] = version
    if version != expected_version:
        result["reason"] = f"libclang_version_mismatch:{version}!={expected_version}"
        return result
    try:
        from clang import native  # noqa: PLC0415,F401

        result["native_library"] = getattr(native, "LIBCLANG_FILE", None) or str(
            getattr(native, "libclang_file", "")
        )
    except Exception:
        pass
    try:
        index = ci.Index.create()
        probe_tu = index.parse(
            "conftest.c",
            unsaved_files=[("conftest.c", "int _cortex_probe(void);\n")],
            args=["-std=c11"],
        )
        if probe_tu is None:
            result["reason"] = "libclang_index_unusable"
            return result
    except Exception as exc:
        result["reason"] = f"libclang_index_unusable:{type(exc).__name__}"
        return result
    result["ready"] = True
    result["reason"] = "ok"
    return result


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcBundleRequest:
    """Explicit source-bundle request for mapped Pro*C generated input.

    The worker reads only ``artifact_path`` (a repository-contained generated
    or validated virtual C/C++ artifact).  Raw ``.pc``/``.pcc`` is never an
    ordinary C input.
    """

    bundle_id: str
    artifact_path: str
    artifact_sha256: str
    source_map_id: str
    source_map_sha256: str
    original_path: str
    language_mode: str  # "c" | "c++"
    mapping_policy: str  # e.g. "exact" | "aligned"

    def __post_init__(self) -> None:
        for name, value in (
            ("bundle_id", self.bundle_id),
            ("artifact_path", self.artifact_path),
            ("artifact_sha256", self.artifact_sha256),
            ("source_map_id", self.source_map_id),
            ("original_path", self.original_path),
        ):
            if not str(value).strip():
                raise ValueError(f"proc bundle request requires {name}")
        if self.language_mode not in {"c", "c++"}:
            raise ValueError(f"unsupported proc language mode: {self.language_mode!r}")
        if self.mapping_policy not in {"exact", "aligned", "partial"}:
            raise ValueError(f"unsupported mapping policy: {self.mapping_policy!r}")
        normalized = self.artifact_path.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
            raise ValueError("proc artifact paths must be repository-relative")
        if normalized.split("/")[-1].lower().endswith(_PROC_SOURCE_EXTENSIONS):
            raise ValueError("raw proc source is not a valid semantic artifact")
        if _CREDENTIAL_TOKEN_PATTERN.search(self.original_path):
            raise ValueError("credential-bearing proc bundle field rejected")


def validate_semantic_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize and validate one semantic worker request (fail closed).

    Returns the validated request dict; raises ValueError on any contract
    violation (unknown schema, unsafe arguments, raw Pro*C input, external
    paths, credential-bearing options).
    """

    if request.get("protocol_version") != SEMANTIC_WORKER_PROTOCOL_VERSION:
        raise ValueError("worker protocol mismatch")
    if request.get("request_schema") != SEMANTIC_REQUEST_SCHEMA:
        raise ValueError("worker request schema mismatch")
    if str(request.get("root") or "").strip() == "":
        raise ValueError("worker request requires root")

    validated: Dict[str, Any] = {
        "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
        "request_schema": SEMANTIC_REQUEST_SCHEMA,
        "root": str(request["root"]),
        "compile_arguments": [str(value) for value in request.get("compile_arguments") or ()],
        "compile_context_fingerprint": str(request.get("compile_context_fingerprint") or ""),
        "source_fingerprint": str(request.get("source_fingerprint") or ""),
        "memory_mb": int(request.get("memory_mb") or 1024),
        "cpu_seconds": int(request.get("cpu_seconds") or 30),
        "max_output_bytes": int(request.get("max_output_bytes") or 0),
        "max_source_bytes": int(request.get("max_source_bytes") or 0),
        "proc_bundle": None,
    }
    for token in validated["compile_arguments"]:
        if _CREDENTIAL_TOKEN_PATTERN.search(token) or token.upper().startswith(
            _CREDENTIAL_FLAG_PREFIXES
        ):
            raise ValueError("credential-bearing compile argument rejected")

    bundle = request.get("proc_bundle")
    if bundle is not None:
        if not isinstance(bundle, Mapping):
            raise ValueError("proc bundle must be an object")
        validated["proc_bundle"] = ProcBundleRequest(
            bundle_id=str(bundle.get("bundle_id") or ""),
            artifact_path=str(bundle.get("artifact_path") or ""),
            artifact_sha256=str(bundle.get("artifact_sha256") or ""),
            source_map_id=str(bundle.get("source_map_id") or ""),
            source_map_sha256=str(bundle.get("source_map_sha256") or ""),
            original_path=str(bundle.get("original_path") or ""),
            language_mode=str(bundle.get("language_mode") or ""),
            mapping_policy=str(bundle.get("mapping_policy") or ""),
        )

    path = str(request.get("path") or "")
    if not path:
        raise ValueError("worker request requires path")
    if path.lower().endswith(_PROC_SOURCE_EXTENSIONS):
        raise ValueError("raw proc source requires an explicit source-bundle request")
    validated["path"] = path
    return validated


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact_relative(root: str, path: Optional[str]) -> str:
    """Normalize a filesystem path to a repository-relative identity.

    Paths outside the repository root collapse to ``<external>/<name>`` so
    normal artifacts never leak absolute local paths.
    """

    if not path:
        return ""
    root_real = os.path.realpath(os.path.abspath(root))
    real = os.path.realpath(os.path.abspath(path))
    # os.path.relpath never raises for foreign paths on POSIX (only on
    # Windows cross-drive), so containment must be checked explicitly.
    if real != root_real and not real.startswith(root_real + os.sep):
        return "<external>/" + os.path.basename(real)
    return os.path.relpath(real, root_real).replace("\\", "/")


def redacted_fingerprint(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


_FUNCTION_CURSOR_KINDS = (
    "FUNCTION_DECL",
    "CXX_METHOD",
    "CONSTRUCTOR",
    "DESTRUCTOR",
    "FUNCTION_TEMPLATE",
)


def _function_symbol_id(cursor: Any, rel_path: str) -> str:
    parameters = []
    for argument in cursor.get_arguments():
        try:
            parameters.append(argument.type.spelling or "?")
        except Exception:
            parameters.append("?")
    if not parameters:
        for child in cursor.get_children():
            if getattr(child.kind, "name", "") == "PARM_DECL":
                try:
                    parameters.append(child.type.spelling or "?")
                except Exception:
                    parameters.append("?")
    try:
        type_spelling = cursor.type.spelling or ""
    except Exception:
        type_spelling = ""
    close = type_spelling.rfind(")")
    qualifiers = normalize_syntax(type_spelling[close + 1 :] if close >= 0 else "")
    try:
        linkage_name = cursor.linkage.name.lower()
    except Exception:
        linkage_name = "external"
    linkage = "internal" if linkage_name in {"internal", "unique_external", "no_linkage"} else "external"
    scope_parts: List[str] = []
    parent = cursor.semantic_parent
    while parent is not None and getattr(parent.kind, "name", "") != "TRANSLATION_UNIT":
        if parent.spelling:
            scope_parts.append(parent.spelling)
        parent = parent.semantic_parent
    spelling = cursor.spelling or cursor.displayname or "<anonymous>"
    qualified = "::".join([*reversed(scope_parts), spelling])
    template_arity = sum(
        1
        for child in cursor.get_children()
        if getattr(child.kind, "name", "")
        in {
            "TEMPLATE_TYPE_PARAMETER",
            "TEMPLATE_NON_TYPE_PARAMETER",
            "TEMPLATE_TEMPLATE_PARAMETER",
        }
    )
    return build_function_identity(
        qualified_name=qualified,
        parameter_types=parameters,
        qualifiers=qualifiers,
        template_arity=template_arity,
        linkage=linkage,
        rel_path=rel_path,
        start_byte=int(cursor.extent.start.offset or 0),
        parseable=bool(cursor.spelling),
    ).logical_id


def classify_call(
    referenced: Any,
    ci: Any,
    *,
    call_offset: int,
    referenced_offset: int,
    referenced_extent_bytes: int,
    macro_origin: bool,
) -> Tuple[str, str]:
    """Return (resolution_class, bounded_reason) for one CALL_EXPR.

    Classification never forces a virtual, indirect, or dependent call into a
    direct target; each keeps its explicit weaker class per the Phase 01
    contract.
    """

    if referenced is None:
        return "unresolved", "no_referenced_declaration"
    kind = referenced.kind
    indirect_kinds = [
        ci.CursorKind.PARM_DECL,
        ci.CursorKind.VAR_DECL,
        ci.CursorKind.FIELD_DECL,
    ]
    if hasattr(ci.CursorKind, "BINDING_DECL"):
        indirect_kinds.append(ci.CursorKind.BINDING_DECL)
    if kind in indirect_kinds:
        return "indirect_callsite", f"callee_is_{kind.name.lower()}"
    if hasattr(ci.CursorKind, "CONSTRUCTOR") and kind == ci.CursorKind.CONSTRUCTOR:
        return "constructor_call", "cxx_construct_expr"
    if kind in (ci.CursorKind.FUNCTION_TEMPLATE, ci.CursorKind.CXX_METHOD) and (
        "<#" in (referenced.get_usr() or "") or ">#" in (referenced.get_usr() or "")
    ):
        return "dependent_template_call", "unresolved_template_parameter"
    if kind == ci.CursorKind.CXX_METHOD and referenced.is_pure_virtual_method():
        return "declared_virtual_target", "pure_virtual_dispatch"
    if kind == ci.CursorKind.CXX_METHOD and referenced.is_virtual_method():
        return "declared_virtual_target", "virtual_dispatch"
    # Implicitly-created declarations (C implicit function declarations) share
    # the call's location and have an empty extent.
    if (
        referenced_extent_bytes == 0
        and referenced_offset == call_offset
        and not referenced.is_definition()
    ):
        return "unresolved", "implicit_declaration"
    if macro_origin:
        return RESOLUTION_CLASS_DIRECT_RESOLVED, "macro_expansion"
    return RESOLUTION_CLASS_DIRECT_RESOLVED, ""


# ---------------------------------------------------------------------------
# Semantic extraction
# ---------------------------------------------------------------------------


@dataclass
class SemanticExtractionLimits:
    max_callsites: int = MAX_CALLSITES_PER_TU
    max_diagnostics: int = MAX_DIAGNOSTICS
    max_dependencies: int = MAX_DEPENDENCIES


@dataclass
class SemanticExtractionResult:
    status: str = "ok"  # ok | failed | truncated
    callsites: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""


def extract_semantic_callsite_evidence(
    path: str,
    root: str,
    validated_args: Sequence[str],
    *,
    limits: Optional[SemanticExtractionLimits] = None,
    config_fingerprint: str = "",
    proc_bundle: Optional[ProcBundleRequest] = None,
) -> SemanticExtractionResult:
    """Extract classified callsite evidence for one translation unit.

    This is the CIndex backend implementation.  It emits caller and
    referenced-callee USR identities, linkage/TU disambiguation, spelling and
    macro-expansion locations, diagnostics, and include dependencies — all
    with repository-relative identities only.
    """

    limits = limits or SemanticExtractionLimits()
    try:
        import clang.cindex as ci  # noqa: PLC0415
    except ImportError:
        return SemanticExtractionResult(
            status="failed",
            failure_reason="clang_runtime_unavailable",
            coverage={"status": "ineligible", "detail": "libclang not installed"},
        )

    root_real = os.path.realpath(os.path.abspath(root))
    rel_path = os.path.relpath(os.path.realpath(os.path.abspath(path)), root_real).replace("\\", "/")

    result = SemanticExtractionResult()
    started = time.monotonic()
    try:
        index = ci.Index.create()
        tu = index.parse(
            path,
            args=list(validated_args),
            options=(
                ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
                | ci.TranslationUnit.PARSE_INCOMPLETE
            ),
        )
    except Exception as exc:  # libclang load/parse failure is a typed outcome
        return SemanticExtractionResult(
            status="failed",
            failure_reason=f"translation_unit_error:{type(exc).__name__}",
            coverage={"status": "failed", "detail": "translation unit creation failed"},
        )
    if tu is None:
        return SemanticExtractionResult(
            status="failed",
            failure_reason="translation_unit_none",
            coverage={"status": "failed", "detail": "translation unit unavailable"},
        )

    # Macro instantiation ranges for macro-origin callsites.
    macro_ranges: List[Tuple[int, int, str]] = []
    try:
        for cursor in tu.cursor.walk_preorder():
            if cursor.kind == ci.CursorKind.MACRO_INSTANTIATION and cursor.location.file:
                if os.path.realpath(cursor.location.file.name) == os.path.realpath(path):
                    macro_ranges.append(
                        (cursor.extent.start.offset, cursor.extent.end.offset, cursor.spelling)
                    )
    except Exception:
        macro_ranges = []

    def macro_at(start: int, end: int) -> str:
        for m_start, m_end, name in macro_ranges:
            if start >= m_start and end <= m_end:
                return name
        return ""

    function_extents: List[Tuple[int, int, str, str]] = []  # (start, end, usr, symbol_id)
    function_symbols_by_usr: Dict[str, str] = {}
    pending_calls: List[Any] = []

    error_count = 0
    truncated = False

    try:
        for cursor in tu.cursor.walk_preorder():
            kind = cursor.kind

            if kind == ci.CursorKind.INCLUSION_DIRECTIVE:
                included = cursor.displayname or cursor.spelling or ""
                if included and len(result.dependencies) < limits.max_dependencies:
                    result.dependencies.append(included)
                continue

            loc = cursor.location
            if loc and loc.file:
                try:
                    if os.path.realpath(loc.file.name) != os.path.realpath(path):
                        continue
                except Exception:
                    pass

            if kind.name in _FUNCTION_CURSOR_KINDS:
                usr = cursor.get_usr() or ""
                if usr:
                    symbol_id = _function_symbol_id(cursor, rel_path)
                    function_symbols_by_usr[usr] = symbol_id
                    if cursor.is_definition():
                        function_extents.append(
                            (
                                cursor.extent.start.offset,
                                cursor.extent.end.offset,
                                usr,
                                symbol_id,
                            )
                        )

            # Direct calls plus object-construction sites.  Overloaded
            # operators invoked via operator syntax (``a + b``) surface as
            # CALL_EXPR cursors referencing the operator declaration.
            construct_kind = getattr(ci.CursorKind, "CXX_CONSTRUCT_EXPR", None)
            if kind != ci.CursorKind.CALL_EXPR and not (
                construct_kind is not None and kind == construct_kind
            ):
                continue
            if len(pending_calls) >= limits.max_callsites:
                truncated = True
                break
            pending_calls.append(cursor)

        frozen_extents = tuple(
            sorted(function_extents, key=lambda item: (item[0], item[1], item[2]))
        )

        def enclosing_function(offset: int) -> Tuple[str, str]:
            best_span = None
            best: Tuple[str, str] = ("", "")
            for start, end, usr, symbol_id in frozen_extents:
                if start <= offset <= end:
                    span = end - start
                    if best_span is None or span < best_span:
                        best_span = span
                        best = (usr, symbol_id)
            return best

        call_ordinals: Dict[Tuple[str, int], int] = {}
        for cursor in pending_calls:
            loc = cursor.location
            referenced = cursor.referenced
            call_offset = cursor.extent.start.offset
            if referenced is not None:
                referenced_offset = referenced.location.offset if referenced.location else -1
                referenced_extent = referenced.extent.end.offset - referenced.extent.start.offset
            else:
                referenced_offset, referenced_extent = -1, -1
            macro_name = macro_at(cursor.extent.start.offset, cursor.extent.end.offset)
            resolution_class, reason = classify_call(
                referenced,
                ci,
                call_offset=call_offset,
                referenced_offset=referenced_offset,
                referenced_extent_bytes=referenced_extent,
                macro_origin=bool(macro_name),
            )

            caller_usr, caller_symbol_id = enclosing_function(call_offset)
            ordinal_key = (caller_symbol_id, call_offset)
            call_ordinal = call_ordinals.get(ordinal_key, 0)
            call_ordinals[ordinal_key] = call_ordinal + 1
            callee_usr = referenced.get_usr() if referenced is not None else ""
            callee_symbol_id = ""
            callee_linkage = ""
            if referenced is not None:
                callee_linkage = referenced.linkage.name
                callee_symbol_id = function_symbols_by_usr.get(callee_usr, "")

            site: Dict[str, Any] = {
                "schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
                "semantic_provider": "clang_worker",
                "backend": SEMANTIC_BACKEND_ID,
                "file_path": rel_path,
                "call_start_byte": call_offset,
                "spelling_start_byte": call_offset,
                "expansion_start_byte": call_offset,
                "call_ordinal": call_ordinal,
                "call_end_byte": cursor.extent.end.offset,
                "call_line": loc.line if loc else 0,
                "call_column": loc.column if loc else 0,
                "call_arity": sum(1 for _ in cursor.get_arguments()),
                "callee_name": (referenced.spelling if referenced is not None else "") or cursor.spelling,
                "callee_usr": callee_usr,
                "callee_symbol_id": callee_symbol_id,
                "callee_linkage": callee_linkage,
                "resolution_class": resolution_class,
                "resolution_reason": reason,
                "caller_usr": caller_usr,
                "caller_symbol_id": caller_symbol_id,
                "tu_key": rel_path,
                "config_fingerprint": config_fingerprint,
                "macro_origin": macro_name or None,
            }
            if proc_bundle is not None:
                site["proc_bundle_id"] = proc_bundle.bundle_id
                site["source_map_id"] = proc_bundle.source_map_id
                site["generated_code_class"] = _generated_code_class(
                    cursor.spelling or "", macro_name, proc_bundle
                )
            result.callsites.append(site)
    except Exception as exc:
        return SemanticExtractionResult(
            status="failed",
            failure_reason=f"traversal_error:{type(exc).__name__}",
            coverage={"status": "failed", "detail": "cursor traversal failed"},
        )

    for diag in tu.diagnostics:
        if len(result.diagnostics) >= limits.max_diagnostics:
            break
        if diag.severity >= ci.Diagnostic.Error:
            error_count += 1
        diag_loc = diag.location
        result.diagnostics.append(
            {
                "severity": int(diag.severity),
                "spelling": diag.spelling[:500],
                "file": redact_relative(root, diag_loc.file.name) if diag_loc and diag_loc.file else "",
                "offset": diag_loc.offset if diag_loc else 0,
            }
        )

    has_errors = error_count > 0
    result.status = "truncated" if truncated else "ok"
    result.coverage = {
        "status": "complete" if not has_errors else "partial",
        "detail": "" if not has_errors else f"{error_count} error diagnostics",
        "tu_key": rel_path,
        "config_fingerprint": config_fingerprint,
        "callsite_count": len(result.callsites),
        "direct_resolved_count": sum(
            1 for s in result.callsites if s["resolution_class"] == RESOLUTION_CLASS_DIRECT_RESOLVED
        ),
        "error_diagnostic_count": error_count,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
    }
    return result


# Pro*C precompiler runtime/wrapper symbol shapes observed in Oracle
# generated C/C++ output.  Calls matching these can never be confused with
# original application callsites.
_PROC_RUNTIME_PATTERN = re.compile(r"^(sql|sqlca|oraca|sqlglm|sqlror|sqlld|ori|orl|orup)"
                                   r"[A-Za-z0-9_]*$")
_PROC_WRAPPER_PATTERN = re.compile(r"^(proc_|sqlproc_|epc_|EPC)[A-Za-z0-9_]*$")


def _generated_code_class(callee_spelling: str, macro_name: str, bundle: ProcBundleRequest) -> str:
    if macro_name:
        return "macro_expansion"
    if _PROC_RUNTIME_PATTERN.match(callee_spelling):
        return "precompiler_runtime"
    if _PROC_WRAPPER_PATTERN.match(callee_spelling):
        return "precompiler_wrapper"
    if bundle.mapping_policy == "partial":
        return "unmapped_generated"
    return "original_application"


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------


def build_semantic_response(
    request: Mapping[str, Any],
    extraction: SemanticExtractionResult,
    *,
    proc_bundle: Optional[ProcBundleRequest] = None,
    libclang_version: str = "",
) -> Dict[str, Any]:
    """Assemble the worker terminal response (typed outcome, no raw paths)."""

    outcome = extraction.status
    response: Dict[str, Any] = {
        "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
        "request_schema": SEMANTIC_REQUEST_SCHEMA,
        "status": outcome,
        "backend": SEMANTIC_BACKEND_ID,
        "backend_version": libclang_version,
        "schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
        "error": extraction.failure_reason,
        "callsites": extraction.callsites,
        "coverage": extraction.coverage,
        "diagnostics": extraction.diagnostics,
        "dependencies": extraction.dependencies[:MAX_DEPENDENCIES],
    }
    if proc_bundle is not None:
        response["proc_bundle"] = {
            "bundle_id": proc_bundle.bundle_id,
            "bundle_fingerprint": redacted_fingerprint(
                proc_bundle.bundle_id,
                proc_bundle.artifact_sha256,
                proc_bundle.source_map_sha256,
                proc_bundle.mapping_policy,
            ),
            "source_map_id": proc_bundle.source_map_id,
            "original_path": proc_bundle.original_path,
            "language_mode": proc_bundle.language_mode,
            "mapping_policy": proc_bundle.mapping_policy,
            # Redacted precompiler fingerprint: hash of artifact/map identity,
            # never raw commands, options, or credentials.
            "precompiler_fingerprint": redacted_fingerprint(
                "proc", proc_bundle.bundle_id, proc_bundle.artifact_sha256
            ),
        }
    return response
