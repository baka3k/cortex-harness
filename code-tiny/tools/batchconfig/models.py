"""Dataclasses describing structural facts extracted from the project's
pseudo-INI (`KEY:VALUE` per line, not `[section]key=value`) config files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


ANALYZER_VERSION = "batchconfig-regex-v1"
SUPPORTED_EXTENSIONS = (".ini",)


@dataclass
class ConfigEntry:
    key: str
    value: str
    line: int


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class ConfigFile:
    file_path: str
    code: str
    comment: str
    start_line: int
    end_line: int
    source_encoding: str
    source_encoding_lossy: bool
    entries: List[ConfigEntry] = field(default_factory=list)
