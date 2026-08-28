#!/usr/bin/env python3
"""Generate the deterministic 20,186-file graph-ingest scale fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TOTAL_FILES = 20_186
COMPILED_FILES = 3_281
FANOUT_HEADERS = 501
PROC_FILES = TOTAL_FILES - COMPILED_FILES - FANOUT_HEADERS


def _write(root: Path, path: Path, content: str, digest: hashlib._Hash) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    relative = path.relative_to(root).as_posix()
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(content.encode("utf-8"))
    digest.update(b"\0")


def generate(output: Path) -> dict[str, object]:
    output = output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"refusing non-empty fixture directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()

    fanout = output / "include" / "fanout"
    includes = []
    for index in range(FANOUT_HEADERS - 1):
        name = f"leaf_{index:04d}.h"
        includes.append(f'#include "{name}"')
        _write(
            output,
            fanout / name,
            f"#pragma once\n#define FIXTURE_LEAF_{index:04d} {index}\n",
            digest,
        )
    _write(
        output,
        fanout / "root.h",
        "#pragma once\n" + "\n".join(includes) + "\n",
        digest,
    )

    compile_commands = []
    for index in range(COMPILED_FILES):
        relative = Path("src") / "compiled" / f"unit_{index:05d}.c"
        _write(
            output,
            output / relative,
            '#include "fanout/root.h"\n'
            f"int fixture_{index:05d}(void) {{ return {index % 7}; }}\n",
            digest,
        )
        compile_commands.append(
            {
                "directory": str(output),
                "file": str(output / relative),
                "arguments": [
                    "clang",
                    "-I",
                    str(output / "include"),
                    "-c",
                    str(output / relative),
                ],
            }
        )

    for index in range(PROC_FILES):
        relative = Path("src") / "proc" / f"unit_{index:05d}.pc"
        _write(
            output,
            output / relative,
            f"/* deterministic Pro*C staging fixture {index:05d} */\n",
            digest,
        )

    compile_path = output / "compile_commands.json"
    compile_content = json.dumps(compile_commands, indent=2, sort_keys=True) + "\n"
    _write(output, compile_path, compile_content, digest)
    manifest = {
        "schema_version": 1,
        "total_source_files": TOTAL_FILES,
        "compile_command_entries": COMPILED_FILES,
        "fanout_headers": FANOUT_HEADERS,
        "proc_files": PROC_FILES,
        "content_manifest_sha256": digest.hexdigest(),
    }
    (output / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
