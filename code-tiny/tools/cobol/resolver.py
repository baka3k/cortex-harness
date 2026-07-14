"""Copybook, program, paragraph, and data-symbol resolution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping, Tuple

from .models import Diagnostic, ParsedDataItem, ParsedFile
from .parser import COPYBOOK_EXTENSIONS


DEPENDENCY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResolvedProject:
    files: Tuple[ParsedFile, ...]
    programs: Mapping[str, ParsedFile]
    copybooks: Mapping[str, ParsedFile]
    include_graph: Mapping[str, Tuple[str, ...]]
    include_closure: Mapping[str, Tuple[str, ...]]
    diagnostics: Tuple[Diagnostic, ...]

    def imported_data_items(self, source_path: str) -> Tuple[ParsedDataItem, ...]:
        by_path = {item.path: item for item in self.files}
        values: list[ParsedDataItem] = []
        for path in self.include_closure.get(source_path, ()):
            target = by_path.get(path)
            if target:
                values.extend(target.data_items)
        return tuple(values)


@dataclass(frozen=True)
class DependencyIndex:
    dependencies: Mapping[str, Tuple[str, ...]]

    @classmethod
    def from_resolved(cls, project: ResolvedProject) -> "DependencyIndex":
        return cls({owner: tuple(sorted(targets)) for owner, targets in sorted(project.include_graph.items())})

    def impacted_files(self, changed: Iterable[str], deleted: Iterable[str] = ()) -> set[str]:
        seeds = {str(path).replace("\\", "/") for path in (*tuple(changed), *tuple(deleted))}
        reverse: dict[str, set[str]] = {}
        for owner, targets in self.dependencies.items():
            for target in targets:
                reverse.setdefault(target, set()).add(owner)
        impacted = set(seeds)
        queue = list(sorted(seeds))
        while queue:
            target = queue.pop(0)
            for owner in sorted(reverse.get(target, ())):
                if owner not in impacted:
                    impacted.add(owner)
                    queue.append(owner)
        return impacted

    def save(self, path: Path) -> None:
        payload = {"schema_version": DEPENDENCY_SCHEMA_VERSION, "dependencies": self.dependencies}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "DependencyIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != DEPENDENCY_SCHEMA_VERSION:
            raise ValueError("unsupported COBOL dependency index schema")
        values = payload.get("dependencies", {})
        if not isinstance(values, dict):
            raise ValueError("invalid COBOL dependency index")
        return cls({str(owner): tuple(sorted(map(str, targets))) for owner, targets in values.items()})


def resolve_project(
    files: Iterable[ParsedFile],
    *,
    copybook_extensions: Iterable[str] = COPYBOOK_EXTENSIONS,
) -> ResolvedProject:
    ordered = tuple(sorted(files, key=lambda item: item.path))
    programs: dict[str, ParsedFile] = {}
    copybooks: dict[str, ParsedFile] = {}
    copybook_candidates: dict[str, list[ParsedFile]] = {}
    extension_order = {
        (value.lower() if value.startswith(".") else f".{value.lower()}"): index
        for index, value in enumerate(copybook_extensions)
    }
    diagnostics: list[Diagnostic] = []
    for source in ordered:
        seen_paragraphs: set[str] = set()
        for paragraph in source.paragraphs:
            if paragraph.name in seen_paragraphs:
                diagnostics.append(
                    Diagnostic(
                        "COBOL_DUPLICATE_PARAGRAPH",
                        f"paragraph {paragraph.name} is defined more than once in {source.path}",
                        evidence=paragraph.evidence,
                    )
                )
            seen_paragraphs.add(paragraph.name)
        if source.program_name:
            existing = programs.get(source.program_name)
            if existing:
                diagnostics.append(
                    Diagnostic(
                        "COBOL_DUPLICATE_PROGRAM",
                        f"program {source.program_name} is also defined in {existing.path}",
                        evidence=source.paragraphs[0].evidence if source.paragraphs else None,
                    )
                )
            else:
                programs[source.program_name] = source
        if source.is_copybook:
            aliases = {Path(source.path).stem.upper(), Path(source.path).name.upper()}
            for alias in aliases:
                copybook_candidates.setdefault(alias, []).append(source)

    for alias, candidates in sorted(copybook_candidates.items()):
        ranked = sorted(
            candidates,
            key=lambda item: (extension_order.get(Path(item.path).suffix.lower(), len(extension_order)), item.path),
        )
        copybooks[alias] = ranked[0]
        if len(ranked) > 1 and "." not in alias:
            diagnostics.append(
                Diagnostic(
                    "COBOL_COPYBOOK_AMBIGUOUS",
                    f"copybook {alias} matched multiple files; selected {ranked[0].path}",
                    details={"candidates": [item.path for item in ranked]},
                )
            )

    include_graph: dict[str, Tuple[str, ...]] = {}
    for source in ordered:
        targets: list[str] = []
        for include in source.copies:
            target = copybooks.get(include.name.upper())
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        "COBOL_COPYBOOK_NOT_FOUND",
                        f"copybook {include.name} was not found",
                        evidence=include.evidence,
                        details={"copybook": include.name},
                    )
                )
                continue
            targets.append(target.path)
        include_graph[source.path] = tuple(dict.fromkeys(targets))

    closure: dict[str, Tuple[str, ...]] = {}
    sources_by_path = {item.path: item for item in ordered}
    for source in ordered:
        discovered: list[str] = []

        def visit(owner: str, chain: tuple[str, ...]) -> None:
            for target in include_graph.get(owner, ()):
                if target in chain:
                    include_evidence = next(
                        (
                            include.evidence
                            for include in sources_by_path[owner].copies
                            if Path(target).stem.upper() == include.name.upper()
                        ),
                        None,
                    )
                    diagnostics.append(
                        Diagnostic(
                            "COBOL_COPYBOOK_CYCLE",
                            "copybook cycle: " + " -> ".join((*chain, target)),
                            evidence=include_evidence,
                            details={"chain": (*chain, target)},
                        )
                    )
                    continue
                if target not in discovered:
                    discovered.append(target)
                    visit(target, (*chain, target))

        visit(source.path, (source.path,))
        closure[source.path] = tuple(discovered)

    for source in ordered:
        for binding in source.file_bindings:
            if not binding.assignment or not binding.has_description:
                diagnostics.append(
                    Diagnostic(
                        "COBOL_FILE_BINDING_PARTIAL",
                        f"file {binding.name} is missing {'assignment' if not binding.assignment else 'FD/SD description'}",
                        evidence=binding.evidence,
                    )
                )
    return ResolvedProject(
        ordered,
        dict(sorted(programs.items())),
        dict(sorted(copybooks.items())),
        dict(sorted(include_graph.items())),
        dict(sorted(closure.items())),
        tuple(diagnostics),
    )
