from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.jp1.sniff import is_jp1_file


def test_sniff_accepts_jobnet_and_rejects_plain_text(tmp_path: Path) -> None:
    jobnet = tmp_path / "jobnet.txt"
    jobnet.write_text("unit=ROOT,,host,;\n{\nty=n;\n}\n", encoding="utf-8")
    plain = tmp_path / "notes.txt"
    plain.write_text("unit=measurement\nplain notes\n", encoding="utf-8")

    assert is_jp1_file(str(jobnet)) is True
    assert is_jp1_file(str(plain)) is False