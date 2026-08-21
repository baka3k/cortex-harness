"""Phase 01 call-evidence contract tests: containment, validation, writer guard."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common import call_evidence  # noqa: E402
from tools.common import payload_validation  # noqa: E402
from tools.cplus import cplus_analyzer  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


class _FakeDriver:
    provider = "falkordb"

    def __init__(self) -> None:
        self.queries = []

    async def execute_query(self, query, parameters=None, database=None, **kwargs):
        self.queries.append((query, dict(parameters or {}), database))
        return ([{"count": len((parameters or {}).get("rows", []))}], [], None)


def _strong_row(**overrides):
    props = {
        "resolution_class": "direct_resolved",
        "semantic_provider": "clang_worker",
        "tu_key": "tu://direct.c:1",
        "config_fingerprint": "cfg-1",
        "callee_usr": "c:@F@target",
    }
    props.update(overrides)
    return {
        "caller_id": "caller-1",
        "callee_id": "callee-1",
        "site_id": "site-1",
        "props": props,
    }


class CallEvidenceContractTests(unittest.TestCase):
    def test_tree_sitter_is_never_an_approved_semantic_provider(self) -> None:
        self.assertIn("clang_worker", call_evidence.SEMANTIC_PROVIDERS)
        self.assertNotIn("tree_sitter", call_evidence.SEMANTIC_PROVIDERS)

    def test_strong_evidence_requires_provider_and_identity(self) -> None:
        self.assertTrue(call_evidence.is_strong_call_evidence(_strong_row()["props"]))
        for missing in ("semantic_provider", "tu_key", "config_fingerprint", "callee_usr"):
            props = dict(_strong_row()["props"])
            props.pop(missing)
            self.assertFalse(call_evidence.is_strong_call_evidence(props), missing)
        weak = dict(_strong_row()["props"])
        weak["semantic_provider"] = "tree_sitter"
        self.assertFalse(call_evidence.is_strong_call_evidence(weak))
        lexical = dict(_strong_row()["props"])
        lexical["resolution_class"] = "lexical_candidate"
        self.assertFalse(call_evidence.is_strong_call_evidence(lexical))

    def test_enforce_strong_call_row_fails_closed_and_ignores_foreign_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved semantic provider"):
            call_evidence.enforce_strong_call_row(
                _strong_row(resolution_class="direct_resolved", semantic_provider="tree_sitter")
            )
        # Rows outside the contract (other language analyzers) pass through.
        call_evidence.enforce_strong_call_row({"caller_id": "a", "callee_id": "b"})
        # Weak classified rows are legal weak evidence.
        call_evidence.enforce_strong_call_row(
            _strong_row(resolution_class="lexical_candidate")
        )
        with self.assertRaises(ValueError):
            call_evidence.enforce_strong_call_row(_strong_row(resolution_class="bogus"))

    def test_legacy_call_rows_migrate_to_lexical_candidate(self) -> None:
        row = call_evidence.normalize_call_row({"caller_id": "a", "callee_name": "f"})
        self.assertEqual(row["resolution_class"], "lexical_candidate")
        self.assertEqual(row["semantic_provider"], "tree_sitter")

    def test_callsite_identity_excludes_run_provenance(self) -> None:
        first = call_evidence.callsite_site_id("c", "t", "f.c", 3, 5, "call_expression")
        second = call_evidence.callsite_site_id("c", "t", "f.c", 3, 5, "call_expression")
        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            call_evidence.callsite_site_id("c", "t", "f.c", 4, 5, "call_expression"),
        )

    def test_coverage_records_and_completeness(self) -> None:
        record = call_evidence.SemanticCoverageRecord(
            project_id="p", revision="r", language="cplus", tu_key="tu",
            config_fingerprint="cfg", analyzer_version="1", policy_version="1",
            status="partial",
        )
        self.assertFalse(call_evidence.coverage_is_complete([record.to_dict()]))
        complete = call_evidence.SemanticCoverageRecord(
            project_id="p", revision="r", language="cplus", tu_key="tu",
            config_fingerprint="cfg", analyzer_version="1", policy_version="1",
            status="complete",
        )
        self.assertTrue(
            call_evidence.coverage_is_complete([complete.to_dict(), complete.to_dict()])
        )
        self.assertFalse(call_evidence.coverage_is_complete([]))
        with self.assertRaises(ValueError):
            call_evidence.SemanticCoverageRecord(
                project_id="p", revision="r", language="cplus", tu_key="tu",
                config_fingerprint="cfg", analyzer_version="1", policy_version="1",
                status="mostly-fine",
            )

    def test_proc_source_bundle_identity_and_eligibility(self) -> None:
        bundle = call_evidence.ProcSourceBundle(
            original_path="src/proc.pc",
            original_sha256="a" * 64,
            masked_sha256="b" * 64,
            source_map_quality="exact",
            compile_context_fingerprint="ctx-1",
            generated_artifacts=(
                call_evidence.GeneratedArtifactRef(
                    artifact_path="gen/proc.c",
                    sha256="c" * 64,
                    generated_code_class="original_application",
                ),
            ),
        )
        self.assertTrue(bundle.semantic_eligible)
        self.assertEqual(len(bundle.fingerprint), 64)
        weak = call_evidence.ProcSourceBundle(
            original_path="src/proc.pc",
            original_sha256="a" * 64,
            masked_sha256="b" * 64,
        )
        self.assertFalse(weak.semantic_eligible)
        # Identity is content-bound: changing the mask changes the fingerprint.
        remasked = call_evidence.ProcSourceBundle(
            original_path="src/proc.pc",
            original_sha256="a" * 64,
            masked_sha256="d" * 64,
        )
        self.assertNotEqual(weak.fingerprint, remasked.fingerprint)

    def test_generated_artifact_refs_are_repository_contained(self) -> None:
        with self.assertRaises(ValueError):
            call_evidence.GeneratedArtifactRef(
                artifact_path="/abs/proc.c", sha256="c" * 64
            )
        with self.assertRaises(ValueError):
            call_evidence.GeneratedArtifactRef(
                artifact_path="../escape/proc.c", sha256="c" * 64
            )


class JournalPossibleCallsContractTests(unittest.TestCase):
    def test_possible_calls_site_has_a_dedicated_replay_contract(self) -> None:
        from tools.graph.journal.executor import compile_persisted_mutation
        from tools.graph.journal.operation import GraphWriteOperation

        operation = GraphWriteOperation.for_label("possible_calls:site")
        self.assertEqual(operation.reconciliation, "possible_call_site")
        query, params = compile_persisted_mutation(
            operation,
            [{"caller_id": "a", "callee_id": "b", "site_id": "s", "props": {}}],
        )
        self.assertIn("POSSIBLE_CALLS", query)
        self.assertNotIn("MERGE (caller)-[edge:CALLS", query)
        strict = GraphWriteOperation.for_label("calls:site")
        self.assertEqual(strict.reconciliation, "call_site")


class PayloadValidationContractTests(unittest.TestCase):
    def _payload(self, calls):
        return {
            "file_def": {"file_path": "a.c", "start_line": 1, "end_line": 2},
            "functions": [
                {
                    "symbol_id": "fn-caller", "name": "caller", "qualified_name": "caller",
                    "kind": "function", "scope_name": "", "file_path": "a.c",
                    "start_line": 1, "end_line": 2, "arity": 0, "code": "",
                    "comment": "", "summary": "", "note": "",
                },
                {
                    "symbol_id": "fn-target", "name": "target", "qualified_name": "target",
                    "kind": "function", "scope_name": "", "file_path": "a.c",
                    "start_line": 1, "end_line": 2, "arity": 0, "code": "",
                    "comment": "", "summary": "", "note": "",
                },
            ],
            "calls": calls,
        }

    def test_legacy_calls_are_migrated_to_weak_evidence(self) -> None:
        validated, quarantine = payload_validation.validate_cplus_payload(
            self._payload([{"caller_id": "fn-caller", "callee_id": "fn-target", "callee_name": "target"}]),
            project_id="p",
        )
        self.assertEqual(len(validated["calls"]), 1)
        self.assertEqual(validated["calls"][0]["resolution_class"], "lexical_candidate")
        self.assertEqual(validated["calls"][0]["semantic_provider"], "tree_sitter")
        self.assertFalse(quarantine)

    def test_unclaimed_direct_resolution_is_demoted_not_published(self) -> None:
        call = {
            "caller_id": "fn-caller",
            "callee_id": "fn-target-was-never-accepted",
            "callee_name": "target",
            "resolution_class": "direct_resolved",
            "semantic_provider": "clang_worker",
        }
        validated, quarantine = payload_validation.validate_cplus_payload(
            self._payload([call]), project_id="p",
        )
        row = validated["calls"][0]
        self.assertEqual(row["resolution_class"], "lexical_candidate")
        self.assertEqual(row["semantic_provider"], "tree_sitter")
        self.assertEqual(row["demoted_from"], "direct_resolved")

    def test_incomplete_strong_claim_is_demoted_even_with_accepted_callee(self) -> None:
        call = {
            "caller_id": "fn-caller",
            "callee_id": "fn-target",
            "callee_name": "target",
            "resolution_class": "direct_resolved",
            "semantic_provider": "clang_worker",
            # missing tu_key/config_fingerprint/callee_usr
        }
        validated, _ = payload_validation.validate_cplus_payload(
            self._payload([call]), project_id="p",
        )
        row = validated["calls"][0]
        self.assertEqual(row["resolution_class"], "lexical_candidate")
        self.assertEqual(row["demoted_from"], "direct_resolved")

    def test_unknown_resolution_class_is_quarantined(self) -> None:
        call = {
            "caller_id": "fn-caller",
            "callee_id": "fn-target",
            "resolution_class": "definitely_resolved",
        }
        validated, quarantine = payload_validation.validate_cplus_payload(
            self._payload([call]), project_id="p",
        )
        self.assertEqual(validated["calls"], [])
        self.assertEqual(quarantine[0].reason, payload_validation.QuarantineReason.INVALID_RECORD)

    def test_proc_nodes_require_a_recognized_concrete_label(self) -> None:
        def proc_node(label):
            return {
                "symbol_id": f"sql-{label}", "name": "sel", "qualified_name": "sel",
                "kind": "statement", "file_path": "a.pc", "start_line": 1,
                "end_line": 1, "code": "", "comment": "", "summary": "", "note": "",
                "label": label,
            }

        payload = {
            "file_def": {"file_path": "a.pc", "start_line": 1, "end_line": 2},
            "proc_nodes": [proc_node("SqlCursor"), proc_node("ProcStatement")],
        }
        validated, quarantine = payload_validation.validate_cplus_payload(payload, project_id="p")
        labels = {row["label"] for row in validated["proc_nodes"]}
        self.assertEqual(labels, {"SqlCursor"})
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].reason, payload_validation.QuarantineReason.INVALID_RECORD)


class WriterGuardTests(unittest.TestCase):
    def test_write_calls_with_site_rejects_unsupported_strong_claims(self) -> None:
        writer = LanguageCodeWriter(_FakeDriver())
        with self.assertRaisesRegex(ValueError, "approved semantic provider"):
            asyncio.run(
                writer.write_calls_with_site([
                    _strong_row(resolution_class="direct_resolved", semantic_provider="tree_sitter")
                ])
            )
        # Fully evidenced rows and contract-less rows write normally.
        driver = _FakeDriver()
        writer = LanguageCodeWriter(driver)
        asyncio.run(writer.write_calls_with_site([_strong_row()]))
        asyncio.run(writer.write_calls_with_site([{"caller_id": "a", "callee_id": "b", "site_id": "s", "props": {}}]))
        self.assertEqual(len(driver.queries), 2)


def _run_corpus_build(root: str, cache: str):
    class _Writer:
        def __init__(self) -> None:
            self.driver = _FakeDriver()
            self.database = "code"
            self.batch_size = 100
            self.calls = []
            self.node_writes = []
            self.relations = []
            self.file_ids = set()

        async def write_nodes_batch(self, key, cypher, rows, state=None, state_writer=None):
            self.node_writes.append((key, cypher, list(rows)))
            return len(rows)

        async def write_all(self, **kwargs):
            self.relations.extend(kwargs.get("relations") or [])
            return {}

        async def write_calls_with_site(self, calls):
            self.calls.extend(calls)
            return len(calls)

    writer = _Writer()
    asyncio.run(
        cplus_analyzer.build_call_graph(
            root=root,
            code_writer=writer,
            qdrant_writer=None,
            embedder=None,
            batch_size=16,
            qdrant_batch_size=16,
            cache_dir=cache,
            keep_cache=False,
            parse_cache=False,
            neo4j_batch_size=16,
            neo4j_calls_batch_size=16,
            neo4j_state_path=None,
            project_id="corpus",
            project_name="Corpus",
            language="cplus",
            repo=root,
            build_system="",
            event_map_path=None,
            call_stats_path=None,
            possible_calls_path=None,
            unresolved_calls_path=None,
            parse_errors_path=None,
            parse_run_id="corpus-run",
            commit_sha="deadbeef",
            verbose=False,
        )
    )
    return writer


class CorpusContainmentTests(unittest.TestCase):
    """Run the reviewed corpus and prove containment plus baseline stability."""

    corpus_dir = ROOT / "tests" / "fixtures" / "cplus_semantic_calls"
    baseline_path = corpus_dir / "baseline.json"

    def test_corpus_contains_no_strict_calls_and_stable_weak_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            for source in self.corpus_dir.glob("*.[cp]*"):
                if source.suffix in {".c", ".cpp", ".pc", ".pcc", ".h"}:
                    (Path(root) / source.name).write_bytes(source.read_bytes())
            writer = _run_corpus_build(root, cache)

        strict_call_queries = [
            query for query, _, _ in writer.driver.queries
            if "CALLS" in query and "POSSIBLE_CALLS" not in query and "UNKNOWN_CALL" not in query
        ]
        self.assertFalse(strict_call_queries, "Tree-sitter evidence must never publish CALLS")
        self.assertFalse(writer.calls)

        possible = [
            row for query, params, _ in writer.driver.queries
            if "POSSIBLE_CALLS" in query
            for row in params.get("rows", [])
        ]
        unknown = [
            row for query, params, _ in writer.driver.queries
            if "UNKNOWN_CALL" in query
            for row in params.get("rows", [])
        ]
        self.assertTrue(possible)
        self.assertTrue(
            all(row["props"]["resolution_class"] == "lexical_candidate" for row in possible)
        )
        self.assertTrue(
            all(row["props"]["semantic_provider"] == "tree_sitter" for row in possible)
        )
        self.assertTrue(unknown)
        self.assertTrue(
            all(row["props"]["resolution_class"] == "unresolved" for row in unknown)
        )

        baseline = {
            "schema_version": call_evidence.CALL_EVIDENCE_SCHEMA_VERSION,
            "possible_calls": len(possible),
            "unknown_calls": len(unknown),
            "possible_by_file": _count_by_file(possible),
            "unknown_by_file": _count_by_file(unknown),
        }
        self.assertTrue(
            self.baseline_path.exists(),
            "baseline.json must be committed; refusing to self-generate a baseline",
        )
        committed = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(
            committed, baseline,
            "call-edge class counts changed; review and update baseline.json deliberately",
        )

        # Pro*C SQL facts survive the call-edge downgrade unchanged.
        expected = json.loads(
            (self.corpus_dir / "expected.json").read_text(encoding="utf-8")
        )
        proc_expectation = expected["files"]["proc.pc"]["sql_facts_must_survive"]
        written_keys = {key for key, _, _ in writer.node_writes}
        for label in proc_expectation["labels"]:
            self.assertIn(label, written_keys, f"missing Pro*C label {label}")
        relation_types = {relation.get("rel_type") for relation in writer.relations}
        for rel_type in proc_expectation["relations"]:
            self.assertIn(rel_type, relation_types, f"missing Pro*C relation {rel_type}")


def _count_by_file(rows):
    counts: dict[str, int] = {}
    for row in rows:
        path = row["props"]["file_path"]
        counts[path] = counts.get(path, 0) + 1
    return counts


if __name__ == "__main__":
    unittest.main()
