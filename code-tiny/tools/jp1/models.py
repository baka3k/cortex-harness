"""Dataclasses describing structural facts extracted from JP1/AJS job-net
unit-definition export files (the `unit=...{ ... }` DSL).

Mirrors the shell/perl analyzer model shape closely enough that the existing
generic graph writer (`tools.graph.writer.language_writer.LanguageCodeWriter`)
can persist these rows without new writer methods: units map to generic
`Symbol`-style rows and `CONTAINS`/`PRECEDES`/`EXECUTES` edges map to generic
`RelationEdge` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


ANALYZER_VERSION = "jp1-regex-v1"
SUPPORTED_EXTENSIONS = (".txt",)


@dataclass
class Jp1SequenceEdge:
    from_unit: str
    to_unit: str
    line: int


@dataclass
class Jp1Unit:
    unit_id: str
    unit_type: str  # "n" (jobnet/group), "j" (job), etc. raw `ty=` value
    comment: str
    parent_id: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    exec_command: str = ""
    sequence_edges: List[Jp1SequenceEdge] = field(default_factory=list)
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class Jp1DefinitionFile:
    file_path: str
    code: str
    comment: str
    start_line: int
    end_line: int
    source_encoding: str
    source_encoding_lossy: bool
    units: List[Jp1Unit] = field(default_factory=list)
