"""Python-only Dart parser built on the tree-sitter-dart grammar."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
import time
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence, Tuple

from tree_sitter import Language, Node, Parser, Tree
try:
    import tree_sitter_dart
except ImportError:  # pragma: no cover - exercised by CLI environments without extras
    tree_sitter_dart = None  # type: ignore[assignment]

from .models import (
    AnalysisFacts,
    DiagnosticRecord,
    EdgeRecord,
    HeaderRecord,
    NodeRecord,
    SourceEvidence,
    SummaryRecord,
)
from .protocol import PROTOCOL_VERSION


SKIPPED_DIRECTORIES = frozenset(
    {
        ".dart_tool",
        ".git",
        ".idea",
        "build",
        # Common environment / build / cache directories shared with other
        # analyzers; keeps ``analyze_project`` consistent with
        # ``tools.common.scan_ignore.COMMON_SCAN_EXCLUDE`` so files inside
        # ``.venv`` / ``node_modules`` / etc. are never scanned.
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "*.egg-info",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "out",
        "target",
        ".gradle",
        "dist",
        "bin",
        "obj",
        "Pods",
        "DerivedData",
        "vendor",
        ".vscode",
        ".cache",
        ".cortext-harness",
        ".flutter-plugins",
        ".flutter-plugins-dependencies",
    }
)
DECLARATION_TYPES = {
    "class_definition": "class",
    "mixin_declaration": "mixin",
    "enum_declaration": "enum",
    "extension_declaration": "extension",
    "extension_type_declaration": "extension_type",
    "type_alias": "type_alias",
}


def create_parser() -> Parser:
    """Create a Dart parser using the precompiled Python grammar wheel."""
    if tree_sitter_dart is None:
        raise RuntimeError(
            "tree-sitter-dart is required for Dart parsing; install code-tiny/requirements.txt"
        )
    return Parser(Language(tree_sitter_dart.language()))


def parser_version() -> str:
    try:
        return version("tree-sitter-dart")
    except PackageNotFoundError:  # pragma: no cover - import would already fail
        return "unknown"


@dataclass
class _Unit:
    path: Path
    relative_path: str
    package_uri: str
    source: bytes
    tree: Tree
    file_node: NodeRecord
    imports: List[Tuple[str, SourceEvidence]] = field(default_factory=list)
    exports: List[Tuple[str, SourceEvidence]] = field(default_factory=list)
    parts: List[Tuple[str, SourceEvidence]] = field(default_factory=list)
    declarations: List[NodeRecord] = field(default_factory=list)
    contains: List[EdgeRecord] = field(default_factory=list)


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _evidence(relative_path: str, node: Node) -> SourceEvidence:
    return SourceEvidence(
        file=relative_path,
        offset=node.start_byte,
        length=node.end_byte - node.start_byte,
        start_line=node.start_point.row + 1,
        start_column=node.start_point.column + 1,
        end_line=node.end_point.row + 1,
        end_column=node.end_point.column + 1,
    )


def _generated(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    return (
        "generated" in path.parts
        or relative_path.endswith((".g.dart", ".freezed.dart", ".mocks.dart"))
    )


def _package_uri(relative_path: str, package_name: str) -> str:
    if relative_path.startswith("lib/"):
        return f"package:{package_name}/{relative_path[4:]}"
    return relative_path


def _identity(package_uri: str, kind: str, qualified_name: str, offset: int) -> str:
    return f"{package_uri}|{kind}|{qualified_name}|{offset}"


def _name_node(node: Node) -> Node | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return named
    return next(
        (child for child in node.named_children if child.type in {"identifier", "type_identifier"}),
        None,
    )


def _string_value(source: bytes, node: Node) -> str:
    value = _text(source, node).strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    return value


def _uri(unit: _Unit, node: Node) -> Tuple[str, SourceEvidence] | None:
    literal = next((item for item in _walk(node) if item.type == "string_literal"), None)
    if literal is None:
        return None
    return _string_value(unit.source, literal), _evidence(unit.relative_path, literal)


def _make_node(
    unit: _Unit,
    node: Node,
    *,
    kind: str,
    name: str,
    qualified_name: str | None = None,
    class_name: str = "",
    end_node: Node | None = None,
) -> NodeRecord:
    end = end_node or node
    evidence = _evidence(unit.relative_path, node)
    if end.end_byte > node.end_byte:
        evidence = SourceEvidence(
            file=evidence.file,
            offset=evidence.offset,
            length=end.end_byte - node.start_byte,
            start_line=evidence.start_line,
            start_column=evidence.start_column,
            end_line=end.end_point.row + 1,
            end_column=end.end_point.column + 1,
        )
    qualified = qualified_name or name
    code = unit.source[node.start_byte : end.end_byte].decode("utf-8", errors="replace")
    return NodeRecord(
        identity=_identity(unit.package_uri, kind, qualified, node.start_byte),
        kind=kind,
        properties={
            "name": name,
            "qualified_name": qualified,
            "class_name": class_name,
            "scope_name": class_name,
            "package_uri": unit.package_uri,
            "code": code,
            "generated": _generated(unit.relative_path),
            "exported": not name.startswith("_"),
        },
        evidence=evidence,
    )


def _next_body(children: Sequence[Node], index: int) -> Node | None:
    if index + 1 < len(children) and children[index + 1].type == "function_body":
        return children[index + 1]
    return None


def _function_name(source: bytes, signature: Node) -> str | None:
    name = signature.child_by_field_name("name")
    if name is None:
        name = next(
            (item for item in signature.named_children if item.type == "identifier"),
            None,
        )
    return _text(source, name) if name is not None else None


def _add_containment(unit: _Unit, owner: NodeRecord, child: NodeRecord) -> None:
    unit.contains.append(
        EdgeRecord(
            source=owner.identity,
            target=child.identity,
            relationship="CONTAINS",
            evidence=child.evidence,
        )
    )


def _extract_class_members(unit: _Unit, class_node: Node, owner: NodeRecord) -> None:
    body = class_node.child_by_field_name("body")
    if body is None:
        body = next((child for child in class_node.named_children if child.type == "class_body"), None)
    if body is None:
        return
    children = body.named_children
    class_name = str(owner.properties["name"])
    for index, child in enumerate(children):
        signature = child
        if child.type == "method_signature" and child.named_children:
            signature = child.named_children[-1]
        if signature.type == "function_signature":
            name = _function_name(unit.source, signature)
            if not name:
                continue
            declaration = _make_node(
                unit,
                child,
                kind="method",
                name=name,
                qualified_name=f"{class_name}.{name}",
                class_name=class_name,
                end_node=_next_body(children, index),
            )
        elif child.type == "declaration":
            constructor = next(
                (
                    item
                    for item in _walk(child)
                    if item.type
                    in {"constant_constructor_signature", "constructor_signature", "factory_constructor_signature"}
                ),
                None,
            )
            if constructor is None:
                continue
            name_node = _name_node(constructor)
            name = _text(unit.source, name_node) if name_node is not None else class_name
            declaration = _make_node(
                unit,
                child,
                kind="constructor",
                name=name,
                qualified_name=f"{class_name}.{name}",
                class_name=class_name,
                end_node=_next_body(children, index),
            )
        else:
            continue
        unit.declarations.append(declaration)
        _add_containment(unit, owner, declaration)


def _extract_fields(unit: _Unit, owner_by_range: Sequence[Tuple[int, int, NodeRecord]]) -> None:
    for declaration in _walk(unit.tree.root_node):
        if declaration.type != "static_final_declaration":
            continue
        name_node = _name_node(declaration)
        if name_node is None:
            continue
        name = _text(unit.source, name_node)
        owner = next(
            (
                record
                for start, end, record in owner_by_range
                if start <= declaration.start_byte and declaration.end_byte <= end
            ),
            unit.file_node,
        )
        class_name = "" if owner.kind == "file" else str(owner.properties["name"])
        qualified = f"{class_name}.{name}" if class_name else name
        field = _make_node(
            unit,
            declaration,
            kind="field",
            name=name,
            qualified_name=qualified,
            class_name=class_name,
        )
        unit.declarations.append(field)
        _add_containment(unit, owner, field)


def _extract_unit(unit: _Unit) -> None:
    root = unit.tree.root_node
    for child in root.named_children:
        if child.type in {"import_or_export", "part_directive"}:
            uri = _uri(unit, child)
            if uri is None:
                continue
            if any(item.type == "library_import" for item in _walk(child)):
                unit.imports.append(uri)
            elif any(item.type == "library_export" for item in _walk(child)):
                unit.exports.append(uri)
            elif child.type == "part_directive":
                unit.parts.append(uri)

    root_children = root.named_children
    owners: List[Tuple[int, int, NodeRecord]] = []
    for index, child in enumerate(root_children):
        kind = DECLARATION_TYPES.get(child.type)
        if kind:
            name_node = _name_node(child)
            if name_node is None:
                continue
            name = _text(unit.source, name_node)
            declaration = _make_node(unit, child, kind=kind, name=name)
            superclass = child.child_by_field_name("superclass")
            if superclass is not None:
                type_name = next(
                    (item for item in _walk(superclass) if item.type == "type_identifier"),
                    None,
                )
                if type_name is not None:
                    declaration.properties["superclass"] = _text(unit.source, type_name)
            unit.declarations.append(declaration)
            _add_containment(unit, unit.file_node, declaration)
            owners.append((child.start_byte, child.end_byte, declaration))
            if kind in {"class", "mixin", "extension", "extension_type"}:
                _extract_class_members(unit, child, declaration)
        elif child.type == "function_signature":
            name = _function_name(unit.source, child)
            if not name:
                continue
            declaration = _make_node(
                unit,
                child,
                kind="function",
                name=name,
                end_node=_next_body(root_children, index),
            )
            unit.declarations.append(declaration)
            _add_containment(unit, unit.file_node, declaration)
    _extract_fields(unit, owners)


def _resolve_uri(uri: str, unit: _Unit, root: Path, package_name: str) -> str | None:
    if uri.startswith(f"package:{package_name}/"):
        return f"lib/{uri.split('/', 1)[1]}"
    if uri.startswith("package:") or ":" in uri:
        return None
    target = (unit.path.parent / uri).resolve()
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return None


def _call_name(source: bytes, argument_part: Node) -> str | None:
    selector = argument_part.parent
    expression = selector.parent if selector is not None else None
    if selector is None or expression is None:
        return None
    siblings = expression.named_children
    try:
        stop = siblings.index(selector)
    except ValueError:
        return None
    names: List[str] = []
    for sibling in siblings[: stop + 1]:
        if sibling.type in {"identifier", "type_identifier"}:
            names.append(_text(source, sibling))
        elif sibling.type == "selector":
            member = next(
                (
                    item
                    for item in _walk(sibling)
                    if item.type == "identifier" and item.end_byte <= argument_part.start_byte
                ),
                None,
            )
            if member is not None:
                names.append(_text(source, member))
    return names[-1] if names else None


def _owner_for_offset(unit: _Unit, offset: int) -> NodeRecord:
    matches = [
        node
        for node in unit.declarations
        if node.evidence.offset <= offset <= node.evidence.offset + node.evidence.length
    ]
    if not matches:
        return unit.file_node
    return min(matches, key=lambda item: item.evidence.length)


def _diagnostics(unit: _Unit) -> List[DiagnosticRecord]:
    values: List[DiagnosticRecord] = []
    for node in _walk(unit.tree.root_node):
        if node.type != "ERROR" and not node.is_missing:
            continue
        values.append(
            DiagnosticRecord(
                severity="error",
                code="dart_syntax_error",
                message="Dart syntax could not be parsed at this source range",
                recoverable=True,
                evidence=_evidence(unit.relative_path, node),
            )
        )
    return values


def _discover(root: Path) -> List[Path]:
    return sorted(
        path
        for path in root.rglob("*.dart")
        if not any(part in SKIPPED_DIRECTORIES for part in path.relative_to(root).parts)
    )


def _choose_call_target(
    candidates: Sequence[NodeRecord], *, object_creation: bool
) -> NodeRecord | None:
    class_kinds = {"class", "mixin", "enum", "extension_type"}
    if object_creation:
        class_candidates = [candidate for candidate in candidates if candidate.kind in class_kinds]
        return class_candidates[0] if len(class_candidates) == 1 else None
    callable_candidates = [
        candidate
        for candidate in candidates
        if candidate.kind == "function" or candidate.kind in class_kinds
    ]
    return callable_candidates[0] if len(callable_candidates) == 1 else None


def analyze_project(
    root: str | Path,
    *,
    project_id: str,
    package_name: str | None = None,
    mode: str = "dart",
) -> AnalysisFacts:
    """Parse a Dart project and resolve project-local relationships in Python."""
    started = time.monotonic()
    project_root = Path(root).resolve()
    package = package_name or project_root.name
    parser = create_parser()
    units: List[_Unit] = []
    diagnostics: List[DiagnosticRecord] = []

    for path in _discover(project_root):
        relative = path.relative_to(project_root).as_posix()
        source = path.read_bytes()
        tree = parser.parse(source)
        package_uri = _package_uri(relative, package)
        evidence = _evidence(relative, tree.root_node)
        file_node = NodeRecord(
            identity=f"file:{package_uri}",
            kind="file",
            properties={
                "name": path.name,
                "path": relative,
                "qualified_name": package_uri,
                "package_uri": package_uri,
                "generated": _generated(relative),
            },
            evidence=evidence,
        )
        unit = _Unit(path, relative, package_uri, source, tree, file_node)
        _extract_unit(unit)
        diagnostics.extend(_diagnostics(unit))
        units.append(unit)

    files_by_path = {unit.relative_path: unit for unit in units}
    declarations_by_name: Dict[str, List[NodeRecord]] = {}
    for unit in units:
        for declaration in unit.declarations:
            declarations_by_name.setdefault(str(declaration.properties["name"]), []).append(declaration)

    edges: List[EdgeRecord] = []
    for unit in units:
        edges.extend(unit.contains)
        visible_paths = {unit.relative_path}
        for uri, _ in (*unit.imports, *unit.exports, *unit.parts):
            target_path = _resolve_uri(uri, unit, project_root, package)
            if target_path is not None:
                visible_paths.add(target_path)
        for relationship, entries in (
            ("IMPORTS", unit.imports),
            ("EXPORTS", unit.exports),
            ("HAS_PART", unit.parts),
        ):
            for uri, evidence in entries:
                target_path = _resolve_uri(uri, unit, project_root, package)
                target = files_by_path.get(target_path or "")
                if target is not None:
                    edges.append(
                        EdgeRecord(
                            source=unit.file_node.identity,
                            target=target.file_node.identity,
                            relationship=relationship,
                            properties={"uri": uri},
                            evidence=evidence,
                        )
                    )

        for declaration in unit.declarations:
            superclass = declaration.properties.get("superclass")
            candidates = (
                [
                    candidate
                    for candidate in declarations_by_name.get(str(superclass), [])
                    if candidate.evidence.file in visible_paths
                ]
                if superclass
                else []
            )
            if len(candidates) == 1:
                edges.append(
                    EdgeRecord(
                        source=declaration.identity,
                        target=candidates[0].identity,
                        relationship="EXTENDS",
                        evidence=declaration.evidence,
                    )
                )

        seen_calls: set[Tuple[str, str, int]] = set()
        for node in _walk(unit.tree.root_node):
            call_name: str | None = None
            object_creation = False
            if node.type == "const_object_expression":
                type_node = next(
                    (item for item in node.named_children if item.type == "type_identifier"),
                    None,
                )
                call_name = _text(unit.source, type_node) if type_node is not None else None
                object_creation = True
            elif node.type == "argument_part":
                call_name = _call_name(unit.source, node)
            if not call_name:
                continue
            candidates = [
                candidate
                for candidate in declarations_by_name.get(call_name, [])
                if candidate.evidence.file in visible_paths
            ]
            target = _choose_call_target(candidates, object_creation=object_creation)
            if target is None:
                continue
            owner = _owner_for_offset(unit, node.start_byte)
            key = owner.identity, target.identity, node.start_byte
            if key in seen_calls or owner.identity == target.identity:
                continue
            seen_calls.add(key)
            edges.append(
                EdgeRecord(
                    source=owner.identity,
                    target=target.identity,
                    relationship="CALLS",
                    properties={"resolved_name": call_name},
                    evidence=_evidence(unit.relative_path, node),
                )
            )

    nodes = tuple(
        sorted(
            [item for unit in units for item in (unit.file_node, *unit.declarations)],
            key=lambda item: item.identity,
        )
    )
    unique_edges = {
        (edge.source, edge.target, edge.relationship, edge.evidence.offset): edge for edge in edges
    }
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return AnalysisFacts(
        header=HeaderRecord(
            schema_version=PROTOCOL_VERSION,
            analyzer_version=f"tree-sitter-dart/{parser_version()}",
            sdk_version="not-required-python-runtime",
            root=str(project_root),
            project_id=project_id,
            mode=mode,
        ),
        nodes=nodes,
        edges=tuple(
            sorted(
                unique_edges.values(),
                key=lambda edge: (
                    edge.relationship,
                    edge.source,
                    edge.target,
                    edge.evidence.offset,
                ),
            )
        ),
        diagnostics=tuple(diagnostics),
        summary=SummaryRecord(
            processed_files=len(units),
            skipped_files=0,
            error_count=sum(item.severity == "error" for item in diagnostics),
            elapsed_ms=elapsed_ms,
        ),
    )
