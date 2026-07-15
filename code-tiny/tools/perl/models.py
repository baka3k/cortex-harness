"""Immutable, deterministic contracts for Perl structural analysis."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


ANALYZER_VERSION = "perl-tree-sitter-v1"
SUPPORTED_EXTENSIONS: Tuple[str, ...] = (".pl", ".pm", ".t")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def redact_text(value: str, limit: int = 4000) -> str:
    """Redact common credential shapes and bound source-derived text."""
    text = value or ""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text[: max(0, limit)]


def stable_id(
    project_id: str,
    file_path: str,
    package: str,
    scope: str,
    kind: str,
    qualified_name: str,
) -> str:
    """Return a checkout-independent semantic identifier."""
    material = "\0".join(
        (
            project_id.strip(),
            file_path.replace("\\", "/").strip("/"),
            package.strip(),
            scope.strip(),
            kind.strip(),
            qualified_name.strip(),
        )
    )
    return f"perl:{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, order=True)
class SourceSpan:
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True, order=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    file_path: str = ""
    span: Optional[SourceSpan] = None
    details: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ParserCapabilities:
    analyzer_version: str
    runtime_package: str
    runtime_version: str
    grammar_package: str
    grammar_version: str
    grammar_abi: int
    grammar_semantic_version: str
    language_name: str
    extensions: Tuple[str, ...]
    supported_nodes: Tuple[str, ...]


@dataclass(frozen=True, order=True)
class DocumentationRecord:
    kind: str
    text: str
    file_path: str
    span: SourceSpan
    truncated: bool = False


@dataclass(frozen=True, order=True)
class FileRecord:
    file_path: str
    language: str
    parser_version: str
    grammar_version: str
    parse_status: str
    coverage: str
    content_sha256: str
    size_bytes: int
    line_count: int
    error_count: int = 0
    truncated: bool = False


@dataclass(frozen=True, order=True)
class SymbolRecord:
    symbol_id: str
    name: str
    kind: str
    fq_name: str
    file_path: str
    span: SourceSpan
    package: str
    scope: str = ""
    declaration_kind: str = ""
    arity: int = 0
    prototype: str = ""
    attributes: Tuple[str, ...] = ()
    code: str = ""
    documentation: str = ""


@dataclass(frozen=True, order=True)
class ImportRecord:
    import_id: str
    kind: str
    module: str
    raw_text: str
    file_path: str
    span: SourceSpan
    is_dynamic: bool = False
    is_conditional: bool = False
    resolved_path: str = ""


@dataclass(frozen=True, order=True)
class ReferenceRecord:
    ref_id: str
    source_symbol_id: str
    source_name: str
    target_name: str
    kind: str
    file_path: str
    span: SourceSpan
    confidence: float
    resolution_status: str
    target_symbol_id: str = ""
    reason: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class DependencyIndex:
    forward: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    reverse: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    @classmethod
    def from_mappings(
        cls,
        forward: Mapping[str, Iterable[str]],
        reverse: Mapping[str, Iterable[str]],
    ) -> "DependencyIndex":
        normalize = lambda source: tuple(
            (key, tuple(sorted(set(values)))) for key, values in sorted(source.items())
        )
        return cls(forward=normalize(forward), reverse=normalize(reverse))

    def forward_map(self) -> Dict[str, Tuple[str, ...]]:
        return dict(self.forward)

    def reverse_map(self) -> Dict[str, Tuple[str, ...]]:
        return dict(self.reverse)


@dataclass(frozen=True)
class ParsedFile:
    file: FileRecord
    symbols: Tuple[SymbolRecord, ...] = ()
    imports: Tuple[ImportRecord, ...] = ()
    references: Tuple[ReferenceRecord, ...] = ()
    documentation: Tuple[DocumentationRecord, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AnalysisResult:
    project_id: str
    normalized_root: str
    analyzer_version: str
    capabilities: ParserCapabilities
    coverage: str
    files: Tuple[FileRecord, ...] = ()
    symbols: Tuple[SymbolRecord, ...] = ()
    imports: Tuple[ImportRecord, ...] = ()
    references: Tuple[ReferenceRecord, ...] = ()
    documentation: Tuple[DocumentationRecord, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    dependency_index: DependencyIndex = field(default_factory=DependencyIndex)
    changed_paths: Tuple[str, ...] = ()
    deleted_paths: Tuple[str, ...] = ()
    counters: Tuple[Tuple[str, int], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self, *, pretty: bool = False) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            sort_keys=True,
        )


def _to_primitive(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_primitive(item) for item in value]
    return value


def _span_from_dict(data: Optional[Mapping[str, Any]]) -> Optional[SourceSpan]:
    return SourceSpan(**data) if data else None


def parsed_file_from_dict(data: Mapping[str, Any]) -> ParsedFile:
    """Rehydrate a cached parsed-file payload."""
    file_record = FileRecord(**data["file"])
    symbols = tuple(
        SymbolRecord(
            **{
                **item,
                "span": SourceSpan(**item["span"]),
                "attributes": tuple(item.get("attributes") or ()),
            }
        )
        for item in data.get("symbols", ())
    )
    imports = tuple(
        ImportRecord(**{**item, "span": SourceSpan(**item["span"])})
        for item in data.get("imports", ())
    )
    references = tuple(
        ReferenceRecord(**{**item, "span": SourceSpan(**item["span"])})
        for item in data.get("references", ())
    )
    documentation = tuple(
        DocumentationRecord(**{**item, "span": SourceSpan(**item["span"])})
        for item in data.get("documentation", ())
    )
    diagnostics = tuple(
        Diagnostic(
            **{
                **item,
                "span": _span_from_dict(item.get("span")),
                "details": tuple(tuple(pair) for pair in item.get("details", ())),
            }
        )
        for item in data.get("diagnostics", ())
    )
    return ParsedFile(file_record, symbols, imports, references, documentation, diagnostics)


def update_reference(reference: ReferenceRecord, **changes: Any) -> ReferenceRecord:
    return replace(reference, **changes)
