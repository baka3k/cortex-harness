from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.proc_sql import extract_exec_sql_statements
from tools.cplus.cplus_analyzer import _load_or_parse_payload, _scan_c_family_files


def test_extracts_proc_sql_facts() -> None:
    source = """int load_customer(void) {
    EXEC SQL SELECT NAME INTO :customer_name FROM CUSTOMER
        WHERE ID = :customer_id;
    EXEC SQL COMMIT;
}
"""

    statements = extract_exec_sql_statements(source)

    assert [statement.operation for statement in statements] == ["SELECT", "COMMIT"]
    assert statements[0].targets == ("CUSTOMER",)
    assert statements[0].host_variables == ("CUSTOMER_NAME", "CUSTOMER_ID")
    assert statements[0].start_line == 2


def test_proc_file_is_scanned_and_emits_graph_facts(tmp_path: Path) -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "procc-application"

    files = _scan_c_family_files(str(fixture_root))
    payload = _load_or_parse_payload(files[0], str(fixture_root), str(tmp_path), False)

    assert files[0].endswith(".pc")
    assert [item["operation"] for item in payload["proc_sql_statements"]] == ["SELECT", "COMMIT"]
    assert any(
        relation["rel_type"] == "DEFINES"
        and relation["source_label"] == "Function"
        and relation["target_label"] == "CplusSqlStatement"
        for relation in payload["relations"]
    )