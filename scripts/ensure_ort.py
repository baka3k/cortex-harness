#!/usr/bin/env python3
"""Provision libonnxruntime for the Rust `ort` embedder into `.cache/ort/<version>/`.

`ort` is built with `load-dynamic`, so nothing is downloaded at compile time; at
runtime it dlopens a dylib that has to exist somewhere. Rather than guessing ONNX
Runtime GitHub release asset names per platform, we copy the binary out of the
`onnxruntime` wheel already pinned in requirements — the exact same ORT build the
parity fixtures were measured with (avoids "ORT 1.22 vs 1.29 numeric drift" class
of bugs entirely).

Cross-platform: the wheel ships `capi/libonnxruntime*.dylib` (macOS),
`capi/libonnxruntime.so*` (Linux) or `capi/onnxruntime.dll` (Windows).

Usage:
    .venv/bin/python scripts/ensure_ort.py            # idempotent
    .venv/bin/python scripts/ensure_ort.py --print-path
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".cache" / "ort"


def onnxruntime_capi() -> tuple[Path, str]:
    """Locate the installed onnxruntime capi directory + its version."""
    try:
        import onnxruntime
    except ImportError as exc:  # pragma: no cover - environment problem
        raise SystemExit(
            "onnxruntime is not importable with this interpreter. Install project "
            "deps first (`make install`), or point PYTHON at .venv/bin/python."
        ) from exc
    return Path(onnxruntime.__file__).resolve().parent / "capi", onnxruntime.__version__


def find_library(capi: Path) -> Path:
    patterns = ("libonnxruntime*.dylib", "libonnxruntime.so*", "onnxruntime.dll")
    for pattern in patterns:
        for candidate in sorted(capi.glob(pattern)):
            if candidate.is_file():
                return candidate
    raise SystemExit(
        f"no ONNX Runtime shared library under {capi} (looked for {', '.join(patterns)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="recopy even if present")
    parser.add_argument("--print-path", action="store_true", help="print the dylib path only")
    args = parser.parse_args()

    interpreter = Path(sys.executable)
    if ".venv" not in str(interpreter) and (REPO / ".venv/bin/python").is_file():
        # Re-exec inside the project venv so `import onnxruntime` resolves there.
        os.execv(str(REPO / ".venv/bin/python"), [str(REPO / ".venv/bin/python"), __file__] + sys.argv[1:])

    capi, version = onnxruntime_capi()
    source = find_library(capi)
    target_dir = CACHE / version
    target = target_dir / source.name

    if target.exists() and not args.force:
        if args.print_path:
            print(target)
        else:
            print(f"[ort] already provisioned: {target}")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    # ORT's versioned soname is what the loader actually opens next; keep the plain
    # name stable so callers can glob for `libonnxruntime*`.
    stable = target_dir / (
        "libonnxruntime.dylib"
        if source.suffix == ".dylib"
        else "onnxruntime.dll"
        if source.suffix == ".dll"
        else "libonnxruntime.so"
    )
    if not stable.exists():
        try:
            stable.symlink_to(source.name)
        except OSError:  # pragma: no cover - windows without dev mode
            shutil.copy2(target, stable)

    if args.print_path:
        print(stable)
    else:
        size_mb = target.stat().st_size / 1e6
        print(
            f"[ort] provisioned onnxruntime {version}: {source.name} ({size_mb:.1f} MB) "
            f"-> {target.relative_to(REPO)} (+ {stable.name})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
