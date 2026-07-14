"""Staged COBOL parse, resolve, semantic, incremental, and graph pipeline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from .models import AnalysisResult, sorted_result
from .parser import COBOL_EXTENSIONS, COPYBOOK_EXTENSIONS, iter_cobol_files, parse_paths
from .parser_runtime import load_parser
from .resolver import DependencyIndex, resolve_project
from .semantics import build_semantic_facts


def analyze_project(
    root: Path,
    *,
    project_id: str,
    language_library: str | None = None,
    paths: Sequence[Path] | None = None,
    copybook_roots: Sequence[Path] = (),
    copybook_extensions: Sequence[str] = COPYBOOK_EXTENSIONS,
) -> tuple[AnalysisResult, DependencyIndex]:
    root = root.resolve()
    parser, runtime_info = load_parser(language_library)
    normalized_extensions = tuple(
        value.lower() if value.startswith(".") else f".{value.lower()}"
        for value in copybook_extensions
    )
    source_extensions = set(COBOL_EXTENSIONS) | set(normalized_extensions)
    source_paths = list(paths) if paths is not None else iter_cobol_files(root, source_extensions)
    for copybook_root in copybook_roots:
        resolved_root = copybook_root.expanduser().resolve()
        if resolved_root.is_dir():
            source_paths.extend(iter_cobol_files(resolved_root, normalized_extensions))
    source_paths = sorted(set(source_paths))
    parsed = parse_paths(source_paths, root, parser)
    resolved = resolve_project(parsed, copybook_extensions=normalized_extensions)
    nodes, edges, diagnostics = build_semantic_facts(resolved, project_id=project_id)
    for source in parsed:
        diagnostics.extend(source.diagnostics)
    result = sorted_result(
        project_id=project_id,
        root=str(root),
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
        runtime=runtime_info.to_dict(),
        processed_files=len(parsed),
        syntax_error_count=sum(item.tree_error_count for item in parsed),
    )
    return result, DependencyIndex.from_resolved(resolved)


def select_incremental_result(result: AnalysisResult, impacted_paths: Iterable[str]) -> AnalysisResult:
    impacted = {str(path).replace("\\", "/") for path in impacted_paths}
    nodes = tuple(node for node in result.nodes if node.file_path in impacted)
    selected_ids = {node.id for node in nodes}
    edges = tuple(edge for edge in result.edges if edge.source_id in selected_ids or edge.evidence.file in impacted)
    diagnostics = tuple(
        item for item in result.diagnostics
        if item.evidence is None or item.evidence.file in impacted
    )
    return replace(
        result,
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
        summary=replace(
            result.summary,
            processed_files=len({node.file_path for node in nodes if node.label == "File"}),
            node_count=len(nodes),
            edge_count=len(edges),
            diagnostic_count=len(diagnostics),
            invalidated_files=len(impacted),
        ),
    )


def graph_rows(result: AnalysisResult) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    nodes_by_label: dict[str, list[dict[str, Any]]] = {}
    for node in result.nodes:
        properties = dict(node.properties)
        properties.update({
            "id": node.id,
            "confidence": node.confidence,
            "source_start_byte": node.evidence.start_byte,
            "source_end_byte": node.evidence.end_byte,
        })
        nodes_by_label.setdefault(node.label, []).append({"id": node.id, "properties": properties})
    relations = [
        {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "rel_type": edge.relationship,
            "properties": {
                **dict(edge.properties),
                "id": edge.id,
                "confidence": edge.confidence,
                "dynamic": edge.dynamic,
                "file_path": edge.evidence.file,
                "start_line": edge.evidence.start_line,
                "end_line": edge.evidence.end_line,
            },
        }
        for edge in result.edges
    ]
    return nodes_by_label, relations


async def write_graph_facts(
    writer: Any,
    result: AnalysisResult,
    *,
    project_name: str | None = None,
    repo: str = "",
    build_system: str = "cobol",
) -> dict[str, int]:
    nodes_by_label, relations = graph_rows(result)
    counts: dict[str, int] = {}
    file_rows = []
    file_metadata_rows = nodes_by_label.pop("File", [])
    for row in file_metadata_rows:
        properties = row["properties"]
        file_rows.append({
            "id": row["id"],
            "path": properties["path"],
            "start_line": properties.get("start_line", 1),
            "end_line": properties.get("end_line", 1),
            "code": "",
            "comment": "",
            "summary": "COBOL source file",
            "note": "",
            "imports": [],
            "exports": [],
            "project_id": result.project_id,
            "project_name": project_name or result.project_id,
            "language": "cobol",
            "repo": repo,
            "build_system": build_system,
        })
    if file_rows:
        counts.update(
            await writer.write_all(
                files=file_rows,
                use_full_writers=True,
                files_variant="with_imports",
            )
        )
        counts["FileMetadata"] = await writer.write_nodes_batch(
            "cobol:FileMetadata",
            "UNWIND $rows AS row MATCH (n:File {id: row.id}) SET n += row.properties",
            file_metadata_rows,
        )
    for label, rows in sorted(nodes_by_label.items()):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label):
            raise ValueError(f"unsafe graph label: {label}")
        query = f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.properties"
        counts[label] = await writer.write_nodes_batch(f"cobol:{label}", query, rows)
    counts["relations"] = await writer.write_relations_typed(relations)
    return counts
