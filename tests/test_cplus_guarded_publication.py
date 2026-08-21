"""Phase 06 guarded publication: two-dimension gates, staged replacement
with stale-edge removal, Pro*C sub-result isolation, journaled evidence
operations, atomic publication, rollback, and vector/report safety."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.common import payload_validation  # noqa: E402
from tools.common.payload_validation import (  # noqa: E402
    QuarantineReason,
    accounting_for_payload,
    validate_cplus_payload,
)
from tools.common.reliability import RunOutcome  # noqa: E402
from tools.cplus import evidence_merge  # noqa: E402
from tools.cplus.guarded_publication import (  # noqa: E402
    ENV_SEMANTIC_PUBLICATION_MODE,
    PublicationExpectation,
    RollbackState,
    SemanticGenerationLedger,
    SemanticPublicationPolicy,
    build_proc_staged_sub_results,
    build_staged_replacement,
    compute_stale_strong_edges,
    expected_effects,
    publication_status,
    publish_staged_generation,
    rollback_to_last_valid_generation,
    sanitize_vector_items,
    strong_edge_publication_decision,
    validate_staged_publication,
    vector_item_rejection_reason,
)
from tools.graph.journal import configure_journal_env  # noqa: E402
from tools.graph.journal.executor import compile_persisted_mutation  # noqa: E402
from tools.graph.journal.operation import GraphWriteOperation  # noqa: E402
from tools.graph.journal.reconcile import compile_reconciliation_readback  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


def _semantic_observation(callee="f2", line=3):
    return {
        "caller_id": "f1",
        "callee_id": callee,
        "file_path": "a.c",
        "line": line,
        "column": 1,
        "call_type": "call",
        "resolution_class": "direct_resolved",
        "semantic_provider": "clang_worker",
        "tu_key": "a.c",
        "config_fingerprint": "cf1",
        "callee_usr": f"usr:{callee}",
        "project_id": "p1",
    }


def _strict_row(site_id="site-1", caller="f1", callee="f2", file_path="a.c"):
    return {
        "caller_id": caller,
        "callee_id": callee,
        "site_id": site_id,
        "props": {
            "resolution_class": "direct_resolved",
            "semantic_provider": "clang_worker",
            "tu_key": file_path,
            "config_fingerprint": "cf1",
            "callee_usr": f"usr:{callee}",
            "site_id": site_id,
            "project_id": "p1",
        },
    }


def _baseline_edge(site_id, file_path="a.c", caller="f1", callee="f2"):
    return {"site_id": site_id, "caller_id": caller, "callee_id": callee, "file_path": file_path}


def _clean_quality():
    return {"tier": "clean", "schema_version": "1"}


def _allowed_policy():
    return {"strong_relation_types": ["CALLS"], "strong_relations_allowed": True}


class GateCompositionTest(unittest.TestCase):
    def test_both_dimensions_pass(self):
        decision = strong_edge_publication_decision(
            policy=SemanticPublicationPolicy(),
            file_quality=_clean_quality(),
            evidence_policy=_allowed_policy(),
            evidence_row=_semantic_observation(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.parse_trust, "clean")
        self.assertEqual(decision.semantic_trust, "strong")

    def test_quarantined_parse_tier_blocks_strong_edges(self):
        decision = strong_edge_publication_decision(
            policy=SemanticPublicationPolicy(),
            file_quality={"tier": "quarantined"},
            evidence_policy=_allowed_policy(),
            evidence_row=_semantic_observation(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "parse_quality_quarantined")

    def test_evidence_policy_blocks_exactly_like_quarantine(self):
        decision = strong_edge_publication_decision(
            policy=SemanticPublicationPolicy(),
            file_quality=_clean_quality(),
            evidence_policy={"strong_relations_allowed": False},
            evidence_row=_semantic_observation(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "evidence_policy_forbids_strong_relations")

    def test_unknown_and_retry_tiers_fail_closed(self):
        for tier in ("unknown", "retry_required", ""):
            with self.subTest(tier=tier):
                quality = {"tier": tier} if tier else {}
                decision = strong_edge_publication_decision(
                    policy=SemanticPublicationPolicy(),
                    file_quality=quality,
                    evidence_policy=_allowed_policy(),
                    evidence_row=_semantic_observation(),
                )
                self.assertFalse(decision.allowed)

    def test_weak_evidence_is_never_promoted(self):
        row = dict(_semantic_observation())
        row["resolution_class"] = "lexical_candidate"
        row["semantic_provider"] = "tree_sitter"
        decision = strong_edge_publication_decision(
            policy=SemanticPublicationPolicy(),
            file_quality=_clean_quality(),
            evidence_policy=_allowed_policy(),
            evidence_row=row,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.semantic_trust, "weak")

    def test_generated_and_map_and_bundle_dimensions_block(self):
        base = dict(
            policy=SemanticPublicationPolicy(),
            file_quality=_clean_quality(),
            evidence_policy=_allowed_policy(),
            evidence_row=_semantic_observation(),
        )
        generated = strong_edge_publication_decision(
            **base, generated_code_class="precompiler_runtime"
        )
        self.assertEqual(generated.reason, "generated_class_precompiler_runtime")
        weak_map = strong_edge_publication_decision(**base, map_quality="line_directive")
        self.assertEqual(weak_map.reason, "map_quality_line_directive")
        stale = strong_edge_publication_decision(**base, bundle_state="stale")
        self.assertEqual(stale.reason, "bundle_state_stale")
        accepted = strong_edge_publication_decision(
            **base,
            generated_code_class="original_application",
            map_quality="exact_span",
            bundle_state="semantic_complete",
        )
        self.assertTrue(accepted.allowed)

    def test_off_and_rollback_modes_disable_semantic_publication(self):
        for mode in ("off", "rollback"):
            decision = strong_edge_publication_decision(
                policy=SemanticPublicationPolicy(mode=mode),
                file_quality=_clean_quality(),
                evidence_policy=_allowed_policy(),
                evidence_row=_semantic_observation(),
            )
            self.assertFalse(decision.allowed, mode)
            self.assertEqual(decision.reason, f"semantic_publication_{mode}")

    def test_environment_override_selects_rollback(self):
        policy = SemanticPublicationPolicy().with_environment(
            {ENV_SEMANTIC_PUBLICATION_MODE: "rollback"}
        )
        self.assertEqual(policy.mode, "rollback")
        self.assertFalse(policy.semantic_publication_allowed)
        with self.assertRaises(ValueError):
            SemanticPublicationPolicy().with_environment(
                {ENV_SEMANTIC_PUBLICATION_MODE: "yolo"}
            )

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            SemanticPublicationPolicy(mode="always")


class ProcSubResultTest(unittest.TestCase):
    def _reconciliation(self, strict=1, total=3, bundle_state="semantic_complete"):
        return {
            "bundle_state": bundle_state,
            "rows": [{"i": i} for i in range(total)],
            "strict_rows": [{"strict": True} for _ in range(strict)],
            "rejected": {"map_missing": total - strict},
            "sql_facts_preserved": True,
        }

    def test_sql_publishes_while_semantic_lane_unavailable(self):
        sub_results = build_proc_staged_sub_results(
            sql_rows=[{"label": "SqlStatement", "id": "s1"}],
            sql_relations=[],
            semantic_reconciliation=None,
            semantic_lane_unavailable=True,
        )
        self.assertEqual(sub_results["sql_original_result"].status, "accepted")
        self.assertTrue(sub_results["sql_original_result"].preserves_prior_facts)
        self.assertEqual(sub_results["semantic_mapped_result"].status, "rejected")
        self.assertIn(
            "semantic_lane_unavailable", sub_results["semantic_mapped_result"].reasons
        )

    def test_mapped_calls_retained_while_sql_grammar_failed(self):
        sub_results = build_proc_staged_sub_results(
            sql_rows=[{"label": "SqlStatement", "id": "s1"}],
            sql_grammar_failed=True,
            semantic_reconciliation=self._reconciliation(strict=2, total=4),
        )
        sql = sub_results["sql_original_result"]
        semantic = sub_results["semantic_mapped_result"]
        self.assertEqual(sql.status, "rejected")
        self.assertFalse(sql.preserves_prior_facts)
        self.assertEqual(semantic.status, "accepted")
        self.assertEqual(len(semantic.rows), 2)
        self.assertEqual(semantic.accounting.accepted, 2)
        self.assertEqual(semantic.accounting.quarantined, 2)

    def test_original_region_integrity_failure_closes_both_lanes(self):
        sub_results = build_proc_staged_sub_results(
            sql_rows=[{"label": "SqlCursor", "id": "c1"}],
            original_region_integrity_failed=True,
            semantic_reconciliation=self._reconciliation(strict=1, total=2),
        )
        self.assertEqual(sub_results["sql_original_result"].status, "rejected")
        self.assertEqual(sub_results["semantic_mapped_result"].status, "rejected")

    def test_quarantined_strict_absence_is_not_an_error(self):
        sub_results = build_proc_staged_sub_results(
            sql_rows=[{"label": "SqlStatement", "id": "s1"}],
            semantic_reconciliation=self._reconciliation(strict=0, total=3),
        )
        semantic = sub_results["semantic_mapped_result"]
        self.assertEqual(semantic.status, "quarantined")
        self.assertEqual(semantic.accounting.discovered, 3)
        self.assertTrue(semantic.preserves_prior_facts)

    def test_accounting_is_balanced(self):
        sub_results = build_proc_staged_sub_results(
            sql_rows=[{"label": "SqlStatement", "id": "s1"}],
            sql_relations=[{"rel_type": "READS_FROM"}],
            sql_quarantined=1,
            semantic_reconciliation=self._reconciliation(strict=1, total=2),
        )
        for name, sub in sub_results.items():
            accounting = sub.accounting
            self.assertEqual(
                accounting.discovered,
                accounting.accepted + accounting.quarantined + accounting.rejected,
                name,
            )


class StagedReplacementTest(unittest.TestCase):
    def test_stale_strong_edges_removed_on_downgrade(self):
        stale = compute_stale_strong_edges(
            baseline_strong_edges=[
                _baseline_edge("s1"),
                _baseline_edge("s2"),
                _baseline_edge("s-other", file_path="b.c"),
            ],
            reaccepted_site_ids={"s2"},
            affected_files={"a.c"},
            file_reasons={"a.c": "stale_map"},
        )
        self.assertEqual([edge.site_id for edge in stale], ["s1"])
        self.assertEqual(stale[0].reason, "stale_map")
        self.assertEqual(stale[0].caller_id, "f1")

    def test_deleted_and_renamed_sources_schedule_deletes(self):
        stale = compute_stale_strong_edges(
            baseline_strong_edges=[_baseline_edge("s1"), _baseline_edge("s2", file_path="old.pc")],
            reaccepted_site_ids=set(),
            affected_files={"a.c", "old.pc"},
            file_reasons={"a.c": "source_deleted", "old.pc": "source_renamed"},
        )
        reasons = {edge.site_id: edge.reason for edge in stale}
        self.assertEqual(reasons, {"s1": "source_deleted", "s2": "source_renamed"})

    def test_staged_replacement_is_deterministic(self):
        def build():
            merge = evidence_merge.merge_call_evidence(
                [_semantic_observation()], project_id="p1"
            )
            return build_staged_replacement(
                project_id="p1",
                revision="r1",
                policy=SemanticPublicationPolicy(),
                merge_result=merge,
                baseline_strong_edges=[_baseline_edge("gone")],
                affected_files={"a.c"},
                vector_point_ids=["pt1"],
            )

        first, second = build(), build()
        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertEqual(len(first.strict_call_rows), 1)
        self.assertEqual(len(first.stale_strong_edges), 1)
        self.assertEqual(first.stale_strong_edges[0].reason, "downgraded")

    def test_policy_downgrade_removes_all_affected_strong_edges(self):
        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        staged = build_staged_replacement(
            project_id="p1",
            revision="r2",
            policy=SemanticPublicationPolicy(mode="off"),
            merge_result=merge,
            baseline_strong_edges=[_baseline_edge("s1")],
            affected_files={"a.c"},
        )
        # Rollback/off policies never carry strict rows into staging.
        self.assertEqual(staged.strict_call_rows, [])
        self.assertEqual(
            [edge.reason for edge in staged.stale_strong_edges], ["downgraded"]
        )

    def test_reaccepted_edges_survive(self):
        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        # Baseline CALLS edges are keyed by the strict row's site identity.
        site_id = merge.strict_call_rows[0]["site_id"]
        staged = build_staged_replacement(
            project_id="p1",
            revision="r3",
            policy=SemanticPublicationPolicy(),
            merge_result=merge,
            baseline_strong_edges=[_baseline_edge(site_id)],
            affected_files={"a.c"},
        )
        self.assertEqual(staged.stale_strong_edges, [])
        self.assertEqual(len(staged.strict_call_rows), 1)


class ProcPayloadValidationTest(unittest.TestCase):
    def _payload(self, nodes, relations=()):
        return {
            "file_def": {"file_path": "app.pc"},
            "functions": [
                {
                    "symbol_id": "fn1",
                    "name": "do_work",
                    "qualified_name": "do_work",
                    "kind": "function",
                    "scope_name": "",
                    "file_path": "app.pc",
                    "start_line": 1,
                    "end_line": 20,
                    "arity": 0,
                    "code": "",
                    "comment": "",
                    "summary": "",
                    "note": "",
                }
            ],
            "proc_nodes": nodes,
            "relations": relations,
        }

    def _sql_statement(self, **overrides):
        row = {
            "label": "SqlStatement",
            "symbol_id": "sql::app.pc::10::SELECT",
            "id": "sql::app.pc::10::SELECT",
            "name": "SELECT",
            "qualified_name": "SELECT",
            "kind": "SqlStatement",
            "file_path": "app.pc",
            "start_line": 10,
            "end_line": 10,
            "code": "EXEC SQL SELECT 1",
            "comment": "",
            "summary": "",
            "note": "",
        }
        row.update(overrides)
        return row

    def test_all_five_concrete_labels_are_accepted(self):
        labels = ["SqlStatement", "SqlDirective", "SqlCursor", "SqlHostVariable", "DatabaseTable"]
        nodes = [
            self._sql_statement(label=label, symbol_id=f"id-{label}", id=f"id-{label}")
            for label in labels
        ]
        validated, quarantine = validate_cplus_payload(self._payload(nodes), project_id="p1")
        self.assertEqual(len(validated["proc_nodes"]), 5)
        self.assertEqual(quarantine, ())

    def test_missing_or_unknown_proc_label_is_quarantined_not_defaulted(self):
        nodes = [
            self._sql_statement(label="", symbol_id="blank", id="blank"),
            self._sql_statement(label="ProcStatement", symbol_id="generic", id="generic"),
        ]
        validated, quarantine = validate_cplus_payload(self._payload(nodes), project_id="p1")
        self.assertEqual(validated["proc_nodes"], [])
        self.assertEqual({record.reason for record in quarantine}, {QuarantineReason.INVALID_RECORD})

    def test_proc_relation_endpoints_are_validated(self):
        statement = self._sql_statement()
        host = self._sql_statement(
            label="SqlHostVariable", symbol_id="hv1", id="hv1", name="hv"
        )
        good = [
            {
                "source_label": "Function",
                "target_label": "SqlStatement",
                "source_id": "fn1",
                "target_id": statement["id"],
                "rel_type": "DECLARES_STATEMENT",
            },
            {
                "source_label": "SqlStatement",
                "target_label": "SqlHostVariable",
                "source_id": statement["id"],
                "target_id": "hv1",
                "rel_type": "BINDS_PARAMETER",
            },
        ]
        bad = [
            {
                "source_label": "Function",
                "target_label": "SqlCursor",  # DECLARES_STATEMENT may not target a cursor
                "source_id": "fn1",
                "target_id": "cursor1",
                "rel_type": "DECLARES_STATEMENT",
            },
        ]
        validated, quarantine = validate_cplus_payload(
            self._payload([statement, host], good + bad), project_id="p1"
        )
        self.assertEqual(len(validated["relations"]), 2)
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].reason, QuarantineReason.INVALID_RECORD)

    def test_accounting_stays_balanced_with_proc_quarantine(self):
        nodes = [self._sql_statement(label="Bogus", symbol_id="x", id="x")]
        validated, quarantine = validate_cplus_payload(self._payload(nodes), project_id="p1")
        accounting = accounting_for_payload(validated, quarantine)
        self.assertEqual(
            accounting.discovered,
            accounting.accepted + accounting.quarantined + accounting.rejected,
        )
        self.assertEqual(accounting.quarantined, 1)


class PublicationValidationTest(unittest.TestCase):
    def _staged(self):
        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        return build_staged_replacement(
            project_id="p1",
            revision="r1",
            policy=SemanticPublicationPolicy(),
            merge_result=merge,
            evidence_observations=merge.observation_writer_rows(),
            vector_point_ids=["pt1"],
        )

    def test_exact_counts_pass(self):
        staged = self._staged()
        result = validate_staged_publication(
            staged,
            readback={
                "strict_calls": 1,
                "evidence_sites": 1,
                "evidence_observations": 1,
                "vector_items": 1,
                "stale_strong_edge_survivors": 0,
            },
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, ())

    def test_count_mismatch_and_missing_readback_block(self):
        staged = self._staged()
        result = validate_staged_publication(
            staged,
            readback={
                "strict_calls": 0,
                "evidence_sites": 1,
                "stale_strong_edge_survivors": 0,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn(
            "count_mismatch:strict_calls:expected=1:actual=0", result.violations
        )
        self.assertTrue(
            any(v.startswith("missing_readback:") for v in result.violations)
        )

    def test_surviving_stale_strong_edge_blocks_publication(self):
        staged = self._staged()
        result = validate_staged_publication(
            staged,
            readback={
                "strict_calls": 1,
                "evidence_sites": 1,
                "evidence_observations": 1,
                "vector_items": 1,
                "stale_strong_edge_survivors": 1,
            },
        )
        self.assertFalse(result.ok)
        self.assertIn("stale_strong_edges_survived:expected=0:actual=1", result.violations)

    def test_coverage_requirement_is_enforced(self):
        staged = self._staged()
        result = validate_staged_publication(
            staged,
            readback={
                "strict_calls": 1,
                "evidence_sites": 1,
                "evidence_observations": 1,
                "vector_items": 1,
                "stale_strong_edge_survivors": 0,
            },
            coverage_block={"status": "partial"},
            expectation=PublicationExpectation(
                strict_call_count=1,
                evidence_site_count=1,
                evidence_observation_count=1,
                vector_item_count=1,
                coverage_status="complete",
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("coverage_mismatch:expected=complete:actual=partial", result.violations)

    def test_dangling_observations_are_not_expected_edges(self):
        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation(callee="missing")],
            accepted_function_ids={"f1", "f2"},
            project_id="p1",
        )
        staged = build_staged_replacement(
            project_id="p1",
            revision="r1",
            policy=SemanticPublicationPolicy(),
            merge_result=merge,
            evidence_observations=merge.observation_writer_rows(),
        )
        expectation = expected_effects(staged)
        self.assertEqual(expectation.evidence_observation_count, 0)


class _PublishBoundary:
    """Simulate the concurrency owner's atomic publish boundary."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.published = []

    def __call__(self, manifest, validate):
        if self.fail:
            raise RuntimeError("store maintenance window")
        validate(manifest)
        self.published.append(manifest)
        return manifest


class PublicationPipelineTest(unittest.TestCase):
    def _staged(self):
        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        return build_staged_replacement(
            project_id="p1",
            revision="rev-1",
            policy=SemanticPublicationPolicy(),
            merge_result=merge,
            evidence_observations=merge.observation_writer_rows(),
            vector_point_ids=["pt1"],
        )

    def _readback(self):
        return {
            "strict_calls": 1,
            "evidence_sites": 1,
            "evidence_observations": 1,
            "vector_items": 1,
            "stale_strong_edge_survivors": 0,
        }

    def test_successful_publication_updates_ledger(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            outcome = publish_staged_generation(
                self._staged(),
                validate_and_publish=_PublishBoundary(),
                readback=self._readback(),
                ledger=ledger,
                revision="rev-1",
            )
            self.assertEqual(outcome.outcome, RunOutcome.SUCCESS)
            self.assertTrue(outcome.generation_id)
            self.assertEqual(ledger.last_valid()["revision"], "rev-1")
            self.assertEqual(outcome.accounting["revision"], "rev-1")

    def test_validation_failure_keeps_last_generation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            ledger.record(
                generation_id="gen-prev",
                revision="rev-0",
                fingerprint="fp0",
                policy=SemanticPublicationPolicy().to_dict(),
            )
            bad_readback = dict(self._readback(), strict_calls=0)
            outcome = publish_staged_generation(
                self._staged(),
                validate_and_publish=_PublishBoundary(),
                readback=bad_readback,
                ledger=ledger,
                revision="rev-1",
            )
            self.assertEqual(outcome.outcome, RunOutcome.FAILED_TERMINAL)
            self.assertEqual(outcome.retained_generation, "gen-prev")
            self.assertEqual(ledger.last_valid()["generation_id"], "gen-prev")
            self.assertTrue(outcome.failure.to_dict()["details"]["violations"])

    def test_undrained_queue_blocks_publication_retryably(self):
        outcome = publish_staged_generation(
            self._staged(),
            validate_and_publish=_PublishBoundary(),
            readback=self._readback(),
            queue_drained=False,
        )
        self.assertEqual(outcome.outcome, RunOutcome.FAILED_RETRYABLE)
        self.assertEqual(outcome.failure.code, "semantic_queue_not_drained")

    def test_publication_boundary_failure_is_ambiguous_and_retains(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            ledger.record(
                generation_id="gen-prev",
                revision="rev-0",
                fingerprint="fp0",
                policy=SemanticPublicationPolicy().to_dict(),
            )
            outcome = publish_staged_generation(
                self._staged(),
                validate_and_publish=_PublishBoundary(fail=True),
                readback=self._readback(),
                ledger=ledger,
                revision="rev-1",
            )
            self.assertEqual(outcome.outcome, RunOutcome.AMBIGUOUS)
            self.assertEqual(outcome.retained_generation, "gen-prev")
            self.assertEqual(ledger.last_valid()["generation_id"], "gen-prev")

    def test_real_generation_manager_boundary_publishes_atomically(self):
        import tempfile

        from cortex_harness.storage.contracts import GenerationState, PhysicalTargetKey
        from cortex_harness.storage.generation import GenerationManager

        with tempfile.TemporaryDirectory() as tmp:
            target = PhysicalTargetKey.from_paths(
                instance_id="local",
                owner_id="cplus",
                graph_path=Path(tmp) / "graph.rdb",
                vector_path=Path(tmp) / "vector",
            )
            manager = GenerationManager(Path(tmp) / "store", target)
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            outcome = publish_staged_generation(
                self._staged(),
                validate_and_publish=manager,
                readback=self._readback(),
                ledger=ledger,
                revision="rev-1",
            )
            self.assertEqual(outcome.outcome, RunOutcome.SUCCESS)
            active = manager.load_active()
            self.assertIsNotNone(active)
            self.assertEqual(active.state, GenerationState.PUBLISHED)
            self.assertEqual(active.generation_id, outcome.generation_id)
            self.assertEqual(ledger.last_valid()["generation_id"], outcome.generation_id)

    def test_owner_contract_rejection_is_terminal_not_ambiguous(self):
        import tempfile

        class _RejectingManager:
            def allocate(self, revision, generation_id=None):
                return None

            def publish(self, manifest, validate):
                raise ValueError("cannot publish a generation for a different physical target")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            outcome = publish_staged_generation(
                self._staged(),
                validate_and_publish=_RejectingManager(),
                readback=self._readback(),
                ledger=ledger,
                revision="rev-1",
            )
            self.assertEqual(outcome.outcome, RunOutcome.FAILED_TERMINAL)
            self.assertEqual(outcome.failure.code, "semantic_publication_contract_rejected")
            self.assertIsNone(ledger.last_valid())


class RollbackTest(unittest.TestCase):
    def test_rollback_selects_last_valid_generation_without_reparse(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            ledger.record(
                generation_id="gen-9",
                revision="rev-9",
                fingerprint="fp9",
                policy=SemanticPublicationPolicy().to_dict(),
            )
            policy, state = rollback_to_last_valid_generation(ledger)
            self.assertEqual(policy.mode, "rollback")
            self.assertFalse(policy.semantic_publication_allowed)
            self.assertTrue(state.active)
            self.assertEqual(state.served_generation, "gen-9")
            self.assertEqual(state.served_revision, "rev-9")
            self.assertTrue(state.weak_evidence_preserved)
            # The rollback policy itself keeps blocking strong edges.
            decision = strong_edge_publication_decision(
                policy=policy,
                file_quality=_clean_quality(),
                evidence_policy=_allowed_policy(),
                evidence_row=_semantic_observation(),
            )
            self.assertFalse(decision.allowed)

    def test_rollback_without_recorded_generation_stays_in_containment(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            _, state = rollback_to_last_valid_generation(ledger)
            self.assertTrue(state.active)
            self.assertEqual(state.served_generation, "")
            self.assertIn("no recorded semantic generation", state.detail)

    def test_ledger_history_is_bounded_and_durable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "nested" / "ledger.json")
            for index in range(12):
                ledger.record(
                    generation_id=f"gen-{index}",
                    revision=f"rev-{index}",
                    fingerprint=f"fp{index}",
                    policy=SemanticPublicationPolicy().to_dict(),
                )
            status = ledger.status()
            self.assertEqual(status["generation_count"], 8)
            self.assertEqual(status["last_valid_generation"], "gen-11")

    def test_corrupt_ledger_is_visible_not_silently_reset(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text("{not json", encoding="utf-8")
            ledger = SemanticGenerationLedger(path)
            status = ledger.status()
            self.assertTrue(status.get("unreadable"))
            self.assertIn("repair", status.get("detail", ""))
            _, rollback = rollback_to_last_valid_generation(ledger)
            self.assertIn("repair", rollback.detail)

    def test_status_surfaces_queue_coverage_generation_revision_policy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SemanticGenerationLedger(Path(tmp) / "ledger.json")
            policy, rollback = rollback_to_last_valid_generation(ledger)
            status = publication_status(
                policy=policy,
                queue={"state": "drained"},
                coverage_block={"status": "partial"},
                ledger=ledger,
                revision="rev-1",
                rollback=rollback,
            )
            for key in ("semantic_policy", "queue", "coverage", "generation", "revision", "rollback"):
                self.assertIn(key, status)
            self.assertEqual(status["semantic_policy"]["mode"], "rollback")
            self.assertEqual(status["rollback"]["weak_evidence_preserved"], True)


class VectorSafetyTest(unittest.TestCase):
    def test_generated_classes_never_embed(self):
        for generated_class in (
            "precompiler_wrapper",
            "precompiler_runtime",
            "generated_declaration",
            "unmapped_generated",
        ):
            reason = vector_item_rejection_reason(
                {"id": "x", "generated_code_class": generated_class}
            )
            self.assertEqual(reason, f"generated_class_{generated_class}")

    def test_credential_bearing_text_never_embeds(self):
        reason = vector_item_rejection_reason(
            {
                "id": "stmt1",
                "label": "SqlStatement",
                "code": "EXEC SQL CONNECT :u IDENTIFIED BY :p",
                "summary": "connect user id=scott password=tiger",
            }
        )
        self.assertTrue(reason and reason.startswith("credential_bearing_"))

    def test_approved_original_sql_facts_embed(self):
        reason = vector_item_rejection_reason(
            {
                "id": "stmt1",
                "label": "SqlStatement",
                "code": "EXEC SQL SELECT c1 INTO :v FROM t1",
                "summary": "select from t1",
            }
        )
        self.assertIsNone(reason)

    def test_masked_origin_is_rejected(self):
        reason = vector_item_rejection_reason(
            {"id": "m1", "label": "SqlStatement", "vector_origin": "masked"}
        )
        self.assertEqual(reason, "masked_origin")

    def test_sanitizer_accounts_for_every_exclusion(self):
        items = [
            {"id": "ok1", "label": "SqlStatement", "code": "EXEC SQL SELECT 1"},
            {"id": "gen1", "generated_code_class": "precompiler_runtime"},
            {"id": "secret1", "code": "password=hunter2"},
        ]
        safe, rejections = sanitize_vector_items(items)
        self.assertEqual([item["id"] for item in safe], ["ok1"])
        self.assertEqual(
            {rejection["identity"]: rejection["reason"] for rejection in rejections},
            {"gen1": "generated_class_precompiler_runtime", "secret1": "credential_bearing_code"},
        )


class JournalEvidenceOperationTest(unittest.TestCase):
    def test_staging_node_contracts_are_registered(self):
        expected = {
            "call_evidence:sites": ("CallSite", "site_id", "site_id", "props"),
            "call_evidence:configurations": (
                "BuildConfiguration",
                "config_fingerprint",
                "config_fingerprint",
                "props",
            ),
            "call_evidence:coverage": ("SemanticCoverage", "fingerprint", "fingerprint", "props"),
        }
        for label, (node_label, identity, row_identity, row_props) in expected.items():
            operation = GraphWriteOperation.for_label(label)
            self.assertEqual(operation.reconciliation, "node_identity", label)
            self.assertEqual(operation.node_label, node_label, label)
            self.assertEqual(operation.identity_property, identity, label)
            self.assertEqual(operation.row_identity_property, row_identity, label)
            self.assertEqual(operation.row_properties_property, row_props, label)

    def test_evidence_edge_operations_compile_replay_and_readback(self):
        operation = GraphWriteOperation.for_label(
            "call_evidence:edges:CallSite:site_id:OBSERVED_AS:Function:id:evidence_id"
        )
        self.assertEqual(operation.reconciliation, "evidence_edge")
        rows = [
            {
                "source_label": "CallSite",
                "source_property": "site_id",
                "source_id": "s1",
                "target_label": "Function",
                "target_property": "id",
                "target_id": "f2",
                "rel_type": "OBSERVED_AS",
                "edge_property": "evidence_id",
                "edge_id": "e1",
                "project_id": "p1",
                "props": {"resolution_class": "direct_resolved"},
            }
        ]
        query, params = compile_persisted_mutation(operation, rows)
        self.assertIn("MERGE (a)-[r:OBSERVED_AS {evidence_id: row.edge_id}]->(b)", query)
        readback = compile_reconciliation_readback(operation, params["rows"])
        self.assertIsNotNone(readback)
        self.assertIn("OPTIONAL MATCH", readback[0])

    def test_pattern_merge_edges_compile_without_edge_key(self):
        operation = GraphWriteOperation.for_label(
            "call_evidence:edges:Function:id:HAS_CALLSITE:CallSite:site_id"
        )
        rows = [
            {
                "source_label": "Function",
                "source_property": "id",
                "source_id": "f1",
                "target_label": "CallSite",
                "target_property": "site_id",
                "target_id": "s1",
                "rel_type": "HAS_CALLSITE",
                "project_id": "p1",
            }
        ]
        query, _ = compile_persisted_mutation(operation, rows)
        self.assertIn("MERGE (a)-[r:HAS_CALLSITE]->(b)", query)

    def test_keyed_edge_without_edge_id_fails_closed(self):
        from tools.graph.writer.query_contract import group_evidence_edges

        with self.assertRaises(ValueError):
            group_evidence_edges(
                [
                    {
                        "source_label": "CallSite",
                        "source_property": "site_id",
                        "source_id": "s1",
                        "target_label": "Function",
                        "target_property": "id",
                        "target_id": "f2",
                        "rel_type": "OBSERVED_AS",
                        "edge_property": "evidence_id",
                        "project_id": "p1",
                    }
                ]
            )

    def test_unregistered_labels_still_fail_closed(self):
        operation = GraphWriteOperation.for_label("call_evidence:observations")
        self.assertEqual(operation.reconciliation, "unsupported")


class _RequiredModeDriver:
    provider = "falkordb"

    def __init__(self, config=None) -> None:
        self.journal_config = config
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        return ([{"count": len(values.get("rows", []))}], [], None)


class _CountMismatchDriver(_RequiredModeDriver):
    """Simulates a batch whose required endpoints did not all resolve."""

    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        count = len(values.get("rows", []))
        if "OBSERVED_AS" in query and "MATCH (a:CallSite" in query:
            count = max(0, count - 1)  # one callee endpoint missing
        return ([{"count": count}], [], None)


class _RecordingDeleteDriver(_RequiredModeDriver):
    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        if query.strip().startswith("UNWIND $rows AS row") and "DELETE r" in query:
            return ([{"count": len(values.get("rows", []))}], [], None)
        return ([{"count": len(values.get("rows", []))}], [], None)


class RequiredModeWriterTest(unittest.TestCase):
    """The evidence staging plane must survive journal-required mode."""

    def _writer(self, tmp_path: Path):
        env: dict[str, str] = {}
        config = configure_journal_env(
            env,
            root=tmp_path / "source",
            project_id="demo",
            parser="python",
            source_revision="revision-1",
            source_snapshot="snapshot-1",
            physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
            cache_dir=tmp_path / "cache",
            generation="attempt-1",
        )
        driver = _RequiredModeDriver(config)
        return LanguageCodeWriter(driver, batch_size=10), driver

    def test_evidence_writes_are_journaled_in_required_mode(self):
        import tempfile

        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            writer, driver = self._writer(Path(tmp))
            try:
                counts = asyncio.run(
                    writer.write_all(
                        call_evidence_sites=merge.site_writer_rows(),
                        call_evidence_observations=merge.observation_writer_rows(),
                        build_configurations=[
                            {
                                "config_fingerprint": "cf1",
                                "site_id": merge.call_sites[0].site_id,
                                "props": {"project_id": "p1", "compiler": "gcc"},
                            }
                        ],
                        semantic_coverage=[
                            {
                                "fingerprint": "cov1",
                                "props": {"status": "complete", "tu_key": "a.c"},
                            }
                        ],
                    )
                )
            finally:
                writer.close_journal()
            self.assertEqual(counts["call_evidence_sites"], 1)
            self.assertEqual(counts["call_evidence_observations"], 1)
            self.assertEqual(counts["build_configurations"], 1)
            self.assertEqual(counts["semantic_coverage"], 1)
            joined = "\n".join(query for query, _ in driver.calls)
            self.assertIn("MERGE (site:CallSite {site_id: row.site_id})", joined)
            self.assertIn("OBSERVED_AS", joined)
            self.assertIn("IN_CONFIGURATION", joined)

    def test_strong_call_rows_write_through_the_journaled_site_contract(self):
        import tempfile

        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            writer, driver = self._writer(Path(tmp))
            try:
                written = asyncio.run(
                    writer.write_calls_with_site(merge.strict_call_rows)
                )
            finally:
                writer.close_journal()
            self.assertEqual(written, 1)

    def test_proc_evidence_joins_are_journaled(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            writer, driver = self._writer(Path(tmp))
            try:
                written = asyncio.run(
                    writer.write_proc_evidence_joins(
                        [
                            {
                                "function_id": "sem1",
                                "statement_id": "sql1",
                                "join_quality": "unique",
                                "project_id": "p1",
                            }
                        ],
                        [
                            {
                                "host_variable_id": "hv1",
                                "declaration_id": "d1",
                                "declaration_kind": "variable",
                                "join_quality": "unique",
                                "project_id": "p1",
                            }
                        ],
                    )
                )
            finally:
                writer.close_journal()
            self.assertEqual(written, 2)
            joined = "\n".join(query for query, _ in driver.calls)
            self.assertIn("EXECUTES_SQL", joined)
            self.assertIn("RESOLVES_HOST_DECLARATION", joined)

    def test_unresolved_required_endpoint_fails_closed_in_required_mode(self):
        import tempfile

        from tools.graph.journal import JournalError

        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            env: dict[str, str] = {}
            config = configure_journal_env(
                env,
                root=Path(tmp) / "source",
                project_id="demo",
                parser="python",
                source_revision="revision-1",
                source_snapshot="snapshot-1",
                physical_target=f"falkordb:{tmp}/code.rdb:demo",
                cache_dir=Path(tmp) / "cache",
                generation="attempt-1",
            )
            driver = _CountMismatchDriver(config)
            writer = LanguageCodeWriter(driver, batch_size=10)
            try:
                with self.assertRaises(JournalError):
                    asyncio.run(
                        writer.write_call_evidence_observations(
                            merge.observation_writer_rows()
                        )
                    )
            finally:
                writer.close_journal()

    def test_sites_merge_before_configuration_edges(self):
        import tempfile

        merge = evidence_merge.merge_call_evidence(
            [_semantic_observation()], project_id="p1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            writer, driver = self._writer(Path(tmp))
            try:
                asyncio.run(
                    writer.write_all(
                        call_evidence_sites=merge.site_writer_rows(),
                        build_configurations=[
                            {
                                "config_fingerprint": "cf1",
                                "site_id": merge.call_sites[0].site_id,
                                "props": {"project_id": "p1", "compiler": "gcc"},
                            }
                        ],
                    )
                )
            finally:
                writer.close_journal()
            queries = [query for query, _ in driver.calls]
            site_merge_index = next(
                index
                for index, query in enumerate(queries)
                if "MERGE (site:CallSite {site_id: row.site_id})" in query
            )
            in_configuration_index = next(
                index
                for index, query in enumerate(queries)
                if "IN_CONFIGURATION" in query
            )
            # The required-endpoint IN_CONFIGURATION edge can only run after
            # the CallSite nodes it targets were merged.
            self.assertLess(site_merge_index, in_configuration_index)

    def test_non_dangling_observation_without_callee_fails_closed(self):
        driver = _RequiredModeDriver()
        writer = LanguageCodeWriter(driver, database="code")
        with self.assertRaises(ValueError):
            asyncio.run(
                writer.write_call_evidence_observations(
                    [{"site_id": "s1", "callee_id": "", "evidence_id": "e1"}]
                )
            )

    def test_stale_strong_edge_deletion_runs_fenced_and_counts(self):
        import tempfile

        from tools.cplus.guarded_publication import (
            StaleStrongEdge,
            apply_stale_strong_edge_deletions,
        )

        stale = [
            StaleStrongEdge("f1", "f2", "s1", "a.c", "stale_map"),
            StaleStrongEdge("f1", "f3", "s2", "a.c", "downgraded"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _, driver = self._writer(Path(tmp))
            deleted = asyncio.run(
                apply_stale_strong_edge_deletions(
                    driver=driver, database="code", stale_edges=stale
                )
            )
            self.assertEqual(deleted, 2)
            delete_queries = [
                (query, params)
                for query, params in driver.calls
                if "DELETE r" in query
            ]
            self.assertEqual(len(delete_queries), 1)
            self.assertIn("MATCH (caller:Function {id: row.caller_id})", delete_queries[0][0])
            self.assertEqual(
                [row["site_id"] for row in delete_queries[0][1]["rows"]], ["s1", "s2"]
            )


if __name__ == "__main__":
    unittest.main()
