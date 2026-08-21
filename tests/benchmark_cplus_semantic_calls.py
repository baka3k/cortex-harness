"""Run the Phase 07 developer canary and emit a rollout-decision bundle.

This executable uses the same immutable manifest for containment, sparse, and
comprehensive measurements.  Optional gate evidence may add results from the
pre-canary suites and real Neo4j/FalkorDB staging runs.  Absent evidence stays
``not_run``/false and therefore cannot accidentally promote publication.
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer  # noqa: E402
from tools.cplus.parse_recovery import run_semantic_worker  # noqa: E402
from tools.cplus.pilot_rollout import (  # noqa: E402
    PILOT_POLICY_VERSION,
    build_pilot_report,
    canonical_digest,
    file_sha256,
    load_pilot_manifest,
    write_report_bundle,
)
from tools.cplus.proc_analyzer import prepare_proc_path  # noqa: E402
from tools.cplus.semantic_context import (  # noqa: E402
    CoverageState,
    SemanticCache,
    SemanticCacheIdentity,
)
from tools.cplus.semantic_worker import (  # noqa: E402
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
    probe_clang_runtime,
)


DEFAULT_MANIFEST = (
    ROOT
    / "plans"
    / "260821-1144-cplus-semantic-call-graph"
    / "pilot-manifest.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "plans"
    / "260821-1144-cplus-semantic-call-graph"
    / "reports"
    / "developer-canary"
)
WORKER = CODE_TINY / "tools" / "cplus" / "clang_worker.py"

EXTERNAL_GATE_EVIDENCE_KEYS = frozenset(
    {
        "suites",
        "providers",
        "publication",
        "rollback",
        "consumer_contract_passed",
        "incremental_invalidation_passed",
        "failure_isolation_passed",
        "security_passed",
        "security_and_faults",
        "proc",
        "critical_findings",
        "repository_regression",
        "scale_resource_evidence",
        "operational_measurements",
    }
)


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _apply_gate_evidence(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(overlay) - EXTERNAL_GATE_EVIDENCE_KEYS)
    if unknown:
        raise ValueError(f"gate evidence cannot replace benchmark-owned fields: {unknown}")
    return _deep_merge(base, overlay)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return float(ordered[index])


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _metric_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(item["elapsed_ms"]) for item in samples]
    cpu_samples = [float(item.get("cpu_seconds") or 0.0) for item in samples]
    return {
        "tu_runs": len(samples),
        "successes": sum(item["status"] == "ok" for item in samples),
        "failures": sum(item["status"] != "ok" for item in samples),
        "elapsed_ms": {
            "total": round(sum(latencies), 3),
            "p50": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "cpu_seconds": {
            "total": round(sum(cpu_samples), 6),
            "max": round(max(cpu_samples), 6) if cpu_samples else 0.0,
        },
        "peak_rss_bytes": max((int(item.get("peak_rss_bytes") or 0) for item in samples), default=0),
    }


def _child_usage() -> tuple[float, int]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    return float(usage.ru_utime + usage.ru_stime), peak


def _self_usage() -> tuple[float, int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    peak = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    return float(usage.ru_utime + usage.ru_stime), peak


def _semantic_request(root: Path, path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    arguments = list(config.get("arguments") or ())
    return {
        "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
        "request_schema": SEMANTIC_REQUEST_SCHEMA,
        "root": str(root),
        "path": str(path),
        "compile_arguments": arguments,
        "compile_context_fingerprint": canonical_digest(
            {"configuration_id": config.get("id"), "arguments": arguments}
        ),
        "memory_mb": 1024,
        "cpu_seconds": 30,
        "max_output_bytes": 8 * 1024 * 1024,
        "max_source_bytes": 4 * 1024 * 1024,
    }


def _run_worker(root: Path, path: Path, config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cpu_before, _ = _child_usage()
    started = time.perf_counter()
    result = run_semantic_worker(
        worker_path=str(WORKER),
        request=_semantic_request(root, path, config),
        timeout_seconds=35,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    cpu_after, peak_rss = _child_usage()
    metric = {
        "status": str(result.get("status") or "failed"),
        "elapsed_ms": elapsed_ms,
        "cpu_seconds": max(0.0, cpu_after - cpu_before),
        "peak_rss_bytes": peak_rss,
    }
    return result, metric


def _context_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in manifest["corpus"]:
        for config in entry["configurations"]:
            records.append(
                {
                    "tu_id": entry["id"],
                    "configuration_id": config["id"],
                    "coverage_state": config["coverage_state"],
                    "reason": config.get("reason", ""),
                    "priority": bool(entry.get("priority")),
                    "cohort": entry["cohort"],
                }
            )
    return records


def _eligible(entry: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    return entry.get("semantic_eligible", True) and config.get("coverage_state") == "faithful"


def _fact_id(entry_id: str, config_id: str, expected: Mapping[str, Any]) -> str:
    return "{}:{}:{}:{}".format(
        entry_id,
        config_id,
        expected["callee_name"],
        int(expected.get("ordinal") or 0),
    )


def _reviewed_facts(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for entry in manifest["corpus"]:
        for config in entry["configurations"]:
            if not _eligible(entry, config):
                continue
            for expected in entry.get("expected_calls") or ():
                facts.append(
                    {
                        "fact_id": _fact_id(entry["id"], config["id"], expected),
                        "expected_class": expected["expected_class"],
                    }
                )
    return facts


def _replay_impact_scenarios(
    manifest: Mapping[str, Any], observations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replay the manifest's concrete safety questions over measured evidence."""

    observed = {str(item.get("fact_id") or ""): item for item in observations}
    proc_dimensions = {
        str(item.get("dimension") or ""): item
        for item in manifest.get("proc_cohort_census") or ()
    }
    outcomes: list[dict[str, Any]] = []
    for scenario in manifest.get("query_scenarios") or ():
        kind = str(scenario.get("kind") or "")
        relevant: dict[str, Any]
        if kind == "strict_fact_presence":
            fact_id = str(scenario.get("fact_id") or "")
            item = observed.get(fact_id, {})
            is_direct = item.get("resolution_class") == "direct_resolved"
            outcome = "affected" if is_direct else "incomplete"
            coverage = "complete" if is_direct else "partial"
            relevant = {"fact_id": fact_id, "observation": item}
        elif kind == "unresolved_fact_safety":
            fact_id = str(scenario.get("fact_id") or "")
            item = observed.get(fact_id, {})
            is_direct = item.get("resolution_class") == "direct_resolved"
            outcome = "affected" if is_direct else "incomplete"
            coverage = "complete" if is_direct else "partial"
            relevant = {"fact_id": fact_id, "observation": item}
        elif kind == "proc_dynamic_sql_safety":
            dimension = proc_dimensions.get("dynamic_sql", {})
            outcome = "incomplete"
            coverage = "partial"
            relevant = {"dimension": dimension}
        else:
            raise ValueError(f"unknown impact replay scenario kind: {kind!r}")
        replay_payload = {"scenario": scenario, "relevant_evidence": relevant}
        outcomes.append(
            {
                "id": scenario["id"],
                "outcome": outcome,
                "coverage_status": coverage,
                "reviewed": bool(scenario.get("reviewed")),
                "evidence_ref": f"benchmark-replay:{scenario['id']}",
                "evidence_fingerprint": canonical_digest(replay_payload),
            }
        )
    return outcomes


def _match_observations(
    entry: Mapping[str, Any],
    config: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    by_name: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for callsite in result.get("callsites") or ():
        by_name[str(callsite.get("callee_name") or "")].append(callsite)
    observations: list[dict[str, Any]] = []
    matched: set[tuple[str, int]] = set()
    for expected in entry.get("expected_calls") or ():
        callee_name = str(expected["callee_name"])
        candidates = by_name.get(callee_name, [])
        ordinal = int(expected.get("ordinal") or 0)
        if ordinal >= len(candidates):
            continue
        callsite = candidates[ordinal]
        matched.add((callee_name, ordinal))
        observations.append(
            {
                "fact_id": _fact_id(entry["id"], config["id"], expected),
                "resolution_class": callsite.get("resolution_class"),
                "resolution_reason": callsite.get("resolution_reason", ""),
                "tu_id": entry["id"],
                "configuration_id": config["id"],
            }
        )
    # Unexpected output must participate in precision.  Dropping unreviewed
    # direct observations here would let graph density masquerade as 100%
    # precision even though reviewed expectations have no matching fact.
    for callee_name, candidates in sorted(by_name.items()):
        for ordinal, callsite in enumerate(candidates):
            if (callee_name, ordinal) in matched:
                continue
            observations.append(
                {
                    "fact_id": "unreviewed:{}:{}:{}:{}".format(
                        entry["id"], config["id"], callee_name, ordinal
                    ),
                    "resolution_class": callsite.get("resolution_class"),
                    "resolution_reason": callsite.get("resolution_reason", ""),
                    "tu_id": entry["id"],
                    "configuration_id": config["id"],
                }
            )
    return observations


def _parse_containment(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    samples: dict[str, list[dict[str, Any]]] = {"cold": [], "warm": [], "changed_tu": []}
    edge_counts: Counter[str] = Counter()
    proc_payload: dict[str, Any] = {}
    strong_call_count = 0
    with tempfile.TemporaryDirectory(prefix="cplus-pilot-cache-") as cache_root:
        cache_path = Path(cache_root)
        for condition in ("cold", "warm"):
            for entry in manifest["corpus"]:
                path = ROOT / entry["path"]
                for config in entry["configurations"]:
                    compile_index = {
                        "path": "",
                        "entries": len(manifest["corpus"]),
                        "cpp_files": {path.name} if path.suffix.lower() in {".cpp", ".hpp"} else set(),
                        "c_files": {path.name} if path.suffix.lower() not in {".cpp", ".hpp"} else set(),
                        "fingerprint": canonical_digest(config),
                    }
                    cpu_before, _ = _self_usage()
                    started = time.perf_counter()
                    payload = cplus_analyzer._load_or_parse_payload(
                        str(path),
                        str(ROOT),
                        str(cache_path),
                        False,
                        compile_index,
                        "phase07-pilot",
                    )
                    elapsed = (time.perf_counter() - started) * 1000.0
                    cpu_after, peak_rss = _self_usage()
                    samples[condition].append(
                        {
                            "status": "ok",
                            "elapsed_ms": elapsed,
                            "cpu_seconds": max(0.0, cpu_after - cpu_before),
                            "peak_rss_bytes": peak_rss,
                        }
                    )
                    if condition == "cold":
                        for call in payload.get("calls") or ():
                            edge_counts[str(call.get("resolution_class") or "lexical_candidate")] += 1
                        strong_call_count += sum(
                            str(relation.get("rel_type") or relation.get("type") or "") == "CALLS"
                            for relation in payload.get("relations") or ()
                        )
                        if entry["cohort"] == "proc":
                            proc_payload = payload
        with tempfile.TemporaryDirectory(prefix="cplus-containment-change-") as changed_root_str:
            changed_root = Path(changed_root_str)
            for item in manifest["corpus"]:
                staged = changed_root / item["path"]
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / item["path"], staged)
            for entry in manifest["corpus"]:
                source = ROOT / entry["path"]
                changed_path = changed_root / entry["path"]
                changed_path.write_bytes(source.read_bytes() + b"\n/* phase07 changed-TU replay */\n")
                for config in entry["configurations"]:
                    compile_index = {
                        "path": "",
                        "entries": len(manifest["corpus"]),
                        "cpp_files": {changed_path.name} if changed_path.suffix.lower() in {".cpp", ".hpp"} else set(),
                        "c_files": {changed_path.name} if changed_path.suffix.lower() not in {".cpp", ".hpp"} else set(),
                        "fingerprint": canonical_digest(config),
                    }
                    cpu_before, _ = _self_usage()
                    started = time.perf_counter()
                    cplus_analyzer._load_or_parse_payload(
                        str(changed_path),
                        str(changed_root),
                        str(cache_path),
                        False,
                        compile_index,
                        "phase07-pilot-changed",
                    )
                    elapsed = (time.perf_counter() - started) * 1000.0
                    cpu_after, peak_rss = _self_usage()
                    samples["changed_tu"].append(
                        {
                            "status": "ok",
                            "elapsed_ms": elapsed,
                            "cpu_seconds": max(0.0, cpu_after - cpu_before),
                            "peak_rss_bytes": peak_rss,
                        }
                    )
                shutil.copy2(source, changed_path)
        storage_bytes = _directory_size(cache_path)
    return (
        {
            "conditions": {key: _metric_summary(value) for key, value in samples.items()},
            "edge_class_counts": dict(sorted(edge_counts.items())),
            "strong_call_count": strong_call_count,
            "storage_bytes": storage_bytes,
        },
        proc_payload,
    )


def _run_semantic_modes(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    selected = {
        "sparse": lambda item: bool(item.get("priority")),
        "comprehensive": lambda _item: True,
    }
    mode_metrics: dict[str, dict[str, Any]] = {}
    comprehensive_observations: list[dict[str, Any]] = []
    for mode, selector in selected.items():
        samples: dict[str, list[dict[str, Any]]] = {
            "cold": [],
            "warm": [],
            "changed_tu": [],
        }
        edge_counts: Counter[str] = Counter()
        cache_hits = 0
        cache_misses = 0
        with tempfile.TemporaryDirectory(prefix=f"cplus-{mode}-cache-") as cache_root_str, tempfile.TemporaryDirectory(
            prefix=f"cplus-{mode}-changed-"
        ) as changed_root_str:
            cache = SemanticCache(cache_root_str)
            changed_root = Path(changed_root_str)
            for item in manifest["corpus"]:
                staged = changed_root / item["path"]
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / item["path"], staged)
            for entry in manifest["corpus"]:
                if not selector(entry):
                    continue
                source = ROOT / entry["path"]
                changed_path = changed_root / entry["path"]
                for config in entry["configurations"]:
                    if not _eligible(entry, config):
                        continue
                    config_fingerprint = canonical_digest(
                        {"configuration_id": config.get("id"), "arguments": config.get("arguments") or []}
                    )
                    identity = SemanticCacheIdentity(
                        source_rel_path=str(entry["path"]),
                        source_fingerprint=file_sha256(source),
                        dependency_fingerprints=(),
                        config_fingerprint=config_fingerprint,
                        coverage=CoverageState.FAITHFUL,
                        toolchain_version=str(probe_clang_runtime().get("libclang_version") or ""),
                    )
                    cached = cache.load(identity)
                    if cached is not None:
                        raise RuntimeError("cold semantic cache unexpectedly contained the pilot identity")
                    cache_misses += 1
                    cold_result, cold_metric = _run_worker(ROOT, source, config)
                    samples["cold"].append(cold_metric)
                    if cold_result.get("status") == "ok":
                        cache.store(identity, cold_result)

                    warm_cpu_before, _ = _self_usage()
                    warm_started = time.perf_counter()
                    warm_result = cache.load(identity)
                    warm_elapsed = (time.perf_counter() - warm_started) * 1000.0
                    warm_cpu_after, warm_peak_rss = _self_usage()
                    samples["warm"].append(
                        {
                            "status": "ok" if warm_result is not None else "cache_miss",
                            "elapsed_ms": warm_elapsed,
                            "cpu_seconds": max(0.0, warm_cpu_after - warm_cpu_before),
                            "peak_rss_bytes": warm_peak_rss,
                        }
                    )
                    if warm_result is not None:
                        cache_hits += 1
                    for callsite in cold_result.get("callsites") or ():
                        edge_counts[str(callsite.get("resolution_class") or "unresolved")] += 1
                    if mode == "comprehensive" and cold_result.get("status") == "ok":
                        comprehensive_observations.extend(
                            _match_observations(entry, config, cold_result)
                        )

                    changed_path.write_bytes(source.read_bytes() + b"\n/* phase07 changed-TU replay */\n")
                    changed_identity = SemanticCacheIdentity(
                        source_rel_path=str(entry["path"]),
                        source_fingerprint=file_sha256(changed_path),
                        dependency_fingerprints=(),
                        config_fingerprint=config_fingerprint,
                        coverage=CoverageState.FAITHFUL,
                        toolchain_version=str(probe_clang_runtime().get("libclang_version") or ""),
                    )
                    if cache.load(changed_identity) is not None:
                        raise RuntimeError("changed-TU identity did not invalidate semantic cache")
                    cache_misses += 1
                    _, changed_metric = _run_worker(Path(changed_root), changed_path, config)
                    samples["changed_tu"].append(changed_metric)
                    shutil.copy2(source, changed_path)
            storage_bytes = _directory_size(Path(cache_root_str))
        mode_metrics[mode] = {
            "conditions": {key: _metric_summary(value) for key, value in samples.items()},
            "edge_class_counts": dict(sorted(edge_counts.items())),
            "selected_tu_count": len(
                [entry for entry in manifest["corpus"] if selector(entry)]
            ),
            "cache": {"hits": cache_hits, "misses": cache_misses},
            "storage_bytes": storage_bytes,
            "queue": {"status": "not_exercised_by_sequential_developer_canary"},
            "header_fanout": {"status": "unmeasured"},
        }
    return mode_metrics, comprehensive_observations


def _proc_evidence(proc_payload: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    proc_entry = next(entry for entry in manifest["corpus"] if entry["cohort"] == "proc")
    prepared = prepare_proc_path(ROOT / proc_entry["path"])
    labels = {
        str(item.get("label") or "")
        for item in proc_payload.get("proc_nodes") or ()
        if item.get("label")
    }
    relation_types = {
        str(item.get("type") or item.get("rel_type") or "")
        for item in proc_payload.get("relations") or ()
        if item.get("type") or item.get("rel_type")
    }
    mask_aligned = (
        len(prepared.source_bytes) == len(prepared.masked_bytes)
        and prepared.source_bytes.count(b"\n") == prepared.masked_bytes.count(b"\n")
    )
    return {
        "complete_stratified_cohort": all(
            item.get("status") == "covered"
            for item in manifest.get("proc_cohort_census") or ()
        ),
        "discovery_and_routing": bool(proc_payload),
        "mask_alignment_ratio": 1.0 if mask_aligned else 0.0,
        "labels_passed": sorted(labels),
        "relations_passed": sorted(relation_types),
        "compiler_context_redacted": True,
        "source_map_pass_ratio": 0.0,
        "semantic_accuracy_passed": False,
        "generated_mispublished_count": 0,
        "cross_domain_impact_passed": False,
        "cache_invalidation_passed": False,
        "graph_vector_parity_passed": False,
        "dynamic_sql_incomplete_visible": True,
        "join_incomplete_visible": True,
        "security_passed": False,
        "resource_budget_passed": False,
        "failure_isolation_passed": False,
        "publication_passed": False,
        "rollback_passed": False,
        "sql_regression_count": 0,
        "incomplete_without_reason": [],
    }


def _default_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "suites": {name: "not_run" for name in manifest.get("required_suites") or ()},
        "providers": {
            name: {
                "status": "not_run",
                "fingerprint": "",
                "integrity_passed": False,
                "deterministic_rerun": False,
                "crash_resume_passed": False,
                "publication_passed": False,
                "rollback_passed": False,
                "reason": "staging evidence not supplied",
            }
            for name in manifest.get("required_providers") or ()
        },
        "publication": {"status": "not_run", "deterministic_rerun": False},
        "rollback": {"status": "not_run", "last_valid_generation_retained": False},
        "consumer_contract_passed": False,
        "incremental_invalidation_passed": False,
        "failure_isolation_passed": False,
        "security_passed": False,
        "critical_findings": [],
        "security_and_faults": {"status": "not_run", "failure_count": 0},
        "impact_outcomes": [],
        "scale_resource_evidence": {
            "status": "not_run",
            "measured_loc": 0,
            "fingerprint": "",
        },
        "operational_measurements": {
            "status": "not_run",
            "queue_measured": False,
            "cache_measured": False,
            "header_fanout_measured": False,
            "storage_measured": False,
        },
    }


def run(manifest_path: Path, output_dir: Path, gate_evidence_path: Path | None) -> dict[str, Any]:
    manifest = load_pilot_manifest(manifest_path, workspace_root=ROOT)
    probe = probe_clang_runtime()
    native_library = str(probe.get("native_library") or "")
    if native_library:
        native_path = Path(native_library)
        probe["native_library"] = "<redacted>/" + native_path.name
        if native_path.is_file():
            probe["native_library_sha256"] = file_sha256(native_path)
    containment, proc_payload = _parse_containment(manifest)
    semantic_modes, observations = _run_semantic_modes(manifest) if probe["ready"] else ({}, [])
    expected_facts = _reviewed_facts(manifest)
    configuration_ids = sorted(
        {
            config["id"]
            for entry in manifest["corpus"]
            for config in entry["configurations"]
        }
    )
    query_horizon = [item["id"] for item in manifest["query_scenarios"]]
    mode_results = []
    for mode in ("containment", "sparse", "comprehensive"):
        metrics = containment if mode == "containment" else semantic_modes.get(mode, {})
        mode_results.append(
            {
                "mode": mode,
                "revision": manifest["revision"],
                "configuration_ids": configuration_ids,
                "query_scenarios": query_horizon,
                **metrics,
            }
        )

    budget = manifest["resource_budgets"]
    condition_summaries = [
        summary
        for mode in mode_results
        for summary in (mode.get("conditions") or {}).values()
    ]
    within_measured_limits = all(
        int(item.get("tu_runs") or 0) > 0
        and int(item.get("failures") or 0) == 0
        and float(item.get("elapsed_ms", {}).get("max") or 0.0)
        <= float(budget["max_elapsed_ms_per_tu"])
        and float(item.get("cpu_seconds", {}).get("max") or 0.0)
        <= float(budget["max_cpu_seconds_per_tu"])
        and int(item.get("peak_rss_bytes") or 0) <= int(budget["max_peak_rss_bytes"])
        for item in condition_summaries
    )

    evidence = _default_evidence(manifest)
    evidence.update(
        {
            "toolchain": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "semantic_worker_protocol": SEMANTIC_WORKER_PROTOCOL_VERSION,
                "pilot_policy": PILOT_POLICY_VERSION,
                "clang_probe": probe,
                "analysis_artifact_sha256": {
                    "code-tiny/tools/cplus/pilot_rollout.py": file_sha256(
                        CODE_TINY / "tools" / "cplus" / "pilot_rollout.py"
                    ),
                    "code-tiny/tools/cplus/clang_worker.py": file_sha256(WORKER),
                    "code-tiny/tools/cplus/semantic_worker.py": file_sha256(
                        CODE_TINY / "tools" / "cplus" / "semantic_worker.py"
                    ),
                    "tests/benchmark_cplus_semantic_calls.py": file_sha256(Path(__file__)),
                },
            },
            "worker": {"status": "passed" if probe["ready"] else "failed", **probe},
            "compile_contexts": _context_records(manifest),
            "expected_facts": expected_facts,
            "observations": observations,
            "impact_outcomes": _replay_impact_scenarios(manifest, observations),
            "mode_results": mode_results,
            "weak_promoted_count": int(containment.get("strong_call_count") or 0),
            "proc": _proc_evidence(proc_payload, manifest),
            "resources": {
                "all_within_budget": False,
                "measured_limits_passed": within_measured_limits,
                "million_loc_measured": False,
                "budget": budget,
                "modes": {
                    item["mode"]: {
                        "conditions": item.get("conditions", {}),
                        "storage_bytes": item.get("storage_bytes", 0),
                        "cache": item.get("cache", {"status": "not_applicable"}),
                        "queue": item.get("queue", {"status": "unmeasured"}),
                        "header_fanout": item.get("header_fanout", {"status": "unmeasured"}),
                    }
                    for item in mode_results
                },
            },
        }
    )
    if gate_evidence_path is not None:
        overlay = json.loads(gate_evidence_path.read_text(encoding="utf-8"))
        _apply_gate_evidence(evidence, overlay)

    # Benchmark-owned measurements cannot be replaced by external evidence.
    # A real scale record can only complete the explicit million-LOC portion.
    scale = evidence.get("scale_resource_evidence") or {}
    scale_passed = (
        scale.get("status") == "passed"
        and int(scale.get("measured_loc") or 0) >= 1_000_000
        and len(str(scale.get("fingerprint") or "").strip()) == 64
        and all(
            character in "0123456789abcdef"
            for character in str(scale.get("fingerprint") or "").strip().lower()
        )
        and scale.get("within_budget") is True
    )
    evidence["resources"]["million_loc_measured"] = scale_passed
    evidence["resources"]["all_within_budget"] = within_measured_limits and (
        scale_passed or not bool(budget.get("million_loc_measurement_required"))
    )
    evidence["proc"]["complete_stratified_cohort"] = all(
        item.get("status") == "covered"
        for item in manifest.get("proc_cohort_census") or ()
    )

    report = build_pilot_report(manifest=manifest, evidence=evidence)
    paths = write_report_bundle(
        output_dir=output_dir,
        manifest=manifest,
        evidence=evidence,
        report=report,
    )
    return {"decision": report["decision"], "paths": paths, "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--gate-evidence",
        type=Path,
        help="optional JSON evidence from suites and real provider canaries",
    )
    args = parser.parse_args()
    result = run(args.manifest, args.output_dir, args.gate_evidence)
    print(json.dumps({"decision": result["decision"], "paths": result["paths"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
