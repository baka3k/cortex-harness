from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_graph_ingest", ROOT / "scripts" / "audit_graph_ingest.py"
)
assert SPEC and SPEC.loader
audit_graph_ingest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_graph_ingest)

from tools.graph.journal import (  # noqa: E402
    BatchSpec,
    OperationPhase,
    RunMetadata,
    RunStatus,
    SQLiteJournal,
)


def _journal(**overrides):
    value = {
        "status": "drained",
        "endpoint_audit": "sealed",
        "endpoint_audit_evidence": {"valid": True},
        "conservation": {
            "conserved": True,
            "node": {"conflict": 0, "rejected": 0},
            "edge": {"conflict": 0, "rejected": 0},
            "producers": {"open": 0},
        },
    }
    value.update(overrides)
    return value


def test_journal_requires_seal_and_conservation() -> None:
    assert audit_graph_ingest._journal_is_valid(_journal())
    assert not audit_graph_ingest._journal_is_valid(
        _journal(endpoint_audit=None)
    )
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation={"conserved": False})
    )


def test_journal_rejects_conflicts_and_open_producers() -> None:
    conservation = {
        "conserved": True,
        "node": {"conflict": 1, "rejected": 0},
        "edge": {"conflict": 0, "rejected": 0},
        "producers": {"open": 0},
    }
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation=conservation)
    )
    conservation["node"]["conflict"] = 0
    conservation["producers"]["open"] = 1
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation=conservation)
    )


def test_loaded_audit_rejects_post_seal_endpoint_drift(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    metadata = RunMetadata(
        project_id="demo",
        scope_id="scope",
        source_revision="rev",
        source_snapshot="snapshot",
        physical_target="falkordb:test",
        generation="generation",
        parser="cplus",
        parser_version="1",
        schema_fingerprint="schema",
        query_shape_version="language-writer-node-first-v1",
        operation_versions={"node": 1, "edge": 1},
    )
    node_operation = {
        "label": "files",
        "phase": "nodes",
        "version": 1,
        "reconciliation": "node_identity",
        "node_label": "File",
        "identity_property": "id",
        "row_identity_property": "id",
        "mutation_kind": "merge",
    }
    edge_operation = {
        "label": "relations:includes",
        "phase": "relationships",
        "version": 1,
        "reconciliation": "typed_relationship",
    }
    edge_row = {
        "source_label": "File",
        "source_id": "a.c",
        "target_label": "File",
        "target_id": "b.h",
        "rel_type": "INCLUDES",
        "project_id_normalized": "demo",
        "properties": {},
    }
    with SQLiteJournal(path) as journal:
        run = journal.open_run(metadata)
        nodes = journal.create_artifact(
            run.run_id,
            [
                {"id": "a.c", "project_id_normalized": "demo"},
                {"id": "b.h", "project_id_normalized": "demo"},
            ],
        )
        node_batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.NODES,
                "files",
                0,
                nodes,
                2,
                operation=node_operation,
            ),
        )
        node_lease = journal.claim_job(node_batch.job_id)
        assert node_lease is not None
        journal.ack_batch(node_lease.job_id, node_lease.fencing_token or "")
        edge_artifact = journal.create_artifact(run.run_id, [edge_row])
        edge_batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.RELATIONSHIPS,
                "relations:includes",
                1,
                edge_artifact,
                1,
                operation=edge_operation,
            ),
        )
        journal.complete_producers(run.run_id)
        journal.seal_endpoint_audit(run.run_id, audited_rows=1)
        edge_lease = journal.claim_job(edge_batch.job_id)
        assert edge_lease is not None
        journal.ack_batch(edge_lease.job_id, edge_lease.fencing_token or "")
        assert journal.close_run_production(run.run_id).status is RunStatus.DRAINED

    loaded, _, missing = audit_graph_ingest._load_journal_evidence(
        path, run.run_id
    )
    assert not missing
    assert audit_graph_ingest._journal_is_valid(loaded[0])

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE edge_endpoint SET identity_json = '\"tampered\"' "
            "WHERE run_id = ? AND role = 'target'",
            (run.run_id,),
        )
        connection.commit()
    finally:
        connection.close()

    tampered, _, _ = audit_graph_ingest._load_journal_evidence(path, run.run_id)
    assert tampered[0]["endpoint_audit_evidence"]["valid"] is False
    assert not audit_graph_ingest._journal_is_valid(tampered[0])
