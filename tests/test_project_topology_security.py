import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.detector import parse_descriptor_file  # noqa: E402
from tools.project_topology.models import DiagnosticCode  # noqa: E402
from tools.project_topology.registry import descriptor_spec_for_path  # noqa: E402


def test_maven_doctype_is_rejected_without_entity_resolution(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        '<!DOCTYPE project [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<project><artifactId>&xxe;</artifactId></project>",
        encoding="utf-8",
    )
    result = parse_descriptor_file(
        root=tmp_path,
        project_id="secure",
        path="pom.xml",
        spec=descriptor_spec_for_path("pom.xml"),
    )
    assert result.diagnostics[0].code == DiagnosticCode.XML_UNSAFE
    assert "root:" not in result.descriptor.summary


def test_descriptor_symlinks_are_not_followed(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-pom.xml"
    outside.write_text("<project/>", encoding="utf-8")
    link = tmp_path / "pom.xml"
    link.symlink_to(outside)
    try:
        result = parse_descriptor_file(
            root=tmp_path,
            project_id="secure",
            path="pom.xml",
            spec=descriptor_spec_for_path("pom.xml"),
        )
        assert result.diagnostics[0].code == DiagnosticCode.MODULE_PATH_ESCAPE
    finally:
        outside.unlink()


def test_oversized_descriptor_is_rejected_before_read(tmp_path):
    pom = tmp_path / "pom.xml"
    spec = descriptor_spec_for_path("pom.xml")
    pom.write_bytes(b"x" * (spec.max_bytes + 1))
    result = parse_descriptor_file(
        root=tmp_path,
        project_id="secure",
        path="pom.xml",
        spec=spec,
    )
    assert result.diagnostics[0].code == DiagnosticCode.DESCRIPTOR_TOO_LARGE
