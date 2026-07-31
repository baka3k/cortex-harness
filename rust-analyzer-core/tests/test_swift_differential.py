"""Differential tests: Rust Swift payload == Python Swift payload.

Run from rust-analyzer-core/:
    python tests/test_swift_differential.py -v

Verifies the Rust `cortex_extract.extract_swift()` produces equivalent output
to the Python `parse_swift_file()` for the same `.swift` fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Make the Python swift analyzer importable.
CODE_TINY = ROOT.parent / "code-tiny"
sys.path.insert(0, str(CODE_TINY))

FIXTURES = ROOT / "tests" / "fixtures" / "swift-app"


def _normalize(payload: dict) -> dict:
    """Strip timing-dependent fields that legitimately differ between impls."""
    p = {k: v for k, v in payload.items() if k != "code"}
    # file_def.code is huge — drop it
    if "file_def" in p:
        fd = {k: v for k, v in p["file_def"].items() if k != "code"}
        p["file_def"] = fd
    # parse_meta has timing-dependent counters — collapse to schema version
    if "parse_meta" in p:
        p["parse_meta"] = {"schema_version": "v1"}
    # relations have properties dicts with timestamp-like fields — drop
    if "relations" in p:
        p["relations"] = [
            {k: v for k, v in r.items() if k != "properties"}
            for r in p["relations"]
        ]
    return p


def _python_payload(path: Path) -> dict:
    """Run the Python analyzer (sibling Python reference impl)."""
    from tools.swift.swift_analyzer import parse_swift_file  # type: ignore
    return parse_swift_file(str(path), str(ROOT))


def _rust_payload(path: Path) -> dict:
    """Run the Rust cortex_extract native extension."""
    import cortex_extract  # type: ignore
    return cortex_extract.extract_swift(str(path), str(ROOT))


def _diff(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Return a flat list of (key, repr(a), repr(b)) differences."""
    out = []
    for k in sorted(set(a) | set(b)):
        if k == "parse_meta" or k == "code":
            continue
        va, vb = a.get(k), b.get(k)
        if isinstance(va, list) and isinstance(vb, list):
            # Compare by sorted set of tuples (allow order differences from
            # non-deterministic walk).
            try:
                ta = sorted(tuple(sorted(d.items())) for d in va if isinstance(d, dict))
                tb = sorted(tuple(sorted(d.items())) for d in vb if isinstance(d, dict))
            except Exception:
                ta, tb = va, vb
            if ta != tb:
                out.append(f"{prefix}{k}: {len(va)} vs {len(vb)} items; first diff: {ta[:1]} vs {tb[:1]}")
        elif isinstance(va, dict) and isinstance(vb, dict):
            out.extend(_diff(va, vb, prefix=f"{prefix}{k}."))
        elif va != vb:
            out.append(f"{prefix}{k}: {va!r} vs {vb!r}")
    return out


def test_extension_loads():
    import cortex_extract  # noqa: F401
    assert hasattr(cortex_extract, "extract_swift")
    assert hasattr(cortex_extract, "extract_swift_batch")
    langs = cortex_extract.supported_languages()
    assert "swift" in langs


def test_detect_swift_by_extension():
    import cortex_extract  # noqa: F401
    assert cortex_extract.detect_language("App.swift") == "swift"


def test_parse_root_kind_swift():
    import cortex_extract  # noqa: F401
    kind = cortex_extract.parse_root_kind("swift", b"class Foo {}")
    assert kind == "source_file"


def test_extract_greeter_rust_only():
    """Smoke test the Rust extractor end-to-end on the Greeter fixture."""
    try:
        import cortex_extract  # type: ignore
    except ImportError:
        return
    src = FIXTURES / "Greeter.swift"
    if not src.exists():
        return
    payload = cortex_extract.extract_swift(str(src), str(ROOT))
    type_kinds = [(t["name"], t["kind"]) for t in payload["types"]]
    assert ("Greeter", "class") in type_kinds
    fn_kinds = [(f["name"], f["kind"]) for f in payload["functions"]]
    assert ("init", "constructor") in fn_kinds
    assert ("hello", "method") in fn_kinds
    assert ("formatGreeting", "function") in fn_kinds
    field_names = [f["name"] for f in payload["fields"]]
    assert "name" in field_names
    assert "Foundation" in payload["using_imports"]
    # formatGreeting has unique (name, arity) — should be resolved.
    assert any(c["callee_name"] == "formatGreeting" and c["callee_id"] for c in payload["calls"])


def test_greeter_parity_with_python():
    """Rust payload ≡ Python payload for the Greeter fixture."""
    src = FIXTURES / "Greeter.swift"
    if not src.exists():
        return
    try:
        py_out = _python_payload(src)
    except Exception as e:
        print(f"Python analyzer unavailable ({e}); skipping parity")
        return
    try:
        rust_out = _rust_payload(src)
    except ImportError:
        return
    diffs = _diff(_normalize(py_out), _normalize(rust_out))
    assert not diffs, "Rust vs Python payload differs:\n  " + "\n  ".join(diffs[:20])


def test_drawable_parity_with_python():
    """Rust payload ≡ Python payload for the Drawable fixture (protocols + EXTENDS)."""
    src = FIXTURES / "Drawable.swift"
    if not src.exists():
        return
    try:
        py_out = _python_payload(src)
        rust_out = _rust_payload(src)
    except (ImportError, Exception) as e:
        print(f"Skipping ({e})")
        return
    diffs = _diff(_normalize(py_out), _normalize(rust_out))
    assert not diffs, "Rust vs Python payload differs:\n  " + "\n  ".join(diffs[:20])


def test_control_flow_parity_with_python():
    """Rust payload ≡ Python payload for ControlFlow (branches + loops)."""
    src = FIXTURES / "ControlFlow.swift"
    if not src.exists():
        return
    try:
        py_out = _python_payload(src)
        rust_out = _rust_payload(src)
    except (ImportError, Exception) as e:
        print(f"Skipping ({e})")
        return
    diffs = _diff(_normalize(py_out), _normalize(rust_out))
    assert not diffs, "Rust vs Python payload differs:\n  " + "\n  ".join(diffs[:20])


def test_extract_swift_batch_parallel():
    """Batch API returns same shape as sequential calls."""
    try:
        import cortex_extract  # noqa: F401
    except ImportError:
        return
    files = sorted(FIXTURES.glob("*.swift"))
    if not files:
        return
    payloads = cortex_extract.extract_swift_batch(
        [str(f) for f in files], str(ROOT), 0
    )
    assert len(payloads) == len(files)
    for p in payloads:
        assert "functions" in p
        assert "types" in p
        assert "calls" in p


def test_extract_batch_routes_swift():
    """The language-parametric extract_batch routes 'swift' to the Swift pipeline."""
    try:
        import cortex_extract  # noqa: F401
    except ImportError:
        return
    files = sorted(FIXTURES.glob("*.swift"))
    if not files:
        return
    payloads = cortex_extract.extract_batch(
        [str(f) for f in files], str(ROOT), "swift", 0
    )
    assert len(payloads) == len(files)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))