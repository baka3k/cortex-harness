"""Versioned import dependency cache for incremental Dart analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Dict, Iterable, Mapping, Set

from .models import AnalysisFacts, NodeRecord, SummaryRecord


CACHE_VERSION = 1
DEPENDENCY_RELATIONSHIPS = frozenset({"IMPORTS", "EXPORTS", "HAS_PART", "PART_OF"})


@dataclass
class DependencyIndex:
    dependencies: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def from_facts(cls, facts: AnalysisFacts) -> "DependencyIndex":
        owner = {node.identity: node.evidence.file for node in facts.nodes}
        dependencies: Dict[str, Set[str]] = {}
        for edge in facts.edges:
            if edge.relationship not in DEPENDENCY_RELATIONSHIPS:
                continue
            source = owner.get(edge.source)
            target = owner.get(edge.target)
            if source and target and source != target:
                dependencies.setdefault(source, set()).add(target)
        return cls(dependencies)

    def impacted_files(self, changed: Iterable[str], deleted: Iterable[str] = ()) -> Set[str]:
        impacted = {str(path) for path in changed} | {str(path) for path in deleted}
        reverse: Dict[str, Set[str]] = {}
        for source, targets in self.dependencies.items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)
        queue = list(impacted)
        while queue:
            target = queue.pop()
            for dependent in reverse.get(target, set()):
                if dependent not in impacted:
                    impacted.add(dependent)
                    queue.append(dependent)
        return impacted

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "version": CACHE_VERSION,
            "dependencies": {key: sorted(values) for key, values in sorted(self.dependencies.items())},
        }
        target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "DependencyIndex":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(value.get("version", -1)) != CACHE_VERSION:
            raise ValueError(
                f"unsupported Dart dependency cache version {value.get('version')!r}; expected {CACHE_VERSION}"
            )
        raw = value.get("dependencies", {})
        if not isinstance(raw, Mapping):
            raise ValueError("Dart dependency cache dependencies must be an object")
        return cls({str(key): {str(item) for item in values} for key, values in raw.items()})


def select_incremental_facts(facts: AnalysisFacts, paths: Iterable[str]) -> AnalysisFacts:
    """Select facts owned by impacted files and retain endpoints as non-writing references."""
    impacted = {str(path) for path in paths}
    if not impacted:
        return AnalysisFacts(
            header=facts.header,
            nodes=(),
            edges=(),
            diagnostics=(),
            summary=SummaryRecord(
                processed_files=0,
                skipped_files=facts.summary.processed_files,
                error_count=0,
                elapsed_ms=facts.summary.elapsed_ms,
            ),
        )
    nodes_by_identity = {node.identity: node for node in facts.nodes}
    owned = {node.identity: node for node in facts.nodes if node.evidence.file in impacted}
    edges = tuple(edge for edge in facts.edges if edge.evidence.file in impacted)
    endpoint_ids = {identity for edge in edges for identity in (edge.source, edge.target)}
    selected = dict(owned)
    for identity in endpoint_ids - owned.keys():
        node = nodes_by_identity.get(identity)
        if node is None:
            continue
        selected[identity] = NodeRecord(
            identity=node.identity,
            kind=node.kind,
            properties={**node.properties, "_reference_only": True},
            evidence=node.evidence,
        )
    diagnostics = tuple(
        item for item in facts.diagnostics if item.evidence is None or item.evidence.file in impacted
    )
    processed = len({node.evidence.file for node in owned.values() if node.kind == "file"})
    return AnalysisFacts(
        header=facts.header,
        nodes=tuple(sorted(selected.values(), key=lambda node: node.identity)),
        edges=edges,
        diagnostics=diagnostics,
        summary=SummaryRecord(
            processed_files=processed,
            skipped_files=max(0, facts.summary.processed_files - processed),
            error_count=sum(item.severity == "error" for item in diagnostics),
            elapsed_ms=facts.summary.elapsed_ms,
        ),
    )
