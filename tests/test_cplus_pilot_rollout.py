"""Phase 07 pilot, evidence, scorecard, and rollout-decision tests."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.pilot_rollout import (  # noqa: E402
    DECISION_CONTAIN,
    DECISION_PROMOTE,
    PILOT_SCHEMA_VERSION,
    PROC_LABELS,
    PROC_RELATIONS,
    REQUIRED_COHORTS,
    REQUIRED_PROC_COHORT_DIMENSIONS,
    PilotContractError,
    build_pilot_report,
    compare_modes,
    compile_context_census,
    impact_answer_score,
    load_pilot_manifest,
    manifest_fingerprint,
    proc_scorecard,
    reviewed_accuracy,
    validate_pilot_evidence,
    write_report_bundle,
)


MANIFEST_PATH = (
    ROOT
    / "plans"
    / "260821-1144-cplus-semantic-call-graph"
    / "pilot-manifest.json"
)


def _complete_proc_evidence():
    return {
        "complete_stratified_cohort": True,
        "discovery_and_routing": True,
        "mask_alignment_ratio": 1.0,
        "labels_passed": sorted(PROC_LABELS),
        "relations_passed": sorted(PROC_RELATIONS),
        "compiler_context_redacted": True,
        "source_map_pass_ratio": 1.0,
        "semantic_accuracy_passed": True,
        "generated_mispublished_count": 0,
        "cross_domain_impact_passed": True,
        "cache_invalidation_passed": True,
        "graph_vector_parity_passed": True,
        "dynamic_sql_incomplete_visible": True,
        "join_incomplete_visible": True,
        "security_passed": True,
        "resource_budget_passed": True,
        "failure_isolation_passed": True,
        "publication_passed": True,
        "rollback_passed": True,
        "sql_regression_count": 0,
        "incomplete_without_reason": [],
    }


def _complete_manifest():
    return {
        "schema_version": PILOT_SCHEMA_VERSION,
        "pilot_id": "real-pilot",
        "revision": "a" * 40,
        "workload_class": "real",
        "required_cohorts": sorted(REQUIRED_COHORTS),
        "required_suites": [
            "unit",
            "integration",
            "adversarial",
            "provider",
            "incremental",
            "publication",
            "rollback",
            "consumer_contract",
        ],
        "required_providers": ["neo4j", "falkordb"],
        "promotion_thresholds": {
            "direct_precision_min": 0.98,
            "direct_recall_min": 0.95,
            "priority_faithful_context_min": 0.9,
        },
        "resource_budgets": {"million_loc_measurement_required": True},
        "proc_cohort_census": [
            {"dimension": dimension, "status": "covered"}
            for dimension in sorted(REQUIRED_PROC_COHORT_DIMENSIONS)
        ],
        "query_scenarios": [
            {"id": "positive", "expected_outcome": "affected"},
            {"id": "negative", "expected_outcome": "no_impact"},
        ],
    }


def _complete_evidence():
    suites = {
        name: "passed"
        for name in _complete_manifest()["required_suites"]
    }
    horizon = {
        "revision": "a" * 40,
        "configuration_ids": ["cfg"],
        "query_scenarios": ["positive", "negative"],
        "conditions": {"cold": {}, "warm": {}, "changed_tu": {}},
    }
    return {
        "compile_contexts": [
            {
                "tu_id": "a.c",
                "configuration_id": "cfg",
                "coverage_state": "faithful",
                "reason": "",
                "priority": True,
            }
        ],
        "expected_facts": [
            {"fact_id": "direct", "expected_class": "direct_resolved"},
            {"fact_id": "virtual", "expected_class": "declared_virtual_target"},
        ],
        "observations": [
            {"fact_id": "direct", "resolution_class": "direct_resolved"},
            {"fact_id": "virtual", "resolution_class": "declared_virtual_target"},
        ],
        "mode_results": [
            {"mode": mode, **horizon}
            for mode in ("containment", "sparse", "comprehensive")
        ],
        "impact_outcomes": [
            {"id": "positive", "outcome": "affected", "coverage_status": "complete", "reviewed": True, "evidence_ref": "review:positive", "evidence_fingerprint": "b" * 64},
            {"id": "negative", "outcome": "no_impact", "coverage_status": "complete", "reviewed": True, "evidence_ref": "review:negative", "evidence_fingerprint": "c" * 64},
        ],
        "proc": _complete_proc_evidence(),
        "suites": suites,
        "providers": {
            "neo4j": {"status": "passed", "fingerprint": "d" * 64, "integrity_passed": True, "deterministic_rerun": True, "crash_resume_passed": True, "publication_passed": True, "rollback_passed": True},
            "falkordb": {"status": "passed", "fingerprint": "e" * 64, "integrity_passed": True, "deterministic_rerun": True, "crash_resume_passed": True, "publication_passed": True, "rollback_passed": True},
        },
        "worker": {"status": "passed", "ready": True, "libclang_version": "18.1.1"},
        "resources": {"all_within_budget": True, "million_loc_measured": True},
        "operational_measurements": {"status": "passed", "fingerprint": "f" * 64, "queue_measured": True, "cache_measured": True, "header_fanout_measured": True, "storage_measured": True},
        "publication": {"status": "passed", "fingerprint": "1" * 64, "deterministic_rerun": True, "live_provider_canary": True},
        "rollback": {"status": "passed", "fingerprint": "2" * 64, "last_valid_generation_retained": True, "live_provider_canary": True},
        "weak_promoted_count": 0,
        "consumer_contract_passed": True,
        "incremental_invalidation_passed": True,
        "failure_isolation_passed": True,
        "security_passed": True,
        "critical_findings": [],
    }


class ManifestTests(unittest.TestCase):
    def test_checked_in_manifest_is_immutable_and_complete(self):
        manifest = load_pilot_manifest(MANIFEST_PATH, workspace_root=ROOT)
        self.assertEqual(manifest["schema_version"], PILOT_SCHEMA_VERSION)
        self.assertEqual(len(manifest_fingerprint(manifest)), 64)
        self.assertEqual(manifest["workload_class"], "synthetic_developer_canary")
        self.assertEqual(
            set(manifest["required_cohorts"]),
            {entry["cohort"] for entry in manifest["corpus"]},
        )

    def _write_manifest(self, manifest):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_tampered_corpus_hash_fails_closed(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest["corpus"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(PilotContractError, "hash mismatch"):
            load_pilot_manifest(self._write_manifest(manifest), workspace_root=ROOT)

    def test_external_and_traversal_paths_are_rejected(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest["corpus"][0]["path"] = "../outside.c"
        with self.assertRaisesRegex(PilotContractError, "escapes workspace"):
            load_pilot_manifest(self._write_manifest(manifest), workspace_root=ROOT)

    def test_credential_bearing_flags_are_rejected(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest["corpus"][0]["configurations"][0]["arguments"].append(
            "-DPASSWORD=tiger"
        )
        with self.assertRaisesRegex(PilotContractError, "credential-bearing"):
            load_pilot_manifest(self._write_manifest(manifest), workspace_root=ROOT)

    def test_nonfaithful_context_requires_visible_reason(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        config = manifest["corpus"][0]["configurations"][0]
        config["coverage_state"] = "missing"
        config["reason"] = ""
        with self.assertRaisesRegex(PilotContractError, "stable reason"):
            load_pilot_manifest(self._write_manifest(manifest), workspace_root=ROOT)

    def test_revision_blob_hash_must_match_current_manifest_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            source = root / "sample.c"
            source.write_text("int v1(void) { return 1; }\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "sample.c"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=pilot", "-c", "user.email=pilot@example.invalid", "commit", "-q", "-m", "fixture"],
                check=True,
            )
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source.write_text("int v2(void) { return 2; }\n", encoding="utf-8")
            current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = _complete_manifest()
            manifest.update(
                {
                    "revision": revision,
                    "supported_platforms": ["test"],
                    "corpus": [
                        {
                            "id": f"entry-{cohort}",
                            "cohort": cohort,
                            "path": "sample.c",
                            "sha256": current_hash,
                            "configurations": [
                                {"id": f"cfg-{cohort}", "coverage_state": "faithful"}
                            ],
                        }
                        for cohort in sorted(REQUIRED_COHORTS)
                    ],
                }
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(PilotContractError, "does not match manifest revision"):
                load_pilot_manifest(path, workspace_root=root)

    def test_non_git_workspace_uses_content_hash_without_revision_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.c"
            source.write_text("int sample(void) { return 1; }\n", encoding="utf-8")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = _complete_manifest()
            manifest.update(
                {
                    "supported_platforms": ["test"],
                    "corpus": [
                        {
                            "id": f"entry-{cohort}",
                            "cohort": cohort,
                            "path": "sample.c",
                            "sha256": source_hash,
                            "configurations": [
                                {"id": f"cfg-{cohort}", "coverage_state": "faithful"}
                            ],
                        }
                        for cohort in sorted(REQUIRED_COHORTS)
                    ],
                }
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded = load_pilot_manifest(path, workspace_root=root)

            self.assertEqual(loaded["pilot_id"], manifest["pilot_id"])


class MetricTests(unittest.TestCase):
    def test_evidence_rejects_credentials_and_absolute_machine_paths(self):
        with self.assertRaisesRegex(PilotContractError, "credential-bearing"):
            validate_pilot_evidence({"worker": {"detail": "PASSWORD=tiger"}})
        with self.assertRaisesRegex(PilotContractError, "absolute machine path"):
            validate_pilot_evidence({"worker": {"library": "/opt/lib/libclang.dylib"}})
        with self.assertRaisesRegex(PilotContractError, "credential-bearing"):
            validate_pilot_evidence({"providers": {"uri": "neo4j://alice:secret@example.com"}})
        with self.assertRaisesRegex(PilotContractError, "credential-bearing"):
            validate_pilot_evidence({"providers": {"api_token": "topsecret"}})
        with self.assertRaisesRegex(PilotContractError, "absolute machine path"):
            validate_pilot_evidence({"detail": "read config from /opt/private/config before run"})
        with self.assertRaisesRegex(PilotContractError, "credential-bearing"):
            validate_pilot_evidence({"authorization": "Bearer topsecret"})
        with self.assertRaisesRegex(PilotContractError, "absolute machine path"):
            validate_pilot_evidence({"detail": "read /srv/private/config before run"})

    def test_compile_context_census_reports_priority_ratio_and_reasons(self):
        result = compile_context_census(
            [
                {"tu_id": "a", "configuration_id": "1", "coverage_state": "faithful", "priority": True},
                {"tu_id": "b", "configuration_id": "1", "coverage_state": "missing", "priority": True, "reason": "no compile command"},
                {"tu_id": "c", "configuration_id": "1", "coverage_state": "synthetic", "priority": False, "reason": "fallback"},
            ]
        )
        self.assertEqual(result["priority"]["faithful_ratio"], 0.5)
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertTrue(result["all_uncovered_have_stable_reasons"])

    def test_missing_noncoverage_reason_is_a_visible_gate_failure(self):
        result = compile_context_census(
            [{"tu_id": "a", "configuration_id": "1", "coverage_state": "failed", "reason": ""}]
        )
        self.assertFalse(result["all_uncovered_have_stable_reasons"])

    def test_accuracy_separates_direct_from_virtual_and_unreviewed(self):
        result = reviewed_accuracy(
            [
                {"fact_id": "d1", "expected_class": "direct_resolved"},
                {"fact_id": "d2", "expected_class": "direct_resolved"},
                {"fact_id": "v", "expected_class": "declared_virtual_target"},
            ],
            [
                {"fact_id": "d1", "resolution_class": "direct_resolved"},
                {"fact_id": "v", "resolution_class": "direct_resolved"},
                {"fact_id": "extra", "resolution_class": "direct_resolved"},
            ],
        )
        self.assertEqual(result["direct"]["true_positive"], 1)
        self.assertEqual(result["direct"]["false_positive"], 2)
        self.assertEqual(result["direct"]["false_negative"], 1)
        self.assertEqual(result["unreviewed_observation_fact_ids"], ["extra"])

    def test_unsafe_negative_is_independently_counted(self):
        result = impact_answer_score(
            [{"id": "q", "expected_outcome": "no_impact"}],
            [{"id": "q", "outcome": "no_impact", "coverage_status": "partial"}],
        )
        self.assertEqual(result["correctness"], 1.0)
        self.assertEqual(result["unsafe_negative_count"], 1)

    def test_mode_comparison_requires_all_modes_and_same_horizon(self):
        good = [
            {"mode": mode, "revision": "r", "query_scenarios": ["q"], "configuration_ids": ["c"], "conditions": {"cold": {}, "warm": {}, "changed_tu": {}}}
            for mode in ("containment", "sparse", "comprehensive")
        ]
        self.assertTrue(compare_modes(good)["same_horizon"])
        bad = copy.deepcopy(good)
        bad[-1]["revision"] = "other"
        self.assertFalse(compare_modes(bad)["same_horizon"])

    def test_proc_scorecard_requires_exact_five_labels_and_nine_relations(self):
        passed = proc_scorecard(_complete_proc_evidence())
        self.assertTrue(passed["passed"])
        incomplete = _complete_proc_evidence()
        incomplete["relations_passed"] = sorted(PROC_RELATIONS - {"REFERENCES_STATEMENT"})
        result = proc_scorecard(incomplete)
        self.assertFalse(result["passed"])
        self.assertEqual(result["relations"]["missing"], ["REFERENCES_STATEMENT"])


class DecisionTests(unittest.TestCase):
    def test_every_hard_gate_passes_before_promotion(self):
        report = build_pilot_report(
            manifest=_complete_manifest(), evidence=_complete_evidence()
        )
        self.assertEqual(report["decision"]["decision"], DECISION_PROMOTE)
        self.assertTrue(report["decision"]["defaults_may_change"])
        self.assertEqual(report["decision"]["failed_gates"], [])

    def test_weak_promotion_forces_containment(self):
        evidence = _complete_evidence()
        evidence["weak_promoted_count"] = 1
        report = build_pilot_report(manifest=_complete_manifest(), evidence=evidence)
        self.assertEqual(report["decision"]["decision"], DECISION_CONTAIN)
        self.assertIn("zero_weak_to_calls", report["decision"]["failed_gates"])

    def test_proc_failure_cannot_be_averaged_into_cplus_success(self):
        evidence = _complete_evidence()
        evidence["proc"]["source_map_pass_ratio"] = 0.99
        report = build_pilot_report(manifest=_complete_manifest(), evidence=evidence)
        self.assertEqual(report["decision"]["decision"], DECISION_CONTAIN)
        self.assertFalse(report["proc_scorecard"]["passed"])

    def test_critical_finding_is_nonwaivable(self):
        evidence = _complete_evidence()
        evidence["critical_findings"] = ["publication mixed two revisions"]
        report = build_pilot_report(manifest=_complete_manifest(), evidence=evidence)
        self.assertEqual(report["decision"]["decision"], DECISION_CONTAIN)
        self.assertFalse(report["decision"]["gates"]["no_critical_findings"]["passed"])

    def test_synthetic_workload_never_promotes(self):
        manifest = _complete_manifest()
        manifest["workload_class"] = "synthetic_developer_canary"
        report = build_pilot_report(manifest=manifest, evidence=_complete_evidence())
        self.assertFalse(report["decision"]["promotion_allowed"])
        self.assertIn("real_stratified_workload", report["decision"]["failed_gates"])

    def test_report_fingerprint_is_deterministic(self):
        first = build_pilot_report(manifest=_complete_manifest(), evidence=_complete_evidence())
        second = build_pilot_report(manifest=_complete_manifest(), evidence=_complete_evidence())
        self.assertEqual(first["report_fingerprint"], second["report_fingerprint"])

    def test_report_bundle_has_separate_proc_scorecard(self):
        manifest = _complete_manifest()
        evidence = _complete_evidence()
        report = build_pilot_report(manifest=manifest, evidence=evidence)
        with tempfile.TemporaryDirectory() as temp:
            paths = write_report_bundle(
                output_dir=temp,
                manifest=manifest,
                evidence=evidence,
                report=report,
            )
            scorecard = json.loads(Path(paths["proc_scorecard"]).read_text(encoding="utf-8"))
            self.assertTrue(scorecard["passed"])
            self.assertEqual(set(paths), {"manifest", "evidence", "report", "proc_scorecard"})


class BenchmarkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = ROOT / "tests" / "benchmark_cplus_semantic_calls.py"
        spec = importlib.util.spec_from_file_location("phase07_benchmark", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.benchmark = module

    def test_developer_canary_emits_containment_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.benchmark.run(MANIFEST_PATH, Path(temp), None)
            self.assertEqual(result["decision"]["decision"], DECISION_CONTAIN)
            self.assertTrue(result["report"]["accuracy"]["direct"]["precision"] >= 0.98)
            self.assertTrue(result["report"]["accuracy"]["direct"]["recall"] >= 0.95)
            self.assertFalse(result["report"]["decision"]["defaults_may_change"])
            self.assertTrue(
                all(
                    item["evidence_ref"].startswith("benchmark-replay:")
                    for item in result["report"]["impact_answers"]["details"]
                )
            )
            self.assertEqual(
                set(Path(temp).iterdir()),
                {
                    Path(temp) / "pilot-manifest.json",
                    Path(temp) / "pilot-evidence.json",
                    Path(temp) / "rollout-decision.json",
                    Path(temp) / "proc-scorecard.json",
                },
            )

    def test_gate_overlay_cannot_replace_benchmark_owned_evidence(self):
        with self.assertRaisesRegex(ValueError, "benchmark-owned"):
            self.benchmark._apply_gate_evidence({}, {"observations": []})
        with self.assertRaisesRegex(ValueError, "benchmark-owned"):
            self.benchmark._apply_gate_evidence({}, {"impact_outcomes": []})


if __name__ == "__main__":
    unittest.main()
