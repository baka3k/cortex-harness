import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
MODULE_PATH = DOC_TINY / "graphrag_ingest_langextract.py"

if str(DOC_TINY) not in sys.path:
    sys.path.insert(0, str(DOC_TINY))

SPEC = importlib.util.spec_from_file_location("graphrag_ingest_langextract", MODULE_PATH)
assert SPEC and SPEC.loader
INGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGEST)


class InputFileDiscoveryTests(unittest.TestCase):
    def test_skips_microsoft_office_owner_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            document = folder / "specification.docx"
            owner_file = folder / "~$specification.docx"
            document.touch()
            owner_file.touch()

            self.assertEqual(INGEST._iter_input_files(folder), [document])


if __name__ == "__main__":
    unittest.main()
