"""Versioned C/C++/Pro*C call-evidence contract and containment rules.

This module is the single authority for how a callsite observation may be
classified, what evidence a strict ``CALLS`` edge requires, and how Pro*C
source bundles are identified.  Tree-sitter and name/scope/file/arity
heuristics produce weak evidence only; they can never promote an observation
to a semantic ``CALLS`` edge.  Only an approved semantic provider emitting a
``direct_resolved`` observation with complete caller/callee identity and
translation-unit/configuration provenance may do so, and even then only when
the guarded publication gates (later phases) accept it.

Consumers:
- ``tools/common/payload_validation.py`` enforces the contract on payloads.
- ``tools/graph/writer/language_writer.py`` re-enforces it at write time so a
  direct caller cannot bypass validation.
- ``tools/cplus/cplus_analyzer.py`` classifies every extracted callsite.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


CALL_EVIDENCE_SCHEMA_VERSION = "1"

# Resolution classes.  Publication rules per class:
#   direct_resolved           -> eligible for strict CALLS when every gate passes
#   declared_virtual_target   -> never an unconditional runtime CALLS
#   possible_dispatch_target  -> conservative view only
#   indirect_callsite         -> keep function-type/callback evidence, no target
#   dependent_template_call   -> candidate until a concrete instantiation resolves
#   lexical_candidate         -> POSSIBLE_CALLS only
#   constructor_call          -> object-construction evidence, target is a
#                                constructor decl; never a plain CALLS target
#   unresolved                -> UNKNOWN_CALL with a bounded reason
RESOLUTION_CLASS_DIRECT_RESOLVED = "direct_resolved"
RESOLUTION_CLASS_LEXICAL_CANDIDATE = "lexical_candidate"
RESOLUTION_CLASS_UNRESOLVED = "unresolved"
RESOLUTION_CLASSES = frozenset({
    RESOLUTION_CLASS_DIRECT_RESOLVED,
    "declared_virtual_target",
    "possible_dispatch_target",
    "indirect_callsite",
    "dependent_template_call",
    RESOLUTION_CLASS_LEXICAL_CANDIDATE,
    "constructor_call",
    RESOLUTION_CLASS_UNRESOLVED,
})

# The only providers allowed to publish resolved direct CALLS evidence.
# Tree-sitter and heuristic resolvers are coverage-plane tools and can never
# appear here.
SEMANTIC_PROVIDERS = frozenset({"clang_worker"})

# Coverage status for one (project, revision, translation unit/configuration,
# analyzer/policy version) frontier.  Consumers must answer "incomplete"
# rather than empty/negative when the traversal frontier is not complete.
COVERAGE_STATUSES = frozenset({
    "complete",
    "partial",
    "ineligible",
    "failed",
    "not_analyzed",
})

# Properties every strict CALLS edge must carry.  Legacy rows without a
# resolution class are migrated explicitly to lexical_candidate, never to a
# strong class.
STRONG_CALL_REQUIRED_PROPS = (
    "resolution_class",
    "semantic_provider",
    "tu_key",
    "config_fingerprint",
    "callee_usr",
)


def callsite_site_id(
    caller_id: str,
    callee_id: str,
    file_path: str,
    line: int,
    column: int,
    call_type: str,
) -> str:
    """Stable merge identity for a callsite.

    Deliberately excludes ``parse_run_id`` and any analyzer-run identity:
    repeated analysis of the same source and configuration must produce the
    same id.  Provenance stays on the edge properties, not in the key.
    """

    key = f"{caller_id}:{callee_id}:{file_path}:{line}:{column}:{call_type}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def normalize_call_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a call row classified under the current contract.

    Legacy rows with no ``resolution_class`` are migrated explicitly to
    ``lexical_candidate`` with the Tree-sitter provider.  An unknown class
    raises, fail closed.
    """

    normalized = dict(row)
    resolution_class = normalized.get("resolution_class")
    if resolution_class is None:
        resolution_class = RESOLUTION_CLASS_LEXICAL_CANDIDATE
        normalized["resolution_class"] = resolution_class
        normalized.setdefault("semantic_provider", "tree_sitter")
    if resolution_class not in RESOLUTION_CLASSES:
        raise ValueError(f"unknown call resolution class: {resolution_class!r}")
    return normalized


def is_strong_call_evidence(props: Mapping[str, Any]) -> bool:
    """True only for approved-provider, fully identified direct evidence."""

    if props.get("resolution_class") != RESOLUTION_CLASS_DIRECT_RESOLVED:
        return False
    if props.get("semantic_provider") not in SEMANTIC_PROVIDERS:
        return False
    for prop in STRONG_CALL_REQUIRED_PROPS:
        value = props.get(prop)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def enforce_strong_call_row(row: Mapping[str, Any]) -> None:
    """Fail closed when a row claims or requires strict CALLS without evidence.

    Rows that do not participate in this contract (other language analyzers
    that have not adopted it yet) are passed through unchanged; enforcement
    activates for any row that carries a ``resolution_class``.
    """

    props = row.get("props") if isinstance(row.get("props"), Mapping) else row
    if "resolution_class" not in props:
        return
    resolution_class = props.get("resolution_class")
    if resolution_class not in RESOLUTION_CLASSES:
        raise ValueError(f"unknown call resolution class: {resolution_class!r}")
    if resolution_class == RESOLUTION_CLASS_DIRECT_RESOLVED and not is_strong_call_evidence(props):
        missing = [
            prop
            for prop in STRONG_CALL_REQUIRED_PROPS
            if not str(props.get(prop) or "").strip()
        ]
        raise ValueError(
            "direct_resolved call evidence requires an approved semantic provider "
            f"and complete identity fields; missing or invalid: {missing}"
        )


# ---------------------------------------------------------------------------
# Pro*C source bundles
# ---------------------------------------------------------------------------

PROC_NODE_LABELS = frozenset({
    "SqlStatement",
    "SqlDirective",
    "SqlCursor",
    "SqlHostVariable",
    "DatabaseTable",
})

PROC_RELATION_TYPES = frozenset({
    "DECLARES_STATEMENT",
    "DECLARES_DIRECTIVE",
    "BINDS_PARAMETER",
    "DECLARES_CURSOR",
    "REFERENCES_CURSOR",
    "REFERENCES_STATEMENT",
    "READS_FROM",
    "WRITES_TO",
    "REFERENCES_TABLE",
})

# Classification of calls observed inside precompiler-generated code.  Only
# ``original_application`` evidence may enter the strict original-source call
# view.
GENERATED_CODE_CLASSES = frozenset({
    "original_application",
    "macro_expansion",
    "precompiler_wrapper",
    "precompiler_runtime",
    "generated_declaration",
    "unmapped_generated",
})

# Quality states for the original/generated source map.
SOURCE_MAP_QUALITY_STATES = frozenset({
    "exact",
    "aligned",
    "partial",
    "missing",
    "conflicting",
})


class SourceMapQuality(str, Enum):
    EXACT = "exact"
    ALIGNED = "aligned"
    PARTIAL = "partial"
    MISSING = "missing"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class GeneratedArtifactRef:
    """Reference to a supplied precompiler artifact under an allowlisted root.

    The artifact path is provenance for cache/invalidation and worker input;
    it is never a user-visible source identity and never carries raw
    precompiler command text or credentials.
    """

    artifact_path: str
    sha256: str
    generated_code_class: str = "unmapped_generated"

    def __post_init__(self) -> None:
        path = self.artifact_path.strip()
        if not path or not self.sha256.strip():
            raise ValueError("generated artifact references require path and content hash")
        # Artifact paths are repository-contained provenance references, never
        # absolute or escaping paths and never raw precompiler command text.
        normalized = path.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
            raise ValueError("generated artifact paths must be repository-relative")
        if normalized == ".." or normalized.startswith("../"):
            raise ValueError("generated artifact paths must not escape the repository root")
        if self.generated_code_class not in GENERATED_CODE_CLASSES:
            raise ValueError(
                f"unknown generated-code class: {self.generated_code_class!r}"
            )


@dataclass(frozen=True)
class ProcSourceBundle:
    """Identity joining every plane of one Pro*C translation unit.

    Joins the original ``.pc``/``.pcc`` source, the masked structure view,
    optional supplied generated C/C++ artifacts, source-map quality, and the
    redacted compiler/precompiler context.  The fingerprint feeds the analyzer
    cache and semantic-worker requests; absent generated/map evidence keeps
    the bundle weak: no strict CALLS may be derived from it.
    """

    original_path: str
    original_sha256: str
    masked_sha256: str
    generated_artifacts: tuple[GeneratedArtifactRef, ...] = ()
    source_map_quality: str = SourceMapQuality.MISSING.value
    compile_context_fingerprint: str = ""
    precompiler_context_fingerprint: str = ""
    schema_version: str = CALL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.original_path.strip() or not self.original_sha256.strip():
            raise ValueError("proc source bundles require original path and content hash")
        if not self.masked_sha256.strip():
            raise ValueError("proc source bundles require masked content hash")
        if self.source_map_quality not in SOURCE_MAP_QUALITY_STATES:
            raise ValueError(f"unknown source-map quality: {self.source_map_quality!r}")

    @property
    def semantic_eligible(self) -> bool:
        """Strict CALLS requires an accepted context and a usable map."""

        return (
            self.source_map_quality in {SourceMapQuality.EXACT.value, SourceMapQuality.ALIGNED.value}
            and bool(self.compile_context_fingerprint.strip())
            and any(
                artifact.generated_code_class != "unmapped_generated"
                for artifact in self.generated_artifacts
            )
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "original_path": self.original_path,
            "original_sha256": self.original_sha256,
            "masked_sha256": self.masked_sha256,
            "generated_artifacts": [
                {
                    "artifact_path": artifact.artifact_path,
                    "sha256": artifact.sha256,
                    "generated_code_class": artifact.generated_code_class,
                }
                for artifact in self.generated_artifacts
            ],
            "source_map_quality": self.source_map_quality,
            "compile_context_fingerprint": self.compile_context_fingerprint,
            "precompiler_context_fingerprint": self.precompiler_context_fingerprint,
        }
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_payload(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SemanticCoverageRecord:
    """Coverage of one translation unit/configuration frontier."""

    project_id: str
    revision: str
    language: str
    tu_key: str
    config_fingerprint: str
    analyzer_version: str
    policy_version: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATUSES:
            raise ValueError(f"unknown coverage status: {self.status!r}")
        for name, value in (
            ("project_id", self.project_id),
            ("language", self.language),
            ("tu_key", self.tu_key),
            ("analyzer_version", self.analyzer_version),
            ("policy_version", self.policy_version),
        ):
            if not str(value).strip():
                raise ValueError(f"coverage records require {name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "revision": self.revision,
            "language": self.language,
            "tu_key": self.tu_key,
            "config_fingerprint": self.config_fingerprint,
            "analyzer_version": self.analyzer_version,
            "policy_version": self.policy_version,
            "status": self.status,
            "detail": self.detail,
            "fingerprint": _fingerprint_payload({
                "project_id": self.project_id,
                "revision": self.revision,
                "language": self.language,
                "tu_key": self.tu_key,
                "config_fingerprint": self.config_fingerprint,
                "analyzer_version": self.analyzer_version,
                "policy_version": self.policy_version,
                "status": self.status,
            }),
        }


def coverage_is_complete(records: Iterable[Any]) -> bool:
    """True only when every record in the frontier reports complete status."""

    seen = False
    for record in records:
        seen = True
        status = record.get("status") if isinstance(record, Mapping) else getattr(record, "status", "")
        if status != "complete":
            return False
    return seen
