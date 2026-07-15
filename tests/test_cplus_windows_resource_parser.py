from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer
from tools.cplus.rc_parser import (
    extract_message_map_handlers,
    extract_resource_tokens,
    parse_rc_file,
)
from tools.sync import incremental_sync
from tools.sync.owner_manifest import build_owner_maps


RC_FIXTURE = """// Visual Studio resource fixture
#include "resource.h"
#if !defined(AFX_RESOURCE_DLL) || defined(AFX_TARG_JPN)
LANGUAGE LANG_JAPANESE, SUBLANG_DEFAULT

IDR_MAINFRAME ICON "res\\app.ico"

IDD_OPTIONS DIALOGEX 0, 0, 220, 90
STYLE DS_SETFONT | WS_POPUP | WS_CAPTION
CAPTION "外注転送オプション"
FONT 9, "MS UI Gothic"
BEGIN
    CONTROL "地物エクスポート",IDC_CHECK_EXPORT,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,10,10,90,10
    LTEXT "出力フォルダ",IDC_STATIC,20,28,60,8
    EDITTEXT IDC_EDIT_EXPORT,90,25,90,14,ES_AUTOHSCROLL
    PUSHBUTTON "...",IDC_BUTTON_EXPORT,185,25,25,14
END

VS_VERSION_INFO VERSIONINFO
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        VALUE "FileDescription", "外注転送アプリ"
        VALUE "ProductName", "時空間情報管理システム"
    END
END

STRINGTABLE
BEGIN
    IDS_ABOUTBOX "バージョン情報(&A)..."
END
#endif
"""


class _FakeDriver:
    async def execute_query(self, query, parameters=None, database=None, **kwargs):
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], None, None)


class _FakeCodeWriter:
    def __init__(self) -> None:
        self.driver = _FakeDriver()
        self.database = "neo4j"
        self.batch_size = 100
        self.node_batches = {}
        self.relations = []

    async def write_nodes_batch(self, key, cypher, rows, state=None, state_writer=None):
        self.node_batches.setdefault(key, []).extend(rows)
        return len(rows)

    async def write_all(self, **kwargs):
        self.relations.extend(kwargs.get("relations") or [])
        return {}

    async def write_calls_with_site(self, calls):
        return len(calls)


class _FakeQdrantWriter:
    def __init__(self) -> None:
        self.points = []
        self.collection = "test_cplus_resources"

    def ensure_collection(self):
        return None

    def upsert(self, points):
        self.points.extend(points)


class _FakeEmbedder:
    def embed(self, texts, batch_size=16, verbose=False):
        return [[float(len(text)), 1.0] for text in texts]


class CPlusRcParserTest(unittest.TestCase):
    def test_utf16_resource_script_extracts_dialog_controls_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "Demo.rc")
            path.write_bytes(b"\xff\xfe" + RC_FIXTURE.encode("utf-16-le"))

            payload = parse_rc_file(str(path), root)

        self.assertEqual(payload["parse_meta"]["encoding"], "utf-16-le")
        self.assertFalse(payload["parse_meta"]["lossy_decode"])
        self.assertEqual(payload["includes"], ["resource.h"])
        resources = {item["resource_symbol"]: item for item in payload["resources"]}
        self.assertIn("IDD_OPTIONS", resources)
        self.assertEqual(resources["IDD_OPTIONS"]["caption"], "外注転送オプション")
        self.assertIn("地物エクスポート", resources["IDD_OPTIONS"]["note"])
        self.assertEqual(resources["VS_VERSION_INFO"]["summary"], "外注転送アプリ")
        string_table = next(item for item in payload["resources"] if item["kind"] == "stringtable")
        self.assertIn("バージョン情報", string_table["summary"])
        self.assertEqual(resources["IDS_ABOUTBOX"]["kind"], "string")

        controls = {item["resource_symbol"]: item for item in payload["resource_elements"]}
        self.assertEqual(controls["IDC_CHECK_EXPORT"]["control_type"], "control")
        self.assertEqual(controls["IDC_EDIT_EXPORT"]["x"], 90)
        self.assertEqual(controls["IDC_BUTTON_EXPORT"]["text"], "...")
        self.assertEqual(len(payload["relations"]), 5)

    def test_rc2_without_resources_is_valid(self) -> None:
        content = "// manually edited resources\r\n#ifdef APSTUDIO_INVOKED\r\n#error unsupported\r\n#endif\r\n"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "Demo.rc2")
            path.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))
            payload = parse_rc_file(str(path), root)

        self.assertEqual(payload["resources"], [])
        self.assertEqual(payload["resource_elements"], [])
        self.assertFalse(payload["parse_meta"]["has_error"])

    def test_cplus_dispatch_and_scanner_include_rc_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            rc_path = Path(root, "Demo.RC")
            rc_path.write_bytes(b"\xff\xfe" + RC_FIXTURE.encode("utf-16-le"))
            rc2_path = Path(root, "Extra.rc2")
            rc2_path.write_text("// empty", encoding="utf-8")

            scanned = cplus_analyzer._scan_c_family_files(root)
            payload = cplus_analyzer._load_or_parse_payload(
                str(rc_path), root, str(Path(root, ".cache")), False
            )

        self.assertEqual({Path(item).name for item in scanned}, {"Demo.RC", "Extra.rc2"})
        self.assertEqual(payload["parse_meta"]["parser_language"], "windows_rc")
        self.assertGreater(len(payload["resources"]), 0)

    def test_resource_reference_and_message_map_evidence_is_conservative(self) -> None:
        symbols = {"IDC_BUTTON_EXPORT", "IDD_OPTIONS"}
        function_code = "void COptions::DoDataExchange() { DDX_Text(pDX, IDC_BUTTON_EXPORT, value); }"
        self.assertEqual(extract_resource_tokens(function_code, symbols), ["IDC_BUTTON_EXPORT"])

        message_map = """
        BEGIN_MESSAGE_MAP(COptions, CDialogEx)
            ON_BN_CLICKED(IDC_BUTTON_EXPORT, &COptions::OnBrowseExport)
        END_MESSAGE_MAP()
        """
        handlers = extract_message_map_handlers(message_map, symbols)
        self.assertEqual(
            handlers,
            [{
                "macro": "ON_BN_CLICKED",
                "resource_symbol": "IDC_BUTTON_EXPORT",
                "handler": "COptions::OnBrowseExport",
                "line": 3,
            }],
        )

    def test_incremental_and_owner_routing_assign_rc_to_cplus(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            Path(root, "Demo.rc").write_text("STRINGTABLE\nBEGIN\nEND\n", encoding="utf-8")
            Path(root, "Extra.rc2").write_text("// empty", encoding="utf-8")
            grouped = incremental_sync._group_paths_by_parser(
                {"Demo.rc", "Extra.rc2"}, root=root
            )
            owners = build_owner_maps(root=root, parsers=["cplus"])

        self.assertEqual(grouped["cplus"], {"Demo.rc", "Extra.rc2"})
        self.assertEqual(owners.owned_by_parser["cplus"], {"Demo.rc", "Extra.rc2"})

    def test_build_pipeline_writes_resources_and_explicit_handler_edges(self) -> None:
        cpp = """
        #include "resource.h"
        class COptions {
        public:
            void OnBrowseExport();
            void DoDataExchange() { DDX_Text(pDX, IDC_EDIT_EXPORT, value); }
        };
        void COptions::OnBrowseExport() {}
        BEGIN_MESSAGE_MAP(COptions, CDialogEx)
            ON_BN_CLICKED(IDC_BUTTON_EXPORT, &COptions::OnBrowseExport)
        END_MESSAGE_MAP()
        """
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            Path(root, "Demo.rc").write_bytes(b"\xff\xfe" + RC_FIXTURE.encode("utf-16-le"))
            Path(root, "resource.h").write_text(
                "#define IDD_OPTIONS 100\n"
                "#define IDC_CHECK_EXPORT 1001\n"
                "#define IDC_EDIT_EXPORT 1002\n"
                "#define IDC_BUTTON_EXPORT 1003\n",
                encoding="utf-8",
            )
            Path(root, "Options.cpp").write_text(cpp, encoding="utf-8")
            writer = _FakeCodeWriter()

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
                    project_id="demo",
                    project_name="Demo",
                    language="cplus",
                    repo=root,
                    build_system="",
                    event_map_path=None,
                    call_stats_path=None,
                    possible_calls_path=None,
                    unresolved_calls_path=None,
                    parse_errors_path=None,
                    parse_run_id="demo-run",
                    commit_sha="",
                    verbose=False,
                )
            )
            qdrant = _FakeQdrantWriter()
            asyncio.run(
                cplus_analyzer.build_call_graph(
                    root=root,
                    code_writer=None,
                    qdrant_writer=qdrant,
                    embedder=_FakeEmbedder(),
                    batch_size=16,
                    qdrant_batch_size=16,
                    cache_dir=str(Path(cache, "qdrant-run")),
                    keep_cache=False,
                    parse_cache=False,
                    neo4j_batch_size=16,
                    neo4j_calls_batch_size=16,
                    neo4j_state_path=None,
                    project_id="demo",
                    project_name="Demo",
                    language="cplus",
                    repo=root,
                    build_system="",
                    event_map_path=None,
                    call_stats_path=None,
                    possible_calls_path=None,
                    unresolved_calls_path=None,
                    parse_errors_path=None,
                    parse_run_id="demo-vector-run",
                    commit_sha="",
                    verbose=False,
                )
            )

        self.assertGreater(len(writer.node_batches["resources"]), 0)
        controls = writer.node_batches["resource_elements"]
        edit_control = next(row for row in controls if row["resource_symbol"] == "IDC_EDIT_EXPORT")
        self.assertEqual(edit_control["numeric_id"], 1002)
        relation_types = {relation["rel_type"] for relation in writer.relations}
        self.assertIn("BINDS_CONTROL", relation_types)
        self.assertIn("HANDLES_CONTROL", relation_types)
        resource_points = [
            point for point in qdrant.points if point["payload"].get("node_type") == "resource"
        ]
        self.assertGreater(len(resource_points), 0)
        self.assertTrue(any("外注転送オプション" in point["payload"]["note"] for point in resource_points))


if __name__ == "__main__":
    unittest.main()
