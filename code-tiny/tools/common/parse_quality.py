"""Provider-neutral parse-quality contracts and deterministic policy helpers.

The contract intentionally has no graph, vector-store, CLI, or libclang
dependency.  Parser adapters populate records; downstream consumers may retain
the compact provenance while detailed diagnostics stay in run artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PARSE_QUALITY_SCHEMA_VERSION = "1"
RECOVERY_POLICY_VERSION = "1"
MAX_DAMAGE_SIGNATURES = 32


class QualityTier(str, Enum):
    CLEAN = "clean"
    RECOVERED = "recovered"
    RETRY_REQUIRED = "retry_required"
    QUARANTINED = "quarantined"


class ParserBackend(str, Enum):
    TREE_SITTER = "tree_sitter"
    LIBCLANG = "libclang"
    WINDOWS_RESOURCE = "windows_resource"


class RetryStage(str, Enum):
    LEGACY_DECODE = "legacy_decode"
    ALTERNATE_GRAMMAR = "alternate_grammar"
    DIALECT_MASKING = "dialect_masking"
    LIBCLANG = "libclang"


class CandidateOutcome(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    SELECTED = "selected"
    NOT_IMPROVED = "not_improved"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RESOURCE_LIMIT = "resource_limit"
    INVALID = "invalid"


@dataclass(frozen=True)
class DamageSummary:
    error_count: int = 0
    missing_count: int = 0
    damaged_bytes: int = 0
    source_bytes: int = 0
    damaged_span_ratio: float = 0.0
    critical_structural_damage: bool = False
    structural_contexts: Tuple[str, ...] = ()
    signatures: Tuple[str, ...] = ()

    @property
    def diagnostic_count(self) -> int:
        return self.error_count + self.missing_count


@dataclass(frozen=True)
class SemanticYield:
    function_count: int = 0
    type_count: int = 0
    declaration_count: int = 0
    stable_scope_count: int = 0
    call_count: int = 0
    include_count: int = 0

    @property
    def top_level_count(self) -> int:
        return self.function_count + self.type_count + self.declaration_count

    @property
    def useful_reference_count(self) -> int:
        return self.call_count + self.include_count


@dataclass(frozen=True)
class ParseContext:
    backend: ParserBackend = ParserBackend.TREE_SITTER
    parser_language: str = "unknown"
    parser_version: str = "unknown"
    grammar_version: str = "unknown"
    source_encoding: str = "unknown"
    lossy_decode: bool = False
    compile_context_available: bool = False
    compile_context_fingerprint: str = ""
    masking_fingerprint: str = ""
    recovery_policy_version: str = RECOVERY_POLICY_VERSION


@dataclass(frozen=True)
class CandidateSummary:
    damage: DamageSummary
    semantic_yield: SemanticYield
    backend: ParserBackend = ParserBackend.TREE_SITTER


@dataclass(frozen=True)
class ParseQualityRecord:
    file_path: str
    source_fingerprint: str
    context_fingerprint: str
    tier: QualityTier
    damage: DamageSummary
    semantic_yield: SemanticYield
    context: ParseContext
    retry_stages: Tuple[RetryStage, ...] = ()
    candidate_outcome: CandidateOutcome = CandidateOutcome.NOT_ATTEMPTED
    selected_candidate: str = "baseline"
    selection_reason: str = "first_pass"
    elapsed_ms: float = 0.0
    schema_version: str = PARSE_QUALITY_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class QualityThresholds:
    retry_damage_ratio: float = 0.08
    quarantine_damage_ratio: float = 0.35


_CRITICAL_STRUCTURAL_KINDS = frozenset(
    {
        "function_definition",
        "function_declarator",
        "declaration",
        "parameter_declaration",
        "parameter_list",
        "class_specifier",
        "struct_specifier",
        "union_specifier",
        "enum_specifier",
        "namespace_definition",
        "compound_statement",
        "field_declaration",
        "template_declaration",
    }
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def source_fingerprint(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def normalized_repository_path(root: str, path: str) -> str:
    root_real = os.path.realpath(os.path.abspath(root))
    path_real = os.path.realpath(os.path.abspath(path))
    try:
        common = os.path.commonpath((root_real, path_real))
    except ValueError as exc:
        raise ValueError("source path is outside the repository root") from exc
    if common != root_real:
        raise ValueError("source path is outside the repository root")
    rel_path = os.path.relpath(path_real, root_real).replace("\\", "/")
    if rel_path == ".." or rel_path.startswith("../"):
        raise ValueError("source path is outside the repository root")
    return rel_path


def context_fingerprint(context: ParseContext, source_hash: str) -> str:
    payload = {
        "schema_version": PARSE_QUALITY_SCHEMA_VERSION,
        "source_fingerprint": source_hash,
        "context": _json_value(context),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _merge_intervals(intervals: Sequence[Tuple[int, int]]) -> int:
    total = 0
    current_start: Optional[int] = None
    current_end: Optional[int] = None
    for start, end in sorted(intervals):
        if current_start is None:
            current_start, current_end = start, end
            continue
        assert current_end is not None
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        total += current_end - current_start
    return total


def collect_tree_sitter_damage(root_node: Any, source_size: int) -> DamageSummary:
    """Collect ERROR and MISSING diagnostics in one iterative tree walk."""

    error_count = 0
    missing_count = 0
    intervals: List[Tuple[int, int]] = []
    contexts = set()
    signatures: List[str] = []
    critical = False
    stack: List[Tuple[Any, Optional[str]]] = [(root_node, None)]

    while stack:
        node, parent_kind = stack.pop()
        node_type = str(getattr(node, "type", "unknown"))
        is_error = bool(getattr(node, "is_error", False)) or node_type == "ERROR"
        is_missing = bool(getattr(node, "is_missing", False))
        if is_error or is_missing:
            if is_error:
                error_count += 1
            if is_missing:
                missing_count += 1
            start = max(0, min(int(getattr(node, "start_byte", 0) or 0), source_size))
            end = max(start, min(int(getattr(node, "end_byte", start) or start), source_size))
            if end == start and source_size:
                end = min(source_size, start + 1)
            if end > start:
                intervals.append((start, end))
            context_kind = parent_kind or "root"
            contexts.add(context_kind)
            if context_kind in _CRITICAL_STRUCTURAL_KINDS:
                critical = True
            if len(signatures) < MAX_DAMAGE_SIGNATURES:
                point = getattr(node, "start_point", (0, 0)) or (0, 0)
                signatures.append(
                    f"{node_type}@{int(point[0]) + 1}:{int(point[1]) + 1}:{context_kind}"
                )

        children = list(getattr(node, "children", ()) or ())
        for child in reversed(children):
            stack.append((child, node_type))

    damaged_bytes = _merge_intervals(intervals)
    ratio = damaged_bytes / source_size if source_size else 0.0
    return DamageSummary(
        error_count=error_count,
        missing_count=missing_count,
        damaged_bytes=damaged_bytes,
        source_bytes=max(0, source_size),
        damaged_span_ratio=round(ratio, 8),
        critical_structural_damage=critical,
        structural_contexts=tuple(sorted(contexts)),
        signatures=tuple(signatures),
    )


def classify_quality(
    damage: DamageSummary,
    semantic_yield: SemanticYield,
    *,
    lossy_decode: bool = False,
    thresholds: QualityThresholds = QualityThresholds(),
) -> QualityTier:
    if (
        damage.damaged_span_ratio >= thresholds.quarantine_damage_ratio
        or (damage.critical_structural_damage and semantic_yield.top_level_count == 0)
    ):
        return QualityTier.QUARANTINED
    if (
        lossy_decode
        or damage.critical_structural_damage
        or damage.damaged_span_ratio >= thresholds.retry_damage_ratio
    ):
        return QualityTier.RETRY_REQUIRED
    if damage.diagnostic_count:
        return QualityTier.RECOVERED
    return QualityTier.CLEAN


def candidate_score(candidate: CandidateSummary) -> Tuple[int, float, int, int, int, int]:
    """Return the frozen whole-file ordering tuple; lower is better."""

    damage = candidate.damage
    semantic = candidate.semantic_yield
    return (
        1 if damage.critical_structural_damage else 0,
        round(damage.damaged_span_ratio, 8),
        -semantic.top_level_count,
        -semantic.stable_scope_count,
        -semantic.useful_reference_count,
        damage.diagnostic_count,
    )


def candidate_is_strictly_better(
    candidate: CandidateSummary,
    baseline: CandidateSummary,
) -> bool:
    candidate_value = candidate_score(candidate)
    baseline_value = candidate_score(baseline)
    if candidate.backend != baseline.backend:
        # Parser-specific syntax damage and diagnostic ranges are not comparable
        # across backends. Cross-backend replacement therefore requires a strict
        # improvement in the provider-neutral semantic-yield tuple.
        candidate_semantic = candidate.semantic_yield
        baseline_semantic = baseline.semantic_yield
        return (
            -candidate_semantic.top_level_count,
            -candidate_semantic.stable_scope_count,
            -candidate_semantic.useful_reference_count,
        ) < (
            -baseline_semantic.top_level_count,
            -baseline_semantic.stable_scope_count,
            -baseline_semantic.useful_reference_count,
        )
    return candidate_value < baseline_value


def build_quality_record(
    *,
    root: str,
    path: str,
    source: bytes,
    damage: DamageSummary,
    semantic_yield: SemanticYield,
    context: ParseContext,
    retry_stages: Iterable[RetryStage] = (),
    candidate_outcome: CandidateOutcome = CandidateOutcome.NOT_ATTEMPTED,
    selected_candidate: str = "baseline",
    selection_reason: str = "first_pass",
    elapsed_ms: float = 0.0,
) -> ParseQualityRecord:
    source_hash = source_fingerprint(source)
    return ParseQualityRecord(
        file_path=normalized_repository_path(root, path),
        source_fingerprint=source_hash,
        context_fingerprint=context_fingerprint(context, source_hash),
        tier=classify_quality(
            damage,
            semantic_yield,
            lossy_decode=context.lossy_decode,
        ),
        damage=damage,
        semantic_yield=semantic_yield,
        context=context,
        retry_stages=tuple(retry_stages),
        candidate_outcome=candidate_outcome,
        selected_candidate=selected_candidate,
        selection_reason=selection_reason,
        elapsed_ms=max(0.0, float(elapsed_ms)),
    )


def aggregate_quality_records(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    tiers = {tier.value: 0 for tier in QualityTier}
    aggregates: Dict[str, Any] = {
        "file_count": 0,
        "files_with_error": 0,
        "files_with_missing": 0,
        "lossy_decode_file_count": 0,
        "error_node_total": 0,
        "missing_node_total": 0,
        "grammar_retry_attempted": 0,
        "grammar_retry_selected": 0,
        "fallback_attempted": 0,
        "fallback_improved": 0,
        "quarantined_file_count": 0,
        "quality_tiers": tiers,
    }
    for record in records:
        aggregates["file_count"] += 1
        damage = record.get("damage") or {}
        context = record.get("context") or {}
        error_count = int(damage.get("error_count") or 0)
        missing_count = int(damage.get("missing_count") or 0)
        aggregates["error_node_total"] += error_count
        aggregates["missing_node_total"] += missing_count
        aggregates["files_with_error"] += int(error_count > 0)
        aggregates["files_with_missing"] += int(missing_count > 0)
        aggregates["lossy_decode_file_count"] += int(bool(context.get("lossy_decode")))
        tier = str(record.get("tier") or QualityTier.CLEAN.value)
        if tier not in tiers:
            tier = QualityTier.RETRY_REQUIRED.value
        tiers[tier] += 1
        aggregates["quarantined_file_count"] += int(tier == QualityTier.QUARANTINED.value)
        stages = set(record.get("retry_stages") or ())
        aggregates["grammar_retry_attempted"] += int(RetryStage.ALTERNATE_GRAMMAR.value in stages)
        aggregates["grammar_retry_selected"] += int(
            record.get("selected_candidate") == RetryStage.ALTERNATE_GRAMMAR.value
        )
        aggregates["fallback_attempted"] += int(RetryStage.LIBCLANG.value in stages)
        aggregates["fallback_improved"] += int(
            record.get("candidate_outcome") == CandidateOutcome.SELECTED.value
            and record.get("selected_candidate") == RetryStage.LIBCLANG.value
        )
    return aggregates


def atomic_write_json(
    path: str,
    payload: Mapping[str, Any],
    *,
    allowed_root: Optional[str] = None,
    max_bytes: int = 8 * 1024 * 1024,
) -> int:
    """Write a bounded JSON artifact atomically with owner-only permissions."""

    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if allowed_root is not None:
        root_real = os.path.realpath(os.path.abspath(allowed_root))
        target_parent_real = os.path.realpath(parent)
        if os.path.commonpath((root_real, target_parent_real)) != root_real:
            raise ValueError("artifact path is outside the allowed root")
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.islink(parent):
        raise ValueError("artifact directory must not be a symlink")
    encoded = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"artifact exceeds byte cap ({len(encoded)} > {max_bytes})")
    fd, temp_path = tempfile.mkstemp(prefix=".parse-quality-", suffix=".tmp", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return len(encoded)
