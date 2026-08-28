"""Phase 04 evidence merge, staging writes, query profiles, and fail-closed
negative-result semantics."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
MCP_DIR = CODE_TINY / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
MCP_CPLUS_DIR = CODE_TINY / "mcp" / "cplus"
if str(MCP_CPLUS_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_CPLUS_DIR))

from tools.common import call_evidence  # noqa: E402
from tools.cplus import evidence_merge  # noqa: E402
from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


def _lexical(callee="f2", line=3, **extra):
    return {
        "caller_id": "f1",
        "callee_id": callee,
        "file_path": "a.c",
        "line": line,
        "column": 1,
        "call_type": "call",
        "resolution_class": "lexical_candidate",
        "semantic_provider": "tree_sitter",
        "parse_run_id": "run-1",
        "project_id": "p1",
        **extra,
    }


def _semantic(callee="f2", config="cf1", line=3, callee_usr="usr:f2", **extra):
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
        "config_fingerprint": config,
        "callee_usr": callee_usr,
        "project_id": "p1",
        "context_fidelity": "faithful",
        "context_admission": "accepted",
        "execution_coverage": "complete",
        "context_attestation": "attestation-1",
        "manifest_key": f"p1:g1:r1:policy:a.c:{config}",
        **extra,
    }


class EvidenceMergeTest(unittest.TestCase):
    def test_exact_duplicates_collapse_with_provenance(self):
        first = evidence_merge.merge_call_evidence([_lexical(), dict(_lexical(), parse_run_id="run-2")])
        second = evidence_merge.merge_call_evidence([_lexical(), dict(_lexical(), parse_run_id="run-2")])
        self.assertEqual(first.duplicates_collapsed, 1)
        self.assertEqual(len(first.call_sites), 1)
        observation = first.call_sites[0].observations[0]
        self.assertEqual(observation["observation_count"], 2)
        self.assertEqual(sorted(observation["_repeat_runs"]), ["run-1", "run-2"])
        # Deterministic: identical inputs yield identical identities.
        self.assertEqual(
            first.call_sites[0].site_id,
            second.call_sites[0].site_id,
        )
        self.assertEqual(
            first.call_sites[0].observations[0]["evidence_id"],
            second.call_sites[0].observations[0]["evidence_id"],
        )

    def test_semantic_upgrades_site_without_erasing_lexical_evidence(self):
        result = evidence_merge.merge_call_evidence([_lexical(), _semantic()])
        self.assertEqual(len(result.call_sites), 1)
        site = result.call_sites[0]
        self.assertEqual(len(site.observations), 2)
        self.assertEqual(site.resolved_class, "direct_resolved")
        # One strict compatibility CALLS row, linked to site and evidence ids.
        self.assertEqual(len(result.strict_call_rows), 1)
        row = result.strict_call_rows[0]
        self.assertEqual(row["caller_id"], "f1")
        self.assertEqual(row["callee_id"], "f2")
        self.assertEqual(row["props"]["resolution_class"], "direct_resolved")
        self.assertEqual(row["props"]["site_id"], site.site_id)
        self.assertTrue(row["props"]["evidence_id"])

    def test_weak_evidence_never_produces_strict_calls(self):
        result = evidence_merge.merge_call_evidence([_lexical()])
        self.assertEqual(result.strict_call_rows, [])
        self.assertEqual(result.call_sites[0].resolved_class, "lexical_candidate")

    def test_strong_claim_without_provider_fails_closed(self):
        with self.assertRaises(ValueError):
            evidence_merge.merge_call_evidence(
                [_semantic(semantic_provider="tree_sitter", callee_usr="")]
            )

    def test_unknown_resolution_class_fails_closed(self):
        with self.assertRaises(ValueError):
            evidence_merge.merge_call_evidence([_lexical(resolution_class="guess")])

    def test_contradictory_configurations_coexist(self):
        result = evidence_merge.merge_call_evidence(
            [_semantic(config="cf1"), _semantic(config="cf2")]
        )
        self.assertEqual(len(result.call_sites), 1)
        self.assertEqual(len(result.call_sites[0].observations), 2)
        self.assertEqual(sorted(result.call_sites[0].configs), ["cf1", "cf2"])
        self.assertEqual(result.configuration_count, 0)
        result_with_configs = evidence_merge.merge_call_evidence(
            [_semantic(config="cf1")],
            configurations=[{"config_fingerprint": "cf1"}, {"config_fingerprint": "cf2"}],
            project_id="p1",
        )
        self.assertEqual(result_with_configs.configuration_count, 2)

    def test_ambiguous_callee_across_configs_blocks_strict_derivation(self):
        result = evidence_merge.merge_call_evidence(
            [_semantic(callee="f2", config="cf1"), _semantic(callee="f3", config="cf2", callee_usr="usr:f3")]
        )
        # Two valid configurations disagree on the callee: no single accepted
        # observation, no strict CALLS derivation, evidence preserved.
        self.assertEqual(result.strict_call_rows, [])
        self.assertEqual(len(result.call_sites[0].observations), 2)

    def test_cross_project_observation_refused(self):
        with self.assertRaises(ValueError):
            evidence_merge.merge_call_evidence([_lexical(project_id="other")], project_id="p1")

    def test_cross_project_configuration_refused(self):
        with self.assertRaises(ValueError):
            evidence_merge.merge_call_evidence(
                [_lexical()],
                configurations=[{"config_fingerprint": "cf1", "project_id": "other"}],
                project_id="p1",
            )

    def test_frontier_coverage_accumulates(self):
        result = evidence_merge.merge_call_evidence(
            [_lexical()],
            coverage_records=[
                {"status": "complete", "tu_key": "a.c"},
                {"status": "partial", "tu_key": "b.c", "detail": "missing header ctx"},
            ],
        )
        frontier = result.frontier
        self.assertEqual(frontier["status"], "partial")
        self.assertTrue(any("b.c" in reason for reason in frontier["reasons"]))


class ProcJoinTest(unittest.TestCase):
    def test_unique_semantic_function_join(self):
        joins = evidence_merge.merge_proc_function_joins(
            [
                {
                    "statement_id": "sql1",
                    "enclosing_function_id": "lex1",
                    "semantic_candidates": [{"function_id": "sem1", "source_map_quality": "exact"}],
                    "source_map_quality": "exact",
                }
            ]
        )
        self.assertEqual(joins[0]["join_quality"], "unique")
        self.assertEqual(joins[0]["function_id"], "sem1")

    def test_ambiguity_is_preserved_not_selected(self):
        joins = evidence_merge.merge_proc_function_joins(
            [
                {
                    "statement_id": "sql1",
                    "enclosing_function_id": "lex1",
                    "semantic_candidates": [
                        {"function_id": "sem1"},
                        {"function_id": "sem2"},
                    ],
                }
            ]
        )
        self.assertEqual(joins[0]["join_quality"], "ambiguous")
        self.assertEqual(joins[0]["function_id"], "lex1")
        self.assertEqual(joins[0]["semantic_function_ids"], ["sem1", "sem2"])

    def test_unresolved_join_keeps_lexical_function(self):
        joins = evidence_merge.merge_proc_function_joins(
            [{"statement_id": "sql1", "enclosing_function_id": "lex1"}]
        )
        self.assertEqual(joins[0]["join_quality"], "unresolved")
        self.assertEqual(joins[0]["function_id"], "lex1")

    def test_dynamic_sql_makes_frontier_partial(self):
        joins = evidence_merge.merge_proc_function_joins(
            [{"statement_id": "sql1", "enclosing_function_id": "lex1", "is_dynamic_sql": True}]
        )
        coverage = evidence_merge.proc_data_impact_coverage(joins)
        self.assertEqual(coverage["status"], "partial")
        self.assertTrue(any("dynamic_sql" in reason for reason in coverage["reasons"]))

    def test_host_declaration_resolution_states(self):
        rows = evidence_merge.resolve_proc_host_declarations(
            [
                {"host_variable_id": "hv1", "candidates": [{"declaration_id": "d1", "declaration_kind": "local"}]},
                {
                    "host_variable_id": "hv2",
                    "candidates": [
                        {"declaration_id": "d1", "declaration_kind": "local"},
                        {"declaration_id": "d2", "declaration_kind": "param"},
                    ],
                },
                {
                    "host_variable_id": "hv3",
                    "candidates": [
                        {"declaration_id": "d1", "config_fingerprint": "cf1"},
                        {"declaration_id": "d2", "config_fingerprint": "cf2"},
                    ],
                },
                {"host_variable_id": "hv4"},
            ]
        )
        by_host = {}
        for row in rows:
            by_host.setdefault(row["host_variable_id"], []).append(row)
        self.assertEqual(by_host["hv1"][0]["join_quality"], "unique")
        self.assertEqual({r["join_quality"] for r in by_host["hv2"]}, {"ambiguous"})
        self.assertEqual({r["join_quality"] for r in by_host["hv3"]}, {"cross_config"})
        self.assertEqual({r["config_fingerprint"] for r in by_host["hv3"]}, {"cf1", "cf2"})
        self.assertEqual(by_host["hv4"][0]["join_quality"], "unresolved")


class SchemaRegistryTest(unittest.TestCase):
    def test_staging_labels_and_relationships_registered(self):
        self.assertTrue(CODE_GRAPH_SCHEMA.has_identity_index("CallSite"))
        self.assertTrue(CODE_GRAPH_SCHEMA.has_identity_index("BuildConfiguration"))
        self.assertTrue(CODE_GRAPH_SCHEMA.has_identity_index("SemanticCoverage"))
        self.assertTrue(CODE_GRAPH_SCHEMA.has_identity_index("CallSite", property_name="site_id") or
                        any(i.label == "CallSite" and i.properties == ("site_id",) for i in CODE_GRAPH_SCHEMA.indexes))
        registered = {rel[0] for rel in CODE_GRAPH_SCHEMA.relationship_types}
        self.assertIn("HAS_CALLSITE", registered)
        self.assertIn("OBSERVED_AS", registered)
        self.assertIn("IN_CONFIGURATION", registered)
        self.assertIn("EXECUTES_SQL", registered)
        self.assertIn("RESOLVES_HOST_DECLARATION", registered)

    def test_provider_parity_driver_indexes(self):
        # Both providers consume the same index contract.
        driver_indexes = CODE_GRAPH_SCHEMA.driver_indexes()
        labels = {index["label"] for index in driver_indexes}
        self.assertIn("CallSite", labels)
        self.assertIn("BuildConfiguration", labels)
        self.assertIn("SemanticCoverage", labels)

    def test_fingerprint_is_versioned(self):
        payload = {
            "name": CODE_GRAPH_SCHEMA.name,
            "version": CODE_GRAPH_SCHEMA.version,
        }
        self.assertIn("version", payload)
        self.assertEqual(CODE_GRAPH_SCHEMA.version, 2)
        self.assertTrue(CODE_GRAPH_SCHEMA.fingerprint)


class QueryProfileTest(unittest.TestCase):
    def test_exact_scope_manifest_blocks_missing_duplicate_and_partial_keys(self):
        expected = [
            {
                "project_id": "p1",
                "generation_id": "g1",
                "revision": "r1",
                "policy_version": "v1",
                "tu_key": name,
                "config_fingerprint": "cfg",
            }
            for name in ("a.c", "b.c")
        ]
        complete = [dict(item, status="complete") for item in expected]
        self.assertEqual(
            call_evidence.exact_frontier_coverage(expected, complete)["status"],
            "complete",
        )
        missing = call_evidence.exact_frontier_coverage(expected, complete[:1])
        self.assertEqual(missing["status"], "partial")
        self.assertIn("scope_keys_missing", missing["reasons"])
        duplicate = call_evidence.exact_frontier_coverage(
            expected, [complete[0], complete[0], complete[1]]
        )
        self.assertIn("scope_keys_duplicate", duplicate["reasons"])
        partial = call_evidence.exact_frontier_coverage(
            expected, [complete[0], dict(complete[1], status="partial")]
        )
        self.assertIn("scope_keys_incomplete", partial["reasons"])

    def test_profile_relationship_selection(self):
        # The registry is the single authority for profile -> relationships.
        from framework_registry import CAPABILITIES

        profiles = CAPABILITIES["cplus"].default_query_profiles
        self.assertEqual(profiles["strict"], ("CALLS",))
        self.assertEqual(
            profiles["conservative"],
            ("CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"),
        )

    def test_class_admission_per_profile(self):
        self.assertTrue(call_evidence.class_allowed_in_profile("direct_resolved", "strict"))
        self.assertFalse(call_evidence.class_allowed_in_profile("lexical_candidate", "strict"))
        self.assertTrue(call_evidence.class_allowed_in_profile("possible_dispatch_target", "conservative"))

    def test_registry_profiles_for_cplus(self):
        from framework_registry import CAPABILITIES

        capability = CAPABILITIES["cplus"]
        self.assertEqual(capability.default_query_profiles["strict"], ("CALLS",))
        self.assertEqual(
            capability.default_query_profiles["conservative"],
            ("CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"),
        )
        proc_profile = capability.default_query_profiles["proc_data_impact"]
        self.assertIn("EXECUTES_SQL", proc_profile)
        self.assertIn("READS_FROM", proc_profile)
        # Pro*C aliases route to the same capability.
        from framework_registry import capability_for_parser

        self.assertEqual(capability_for_parser("pro*c").name, "cplus")

    def test_traversal_outcomes_fail_closed(self):
        self.assertEqual(
            call_evidence.traversal_outcome("unknown", result_is_empty=True),
            "incomplete",
        )
        self.assertEqual(
            call_evidence.traversal_outcome("partial", result_is_empty=True),
            "incomplete",
        )
        self.assertEqual(
            call_evidence.traversal_outcome("complete", result_is_empty=True),
            "complete",
        )
        self.assertEqual(
            call_evidence.traversal_outcome("partial", result_is_empty=False),
            "complete",
        )


class _FakeDriver:
    provider = "falkordb"

    def __init__(self) -> None:
        self.queries = []

    async def execute_query(self, query, parameters=None, database=None, **kwargs):
        params = dict(parameters or {})
        self.queries.append((query, params, database))
        return ([{"count": len(params.get("rows", []))}], [], None)


class StagingWriterTest(unittest.TestCase):
    def setUp(self):
        self.driver = _FakeDriver()
        self.writer = LanguageCodeWriter(self.driver, database="code")

    def test_write_all_routes_staging_plane(self):
        merge = evidence_merge.merge_call_evidence(
            [_lexical(), _semantic()],
            coverage_records=[{"status": "complete", "tu_key": "a.c"}],
            configurations=[{"config_fingerprint": "cf1", "project_id": "p1"}],
            project_id="p1",
        )
        site = merge.call_sites[0]
        counts = asyncio.run(
            self.writer.write_all(
                call_evidence_sites=[
                    {"site_id": site.site_id, "caller_id": "f1", "callee_id": "f2",
                     "props": site.to_staging_row()}
                ],
                call_evidence_observations=[
                    {
                        "site_id": site.site_id,
                        "callee_id": "f2",
                        "evidence_id": observation["evidence_id"],
                        "project_id": "p1",
                        "props": {"resolution_class": observation["resolution_class"]},
                    }
                    for observation in site.observations
                ],
                build_configurations=[
                    {
                        "config_fingerprint": "cf1",
                        "site_id": site.site_id,
                        "project_id": "p1",
                        "props": {"compiler": "gcc"},
                    }
                ],
                semantic_coverage=[{
                    "fingerprint": "cov1",
                    "project_id": "p1",
                    "props": {"status": "complete", "tu_key": "a.c"},
                }],
            )
        )
        self.assertEqual(counts["call_evidence_sites"], 1)
        self.assertEqual(counts["call_evidence_observations"], 2)
        self.assertEqual(counts["build_configurations"], 1)
        self.assertEqual(counts["semantic_coverage"], 1)
        joined = "\n".join(query for query, _, _ in self.driver.queries)
        self.assertIn("MERGE (site:CallSite", joined)
        self.assertIn("HAS_CALLSITE", joined)
        self.assertIn("OBSERVED_AS", joined)
        self.assertIn("IN_CONFIGURATION", joined)
        self.assertIn("MERGE (coverage:SemanticCoverage", joined)

    def test_write_proc_evidence_joins(self):
        counts = asyncio.run(
            self.writer.write_all(
                proc_function_joins=[
                    {"function_id": "sem1", "statement_id": "sql1",
                     "project_id": "p1",
                     "props": {"join_quality": "unique", "is_dynamic_sql": False}}
                ],
                proc_host_declarations=[
                    {"host_variable_id": "hv1", "declaration_id": "d1",
                     "project_id": "p1",
                     "declaration_kind": "variable",
                     "props": {"join_quality": "unique", "is_indicator": False}}
                ],
            )
        )
        self.assertEqual(counts["proc_evidence_joins"], 2)
        joined = "\n".join(query for query, _, _ in self.driver.queries)
        self.assertIn("EXECUTES_SQL", joined)
        self.assertIn("RESOLVES_HOST_DECLARATION", joined)

    def test_empty_staging_writes_are_noops(self):
        counts = asyncio.run(self.writer.write_all())
        self.assertNotIn("call_evidence_sites", counts)

    def test_site_row_adapter_matches_writer_shape(self):
        merge = evidence_merge.merge_call_evidence([_lexical(), _semantic()])
        row = merge.call_sites[0].to_writer_rows()
        self.assertEqual(
            set(row), {"site_id", "caller_id", "callee_id", "resolution_class", "props"}
        )
        self.assertEqual(row["callee_id"], "f2")
        # Props must be provider-storable scalars plus the joined config list.
        for value in row["props"].values():
            self.assertIsInstance(value, (str, int, float, bool, list))

    def test_staging_summary_reports_strongest_observed_class(self):
        merge = evidence_merge.merge_call_evidence(
            [_lexical(), _lexical(resolution_class="unresolved", callee_id="")]
        )
        self.assertEqual(merge.call_sites[0].resolved_class, "unresolved")

    def test_dangling_observations_are_flagged_and_persisted_on_site_props(self):
        # Phase 06: dangling evidence is computed by the merge layer against
        # the staged identities and persisted on the staging site's props;
        # the observation writer skips flagged rows instead of probing the
        # graph inside a mutation.
        merge = evidence_merge.merge_call_evidence(
            [_semantic(callee="missing_callee"), _lexical(callee="f2", line=4)],
            accepted_function_ids={"f1", "f2"},
            project_id="p1",
        )
        dangling_site = merge.call_sites[0]
        self.assertEqual(dangling_site.file_path, "a.c")
        site_row = dangling_site.to_writer_rows(accepted_function_ids={"f1", "f2"})
        self.assertEqual(len(site_row["props"]["dangling_observation_ids"]), 1)
        observation_rows = dangling_site.observation_writer_rows({"f1", "f2"})
        self.assertEqual(sum(1 for row in observation_rows if row["dangling"]), 1)

        driver = _FakeDriver()
        writer = LanguageCodeWriter(driver, database="code")
        linked = asyncio.run(writer.write_call_evidence_observations(observation_rows))
        self.assertEqual(linked, 0)  # the dangling row writes no edge
        self.assertEqual(driver.queries, [])
        # The merged result exposes both adapters for the whole staged set.
        self.assertEqual(len(merge.observation_writer_rows()), 2)
        self.assertEqual(len(merge.site_writer_rows()), len(merge.call_sites))


class OutcomePayloadTest(unittest.TestCase):
    def test_incomplete_outcome_carries_reasons_and_scope(self):
        payload = call_evidence.frontier_coverage(
            [{"status": "partial", "tu_key": "b.c", "detail": "missing ctx"}]
        )
        self.assertEqual(payload["status"], "partial")

    def test_unknown_coverage_blocks_negative_conclusions(self):
        block = call_evidence.frontier_coverage([])
        self.assertEqual(block["status"], "unknown")
        self.assertEqual(
            call_evidence.traversal_outcome(block["status"], result_is_empty=True),
            "incomplete",
        )


class McpFailClosedTest(unittest.TestCase):
    def test_profile_rel_types_resolution(self):
        from cplus_mcp import _profile_rel_types

        self.assertEqual(_profile_rel_types("cplus", "strict"), ["CALLS"])
        self.assertEqual(
            _profile_rel_types("cplus", "conservative"),
            ["CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"],
        )
        self.assertIsNone(_profile_rel_types("cplus", None))
        self.assertIsNone(_profile_rel_types("cplus", "default"))
        # Registry-declared profiles resolve even when not in the base set.
        self.assertIn(
            "EXECUTES_SQL", _profile_rel_types("cplus", "proc_data_impact")
        )
        with self.assertRaises(ValueError):
            _profile_rel_types("cplus", "yolo")

    def test_strict_filter_drops_non_semantic_calls_edges(self):
        from cplus_mcp import _filter_strict_edges

        graph = {
            "nodes": [
                {"id": "f1"}, {"id": "f2"}, {"id": "f3"}, {"id": "f4"},
            ],
            "edges": [
                {"type": "CALLS", "properties": {"resolution_class": "direct_resolved"},
                 "start_id": "f1", "end_id": "f2"},
                # Legacy heuristic CALLS edge without the evidence contract.
                {"type": "CALLS", "properties": {}, "start_id": "f1", "end_id": "f3"},
                {"type": "POSSIBLE_CALLS", "properties": {"resolution_class": "lexical_candidate"},
                 "start_id": "f1", "end_id": "f4"},
            ],
        }
        filtered, dropped = _filter_strict_edges(graph, "f1")
        self.assertEqual(dropped, 2)
        self.assertEqual(len(filtered["edges"]), 1)
        self.assertEqual(filtered["edges"][0]["end_id"], "f2")
        # Nodes pruned to surviving frontier plus the seed.
        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"f1", "f2"})

    def test_outcome_payload_fail_closed(self):
        from cplus_mcp import _outcome_payload

        payload = _outcome_payload(
            {"status": "unknown", "reasons": ["no semantic coverage records found for the visited frontier"], "record_count": 0},
            result_is_empty=True,
        )
        self.assertEqual(payload["outcome"], "incomplete")
        self.assertIn("semantic_coverage", payload)
        self.assertIn("suggested_next_semantic_scope", payload)
        self.assertIn("not authoritative", payload["reason"])

    def test_outcome_payload_complete(self):
        from cplus_mcp import _outcome_payload

        payload = _outcome_payload(
            {"status": "complete", "reasons": [], "record_count": 2},
            result_is_empty=True,
        )
        self.assertEqual(payload["outcome"], "complete")
        self.assertNotIn("reason", payload)


class _ScorerDriver:
    provider = "falkordb"

    def __init__(self, coverage_rows):
        self.coverage_rows = coverage_rows

    def execute_query_sync(self, query, params, database):
        if "SemanticCoverage" in query:
            return (self.coverage_rows, [], None)
        return ([], [], None)


class WorkflowScorerEvidenceTest(unittest.TestCase):
    def test_negative_recommendation_gated_on_coverage(self):
        from tools.common.workflow_impact_scorer import WorkflowImpactScorer

        scorer = WorkflowImpactScorer(
            _ScorerDriver([{"status": "partial", "tu_key": "a.c", "detail": ""}]),
            database="code",
        )
        result = asyncio.run(scorer.score("fn1", []))
        self.assertEqual(result.outcome, "incomplete")
        self.assertEqual(result.semantic_coverage["status"], "partial")
        self.assertIn("not an authoritative negative", result.recommendation)

    def test_complete_coverage_keeps_plain_negative(self):
        from tools.common.workflow_impact_scorer import WorkflowImpactScorer

        scorer = WorkflowImpactScorer(
            _ScorerDriver([{"status": "complete", "tu_key": "a.c", "detail": ""}]),
            database="code",
        )
        result = asyncio.run(scorer.score("fn1", []))
        self.assertEqual(result.outcome, "complete")
        self.assertIn("No workflow impact detected. Proceed", result.recommendation)

    def test_weak_relationships_flagged_and_confidence_reduced(self):
        from tools.common.workflow_impact_scorer import WorkflowImpactScorer

        scorer = WorkflowImpactScorer(
            _ScorerDriver([{"status": "complete", "tu_key": "a.c", "detail": ""}]),
            database="code",
            flow_relationships=["CALLS", "POSSIBLE_CALLS"],
        )
        result = asyncio.run(scorer.score("fn1", []))
        self.assertIn("conservative evidence, not confirmed direct calls", result.evidence_class_note)


if __name__ == "__main__":
    unittest.main()
