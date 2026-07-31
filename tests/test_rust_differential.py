#!/usr/bin/env python
"""Differential test: Rust cortex_extract.extract_rust vs Python parse_rust_file.

Runs both extractors on the same Rust fixture files and compares the output
payloads field by field. Core fields (functions, calls, fields, types,
namespaces, includes, using_namespaces, using_imports, macros) must match.
"""
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CODE_TINY = os.path.join(_ROOT, "code-tiny")
if _CODE_TINY not in sys.path:
    sys.path.insert(0, _CODE_TINY)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.rust.rust_analyzer import parse_rust_file
import cortex_extract

FIXTURE_DIR = os.path.join(_ROOT, "tests", "fixtures", "rust-app")


def compare_field(rust_val, py_val, key):
    """Compare a single field value, normalizing known format differences."""
    if key == "callee_id":
        return (rust_val or "") == (py_val or "")
    if key in ("scope_name", "caller_scope"):
        # Rust serializes None as "" in these Optional fields; Python asdict keeps None
        return (rust_val or "") == (py_val or "")
    if key == "call_control_frames_json":
        rv = json.loads(rust_val) if rust_val else []
        pv = json.loads(py_val) if py_val else []
        return rv == pv
    return rust_val == py_val


def run_differential():
    rs_files = sorted(
        os.path.join(FIXTURE_DIR, f)
        for f in os.listdir(FIXTURE_DIR)
        if f.endswith(".rs")
    )
    if not rs_files:
        print("ERROR: No .rs fixture files found in", FIXTURE_DIR)
        return 1

    print(f"Found {len(rs_files)} Rust fixture files")
    all_good = True

    for rs_file in rs_files:
        rel = os.path.relpath(rs_file, FIXTURE_DIR)
        print(f"\n{'='*60}")
        print(f"Testing: {rel}")
        print(f"{'='*60}")

        try:
            py_payload = parse_rust_file(rs_file, FIXTURE_DIR)
        except Exception as e:
            print(f"  Python FAILED: {e}")
            all_good = False
            continue

        try:
            rust_payload = cortex_extract.extract_rust(rs_file, FIXTURE_DIR)
        except Exception as e:
            print(f"  Rust FAILED: {e}")
            all_good = False
            continue

        diffs = []

        # Functions
        rust_fns = {fn["symbol_id"]: fn for fn in rust_payload["functions"]}
        py_fns = {fn["symbol_id"]: fn for fn in py_payload["functions"]}
        if rust_fns.keys() != py_fns.keys():
            diffs.append(f"functions: rust={set(rust_fns)} py={set(py_fns)}")
        else:
            for sid in rust_fns:
                r, p = rust_fns[sid], py_fns[sid]
                for k in set(r) | set(p):
                    if not compare_field(r.get(k), p.get(k), k):
                        diffs.append(f"fn[{sid}].{k}: rust={r.get(k)!r} py={p.get(k)!r}")

        # Calls
        rust_calls = sorted(rust_payload["calls"], key=lambda c: (c["call_line"], c["call_column"]))
        py_calls = sorted(py_payload["calls"], key=lambda c: (c["call_line"], c["call_column"]))
        if len(rust_calls) != len(py_calls):
            diffs.append(f"calls count: rust={len(rust_calls)} py={len(py_calls)}")
        else:
            for i, (r, p) in enumerate(zip(rust_calls, py_calls)):
                for k in set(r) | set(p):
                    if not compare_field(r.get(k), p.get(k), k):
                        diffs.append(f"call[{i}].{k}: rust={r.get(k)!r} py={p.get(k)!r}")

        # Fields
        rust_fields = {fl["symbol_id"]: fl for fl in rust_payload["fields"]}
        py_fields = {fl["symbol_id"]: fl for fl in py_payload["fields"]}
        if rust_fields.keys() != py_fields.keys():
            diffs.append(f"fields: rust={set(rust_fields)} py={set(py_fields)}")
        else:
            for sid in rust_fields:
                r, p = rust_fields[sid], py_fields[sid]
                for k in set(r) | set(p):
                    if r.get(k) != p.get(k):
                        diffs.append(f"field[{sid}].{k}: rust={r.get(k)!r} py={p.get(k)!r}")

        # Types (excluding external — Python-side extraction differences)
        rust_real = {t["name"]: t for t in rust_payload["types"] if t["kind"] != "external"}
        py_types = {t["name"]: t for t in py_payload["types"] if t["kind"] != "external"}
        if rust_real.keys() != py_types.keys():
            diffs.append(f"types: rust={set(rust_real)} py={set(py_types)}")
        else:
            for name in rust_real:
                r, p = rust_real[name], py_types[name]
                for k in ("kind", "start_line", "end_line"):
                    if r.get(k) != p.get(k):
                        diffs.append(f"type[{name}].{k}: rust={r.get(k)!r} py={p.get(k)!r}")

        # Namespaces
        rust_ns = {n["symbol_id"]: n for n in rust_payload["namespaces"]}
        py_ns = {n["symbol_id"]: n for n in py_payload["namespaces"]}
        if rust_ns.keys() != py_ns.keys():
            diffs.append(f"namespaces: rust={set(rust_ns)} py={set(py_ns)}")

        # Container fields
        for key in ("includes", "using_namespaces", "using_imports", "macros"):
            if sorted(rust_payload[key]) != sorted(py_payload[key]):
                diffs.append(f"{key}: rust={sorted(rust_payload[key])} py={sorted(py_payload[key])}")

        # Aliases
        rust_aliases = {a["symbol_id"]: a for a in rust_payload["aliases"]}
        py_aliases = {a["symbol_id"]: a for a in py_payload["aliases"]}
        if rust_aliases.keys() != py_aliases.keys():
            diffs.append(f"aliases: rust={set(rust_aliases)} py={set(py_aliases)}")

        if diffs:
            all_good = False
            print(f"  ⚠️  {len(diffs)} differences:")
            for d in diffs[:30]:
                print(f"    {d}")
        else:
            print(f"  ✅ MATCH ({len(rust_fns)} functions, {len(rust_calls)} calls, "
                  f"{len(rust_fields)} fields, {len(rust_real)} types)")

    print(f"\n{'='*60}")
    if all_good:
        print("✅ ALL DIFFERENTIAL TESTS PASSED")
        return 0
    else:
        print("❌ DIFFERENCES FOUND")
        return 1


if __name__ == "__main__":
    sys.exit(run_differential())
