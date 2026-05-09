#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


SOURCE_EXTS_C = {".c"}
SOURCE_EXTS_CPP = {".cc", ".cpp", ".cxx"}
HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".inl", ".inc"}


@dataclass
class CompileDbBootstrapResult:
    ok: bool
    strategy: str
    output_path: str
    source_path: Optional[str] = None
    message: str = ""


def _run_command(cmd: Sequence[str], cwd: Path, verbose: bool) -> Tuple[bool, str]:
    if verbose:
        print("[cmd]", " ".join(shlex.quote(item) for item in cmd))
    try:
        result = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, result.stdout.strip()
    detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
    return False, detail


def _walk_files(root: Path, max_depth: int) -> Iterable[Path]:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel = current.relative_to(root)
        if len(rel.parts) >= max_depth:
            dirnames[:] = []
        for name in filenames:
            yield current / name


def _find_existing_compile_db(root: Path) -> Optional[Path]:
    priority = [
        root / "compile_commands.json",
        root / "build" / "compile_commands.json",
        root / "out" / "compile_commands.json",
    ]
    for candidate in priority:
        if candidate.is_file():
            return candidate.resolve()
    for candidate in _walk_files(root, max_depth=5):
        if candidate.name == "compile_commands.json" and candidate.is_file():
            return candidate.resolve()
    return None


def _copy_or_link(src: Path, dst: Path, symlink: bool) -> None:
    src = src.resolve()
    dst = dst.resolve()
    if src == dst:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if symlink:
        relative_target = os.path.relpath(src, dst.parent)
        dst.symlink_to(relative_target)
    else:
        shutil.copy2(src, dst)


def _has_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _try_cmake(root: Path, build_dir: Path, generator: Optional[str], verbose: bool) -> Optional[Path]:
    if not (root / "CMakeLists.txt").is_file():
        return None
    if not _has_executable("cmake"):
        return None
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd: List[str] = [
        "cmake",
        "-S",
        str(root),
        "-B",
        str(build_dir),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    if generator:
        cmd.extend(["-G", generator])
    ok, detail = _run_command(cmd, cwd=root, verbose=verbose)
    if not ok:
        if verbose:
            print("[cmake] configure failed:", detail)
        return None
    candidate = build_dir / "compile_commands.json"
    if candidate.is_file():
        return candidate.resolve()
    return None


def _try_compiledb_make(root: Path, make_cmd: str, verbose: bool) -> Optional[Path]:
    if not (root / "Makefile").is_file() and not (root / "makefile").is_file():
        return None
    if not _has_executable("compiledb"):
        return None
    cmd = ["compiledb", "-n"] + shlex.split(make_cmd)
    ok, detail = _run_command(cmd, cwd=root, verbose=verbose)
    if not ok:
        if verbose:
            print("[compiledb] failed:", detail)
        return None
    candidate = root / "compile_commands.json"
    if candidate.is_file():
        return candidate.resolve()
    return None


def _try_bear_make(root: Path, make_cmd: str, verbose: bool) -> Optional[Path]:
    if not (root / "Makefile").is_file() and not (root / "makefile").is_file():
        return None
    if not _has_executable("bear"):
        return None
    output = root / "compile_commands.json"
    if output.exists() or output.is_symlink():
        output.unlink()
    cmd = ["bear", "--output", str(output), "--"] + shlex.split(make_cmd)
    ok, detail = _run_command(cmd, cwd=root, verbose=verbose)
    if not ok:
        if verbose:
            print("[bear] failed:", detail)
        return None
    if output.is_file():
        return output.resolve()
    return None


def _collect_include_dirs(root: Path, include_limit: int) -> List[Path]:
    include_dirs: List[Path] = []
    seen = set()
    for path in _walk_files(root, max_depth=8):
        if path.suffix.lower() not in HEADER_EXTS and path.suffix.lower() not in SOURCE_EXTS_C and path.suffix.lower() not in SOURCE_EXTS_CPP:
            continue
        parent = path.parent.resolve()
        key = str(parent)
        if key in seen:
            continue
        seen.add(key)
        include_dirs.append(parent)
    include_dirs.sort(key=lambda item: str(item))
    if include_limit > 0:
        return include_dirs[:include_limit]
    return include_dirs


def _generate_synthetic_compile_db(
    root: Path,
    output: Path,
    c_std: str,
    cpp_std: str,
    include_limit: int,
    verbose: bool,
) -> Optional[Path]:
    sources: List[Path] = []
    for path in _walk_files(root, max_depth=8):
        ext = path.suffix.lower()
        if ext in SOURCE_EXTS_C or ext in SOURCE_EXTS_CPP:
            sources.append(path.resolve())
    if not sources:
        return None
    include_dirs = _collect_include_dirs(root, include_limit)
    include_flags = [f"-I{directory}" for directory in include_dirs]

    rows = []
    for source in sorted(sources, key=lambda item: str(item)):
        ext = source.suffix.lower()
        is_c = ext in SOURCE_EXTS_C
        compiler = "clang" if is_c else "clang++"
        std = c_std if is_c else cpp_std
        command = [compiler, f"-std={std}"] + include_flags + ["-c", str(source)]
        rows.append(
            {
                "directory": str(source.parent),
                "file": str(source),
                "command": " ".join(shlex.quote(item) for item in command),
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    if verbose:
        print(f"[synthetic] wrote {len(rows)} entries to {output}")
    return output.resolve()


def ensure_compile_commands(
    root: str,
    output: Optional[str] = None,
    cmake_build_dir: str = "build",
    cmake_generator: Optional[str] = None,
    make_cmd: str = "make -n",
    c_standard: str = "gnu11",
    cpp_standard: str = "gnu++17",
    include_limit: int = 300,
    skip_existing: bool = False,
    skip_cmake: bool = False,
    skip_compiledb: bool = False,
    skip_bear: bool = False,
    skip_synthetic: bool = False,
    symlink: bool = False,
    verbose: bool = False,
) -> CompileDbBootstrapResult:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return CompileDbBootstrapResult(
            ok=False,
            strategy="none",
            output_path=str(Path(output).resolve() if output else (root_path / "compile_commands.json")),
            message=f"root not found: {root_path}",
        )

    output_path = Path(output).resolve() if output else (root_path / "compile_commands.json").resolve()
    build_dir = Path(cmake_build_dir)
    if not build_dir.is_absolute():
        build_dir = root_path / build_dir
    build_dir = build_dir.resolve()

    if verbose:
        print("[root]", root_path)
        print("[output]", output_path)

    if not skip_existing:
        existing = _find_existing_compile_db(root_path)
        if existing is not None:
            _copy_or_link(existing, output_path, symlink=symlink)
            return CompileDbBootstrapResult(
                ok=True,
                strategy="existing",
                output_path=str(output_path),
                source_path=str(existing),
                message="reused existing compile_commands.json",
            )

    if not skip_cmake:
        cmake_db = _try_cmake(root_path, build_dir, cmake_generator, verbose)
        if cmake_db is not None:
            _copy_or_link(cmake_db, output_path, symlink=symlink)
            return CompileDbBootstrapResult(
                ok=True,
                strategy="cmake",
                output_path=str(output_path),
                source_path=str(cmake_db),
                message="generated via cmake configure-only",
            )

    if not skip_compiledb:
        compiledb_db = _try_compiledb_make(root_path, make_cmd, verbose)
        if compiledb_db is not None:
            _copy_or_link(compiledb_db, output_path, symlink=symlink)
            return CompileDbBootstrapResult(
                ok=True,
                strategy="compiledb",
                output_path=str(output_path),
                source_path=str(compiledb_db),
                message="generated via compiledb",
            )

    if not skip_bear:
        bear_db = _try_bear_make(root_path, make_cmd, verbose)
        if bear_db is not None:
            _copy_or_link(bear_db, output_path, symlink=symlink)
            return CompileDbBootstrapResult(
                ok=True,
                strategy="bear",
                output_path=str(output_path),
                source_path=str(bear_db),
                message="generated via bear",
            )

    if not skip_synthetic:
        synthetic = _generate_synthetic_compile_db(
            root=root_path,
            output=output_path,
            c_std=c_standard,
            cpp_std=cpp_standard,
            include_limit=include_limit,
            verbose=verbose,
        )
        if synthetic is not None:
            return CompileDbBootstrapResult(
                ok=True,
                strategy="synthetic",
                output_path=str(output_path),
                source_path=str(synthetic),
                message="generated synthetic compile_commands.json",
            )

    return CompileDbBootstrapResult(
        ok=False,
        strategy="none",
        output_path=str(output_path),
        message="unable to produce compile_commands.json with available strategies",
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap compile_commands.json using best-effort strategies.",
    )
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument(
        "--output",
        default=None,
        help="Target compile_commands.json path (default: <root>/compile_commands.json)",
    )
    parser.add_argument(
        "--cmake-build-dir",
        default="build",
        help="Build directory for CMake configure-only strategy (default: build)",
    )
    parser.add_argument("--cmake-generator", default=None, help="Optional CMake generator")
    parser.add_argument(
        "--make-cmd",
        default="make -n",
        help="Build command for compiledb/bear strategies (default: 'make -n')",
    )
    parser.add_argument("--c-standard", default="gnu11", help="Synthetic C standard (default: gnu11)")
    parser.add_argument("--cpp-standard", default="gnu++17", help="Synthetic C++ standard (default: gnu++17)")
    parser.add_argument(
        "--include-limit",
        type=int,
        default=300,
        help="Max include directories in synthetic mode, <=0 means unlimited (default: 300)",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Skip reusing existing compile_commands.json")
    parser.add_argument("--skip-cmake", action="store_true", help="Skip CMake configure-only strategy")
    parser.add_argument("--skip-compiledb", action="store_true", help="Skip compiledb strategy")
    parser.add_argument("--skip-bear", action="store_true", help="Skip bear strategy")
    parser.add_argument("--skip-synthetic", action="store_true", help="Skip synthetic fallback strategy")
    parser.add_argument("--symlink", action="store_true", help="Use symlink instead of copy when reusing generated file")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = ensure_compile_commands(
        root=args.root,
        output=args.output,
        cmake_build_dir=args.cmake_build_dir,
        cmake_generator=args.cmake_generator,
        make_cmd=args.make_cmd,
        c_standard=args.c_standard,
        cpp_standard=args.cpp_standard,
        include_limit=args.include_limit,
        skip_existing=args.skip_existing,
        skip_cmake=args.skip_cmake,
        skip_compiledb=args.skip_compiledb,
        skip_bear=args.skip_bear,
        skip_synthetic=args.skip_synthetic,
        symlink=args.symlink,
        verbose=args.verbose,
    )
    if result.ok:
        if result.source_path:
            print(f"[ok] {result.message}: {result.source_path}")
            if result.output_path != result.source_path:
                print(f"[ok] installed at {result.output_path}")
        else:
            print(f"[ok] {result.message}: {result.output_path}")
        return 0
    if "root not found" in result.message:
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2
    print(f"[error] {result.message}", file=sys.stderr)
    print(
        "[hint] install/build hints: cmake, compiledb, or bear; or allow synthetic fallback",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
