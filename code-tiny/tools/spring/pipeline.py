from __future__ import annotations

import os
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from tools.common.git_diff import load_manifest_paths
from tools.spring.adapters import adapters_for_languages
from tools.spring.config import parse_config_file
from tools.spring.detector import SpringProjectDetector, _EXCLUDED_DIRS, safe_rel_path
from tools.spring.extractors import (
    extract_core_facts,
    extract_crosscutting_facts,
    extract_messaging_facts,
    extract_persistence_facts,
    extract_security_facts,
)
from tools.spring.models import (
    ConfigValue,
    Diagnostic,
    LanguageSourceFact,
    SourceSpan,
    SpringAnalysisResult,
    SpringFact,
    SpringRelationship,
    SpringModule,
)
from tools.spring.source_scanner import scan_source_units


def run_spring_foundation(
    *,
    root: str,
    project_id: str,
    project_name: str,
    languages: Sequence[str],
    incremental: bool = False,
    changed_files_manifest: str = "",
    deleted_files_manifest: str = "",
) -> SpringAnalysisResult:
    root_abs = os.path.abspath(root)
    detector = SpringProjectDetector(root_abs)
    modules_raw = detector.discover_modules(languages=languages)

    changed_paths: Set[str] = set()
    if incremental and changed_files_manifest:
        changed_paths = {safe_rel_path(path) for path in load_manifest_paths(changed_files_manifest, root_abs)}
    deleted_paths: Set[str] = set()
    if incremental and deleted_files_manifest:
        deleted_paths = {safe_rel_path(path) for path in load_manifest_paths(deleted_files_manifest, root_abs)}

    module_objects: List[SpringModule] = []
    config_values: List[ConfigValue] = []
    diagnostics: List[Diagnostic] = []
    source_paths: Set[str] = set()
    for item in modules_raw:
        config_files = tuple(item["config_files"])  # type: ignore[index]
        build_files = tuple(item["build_files"])  # type: ignore[index]
        module_languages = tuple(item["languages"])  # type: ignore[index]
        module_objects.append(
            SpringModule(
                root=root_abs,
                rel_path=str(item["rel_path"]),
                languages=module_languages,
                build_files=build_files,
                config_files=config_files,
                evidence=tuple(item["evidence"]),  # type: ignore[index]
                confidence=float(item["confidence"]),
            )
        )
        for rel_path in config_files:
            if changed_paths and rel_path not in changed_paths:
                continue
            values, config_diags = parse_config_file(root_abs, rel_path)
            config_values.extend(values)
            diagnostics.extend(config_diags)

    for abs_path, rel_path in _iter_source_files(root_abs):
        detection = detector.detect_path(rel_path)
        if not detection.is_spring:
            continue
        if changed_paths and rel_path not in changed_paths:
            continue
        source_paths.add(rel_path)

    language_facts: List[LanguageSourceFact] = []
    for adapter in adapters_for_languages(languages):
        language_facts.extend(adapter.collect(root_abs, source_paths))

    source_units = scan_source_units(root_abs, source_paths)
    semantic_facts: List[SpringFact] = _semantic_facts_from_foundation(
        project_id=project_id,
        project_name=project_name,
        modules=module_objects,
        config_values=config_values,
    )
    relationships: List[SpringRelationship] = []
    config_index = _config_index(config_values)
    for extractor, kwargs in (
        (extract_core_facts, {}),
        (extract_persistence_facts, {}),
        (extract_messaging_facts, {"config_index": config_index}),
        (extract_security_facts, {}),
        (extract_crosscutting_facts, {}),
    ):
        facts, rels = extractor(
            units=source_units,
            project_id=project_id,
            project_name=project_name,
            **kwargs,
        )
        semantic_facts.extend(facts)
        relationships.extend(rels)

    if deleted_paths:
        diagnostics.append(
            Diagnostic(
                "spring.incremental.deleted_files",
                f"{len(deleted_paths)} deleted path(s) require Spring graph cleanup",
                "info",
            )
        )

    return SpringAnalysisResult(
        project_id=project_id,
        project_name=project_name,
        root=root_abs,
        modules=tuple(module_objects),
        config_values=tuple(config_values),
        language_facts=tuple(language_facts),
        semantic_facts=tuple(_dedupe_facts(semantic_facts)),
        relationships=tuple(_dedupe_relationships(relationships)),
        diagnostics=tuple(diagnostics),
    )


def _semantic_facts_from_foundation(
    *,
    project_id: str,
    project_name: str,
    modules: Sequence[SpringModule],
    config_values: Sequence[ConfigValue],
) -> List[SpringFact]:
    facts: List[SpringFact] = []
    for module in modules:
        stable_module = (module.rel_path or ".").replace("/", ".").strip(".") or "root"
        source_file = module.build_files[0] if module.build_files else (module.config_files[0] if module.config_files else module.rel_path)
        facts.append(
            SpringFact(
                kind="SpringModule",
                stable_id=f"spring_module::{project_id}::{stable_module}",
                name=module.rel_path or ".",
                source=SourceSpan(source_file or ".", 1, 1),
                project_id=project_id,
                project_name=project_name,
                confidence=module.confidence,
                resolution_status="resolved",
                properties={
                    "module_path": module.rel_path,
                    "languages": list(module.languages),
                    "build_files": list(module.build_files),
                    "config_files": list(module.config_files),
                    "evidence": list(module.evidence),
                },
            )
        )
    for config in config_values:
        key_hash = _stable_hash(f"{config.source.file_path}:{config.profile}:{config.key}")
        facts.append(
            SpringFact(
                kind="SpringConfiguration",
                stable_id=f"spring_config_value::{project_id}::{key_hash}",
                name=config.key,
                source=config.source,
                project_id=project_id,
                project_name=project_name,
                confidence=1.0,
                raw_value=config.raw_value,
                resolved_value=str(config.value),
                properties={
                    "config_key": config.key,
                    "config_value": config.value,
                    "profile": config.profile,
                },
            )
        )
    return facts


def _iter_source_files(root: str) -> Iterable[Tuple[str, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith((".java", ".kt", ".kts")):
                continue
            abs_path = os.path.join(dirpath, name)
            rel_path = safe_rel_path(os.path.relpath(abs_path, root))
            if "/src/" not in f"/{rel_path}":
                continue
            yield abs_path, rel_path


def _stable_hash(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _config_index(values: Sequence[ConfigValue]) -> Dict[str, List[object]]:
    index: Dict[str, List[object]] = {}
    for item in values:
        index.setdefault(item.key, []).append(item.value)
    return index


def _dedupe_facts(facts: Sequence[SpringFact]) -> List[SpringFact]:
    seen = set()
    out: List[SpringFact] = []
    for item in facts:
        if item.stable_id in seen:
            continue
        seen.add(item.stable_id)
        out.append(item)
    return out


def _dedupe_relationships(relationships: Sequence[SpringRelationship]) -> List[SpringRelationship]:
    seen = set()
    out: List[SpringRelationship] = []
    for item in relationships:
        key = (item.type, item.from_id, item.to_id, item.project_id, item.source.file_path, item.source.start_line)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
