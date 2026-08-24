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


CALL_EVIDENCE_SCHEMA_VERSION = "2"

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
    "context_attestation",
    "manifest_key",
)

CONTEXT_FIDELITIES = frozenset({"faithful", "inherited", "synthetic", "missing"})
CONTEXT_ADMISSION_STATES = frozenset({"accepted", "rejected"})
EXECUTION_COVERAGE_STATES = frozenset(
    {"not_analyzed", "complete", "partial", "failed", "truncated", "cancelled"}
)


def context_is_strictly_eligible(props: Mapping[str, Any]) -> bool:
    """Require the three independent context axes and parent attestation."""

    if props.get("context_fidelity") != "faithful":
        return False
    if props.get("context_admission") != "accepted":
        return False
    if props.get("execution_coverage") != "complete":
        return False
    for field in ("context_attestation", "manifest_key"):
        if not isinstance(props.get(field), str) or not str(props[field]).strip():
            return False
    return True


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


def logical_callsite_id(
    *,
    caller_id: str,
    file_path: str,
    spelling_offset: int,
    expansion_offset: int | None = None,
    ordinal: int = 0,
    call_type: str = "call",
) -> str:
    """Stable callee-independent identity for one syntactic callsite."""

    expansion = int(spelling_offset if expansion_offset is None else expansion_offset)
    key = json.dumps(
        {
            "schema": CALL_EVIDENCE_SCHEMA_VERSION,
            "caller": str(caller_id),
            "file": str(file_path).replace("\\", "/"),
            "spelling_offset": max(0, int(spelling_offset)),
            "expansion_offset": max(0, expansion),
            "ordinal": max(0, int(ordinal)),
            "call_type": str(call_type or "call"),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
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
    if not context_is_strictly_eligible(props):
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


# ---------------------------------------------------------------------------
# Phase 04: staging graph labels and evidence joins
# ---------------------------------------------------------------------------

# Staging-graph node labels.  ``CallSite`` is the primary fact; compatibility
# ``CALLS`` edges may only be derived from accepted ``direct_resolved``
# evidence and must stay linked to these stable site/evidence identities.
EVIDENCE_NODE_LABELS = frozenset({"CallSite", "BuildConfiguration", "SemanticCoverage"})

# Evidence-plane relationship types.  None of these redefine existing Pro*C
# or call relationships; the nine Pro*C relations and ``BINDS_PARAMETER``
# keep their meanings.
HAS_CALLSITE_REL = "HAS_CALLSITE"
RESOLVES_TO_REL = "RESOLVES_TO"
OBSERVED_AS_REL = "OBSERVED_AS"
IN_CONFIGURATION_REL = "IN_CONFIGURATION"
MAPS_TO_SOURCE_REL = "MAPS_TO_SOURCE"

# Schema-owner-approved cross-domain evidence joins.  ``EXECUTES_SQL`` links
# a reconciled semantic function to the original SQL region; it never changes
# the SQL node's identity.  ``RESOLVES_HOST_DECLARATION`` links a uniquely
# resolved host/indicator variable to its C declaration evidence;
# ``BINDS_PARAMETER`` remains the statement-to-host relationship.
EXECUTES_SQL_REL = "EXECUTES_SQL"
RESOLVES_HOST_DECLARATION_REL = "RESOLVES_HOST_DECLARATION"

EVIDENCE_RELATION_TYPES = frozenset({
    HAS_CALLSITE_REL,
    RESOLVES_TO_REL,
    OBSERVED_AS_REL,
    IN_CONFIGURATION_REL,
    MAPS_TO_SOURCE_REL,
    EXECUTES_SQL_REL,
    RESOLVES_HOST_DECLARATION_REL,
})

# Quality of an enclosing-function / host-variable reconciliation join.
JOIN_QUALITY_STATES = frozenset({
    "unique",       # lexical and semantic identities agree
    "ambiguous",    # multiple candidates retained; no selection by name/proximity
    "unresolved",   # no semantic identity mapped yet
    "cross_config", # agreement only under some build configurations
})

# Query profiles.  ``strict`` selects only accepted direct semantic CALLS;
# ``conservative`` unions the explicitly weaker evidence classes without
# relabeling them.  The registry of tool-facing profiles (including Pro*C
# data-impact profiles) lives in ``mcp/framework_registry.py``; this module
# owns only the two evidence-view names and their admission rules.
QUERY_PROFILE_STRICT = "strict"
QUERY_PROFILE_CONSERVATIVE = "conservative"
QUERY_PROFILE_DEFAULT = "default"

STRICT_PROFILE_REL_TYPES = ("CALLS",)
CONSERVATIVE_PROFILE_REL_TYPES = (
    "CALLS",
    "POSSIBLE_CALLS",
    "CALLS_FUNCTION_POINTER",
)

# Typed traversal outcomes.  ``incomplete`` replaces authoritative negative
# answers (``no callers`` / ``unaffected``) whenever the semantic frontier of
# the traversal is not complete under the requested configuration policy.
OUTCOME_COMPLETE = "complete"
OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_EMPTY = "empty"
TRAVERSAL_OUTCOMES = frozenset({OUTCOME_COMPLETE, OUTCOME_INCOMPLETE, OUTCOME_EMPTY})

# Resolution classes admitted by each profile.  Strict admits only accepted
# direct_resolved observations; conservative adds the possible/indirect/
# lexical/unresolved classes while every result retains its own class.
CONSERVATIVE_EXTRA_CLASSES = frozenset({
    "declared_virtual_target",
    "possible_dispatch_target",
    "indirect_callsite",
    "dependent_template_call",
    RESOLUTION_CLASS_LEXICAL_CANDIDATE,
    "constructor_call",
    RESOLUTION_CLASS_UNRESOLVED,
})

# Classes that downgrade traversal confidence but never count as confirmed
# direct calls in impact scoring.
WEAK_EVIDENCE_CLASSES = CONSERVATIVE_EXTRA_CLASSES


def class_allowed_in_profile(resolution_class: str, profile: str) -> bool:
    """Whether a resolution class may appear in a profile's results."""

    normalized = str(profile or QUERY_PROFILE_DEFAULT).strip().lower()
    if normalized == QUERY_PROFILE_STRICT:
        return resolution_class == RESOLUTION_CLASS_DIRECT_RESOLVED
    if normalized == QUERY_PROFILE_CONSERVATIVE:
        return (
            resolution_class == RESOLUTION_CLASS_DIRECT_RESOLVED
            or resolution_class in CONSERVATIVE_EXTRA_CLASSES
        )
    return resolution_class == RESOLUTION_CLASS_DIRECT_RESOLVED


def coverage_is_complete(records: Iterable[Any]) -> bool:
    """True only when every record in the frontier reports complete status."""

    seen = False
    for record in records:
        seen = True
        status = record.get("status") if isinstance(record, Mapping) else getattr(record, "status", "")
        if status != "complete":
            return False
    return seen


def _record_status(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record.get("status") or "")
    return str(getattr(record, "status", "") or "")


def _record_detail(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record.get("detail") or "")
    return str(getattr(record, "detail", "") or "")


def _record_identity(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record.get("tu_key") or record.get("frontier") or "")
    return str(getattr(record, "tu_key", "") or "")


def frontier_coverage(records: Iterable[Any]) -> dict[str, Any]:
    """Accumulate coverage over a visited frontier.

    Returns a provider-neutral block with ``status`` one of ``complete``,
    ``partial``, or ``unknown`` (no coverage records at all), the per-status
    reasons, and evidence counts.  ``unknown`` never licenses a negative
    conclusion.
    """

    counts: dict[str, int] = {}
    reasons: list[str] = []
    for record in records:
        status = _record_status(record)
        counts[status] = counts.get(status, 0) + 1
        detail = _record_detail(record)
        identity = _record_identity(record)
        if status != "complete":
            reason = f"{identity or 'frontier'}: {status}"
            if detail:
                reason += f" ({detail})"
            reasons.append(reason)
    if not counts:
        return {
            "status": "unknown",
            "reasons": ["no semantic coverage records found for the visited frontier"],
            "counts": {},
            "record_count": 0,
        }
    if counts.get("complete", 0) == sum(counts.values()):
        status = "complete"
    else:
        status = "partial"
    return {
        "status": status,
        "reasons": reasons,
        "counts": counts,
        "record_count": sum(counts.values()),
    }


_SCOPE_KEY_FIELDS = (
    "project_id",
    "generation_id",
    "revision",
    "policy_version",
    "tu_key",
    "config_fingerprint",
)


def semantic_scope_key(record: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(record.get(field) or "") for field in _SCOPE_KEY_FIELDS)


def exact_frontier_coverage(
    expected_keys: Iterable[Mapping[str, Any]],
    actual_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare immutable expected scope keys with current coverage records.

    Completeness is fail-closed: missing, duplicate, unexpected, stale, or
    non-complete actual rows make the whole requested scope partial. Runtime
    traversal results cannot shrink the expected domain.
    """

    expected = [semantic_scope_key(record) for record in expected_keys]
    actual = [dict(record) for record in actual_records]
    if not expected:
        return {
            "status": "unknown",
            "expected_key_count": 0,
            "actual_key_count": len(actual),
            "reasons": ["semantic_scope_manifest_missing"],
        }
    expected_set = set(expected)
    counts: dict[tuple[str, ...], int] = {}
    actual_by_key: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for record in actual:
        key = semantic_scope_key(record)
        counts[key] = counts.get(key, 0) + 1
        actual_by_key.setdefault(key, []).append(record)
    actual_set = set(actual_by_key)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    incomplete = sorted(
        key
        for key in expected_set & actual_set
        if len(actual_by_key[key]) != 1
        or str(actual_by_key[key][0].get("status") or "") != "complete"
    )
    reasons: list[str] = []
    if missing:
        reasons.append("scope_keys_missing")
    if unexpected:
        reasons.append("scope_keys_unexpected")
    if duplicates:
        reasons.append("scope_keys_duplicate")
    if incomplete:
        reasons.append("scope_keys_incomplete")
    return {
        "status": "complete" if not reasons else "partial",
        "expected_key_count": len(expected_set),
        "actual_key_count": len(actual),
        "missing_key_count": len(missing),
        "unexpected_key_count": len(unexpected),
        "duplicate_key_count": len(duplicates),
        "incomplete_key_count": len(incomplete),
        "reasons": reasons,
        "scope_fingerprint": _fingerprint_payload(sorted(expected_set)),
    }


def traversal_outcome(coverage_status: str, result_is_empty: bool) -> str:
    """Typed outcome for a traversal: empty answers are authoritative only
    under complete coverage; otherwise the traversal is ``incomplete``."""

    if result_is_empty:
        return OUTCOME_COMPLETE if coverage_status == "complete" else OUTCOME_INCOMPLETE
    return OUTCOME_COMPLETE


def suggested_next_semantic_scope(coverage_block: Mapping[str, Any]) -> list[str]:
    """Bounded next-scope hints derived from non-complete coverage reasons."""

    reasons = [
        str(reason)
        for reason in (coverage_block.get("reasons") or [])
        if ": " in str(reason)
    ]
    scopes = [reason.split(":", 1)[0] for reason in reasons][:10]
    if not scopes and str(coverage_block.get("status")) != "complete":
        scopes = ["run semantic analysis to produce coverage records"]
    return scopes
