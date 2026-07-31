"""Line-based parser for the project's pseudo-INI `KEY:VALUE` config files.

Deliberately does not use `configparser` — these files are flat `KEY:VALUE`
lines (no `[section]` headers, no `=` delimiter), which is a different
grammar than standard INI.
"""

from __future__ import annotations

import os
import uuid
from typing import List

from tools.batchconfig.models import ConfigEntry, ConfigFile, RelationEdge
from tools.common.text_encoding import decode_source_bytes


def _stable_id(kind: str, symbol_id: str) -> str:
    return f"{kind}::{uuid.uuid5(uuid.NAMESPACE_URL, symbol_id)}"


def parse_ini_file(path: str, root: str) -> ConfigFile:
    with open(path, "rb") as handle:
        raw = handle.read()
    code, encoding, lossy = decode_source_bytes(raw)
    rel_path = os.path.relpath(path, root)
    lines = code.split("\n")
    line_count = len(lines)

    comment_lines: List[str] = []
    entries: List[ConfigEntry] = []
    in_leading_comment = True
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if in_leading_comment:
                comment_lines.append(stripped[1:].strip())
            continue
        in_leading_comment = False
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        entries.append(ConfigEntry(key=key.strip(), value=value.strip(), line=idx + 1))

    return ConfigFile(
        file_path=rel_path,
        code=code,
        comment="\n".join(comment_lines),
        start_line=1,
        end_line=line_count,
        source_encoding=encoding,
        source_encoding_lossy=lossy,
        entries=entries,
    )


def build_relations(config: ConfigFile) -> List[RelationEdge]:
    """Emit one `DEFINES_CONFIG` relation per `ConfigEntry` line."""
    file_id = _stable_id("file", config.file_path)
    relations: List[RelationEdge] = []
    for entry in config.entries:
        entry_id = _stable_id("config_entry", f"{config.file_path}:{entry.key}")
        relations.append(
            RelationEdge(
                source_id=file_id,
                source_label="File",
                target_id=entry_id,
                target_label="ConfigEntry",
                rel_type="DEFINES_CONFIG",
                properties={"key": entry.key, "value": entry.value, "line": str(entry.line)},
            )
        )
    return relations
