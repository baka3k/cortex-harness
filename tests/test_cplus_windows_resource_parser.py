import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.cplus_analyzer import (  # noqa: E402
    _collect_include_graph,
    _load_or_parse_payload,
    _scan_c_family_files,
)
from tools.cplus.windows_resource_parser import (  # noqa: E402
    is_windows_resource_file,
    parse_windows_resource_file,
)
from tools.sync.incremental_sync import _walk_all_source_files  # noqa: E402
from tools.sync.owner_manifest import build_owner_maps  # noqa: E402


RESOURCE_SCRIPT = '''// Microsoft Visual C++ generated resource script.
#include "resource.h"
#include "res\\Sample.rc2"
LANGUAGE LANG_JAPANESE, SUBLANG_DEFAULT

IDR_MAINFRAME ICON "res\\sample.ico"

IDD_SAMPLE DIALOGEX 0, 0, 200, 80
STYLE DS_SETFONT | WS_POPUP
CAPTION "オプション編集"
BEGIN
    CONTROL         "汎用Shape取込",IDC_CHECK_SHPSIM,"Button",BS_AUTOCHECKBOX,10,10,80,10
    EDITTEXT        IDC_EDIT_SHPSIM,10,25,120,14,ES_AUTOHSCROLL
    DEFPUSHBUTTON   "OK",IDOK,140,60,50,14
END

VS_VERSION_INFO VERSIONINFO
 FILEVERSION 1,0,0,0
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "041104b0"
        BEGIN
            VALUE "FileDescription", "外注拠点転送用データ集約AP起動画面"
            VALUE "ProductName", "時空間情報管理システム"
        END
    END
END

STRINGTABLE
BEGIN
    IDS_ABOUTBOX "バージョン情報 Sample(&A)..."
END
'''

RESOURCE_FRAGMENT = '''// Sample.RC2 - Microsoft Visual C++ で直接編集しないリソース
// 手動で編集されたりソースをここに追加します...
#ifdef APSTUDIO_INVOKED
#error このファイルは、Microsoft Visual C++ で編集できません。
#endif
'''


class CplusWindowsResourceParserTest(unittest.TestCase):
    def test_utf16_rc_extracts_resource_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Sample.rc"
            path.write_bytes(RESOURCE_SCRIPT.encode("utf-16"))

            payload = parse_windows_resource_file(str(path), str(root))

        self.assertEqual(payload["parse_meta"]["encoding"], "utf-16-le")
        self.assertEqual(payload["parse_meta"]["parser_language"], "windows-resource")
        self.assertIn("resource.h", payload["includes"])
        self.assertIn("res\\Sample.rc2", payload["includes"])
        kinds = {item["kind"] for item in payload["types"]}
        self.assertIn("windows_resource_icon", kinds)
        self.assertIn("windows_resource_dialogex", kinds)
        self.assertIn("windows_resource_versioninfo", kinds)
        self.assertIn("windows_resource_stringtable", kinds)
        field_names = {item["name"] for item in payload["fields"]}
        self.assertTrue(
            {"IDC_CHECK_SHPSIM", "IDC_EDIT_SHPSIM", "IDOK", "FileDescription", "ProductName", "IDS_ABOUTBOX"}
            <= field_names
        )
        dialog = next(item for item in payload["types"] if item["name"] == "IDD_SAMPLE")
        self.assertIn("オプション編集", dialog["summary"])
        self.assertNotIn("\x00", payload["file_def"]["code"])

    def test_rc2_without_blocks_is_indexed_as_manual_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Sample.rc2"
            path.write_bytes(RESOURCE_FRAGMENT.encode("utf-16"))

            payload = parse_windows_resource_file(str(path), str(root))

        self.assertTrue(payload["parse_meta"]["manual_resource_file"])
        self.assertEqual(payload["types"][0]["kind"], "windows_resource_fragment")
        self.assertIn("手動で編集", payload["types"][0]["summary"])

    def test_cplus_scanner_and_payload_router_accept_rc_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rc_path = root / "Sample.RC"
            rc2_path = root / "res" / "Sample.rc2"
            cpp_path = root / "main.cpp"
            rc2_path.parent.mkdir()
            rc_path.write_bytes(RESOURCE_SCRIPT.encode("utf-16"))
            rc2_path.write_bytes(RESOURCE_FRAGMENT.encode("utf-16"))
            cpp_path.write_text("int main() { return 0; }\n", encoding="utf-8")

            scanned_paths = _scan_c_family_files(str(root))
            scanned = {Path(path).name for path in scanned_paths}
            include_graph = _collect_include_graph(scanned_paths, str(root))
            walked = _walk_all_source_files(str(root))
            owners = build_owner_maps(root=str(root), parsers=["cplus"])
            payload = _load_or_parse_payload(
                str(rc_path), str(root), str(root / ".cache"), False
            )

        self.assertTrue(is_windows_resource_file(str(rc_path)))
        self.assertEqual(scanned, {"Sample.RC", "Sample.rc2", "main.cpp"})
        self.assertEqual(include_graph["Sample.RC"], ["res/Sample.rc2"])
        self.assertTrue({"Sample.RC", "res/Sample.rc2"} <= walked)
        self.assertTrue(
            {"Sample.RC", "res/Sample.rc2"} <= owners.owned_by_parser["cplus"]
        )
        self.assertEqual(payload["parse_meta"]["parser_language"], "windows-resource")
        self.assertEqual(payload["functions"], [])


if __name__ == "__main__":
    unittest.main()
