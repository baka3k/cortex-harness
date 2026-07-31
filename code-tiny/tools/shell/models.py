"""Dataclasses describing structural facts extracted from POSIX shell scripts.

Mirrors the shape of the cplus/perl analyzer models closely enough that the
existing generic graph writer (`tools.graph.writer.language_writer.LanguageCodeWriter`)
can persist these rows without new writer methods: functions map to `Function`
nodes, and call/config-read edges map to generic `RelationEdge` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


ANALYZER_VERSION = "shell-regex-v1"
SUPPORTED_EXTENSIONS = (".sh",)


@dataclass
class ShellVariable:
    name: str
    raw_expr: str
    line: int


@dataclass
class ShellFunctionDef:
    symbol_id: str
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""


@dataclass
class ShellConfigRead:
    config_key: str
    ini_path_expr: str
    line: int
    enclosing_function: str = ""


@dataclass
class ShellCallEdge:
    callee_ref: str
    line: int
    enclosing_function: str = ""


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class ShellScriptFile:
    file_path: str
    code: str
    comment: str
    start_line: int
    end_line: int
    source_encoding: str
    source_encoding_lossy: bool
    functions: List[ShellFunctionDef] = field(default_factory=list)
    variables: List[ShellVariable] = field(default_factory=list)
    config_reads: List[ShellConfigRead] = field(default_factory=list)
    call_edges: List[ShellCallEdge] = field(default_factory=list)
