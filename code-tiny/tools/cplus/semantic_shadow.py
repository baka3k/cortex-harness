"""Shadow-mode semantic-evidence comparison report (Phase 02).

Runs the isolated semantic worker over a corpus in shadow mode and produces
a comparison artifact against the reviewed Phase 01 expectations.  Shadow
mode never replaces Tree-sitter structure and never publishes consumer
``CALLS`` edges; the report is the only output.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

from tools.common.call_evidence import CALL_EVIDENCE_SCHEMA_VERSION
from tools.cplus.parse_recovery import run_semantic_worker
from tools.cplus.semantic_worker import (
    SEMANTIC_BACKEND_ID,
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
)

SHADOW_REPORT_SCHEMA_VERSION = "2"
DIFFERENTIAL_REPORT_SCHEMA_VERSION = "2"

_CPP_FILES = (".cpp", ".cc", ".cxx", ".hpp", ".hh")
_C_FILES = (".c", ".h")

_STRUCTURAL_COLLECTIONS = {
    "files": "File",
    "namespaces": "Namespace",
    "types": "Type",
    "function_types": "FunctionType",
    "functions": "Function",
    "fields": "Field",
    "aliases": "Alias",
    "templates": "Template",
    "resources": "Resource",
    "resource_elements": "ResourceElement",
    "proc_nodes": "SqlStatement",
    "calls": "Callsite",
}

# These node families are emitted by both structural adapters.  If an accepted
# Tree-sitter identity is entirely absent from raw Clang inventory, that is
# adapter loss rather than an expected difference between structural and
# semantic planes.  Relations/callsites and other evidence-only rows are not
# required to be isomorphic across the two raw planes.
_REQUIRED_ADAPTER_NODE_LABELS = {"Function"}

_VOLATILE_PROJECTION_KEYS = {
    "comment",
    "summary",
    "note",
    "project_id",
    "generation_id",
    "parse_run_id",
    "run_id",
    "elapsed_ms",
    "elapsed_seconds",
    "timestamp",
    "created_at",
    "updated_at",
}

_CALLSITE_SEMANTIC_KEYS = (
    "callee_id",
    "callee_usr",
    "resolution_class",
    "semantic_provider",
    "tu_key",
    "config_fingerprint",
    "context_attestation",
    "manifest_key",
    "context_fidelity",
    "context_admission",
    "execution_coverage",
    "demoted_from",
)


def _stable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_PROJECTION_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _stable_properties(raw: Mapping[str, Any], excluded: Tuple[str, ...] = ()) -> Dict[str, Any]:
    excluded_keys = set(excluded) | _VOLATILE_PROJECTION_KEYS
    return {
        str(key): _stable_value(value)
        for key, value in sorted(raw.items(), key=lambda pair: str(pair[0]))
        if str(key) not in excluded_keys
    }


def _node_identity(raw: Mapping[str, Any], label: str) -> str:
    if label == "Callsite":
        callsite = (
            raw.get("logical_callsite_id")
            or raw.get("site_id")
            or raw.get("callsite_id")
        )
        if callsite:
            return str(callsite)
        return "call::{}::{}::{}::{}".format(
            raw.get("caller_id") or "",
            raw.get("caller_file") or raw.get("file_path") or "",
            raw.get("call_start_byte") or 0,
            raw.get("ordinal") or 0,
        )
    direct = raw.get("id") or raw.get("symbol_id") or raw.get("logical_id")
    if direct:
        return str(direct)
    file_path = str(raw.get("file_path") or "")
    if label == "File":
        return file_path
    name = str(raw.get("qualified_name") or raw.get("name") or raw.get("type_signature") or "")
    return "{}::{}::{}::{}".format(
        label,
        name,
        file_path,
        raw.get("start_byte") or raw.get("start_line") or 0,
    )


def _append_scalar_collection(
    projection: List[Dict[str, Any]], payload: Mapping[str, Any], collection: str, label: str
) -> None:
    values = payload.get(collection) or ()
    if isinstance(values, Mapping):
        iterable = values.items()
    else:
        iterable = enumerate(values)
    for key, value in iterable:
        projection.append(
            {
                "kind": "node",
                "label": label,
                "identity": str(key if isinstance(values, Mapping) else value),
                "span": [0, 0, 0, 0],
                "properties": _stable_value(value),
            }
        )


def canonical_structural_projection(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Canonical, multiplicity-preserving projection for differential gates.

    Only physical isolation and run-specific values are removed.  Structural
    kind, signature, type, span, alias/template details, and relation
    properties remain visible so an adapter cannot silently mutate them.
    """

    projection: List[Dict[str, Any]] = []
    for collection, label in _STRUCTURAL_COLLECTIONS.items():
        rows = payload.get(collection) or ()
        if collection == "files" and not rows and payload.get("file_def"):
            rows = (payload["file_def"],)
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            row_label = str(raw.get("label") or label)
            projection.append(
                {
                    "kind": "node",
                    "label": row_label,
                    "identity": _node_identity(raw, row_label),
                    "span": [
                        int(raw.get("start_byte") or 0),
                        int(raw.get("end_byte") or 0),
                        int(raw.get("start_line") or 0),
                        int(raw.get("end_line") or 0),
                    ],
                    "properties": _stable_properties(
                        raw,
                        (
                            "id",
                            "symbol_id",
                            "logical_id",
                            "logical_callsite_id",
                            "site_id",
                            "callsite_id",
                            "label",
                            "start_byte",
                            "end_byte",
                            "start_line",
                            "end_line",
                        )
                        + (_CALLSITE_SEMANTIC_KEYS if row_label == "Callsite" else ()),
                    ),
                }
            )
    for raw in payload.get("relations") or ():
        if not isinstance(raw, Mapping):
            continue
        projection.append(
            {
                "kind": "relation",
                "label": str(raw.get("rel_type") or ""),
                "identity": [
                    str(raw.get("source_label") or ""),
                    str(raw.get("source_id") or ""),
                    str(raw.get("target_label") or ""),
                    str(raw.get("target_id") or ""),
                ],
                "span": [0, 0, 0, 0],
                "properties": _stable_properties(
                    raw,
                    ("rel_type", "source_label", "source_id", "target_label", "target_id"),
                ),
            }
        )
    _append_scalar_collection(projection, payload, "includes", "Include")
    return sorted(
        projection,
        key=lambda row: json.dumps(row, ensure_ascii=True, sort_keys=True, default=list),
    )


def _row_text(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=list)


def _identity_text(row: Mapping[str, Any]) -> str:
    return json.dumps(
        {"kind": row.get("kind"), "label": row.get("label"), "identity": row.get("identity")},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    )


def _collision_keys(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        grouped[_identity_text(row)].append(_row_text(row))
    return {key: sorted(values) for key, values in grouped.items() if len(values) > 1}


def _classify_delta(
    *,
    stage: str,
    direction: str,
    row: Mapping[str, Any],
    collision_keys: set[str],
    current_identity_keys: set[str],
) -> str:
    if _identity_text(row) in collision_keys:
        return "identity_collision"
    if stage == "raw_clang":
        if (
            direction == "missing"
            and row.get("kind") == "node"
            and row.get("label") in _REQUIRED_ADAPTER_NODE_LABELS
            and _identity_text(row) not in current_identity_keys
        ):
            return "adapter_loss"
        return "expected_plane_difference"
    if stage == "validated_tree_sitter":
        return "validation_rejection"
    return "unexpected_persistence"


def build_differential_artifact(
    *,
    raw_tree_sitter: Mapping[str, Any],
    raw_clang: Mapping[str, Any],
    validated_tree_sitter: Mapping[str, Any],
    persisted_tree_sitter: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a deterministic four-stage plane differential with typed deltas."""

    stages = {
        "raw_tree_sitter": canonical_structural_projection(raw_tree_sitter),
        "raw_clang": canonical_structural_projection(raw_clang),
        "validated_tree_sitter": canonical_structural_projection(validated_tree_sitter),
        "persisted_tree_sitter": canonical_structural_projection(persisted_tree_sitter),
    }
    canonical_counters = {
        name: Counter(_row_text(row) for row in rows)
        for name, rows in stages.items()
    }
    collisions = {name: _collision_keys(rows) for name, rows in stages.items()}
    baseline = canonical_counters["raw_tree_sitter"]
    deltas: List[Dict[str, Any]] = []
    for stage in ("raw_clang", "validated_tree_sitter", "persisted_tree_sitter"):
        current = canonical_counters[stage]
        stage_collision_keys = set(collisions["raw_tree_sitter"]) | set(collisions[stage])
        current_identity_keys = {_identity_text(row) for row in stages[stage]}
        for direction, difference in (
            ("missing", baseline - current),
            ("added", current - baseline),
        ):
            for value, count in sorted(difference.items()):
                row = json.loads(value)
                deltas.append(
                    {
                        "stage": stage,
                        "direction": direction,
                        "classification": _classify_delta(
                            stage=stage,
                            direction=direction,
                            row=row,
                            collision_keys=stage_collision_keys,
                            current_identity_keys=current_identity_keys,
                        ),
                        "count": count,
                        "row": value,
                    }
                )
    for stage, stage_collisions in collisions.items():
        for identity, rows in sorted(stage_collisions.items()):
            deltas.append(
                {
                    "stage": stage,
                    "direction": "collision",
                    "classification": "identity_collision",
                    "count": len(rows),
                    "row": identity,
                }
            )
    blocking = [delta for delta in deltas if delta["classification"] != "expected_plane_difference"]
    tree_stages = ("raw_tree_sitter", "validated_tree_sitter", "persisted_tree_sitter")
    tree_sitter_invariant = (
        canonical_counters[tree_stages[0]]
        == canonical_counters[tree_stages[1]]
        == canonical_counters[tree_stages[2]]
        and not any(collisions[stage] for stage in tree_stages)
    )
    return {
        "schema_version": DIFFERENTIAL_REPORT_SCHEMA_VERSION,
        "stages": stages,
        "deltas": deltas,
        "collisions": collisions,
        "blocking_delta_count": len(blocking),
        "tree_sitter_invariant": tree_sitter_invariant,
        "passed": tree_sitter_invariant and not blocking,
    }


def _unavailable_differential(reason: str) -> Dict[str, Any]:
    return {
        "schema_version": DIFFERENTIAL_REPORT_SCHEMA_VERSION,
        "stages": {},
        "deltas": [
            {
                "stage": "input",
                "direction": "missing",
                "classification": "adapter_loss",
                "count": 1,
                "row": reason,
            }
        ],
        "collisions": {},
        "blocking_delta_count": 1,
        "tree_sitter_invariant": False,
        "passed": False,
        "status": "unavailable",
        "reason": reason,
    }


def _build_file_differential_inputs(
    *, root: str, rel_file: str
) -> Dict[str, Mapping[str, Any]]:
    """Build all four Phase-02 stages for one file on a frozen workspace.

    The persisted stage is the expected provider-neutral payload after the
    normal validation boundary.  Live provider readback remains a Phase-05
    concern and must not be inferred from this local shadow artifact.
    """

    # Local imports avoid making analyzer startup depend on the shadow runner.
    from tools.common.payload_validation import validate_cplus_payload
    from tools.cplus import clang_parser
    from tools.cplus.cplus_analyzer import _load_or_parse_payload

    abs_path = os.path.join(root, rel_file)
    raw_tree_sitter = _load_or_parse_payload(
        abs_path,
        root,
        os.path.join(root, ".cortex", "shadow-parse-cache"),
        False,
        project_id="semantic-shadow",
    )
    raw_clang = clang_parser.parse_and_extract(abs_path, root, "")
    if raw_clang is None:
        raise RuntimeError("clang_adapter_unavailable")
    validated_tree_sitter, _quarantine = validate_cplus_payload(
        raw_tree_sitter, project_id="semantic-shadow"
    )
    return {
        "raw_tree_sitter": raw_tree_sitter,
        "raw_clang": raw_clang,
        "validated_tree_sitter": validated_tree_sitter,
        "persisted_tree_sitter": validated_tree_sitter,
    }


def build_file_differential_artifact(*, root: str, rel_file: str) -> Dict[str, Any]:
    """Build the normal per-file four-stage shadow artifact."""

    return build_differential_artifact(
        **_build_file_differential_inputs(root=root, rel_file=rel_file)
    )


def _default_arguments(path: str) -> List[str]:
    if path.lower().endswith(_CPP_FILES):
        return ["-std=c++17"]
    if path.lower().endswith(_C_FILES):
        return ["-std=c11"]
    return []


# Reviewed-expectation semantics: each semantic_expectation string maps to the
# exact set of acceptable observed resolution classes.  Specialized meanings
# (macro origin, distinct overload USRs) are explicit predicates so every
# expectation can actually fail — no ad hoc string prefix matching.
_EXPECTATION_ALLOWED_CLASSES = {
    "direct_resolved": ("direct_resolved",),
    "direct_resolved_or_unresolved": ("direct_resolved", "unresolved"),
    "unresolved": ("unresolved",),
    "indirect_callsite": ("indirect_callsite",),
    "declared_virtual_target": ("declared_virtual_target",),
    "dependent_template_call": ("dependent_template_call",),
    "macro_expansion_to_direct_resolved": ("direct_resolved",),
    "direct_resolved_distinct_overloads": ("direct_resolved",),
    "direct_resolved_with_proc_bundle": ("direct_resolved",),
    "precompiler_runtime_or_unmapped": ("direct_resolved", "unresolved"),
}


def _expectation_matches(
    want_class: str,
    observed_sites: List[Mapping[str, Any]],
    observed: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Evaluate one expectation against the observed evidence.

    Returns (ok, reason).  Unknown expectation strings fail closed.
    """

    allowed = _EXPECTATION_ALLOWED_CLASSES.get(want_class)
    if allowed is None:
        return False, "unknown_expectation"
    observed_class = str(observed.get("resolution_class") or "")
    if observed_class not in allowed:
        return False, f"class_not_in_{want_class}"
    if want_class == "macro_expansion_to_direct_resolved" and not observed.get("macro_origin"):
        return False, "missing_macro_origin"
    if want_class == "direct_resolved_distinct_overloads":
        usrs = {
            str(site.get("callee_usr") or "")
            for site in observed_sites
            if site.get("callee_name") == observed.get("callee_name")
        }
        usrs.discard("")
        if len(usrs) < len(
            [s for s in observed_sites if s.get("callee_name") == observed.get("callee_name")]
        ):
            return False, "overload_usrs_not_distinct"
    return True, ""


def run_shadow_comparison(
    *,
    root: str,
    files: List[str],
    output_path: str,
    worker_path: str,
    expected: Optional[Mapping[str, Any]] = None,
    differential_inputs: Optional[
        Mapping[str, Mapping[str, Mapping[str, Any]]]
    ] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Run the semantic worker in shadow mode over ``files`` and report.

    ``expected`` is the reviewed expectations document
    (``tests/fixtures/cplus_semantic_calls/expected.json``).  The report
    records per-file observed classes and expectation matches; it is a
    comparison artifact only.  A four-stage differential is always emitted
    for every file.  Callers may supply a ``rel_file -> four stage payloads``
    map (for example, provider readback in a canary); otherwise this runner
    builds raw, validated, and expected-persisted stages locally.  Missing or
    failed stage construction is represented as a blocking artifact.
    """

    expectations = dict((expected or {}).get("files") or {})
    per_file: Dict[str, Any] = {}
    matched = mismatched = missing = 0
    differential_passed = differential_failed = 0

    for rel_file in files:
        abs_path = os.path.join(root, rel_file)
        request = {
            "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
            "request_schema": SEMANTIC_REQUEST_SCHEMA,
            "root": root,
            "path": abs_path,
            "compile_arguments": _default_arguments(rel_file),
            "compile_context_fingerprint": "shadow",
            "memory_mb": 1024,
            "cpu_seconds": 20,
        }
        result = run_semantic_worker(
            worker_path=worker_path,
            request=request,
            timeout_seconds=timeout_seconds,
        )
        entry: Dict[str, Any] = {
            "worker_status": result.get("status"),
            "error": result.get("error") or "",
            "callsites": [],
        }
        if result.get("status") == "ok":
            entry["callsites"] = [
                {
                    "callee_name": site.get("callee_name"),
                    "resolution_class": site.get("resolution_class"),
                    "callee_usr": site.get("callee_usr"),
                    "macro_origin": site.get("macro_origin"),
                }
                for site in result.get("callsites") or ()
            ]

        try:
            supplied = (differential_inputs or {}).get(rel_file)
            if supplied is None:
                entry["differential"] = build_file_differential_artifact(
                    root=root, rel_file=rel_file
                )
            else:
                required_stages = {
                    "raw_tree_sitter",
                    "raw_clang",
                    "validated_tree_sitter",
                    "persisted_tree_sitter",
                }
                missing_stages = sorted(required_stages - set(supplied))
                if missing_stages:
                    entry["differential"] = _unavailable_differential(
                        "missing_stages:" + ",".join(missing_stages)
                    )
                else:
                    entry["differential"] = build_differential_artifact(
                        raw_tree_sitter=supplied["raw_tree_sitter"],
                        raw_clang=supplied["raw_clang"],
                        validated_tree_sitter=supplied["validated_tree_sitter"],
                        persisted_tree_sitter=supplied["persisted_tree_sitter"],
                    )
        except Exception as exc:
            entry["differential"] = _unavailable_differential(
                "stage_construction_failed:" + type(exc).__name__
            )
        if entry["differential"]["passed"]:
            differential_passed += 1
        else:
            differential_failed += 1
        per_file[rel_file] = entry

        file_expectation = expectations.get(rel_file)
        if file_expectation:
            checks = []
            for expected_site in file_expectation.get("callsites") or ():
                want_name = expected_site.get("callee_name")
                want_class = str(expected_site.get("semantic_expectation") or "")
                observed = next(
                    (s for s in entry["callsites"] if s.get("callee_name") == want_name),
                    None,
                )
                if observed is None:
                    missing += 1
                    checks.append({"callee_name": want_name, "match": "missing"})
                    continue
                observed_class = str(observed.get("resolution_class") or "")
                ok, why = _expectation_matches(want_class, entry["callsites"], observed)
                if ok:
                    matched += 1
                    checks.append(
                        {"callee_name": want_name, "match": "match", "observed": observed_class}
                    )
                else:
                    mismatched += 1
                    checks.append(
                        {
                            "callee_name": want_name,
                            "match": "mismatch",
                            "expected": want_class,
                            "observed": observed_class,
                            "reason": why,
                        }
                    )
            entry["expectation_checks"] = checks

    report = {
        "schema_version": SHADOW_REPORT_SCHEMA_VERSION,
        "evidence_schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
        "mode": "shadow",
        "backend": SEMANTIC_BACKEND_ID,
        "published_calls": 0,  # shadow mode never publishes
        "files": per_file,
        "summary": {
            "files": len(files),
            "matched": matched,
            "mismatched": mismatched,
            "missing": missing,
            "differential_passed": differential_passed,
            "differential_failed": differential_failed,
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=True, indent=2, sort_keys=True)
    return report
