import asyncio
import contextlib
import io
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.proc_analyzer import (
    prepare_proc_bytes,
    summarize_proc_root,
)
from tools.cplus.cplus_analyzer import (
    _load_or_parse_payload,
    _scan_c_family_files,
    build_call_graph,
)


class _FakeEmbedder:
    model = None
    model_name = "test-model"
    model_source = "test-model"
    model_content_fingerprint = "test-fingerprint"
    vector_size = 1
    max_embed_chars = None
    chunk_embed = False
    tokenizer = None

    def embed(self, texts, batch_size=8, verbose=False):
        return [[0.0] for _ in texts]


class _FakeQdrantWriter:
    collection = "test_cplus_vectors"

    def __init__(self) -> None:
        self.points = []

    def ensure_collection(self) -> None:
        return None

    def upsert(self, points) -> None:
        self.points.extend(points)


def test_extracts_proc_sql_facts() -> None:
    source = """int load_customer(void) {
    EXEC SQL SELECT NAME INTO :customer_name FROM CUSTOMER
        WHERE ID = :customer_id;
    EXEC SQL COMMIT;
}
"""
    prepared = prepare_proc_bytes(source.encode("utf-8"))
    assert [region.operation_upper for region in prepared.regions] == ["SELECT", "COMMIT"]
    assert prepared.regions[0].targets == ("CUSTOMER",)
    assert set(prepared.regions[0].host_variables) == {"customer_name", "customer_id"}
    assert prepared.regions[0].start_line == 2


def test_proc_file_is_scanned_and_emits_graph_facts(tmp_path: Path) -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "procc-application"

    files = _scan_c_family_files(str(fixture_root))
    payload = _load_or_parse_payload(
        files[0],
        str(fixture_root),
        str(tmp_path),
        False,
        project_id="test-proc",
    )

    assert files[0].endswith(".pc")
    proc_nodes = payload["proc_nodes"]
    operations = [node["operation"] for node in proc_nodes if node.get("label") == "SqlStatement"]
    assert operations == ["SELECT", "COMMIT"]
    relation_types = {relation["rel_type"] for relation in payload["relations"]}
    assert "DECLARES_STATEMENT" in relation_types
    assert "DEFINES" not in relation_types
    assert any(
        relation["source_label"] == "Function" and relation["target_label"] == "SqlStatement"
        for relation in payload["relations"]
    )


def test_embedding_progress_total_includes_proc_nodes(tmp_path: Path) -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "procc-application"
    writer = _FakeQdrantWriter()
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        asyncio.run(
            build_call_graph(
                root=str(fixture_root),
                code_writer=None,
                qdrant_writer=writer,
                embedder=_FakeEmbedder(),
                batch_size=1,
                qdrant_batch_size=16,
                cache_dir=str(tmp_path),
                keep_cache=False,
                parse_cache=True,
                neo4j_batch_size=16,
                neo4j_calls_batch_size=16,
                neo4j_state_path=None,
                project_id="test-proc",
                project_name="Test Pro*C",
                language="cplus",
                repo=str(fixture_root),
                build_system="",
                event_map_path=None,
                call_stats_path=None,
                possible_calls_path=None,
                unresolved_calls_path=None,
                parse_errors_path=None,
                parse_run_id="test-run",
                commit_sha="abc123",
                verbose=True,
            )
        )

    assert len(writer.points) == 6
    assert "[embed] batch 6 / 6" in stdout.getvalue()


def test_prepare_proc_bytes_handles_utf8_and_cp932() -> None:
    text = "EXEC SQL SELECT * FROM T;\n"
    utf8 = text.encode("utf-8")
    prepared = prepare_proc_bytes(utf8)
    assert prepared.encoding == "utf-8"
    assert prepared.source_bytes == utf8

    cp932 = text.encode("cp932")
    prepared2 = prepare_proc_bytes(cp932)
    assert prepared2.encoding in {"utf-8", "cp932"}
    assert len(prepared2.source_bytes) == len(cp932)


def test_summarize_proc_root_counts() -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "procc-application"
    summary = summarize_proc_root(str(fixture_root))
    assert summary["files"] >= 1
    assert summary["statements"] >= 1
    assert "CUSTOMER" in summary["tables"]
