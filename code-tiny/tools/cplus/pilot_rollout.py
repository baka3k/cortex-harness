"""Reproducible Phase 07 semantic-call pilot and rollout decision contract.

The pilot deliberately separates measurement from promotion.  A report can be
complete and reproducible while still deciding to remain in containment.  In
particular, synthetic developer fixtures never satisfy the real-workload gate,
and missing provider or rollback evidence is a failed gate rather than a value
inferred from unit-test success.

This module is intentionally storage-neutral.  The benchmark CLI and real
canary orchestration supply evidence records; this module validates their
shared horizon, calculates the reviewed metrics, builds the separate Pro*C
scorecard, and applies the non-waivable rollout rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PILOT_SCHEMA_VERSION = "1"
PILOT_POLICY_VERSION = "cplus-semantic-pilot-v1"

DECISION_PROMOTE = "promote_comprehensive"
DECISION_CONTAIN = "remain_in_containment"
DECISION_RECONVENE = "revise_and_reconvene"
TERMINAL_DECISIONS = frozenset(
    {DECISION_PROMOTE, DECISION_CONTAIN, DECISION_RECONVENE}
)

CONTEXT_STATES = frozenset(
    {"faithful", "inherited", "synthetic", "missing", "rejected", "failed"}
)
SEMANTIC_MODES = ("containment", "sparse", "comprehensive")
DIRECT_CLASS = "direct_resolved"

REQUIRED_COHORTS = frozenset(
    {"c", "cpp", "headers", "macro_template", "multi_configuration", "generated", "proc"}
)
REQUIRED_SUITES = frozenset(
    {
        "unit",
        "integration",
        "adversarial",
        "provider",
        "incremental",
        "publication",
        "rollback",
        "consumer_contract",
    }
)
REQUIRED_PROVIDERS = frozenset({"neo4j", "falkordb"})
MINIMUM_PROMOTION_THRESHOLDS = {
    "direct_precision_min": 0.98,
    "direct_recall_min": 0.95,
    "priority_faithful_context_min": 0.90,
}
REQUIRED_PROC_COHORT_DIMENSIONS = frozenset(
    {
        "pc_extension",
        "pcc_extension",
        "c_mode",
        "cpp_mode",
        "utf8_encoding",
        "cp932_encoding",
        "sql_statements",
        "directives",
        "cursors",
        "host_variables",
        "dynamic_sql",
        "application_calls",
        "mapped_generated_output",
        "runtime_wrappers",
        "all_map_qualities",
        "configuration_variants",
        "source_change",
        "generated_change",
        "map_change",
        "context_change",
    }
)

PROC_LABELS = frozenset(
    {
        "SqlStatement",
        "SqlDirective",
        "SqlCursor",
        "SqlHostVariable",
        "DatabaseTable",
    }
)
PROC_RELATIONS = frozenset(
    {
        "DECLARES_STATEMENT",
        "DECLARES_DIRECTIVE",
        "BINDS_PARAMETER",
        "DECLARES_CURSOR",
        "REFERENCES_CURSOR",
        "REFERENCES_STATEMENT",
        "READS_FROM",
        "WRITES_TO",
        "REFERENCES_TABLE",
    }
)

_CREDENTIAL_MARKERS = (
    "password=",
    "passwd=",
    "identified by",
    "user id=",
    "userid=",
    "connect_data",
    "oracle_sid",
)
_HEX_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_EMBEDDED_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:^|[\s='\"])[A-Za-z]:[\\/]")
_EMBEDDED_UNIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s='\"])/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~+-]+)+")
_URI_USERINFO = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/@]+@")
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|api_key|access_key|credential|private_key|authorization|auth|bearer|cookie|session|connection_string)(?:$|_)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)


class PilotContractError(ValueError):
    """Raised when a manifest or evidence bundle violates the pilot contract."""


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PilotContractError(message)


def _safe_relative_path(root: Path, raw_path: str) -> Path:
    _require(bool(raw_path), "corpus entry requires path")
    path = Path(raw_path)
    _require(not path.is_absolute(), f"corpus path must be relative: {raw_path!r}")
    root_real = root.resolve()
    resolved = (root_real / path).resolve()
    _require(
        resolved == root_real or root_real in resolved.parents,
        f"corpus path escapes workspace: {raw_path!r}",
    )
    return resolved


def _credential_marker(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)) and str(item or "").strip():
                return f"sensitive_key:{key}"
            marker = _credential_marker(key) or _credential_marker(item)
            if marker:
                return marker
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            marker = _credential_marker(item)
            if marker:
                return marker
        return None
    lowered = str(value or "").lower()
    if _URI_USERINFO.search(str(value or "")):
        return "uri_userinfo"
    if _BEARER_VALUE.search(str(value or "")):
        return "bearer_credential"
    return next((marker for marker in _CREDENTIAL_MARKERS if marker in lowered), None)


def _external_absolute_path(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for item in value.values():
            path = _external_absolute_path(item)
            if path:
                return path
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            path = _external_absolute_path(item)
            if path:
                return path
        return None
    text = str(value or "").strip()
    if (
        text.startswith("/")
        or _WINDOWS_ABSOLUTE_PATH.match(text)
        or _EMBEDDED_WINDOWS_ABSOLUTE_PATH.search(text)
        or _EMBEDDED_UNIX_ABSOLUTE_PATH.search(text)
    ):
        return text
    return None


def validate_pilot_evidence(evidence: Mapping[str, Any]) -> None:
    """Reject secrets and raw machine paths before report persistence."""

    marker = _credential_marker(evidence)
    _require(marker is None, f"credential-bearing evidence rejected: {marker}")
    absolute_path = _external_absolute_path(evidence)
    _require(
        absolute_path is None,
        "absolute machine path in evidence rejected; persist a redacted fingerprint instead",
    )


def load_pilot_manifest(
    path: str | os.PathLike[str], *, workspace_root: str | os.PathLike[str]
) -> dict[str, Any]:
    """Load and verify an immutable, workspace-contained pilot manifest."""

    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict), "pilot manifest must be a JSON object")
    _require(
        str(manifest.get("schema_version")) == PILOT_SCHEMA_VERSION,
        f"unsupported pilot schema: {manifest.get('schema_version')!r}",
    )
    _require(bool(str(manifest.get("pilot_id") or "").strip()), "pilot_id is required")
    revision = str(manifest.get("revision") or "").strip().lower()
    _require(bool(_HEX_REVISION.fullmatch(revision)), "revision must be an immutable git hash")
    _require(
        manifest.get("workload_class") in {"real", "synthetic_developer_canary"},
        "workload_class must be real or synthetic_developer_canary",
    )
    _require(bool(manifest.get("supported_platforms")), "supported_platforms cannot be empty")
    _require(bool(manifest.get("query_scenarios")), "query_scenarios cannot be empty")
    _require(bool(manifest.get("resource_budgets")), "resource_budgets cannot be empty")
    _require(bool(manifest.get("corpus")), "corpus cannot be empty")
    marker = _credential_marker(manifest)
    _require(marker is None, f"credential-bearing manifest value rejected: {marker}")

    root = Path(workspace_root)
    ids: set[str] = set()
    cohort_kinds: set[str] = set()
    for entry in manifest["corpus"]:
        _require(isinstance(entry, dict), "corpus entries must be objects")
        entry_id = str(entry.get("id") or "").strip()
        _require(entry_id and entry_id not in ids, f"duplicate/empty corpus id: {entry_id!r}")
        ids.add(entry_id)
        cohort = str(entry.get("cohort") or "").strip()
        _require(bool(cohort), f"corpus entry {entry_id!r} requires cohort")
        cohort_kinds.add(cohort)
        resolved = _safe_relative_path(root, str(entry.get("path") or ""))
        _require(resolved.is_file(), f"corpus file does not exist: {entry.get('path')!r}")
        expected_hash = str(entry.get("sha256") or "").lower()
        _require(len(expected_hash) == 64, f"corpus entry {entry_id!r} requires sha256")
        _require(
            file_sha256(resolved) == expected_hash,
            f"corpus file hash mismatch: {entry.get('path')!r}",
        )
        contexts = entry.get("configurations") or []
        _require(bool(contexts), f"corpus entry {entry_id!r} requires configurations")
        for context in contexts:
            _require(bool(context.get("id")), f"corpus entry {entry_id!r} has unnamed configuration")
            _require(
                context.get("coverage_state") in CONTEXT_STATES,
                f"corpus entry {entry_id!r} has invalid context state",
            )
            if context.get("coverage_state") != "faithful":
                _require(
                    bool(str(context.get("reason") or "").strip()),
                    f"non-faithful context requires a stable reason: {entry_id!r}",
                )

    git_dir = root / ".git"
    _require(git_dir.exists(), "workspace_root must be a git checkout for revision verification")
    for entry in manifest["corpus"]:
        revision_path = f"{revision}:{entry['path']}"
        check = subprocess.run(
            ["git", "-C", str(root), "show", revision_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _require(
            check.returncode == 0,
            f"corpus file is absent from manifest revision: {entry['path']!r}",
        )
        revision_sha256 = hashlib.sha256(check.stdout).hexdigest()
        _require(
            revision_sha256 == str(entry.get("sha256") or "").lower(),
            f"corpus file hash does not match manifest revision: {entry['path']!r}",
        )

    query_ids = [str(item.get("id") or "").strip() for item in manifest["query_scenarios"]]
    _require(all(query_ids), "query scenarios require ids")
    _require(len(query_ids) == len(set(query_ids)), "query scenario ids must be unique")

    required = set(manifest.get("required_cohorts") or ())
    _require(required == REQUIRED_COHORTS, "required_cohorts is fixed by the Phase 07 policy")
    _require(required.issubset(cohort_kinds), f"manifest is missing cohorts: {sorted(required - cohort_kinds)}")
    _require(
        set(manifest.get("required_suites") or ()) == REQUIRED_SUITES,
        "required_suites is fixed by the Phase 07 policy",
    )
    _require(
        set(manifest.get("required_providers") or ()) == REQUIRED_PROVIDERS,
        "required_providers is fixed by the Phase 07 policy",
    )
    thresholds = dict(manifest.get("promotion_thresholds") or {})
    for name, minimum in MINIMUM_PROMOTION_THRESHOLDS.items():
        _require(
            float(thresholds.get(name, 0.0)) >= minimum,
            f"promotion threshold {name} cannot be lower than {minimum}",
        )
    proc_dimensions = {
        str(item.get("dimension") or "")
        for item in manifest.get("proc_cohort_census") or ()
    }
    _require(
        proc_dimensions == REQUIRED_PROC_COHORT_DIMENSIONS,
        "proc_cohort_census must enumerate every Phase 07 Pro*C dimension",
    )
    for item in manifest.get("proc_cohort_census") or ():
        _require(item.get("status") in {"covered", "unavailable"}, "invalid Pro*C cohort status")
        if item.get("status") != "covered":
            _require(bool(str(item.get("reason") or "").strip()), "unavailable Pro*C cohort requires a stable reason")
    return manifest


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Fingerprint the manifest without a self-referential fingerprint field."""

    payload = dict(manifest)
    payload.pop("manifest_fingerprint", None)
    return canonical_digest(payload)


def validate_hard_policy(manifest: Mapping[str, Any]) -> None:
    """Ensure callers cannot weaken non-waivable promotion policy fields."""

    _require(
        set(manifest.get("required_cohorts") or ()) == REQUIRED_COHORTS,
        "required_cohorts is fixed by the Phase 07 policy",
    )
    _require(
        set(manifest.get("required_suites") or ()) == REQUIRED_SUITES,
        "required_suites is fixed by the Phase 07 policy",
    )
    _require(
        set(manifest.get("required_providers") or ()) == REQUIRED_PROVIDERS,
        "required_providers is fixed by the Phase 07 policy",
    )
    thresholds = dict(manifest.get("promotion_thresholds") or {})
    for name, minimum in MINIMUM_PROMOTION_THRESHOLDS.items():
        _require(
            float(thresholds.get(name, 0.0)) >= minimum,
            f"promotion threshold {name} cannot be lower than {minimum}",
        )
    _require(
        manifest.get("resource_budgets", {}).get("million_loc_measurement_required") is True,
        "million-LOC measurement is a non-waivable Phase 07 gate",
    )
    proc_dimensions = {
        str(item.get("dimension") or "")
        for item in manifest.get("proc_cohort_census") or ()
    }
    _require(
        proc_dimensions == REQUIRED_PROC_COHORT_DIMENSIONS,
        "proc_cohort_census must enumerate every Phase 07 Pro*C dimension",
    )


def compile_context_census(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize context coverage by TU/configuration and priority cohort."""

    counts: Counter[str] = Counter()
    by_tu: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    uncovered: list[dict[str, str]] = []
    priority_total = 0
    priority_faithful = 0
    total = 0
    seen: set[tuple[str, str]] = set()
    duplicate_ids: list[str] = []
    for item in records:
        state = str(item.get("coverage_state") or "")
        _require(state in CONTEXT_STATES, f"unknown compile-context state: {state!r}")
        tu_id = str(item.get("tu_id") or "").strip()
        config_id = str(item.get("configuration_id") or "").strip()
        _require(tu_id and config_id, "context records require tu_id and configuration_id")
        identity = (tu_id, config_id)
        if identity in seen:
            duplicate_ids.append(f"{tu_id}:{config_id}")
        seen.add(identity)
        total += 1
        counts[state] += 1
        by_tu[tu_id][state] += 1
        priority = bool(item.get("priority"))
        if priority:
            priority_total += 1
            if state == "faithful":
                priority_faithful += 1
        if state != "faithful":
            reason = str(item.get("reason") or "").strip()
            uncovered.append(
                {
                    "tu_id": tu_id,
                    "configuration_id": config_id,
                    "coverage_state": state,
                    "reason": reason,
                }
            )
    stable_reasons = all(record["reason"] for record in uncovered)
    ratio = priority_faithful / priority_total if priority_total else 0.0
    return {
        "record_count": total,
        "counts": {state: counts.get(state, 0) for state in sorted(CONTEXT_STATES)},
        "priority": {
            "total": priority_total,
            "faithful": priority_faithful,
            "faithful_ratio": round(ratio, 6),
        },
        "all_uncovered_have_stable_reasons": stable_reasons,
        "uncovered": uncovered,
        "by_tu": {key: dict(sorted(value.items())) for key, value in sorted(by_tu.items())},
        "duplicate_context_ids": sorted(set(duplicate_ids)),
    }


def reviewed_accuracy(
    expected_facts: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate direct-call precision/recall and separate class outcomes.

    ``fact_id`` is the reviewed join key.  Missing observations are false
    negatives for expected direct facts; a direct observation attached to a
    reviewed non-direct fact is a false positive.  Unreviewed direct output is
    also a false positive and cannot disappear into aggregate edge counts.
    """

    expected: dict[str, str] = {}
    for fact in expected_facts:
        fact_id = str(fact.get("fact_id") or "").strip()
        expected_class = str(fact.get("expected_class") or "").strip()
        _require(fact_id and expected_class, "reviewed facts require fact_id and expected_class")
        _require(fact_id not in expected, f"duplicate reviewed fact_id: {fact_id!r}")
        expected[fact_id] = expected_class

    observed: dict[str, str] = {}
    duplicate_ids: list[str] = []
    for item in observations:
        fact_id = str(item.get("fact_id") or "").strip()
        observed_class = str(item.get("resolution_class") or "").strip()
        _require(fact_id and observed_class, "observations require fact_id and resolution_class")
        if fact_id in observed:
            duplicate_ids.append(fact_id)
        observed[fact_id] = observed_class

    expected_direct = {fact_id for fact_id, value in expected.items() if value == DIRECT_CLASS}
    observed_direct = {fact_id for fact_id, value in observed.items() if value == DIRECT_CLASS}
    true_positive = len(expected_direct & observed_direct)
    false_positive_ids = sorted(observed_direct - expected_direct)
    false_negative_ids = sorted(expected_direct - observed_direct)
    precision = true_positive / (true_positive + len(false_positive_ids)) if observed_direct else 0.0
    recall = true_positive / len(expected_direct) if expected_direct else 0.0

    class_results: dict[str, dict[str, int]] = {}
    for class_name in sorted(set(expected.values()) | set(observed.values())):
        expected_ids = {key for key, value in expected.items() if value == class_name}
        observed_ids = {key for key, value in observed.items() if value == class_name}
        class_results[class_name] = {
            "expected": len(expected_ids),
            "observed": len(observed_ids),
            "matched": len(expected_ids & observed_ids),
        }

    return {
        "reviewed_fact_count": len(expected),
        "observation_count": len(observations),
        "direct": {
            "true_positive": true_positive,
            "false_positive": len(false_positive_ids),
            "false_negative": len(false_negative_ids),
            "false_positive_fact_ids": false_positive_ids,
            "false_negative_fact_ids": false_negative_ids,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
        },
        "by_resolution_class": class_results,
        "duplicate_observation_fact_ids": sorted(set(duplicate_ids)),
        "unreviewed_observation_fact_ids": sorted(set(observed) - set(expected)),
    }


def impact_answer_score(
    scenarios: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score impact answers and enforce coverage-aware negative semantics."""

    expected_ids = [str(item.get("id") or "") for item in scenarios]
    outcome_ids = [str(item.get("id") or "") for item in outcomes]
    _require(len(expected_ids) == len(set(expected_ids)), "duplicate impact scenario id")
    _require(len(outcome_ids) == len(set(outcome_ids)), "duplicate impact outcome id")
    expected = {str(item.get("id")): item for item in scenarios}
    actual = {str(item.get("id")): item for item in outcomes}
    details: list[dict[str, Any]] = []
    correct = 0
    unsafe_negative_ids: list[str] = []
    unverified_ids: list[str] = []
    for scenario_id, scenario in expected.items():
        outcome = actual.get(scenario_id, {})
        expected_outcome = str(scenario.get("expected_outcome") or "")
        actual_outcome = str(outcome.get("outcome") or "missing")
        matches = actual_outcome == expected_outcome
        if matches:
            correct += 1
        coverage = str(outcome.get("coverage_status") or "unknown")
        fingerprint = str(outcome.get("evidence_fingerprint") or "").strip().lower()
        verified = (
            bool(outcome.get("reviewed"))
            and bool(str(outcome.get("evidence_ref") or "").strip())
            and len(fingerprint) == 64
            and all(character in "0123456789abcdef" for character in fingerprint)
        )
        if not verified:
            unverified_ids.append(scenario_id)
        is_negative = actual_outcome in {"no_impact", "no_callers", "unaffected"}
        unsafe = is_negative and coverage != "complete"
        if unsafe:
            unsafe_negative_ids.append(scenario_id)
        details.append(
            {
                "id": scenario_id,
                "expected_outcome": expected_outcome,
                "actual_outcome": actual_outcome,
                "coverage_status": coverage,
                "correct": matches,
                "unsafe_negative": unsafe,
                "reviewed_replay_evidence": verified,
                "evidence_ref": str(outcome.get("evidence_ref") or ""),
                "evidence_fingerprint": fingerprint,
            }
        )
    ratio = correct / len(expected) if expected else 0.0
    return {
        "scenario_count": len(expected),
        "correct": correct,
        "correctness": round(ratio, 6),
        "unsafe_negative_count": len(unsafe_negative_ids),
        "unsafe_negative_ids": unsafe_negative_ids,
        "unverified_replay_count": len(unverified_ids),
        "unverified_replay_ids": unverified_ids,
        "details": details,
    }


def compare_modes(mode_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate that all three modes used one revision/query horizon."""

    mode_names = [str(item.get("mode") or "") for item in mode_results]
    duplicate_modes = sorted(
        name for name, count in Counter(mode_names).items() if name and count > 1
    )
    modes = {str(item.get("mode") or ""): dict(item) for item in mode_results}
    missing = [mode for mode in SEMANTIC_MODES if mode not in modes]
    revisions = {str(item.get("revision") or "") for item in modes.values()}
    queries = {canonical_digest(item.get("query_scenarios") or []) for item in modes.values()}
    configs = {canonical_digest(item.get("configuration_ids") or []) for item in modes.values()}
    same_horizon = (
        not missing
        and not duplicate_modes
        and len(revisions) == len(queries) == len(configs) == 1
    )
    required_conditions = {"cold", "warm", "changed_tu"}
    missing_conditions = {
        mode: sorted(required_conditions - set((modes.get(mode, {}).get("conditions") or {}).keys()))
        for mode in SEMANTIC_MODES
    }
    conditions_complete = not missing and not any(missing_conditions.values())
    return {
        "same_horizon": same_horizon,
        "conditions_complete": conditions_complete,
        "missing_conditions": missing_conditions,
        "missing_modes": missing,
        "duplicate_modes": duplicate_modes,
        "revision_count": len(revisions),
        "query_horizon_count": len(queries),
        "configuration_horizon_count": len(configs),
        "modes": {mode: modes.get(mode, {}) for mode in SEMANTIC_MODES},
    }


def proc_scorecard(proc_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build the independent Pro*C component and gate scorecard."""

    labels = set(proc_evidence.get("labels_passed") or ())
    relations = set(proc_evidence.get("relations_passed") or ())
    missing_reasons = list(proc_evidence.get("incomplete_without_reason") or ())
    checks = {
        "complete_stratified_cohort": bool(proc_evidence.get("complete_stratified_cohort")),
        "discovery_and_routing": bool(proc_evidence.get("discovery_and_routing")),
        "decode_and_mask_alignment": float(proc_evidence.get("mask_alignment_ratio") or 0.0) == 1.0,
        "five_labels": labels == PROC_LABELS,
        "nine_relations": relations == PROC_RELATIONS,
        "compiler_context_redacted": bool(proc_evidence.get("compiler_context_redacted")),
        "artifact_and_map_coverage": float(proc_evidence.get("source_map_pass_ratio") or 0.0) == 1.0,
        "semantic_accuracy": bool(proc_evidence.get("semantic_accuracy_passed")),
        "generated_filtering": int(proc_evidence.get("generated_mispublished_count") or 0) == 0,
        "cross_domain_impact": bool(proc_evidence.get("cross_domain_impact_passed")),
        "cache_and_incremental": bool(proc_evidence.get("cache_invalidation_passed")),
        "graph_vector_parity": bool(proc_evidence.get("graph_vector_parity_passed")),
        "dynamic_sql_completeness": bool(proc_evidence.get("dynamic_sql_incomplete_visible")),
        "host_cursor_function_joins": bool(proc_evidence.get("join_incomplete_visible")),
        "security": bool(proc_evidence.get("security_passed")),
        "resource_budget": bool(proc_evidence.get("resource_budget_passed")),
        "failure_isolation": bool(proc_evidence.get("failure_isolation_passed")),
        "publication": bool(proc_evidence.get("publication_passed")),
        "rollback": bool(proc_evidence.get("rollback_passed")),
        "all_incomplete_have_reason": not missing_reasons,
        "sql_regression_zero": int(proc_evidence.get("sql_regression_count") or 0) == 0,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "labels": {"passed": sorted(labels), "missing": sorted(PROC_LABELS - labels)},
        "relations": {
            "passed": sorted(relations),
            "missing": sorted(PROC_RELATIONS - relations),
        },
        "metrics": {
            "mask_alignment_ratio": float(proc_evidence.get("mask_alignment_ratio") or 0.0),
            "source_map_pass_ratio": float(proc_evidence.get("source_map_pass_ratio") or 0.0),
            "generated_mispublished_count": int(proc_evidence.get("generated_mispublished_count") or 0),
            "sql_regression_count": int(proc_evidence.get("sql_regression_count") or 0),
        },
        "incomplete_without_reason": missing_reasons,
    }


def _gate(passed: bool, reason: str, evidence: Any = None) -> dict[str, Any]:
    result = {"passed": bool(passed), "reason": reason}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _valid_fingerprint(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def apply_decision_rules(
    *,
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    census: Mapping[str, Any],
    accuracy: Mapping[str, Any],
    impact: Mapping[str, Any],
    modes: Mapping[str, Any],
    proc: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply every non-waivable Phase 07 promotion gate."""

    thresholds = dict(manifest.get("promotion_thresholds") or {})
    precision_min = float(thresholds.get("direct_precision_min", 0.98))
    recall_min = float(thresholds.get("direct_recall_min", 0.95))
    context_min = float(thresholds.get("priority_faithful_context_min", 0.90))
    direct = accuracy["direct"]
    suites = dict(evidence.get("suites") or {})
    providers = dict(evidence.get("providers") or {})
    resources = dict(evidence.get("resources") or {})
    publication = dict(evidence.get("publication") or {})
    rollback = dict(evidence.get("rollback") or {})

    required_suites = tuple(manifest.get("required_suites") or ())
    suite_failures = [name for name in required_suites if suites.get(name) != "passed"]
    required_providers = tuple(manifest.get("required_providers") or ())
    provider_failures = [
        name
        for name in required_providers
        if not (
            providers.get(name, {}).get("status") == "passed"
            and _valid_fingerprint(providers.get(name, {}).get("fingerprint"))
            and providers.get(name, {}).get("integrity_passed") is True
            and providers.get(name, {}).get("deterministic_rerun") is True
            and providers.get(name, {}).get("crash_resume_passed") is True
            and providers.get(name, {}).get("publication_passed") is True
            and providers.get(name, {}).get("rollback_passed") is True
        )
    ]
    critical_findings = list(evidence.get("critical_findings") or ())

    gates = {
        "real_stratified_workload": _gate(
            manifest.get("workload_class") == "real",
            "real immutable workload required; synthetic fixtures are developer canaries only",
            manifest.get("workload_class"),
        ),
        "shared_mode_horizon": _gate(bool(modes.get("same_horizon")), "all modes must use one revision/config/query horizon"),
        "complete_mode_conditions": _gate(bool(modes.get("conditions_complete")), "containment, sparse, and comprehensive modes require cold/warm/changed-TU measurements", modes.get("missing_conditions")),
        "functional_adversarial_suites": _gate(not suite_failures, "every declared pre-canary suite must pass", suite_failures),
        "zero_weak_to_calls": _gate(int(evidence.get("weak_promoted_count") or 0) == 0, "weak evidence may never publish as CALLS"),
        "direct_precision": _gate(float(direct.get("precision") or 0.0) >= precision_min, f"direct precision must be >= {precision_min:.2%}"),
        "direct_recall": _gate(float(direct.get("recall") or 0.0) >= recall_min, f"direct recall must be >= {recall_min:.2%}"),
        "priority_compile_context": _gate(float(census.get("priority", {}).get("faithful_ratio") or 0.0) >= context_min, f"priority faithful-context ratio must be >= {context_min:.2%}"),
        "visible_noncoverage": _gate(bool(census.get("all_uncovered_have_stable_reasons")), "every uncovered TU/configuration requires a stable reason"),
        "unique_context_records": _gate(not census.get("duplicate_context_ids"), "TU/configuration census identities must be unique", census.get("duplicate_context_ids")),
        "impact_correctness": _gate(float(impact.get("correctness") or 0.0) == 1.0, "all reviewed impact scenarios must match"),
        "reviewed_impact_replay": _gate(int(impact.get("unverified_replay_count") or 0) == 0, "every impact outcome requires reviewed replay evidence"),
        "unique_observations": _gate(not accuracy.get("duplicate_observation_fact_ids"), "observation fact ids must be unique", accuracy.get("duplicate_observation_fact_ids")),
        "safe_negative_answers": _gate(int(impact.get("unsafe_negative_count") or 0) == 0, "incomplete coverage cannot support a negative claim"),
        "proc_component_gates": _gate(bool(proc.get("passed")), "every separate Pro*C component gate must pass"),
        "worker_readiness": _gate(
            evidence.get("worker", {}).get("status") == "passed"
            and evidence.get("worker", {}).get("ready") is True
            and bool(str(evidence.get("worker", {}).get("libclang_version") or "").strip()),
            "pinned semantic worker must be ready with a versioned runtime",
        ),
        "resource_budgets": _gate(
            bool(resources.get("all_within_budget"))
            and (
                not bool(manifest.get("resource_budgets", {}).get("million_loc_measurement_required"))
                or resources.get("million_loc_measured") is True
            ),
            "cold/warm/changed and required million-LOC scale budgets must pass",
        ),
        "operational_measurements": _gate(
            evidence.get("operational_measurements", {}).get("status") == "passed"
            and _valid_fingerprint(evidence.get("operational_measurements", {}).get("fingerprint"))
            and evidence.get("operational_measurements", {}).get("queue_measured") is True
            and evidence.get("operational_measurements", {}).get("cache_measured") is True
            and evidence.get("operational_measurements", {}).get("header_fanout_measured") is True
            and evidence.get("operational_measurements", {}).get("storage_measured") is True,
            "queue, cache, header fan-out, and storage require measured evidence",
        ),
        "provider_canaries": _gate(not provider_failures, "Neo4j and FalkorDB staging canaries must pass", provider_failures),
        "consumer_contract": _gate(bool(evidence.get("consumer_contract_passed")), "strict/conservative consumers must preserve coverage semantics"),
        "incremental_invalidation": _gate(bool(evidence.get("incremental_invalidation_passed")), "changed/config/map invalidation must pass"),
        "publication": _gate(
            publication.get("status") == "passed"
            and publication.get("deterministic_rerun") is True
            and publication.get("live_provider_canary") is True
            and _valid_fingerprint(publication.get("fingerprint")),
            "live atomic publication and deterministic rerun must pass",
        ),
        "rollback": _gate(
            rollback.get("status") == "passed"
            and rollback.get("last_valid_generation_retained") is True
            and rollback.get("live_provider_canary") is True
            and _valid_fingerprint(rollback.get("fingerprint")),
            "live rollback must preserve containment and last-valid generation",
        ),
        "failure_isolation": _gate(bool(evidence.get("failure_isolation_passed")), "timeout/crash/OOM/cancel failures must stay isolated"),
        "security": _gate(bool(evidence.get("security_passed")), "path/flag/credential safety suite must pass"),
        "no_critical_findings": _gate(not critical_findings, "critical findings cannot be waived", critical_findings),
    }

    failed = [name for name, gate in gates.items() if not gate["passed"]]
    if not failed:
        decision = DECISION_PROMOTE
        safe_action = "enable comprehensive eligible-TU semantic publication with the recorded configuration"
    elif any(
        name in failed
        for name in (
            "zero_weak_to_calls",
            "safe_negative_answers",
            "proc_component_gates",
            "publication",
            "rollback",
            "failure_isolation",
            "security",
            "no_critical_findings",
        )
    ):
        decision = DECISION_CONTAIN
        safe_action = "keep semantic publication off; retain Tree-sitter containment and the last valid generation"
    else:
        decision = DECISION_RECONVENE
        safe_action = "continue shadow-only sparse measurement; do not make repository-complete or negative claims"

    return {
        "decision": decision,
        "promotion_allowed": decision == DECISION_PROMOTE,
        "failed_gates": failed,
        "gates": gates,
        "safe_action": safe_action,
        "defaults_may_change": decision == DECISION_PROMOTE,
    }


def build_pilot_report(
    *,
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate evidence, calculate scorecards, and emit one terminal report."""

    validate_hard_policy(manifest)
    validate_pilot_evidence(evidence)

    context_records = list(evidence.get("compile_contexts") or ())
    expected_facts = list(evidence.get("expected_facts") or ())
    observations = list(evidence.get("observations") or ())
    mode_results = list(evidence.get("mode_results") or ())
    impact_outcomes = list(evidence.get("impact_outcomes") or ())

    census = compile_context_census(context_records)
    accuracy = reviewed_accuracy(expected_facts, observations)
    impact = impact_answer_score(manifest.get("query_scenarios") or (), impact_outcomes)
    modes = compare_modes(mode_results)
    proc = proc_scorecard(evidence.get("proc") or {})
    decision = apply_decision_rules(
        manifest=manifest,
        evidence=evidence,
        census=census,
        accuracy=accuracy,
        impact=impact,
        modes=modes,
        proc=proc,
    )

    report = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "policy_version": PILOT_POLICY_VERSION,
        "pilot_id": manifest.get("pilot_id"),
        "revision": manifest.get("revision"),
        "manifest_fingerprint": manifest_fingerprint(manifest),
        "evidence_fingerprint": canonical_digest(evidence),
        "workload_class": manifest.get("workload_class"),
        "toolchain": dict(evidence.get("toolchain") or {}),
        "semantic_policy": dict(manifest.get("semantic_policy") or {}),
        "parse_policy": dict(manifest.get("parse_policy") or {}),
        "compile_context_census": census,
        "mode_comparison": modes,
        "accuracy": accuracy,
        "impact_answers": impact,
        "proc_scorecard": proc,
        "resources": dict(evidence.get("resources") or {}),
        "providers": dict(evidence.get("providers") or {}),
        "security_and_faults": dict(evidence.get("security_and_faults") or {}),
        "publication": dict(evidence.get("publication") or {}),
        "rollback": dict(evidence.get("rollback") or {}),
        "decision": decision,
    }
    report["report_fingerprint"] = canonical_digest(report)
    return report


def write_report_bundle(
    *,
    output_dir: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, str]:
    """Write the versioned plan-scoped JSON bundle with deterministic bytes."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        "manifest": destination / "pilot-manifest.json",
        "evidence": destination / "pilot-evidence.json",
        "report": destination / "rollout-decision.json",
        "proc_scorecard": destination / "proc-scorecard.json",
    }
    payloads = {
        "manifest": manifest,
        "evidence": evidence,
        "report": report,
        "proc_scorecard": report.get("proc_scorecard") or {},
    }
    for key, target in files.items():
        target.write_text(
            json.dumps(payloads[key], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {key: str(path) for key, path in files.items()}
