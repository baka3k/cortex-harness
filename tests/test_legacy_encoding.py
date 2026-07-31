from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.legacy_encoding import decode_legacy_bytes, read_legacy_text


def test_decodes_utf8_and_records_encoding(tmp_path: Path) -> None:
    path = tmp_path / "utf8_sample.txt"
    path.write_bytes("legacy source".encode("utf-8"))

    result = read_legacy_text(path)

    assert result.text == "legacy source"
    assert result.encoding == "utf-8"
    assert result.diagnostics[0].code == "legacy-encoding-selected"


def test_decodes_cp932() -> None:
    source = "/* Japanese comment: test */"
    data = source.replace("Japanese comment: test", "\u65e5\u672c\u8a9e\u30b3\u30e1\u30f3\u30c8").encode("cp932")

    result = decode_legacy_bytes(data)

    assert "\u65e5\u672c\u8a9e" in result.text
    assert result.encoding == "cp932"


def test_decodes_utf16_bom() -> None:
    data = b"\xff\xfe" + "legacy source".encode("utf-16-le")

    result = decode_legacy_bytes(data)

    assert result.text == "legacy source"
    assert result.encoding == "utf-16-le"