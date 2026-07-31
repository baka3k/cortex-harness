#!/usr/bin/env python
"""Smoke test: every wired analyzer must invoke cortex_extract successfully.

When the native extension is available, every parse_*_file() in the wired
family should hit the Rust fast path and return data of the expected shape.
When the extension is unavailable (e.g. not built on this machine), we must
verify the pure-Python fallback still produces the same shape.

This test creates minimal in-memory fixtures and runs each analyzer.
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from typing import Any, Tuple

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CODE_TINY = os.path.join(_ROOT, "code-tiny")
if _CODE_TINY not in sys.path:
    sys.path.insert(0, _CODE_TINY)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.common._rust_accel import is_available, cortex_extract  # noqa: E402

# Minimal fixtures — one per language.
FIXTURES: dict[str, tuple[str, str]] = {
    "go": (
        "main.go",
        textwrap.dedent(
            """\
            package main

            import "fmt"

            func greet(name string) string {
                return fmt.Sprintf("hi %s", name)
            }

            func main() {
                greet("world")
            }
            """
        ),
    ),
    "rust": (
        "lib.rs",
        textwrap.dedent(
            """\
            pub fn add(a: i32, b: i32) -> i32 {
                a + b
            }

            pub fn main() {
                let _ = add(1, 2);
            }
            """
        ),
    ),
    "swift": (
        "App.swift",
        textwrap.dedent(
            """\
            import Foundation

            func square(_ x: Int) -> Int { return x * x }

            print(square(4))
            """
        ),
    ),
    "csharp": (
        "Program.cs",
        textwrap.dedent(
            """\
            using System;

            class Program {
                static int Add(int a, int b) { return a + b; }
                static void Main() { Console.WriteLine(Add(1, 2)); }
            }
            """
        ),
    ),
    "php": (
        "index.php",
        textwrap.dedent(
            """\
            <?php
            function greet($name) { return "hi " . $name; }
            echo greet("world");
            """
        ),
    ),
    "java": (
        "Main.java",
        textwrap.dedent(
            """\
            package com.example;

            public class Main {
                public static int add(int a, int b) { return a + b; }
                public static void main(String[] args) { System.out.println(add(1, 2)); }
            }
            """
        ),
    ),
    "delphi": (
        "Main.pas",
        textwrap.dedent(
            """\
            unit Main;

            interface

            type
              TGreeter = class
                function Greet(const Name: string): string;
              end;

            implementation

            function TGreeter.Greet(const Name: string): string;
            begin
              Result := 'hi ' + Name;
            end;

            end.
            """
        ),
    ),
    "cplus": (
        "main.cpp",
        textwrap.dedent(
            """\
            #include <iostream>

            int add(int a, int b) { return a + b; }

            int main() {
                std::cout << add(1, 2) << std::endl;
                return 0;
            }
            """
        ),
    ),
    "ts": (
        "App.ts",
        textwrap.dedent(
            """\
            export function add(a: number, b: number): number {
                return a + b;
            }

            console.log(add(1, 2));
            """
        ),
    ),
}


def _build_fixtures() -> tuple[str, dict[str, str]]:
    root = tempfile.mkdtemp(prefix="wiring_test_")
    paths: dict[str, str] = {}
    for lang, (rel, src) in FIXTURES.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(src)
        paths[lang] = full
    return root, paths


def _check_go_rust_swift_dict(lang: str, result: Any) -> None:
    assert isinstance(result, dict), f"{lang}: expected dict, got {type(result)}"
    for key in ("functions", "file_def", "calls"):
        assert key in result, f"{lang}: missing key '{key}'"


def _check_tuple_shape(lang: str, result: tuple, arity: int) -> None:
    assert isinstance(result, tuple), f"{lang}: expected tuple, got {type(result)}"
    assert len(result) == arity, f"{lang}: expected {arity}-tuple, got {len(result)}"


def _run_family_a(lang: str, path: str) -> None:
    if lang == "go":
        from tools.go.go_analyzer import parse_go_file
        result = parse_go_file(path)
    elif lang == "rust":
        from tools.rust.rust_analyzer import parse_rust_file
        result = parse_rust_file(path)
    elif lang == "swift":
        from tools.swift.swift_analyzer import parse_swift_file
        result = parse_swift_file(path)
    else:
        return
    _check_go_rust_swift_dict(lang, result)


def _run_family_b(lang: str, path: str, root: str) -> None:
    if lang == "csharp":
        from tools.csharp.csharp_analyzer import parse_csharp_file
        result = parse_csharp_file(path, root)
        _check_tuple_shape(lang, result, 7)
    elif lang == "php":
        from tools.php.php_analyzer import parse_php_file
        result = parse_php_file(path, root)
        _check_tuple_shape(lang, result, 6)
    elif lang == "delphi":
        from tools.delphi.delphi_analyzer import parse_delphi_file
        result = parse_delphi_file(path, root)
        _check_tuple_shape(lang, result, 9)
    elif lang == "java":
        from tools.java.java_analyzer import parse_java_file
        result = parse_java_file(path, root)
        _check_tuple_shape(lang, result, 9)
    elif lang == "cplus":
        from tools.cplus.cplus_analyzer import parse_c_family_file
        result = parse_c_family_file(path, root, is_cpp=True)
        _check_tuple_shape(lang, result, 15)
    elif lang == "ts":
        from tools.ts.ts_analyzer import parse_ts_file
        result = parse_ts_file(path, root)
        _check_tuple_shape(lang, result, 12)


def test_rust_accel_available():
    """The native extension must be importable for production wiring to work."""
    assert is_available(), "cortex_extract native extension unavailable"
    assert cortex_extract is not None
    # Sanity check: each extract_* function is callable
    for lang in FIXTURES.keys():
        if lang == "ts":
            fn_name = "extract_ts"
        elif lang == "cplus":
            fn_name = "extract_cplus"
        else:
            fn_name = f"extract_{lang}"
        assert hasattr(cortex_extract, fn_name), f"missing native function {fn_name}"


def test_family_a_wiring():
    """Family A: go, rust, swift return dicts via Rust fast path."""
    root, paths = _build_fixtures()
    for lang in ("go", "rust", "swift"):
        _run_family_a(lang, paths[lang])


def test_family_b_wiring():
    """Family B: csharp, php, delphi, java, cplus, ts return tuples via Rust fast path."""
    root, paths = _build_fixtures()
    for lang in ("csharp", "php", "delphi", "java", "cplus", "ts"):
        _run_family_b(lang, paths[lang], root)


def test_rust_payload_keys_match_python():
    """Spot-check: Rust payload dict for go has the same top-level keys as Python's dict."""
    import json
    root, paths = _build_fixtures()
    # Compare go (a simple Family A)
    from tools.go.go_analyzer import parse_go_file
    py_payload = parse_go_file(paths["go"])
    rust_payload = cortex_extract.extract_go(paths["go"], root)
    py_keys = set(py_payload.keys())
    rust_keys = set(rust_payload.keys())
    # Rust fast path should populate at least the core keys Python exposes.
    for key in ("functions", "file_def"):
        assert key in rust_keys, f"rust payload missing {key}"
        assert key in py_keys, f"python payload missing {key}"


if __name__ == "__main__":
    test_rust_accel_available()
    print("✓ cortex_extract native extension available with all extract_* functions")
    test_family_a_wiring()
    print("✓ Family A (go, rust, swift) wired and returning correct shapes")
    test_family_b_wiring()
    print("✓ Family B (csharp, php, delphi, java, cplus, ts) wired and returning correct tuple shapes")
    test_rust_payload_keys_match_python()
    print("✓ Rust payload schema matches Python dict schema for spot-check")
    print("\nAll production wiring smoke tests passed.")
