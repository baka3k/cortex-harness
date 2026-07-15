from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from tools.struts.java_validation import parse_java_validation_hooks
from tools.struts.models import (
    Diagnostic,
    PackageConfig,
    StrutsAnalysisResult,
    ValidationRule,
    WebFilterConfig,
)
from tools.struts.resolver import resolve_struts_project
from tools.struts.struts_xml_parser import parse_struts_xml_file
from tools.struts.validation_parser import parse_validation_xml_file
from tools.struts.web_xml_parser import parse_web_xml_file


_IGNORED_DIRS = {".git", ".gradle", ".idea", ".mvn", ".venv", "build", "node_modules", "out", "target", "venv"}


def _iter_files(root: Path) -> Iterable[Path]:
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(item for item in dirs if item not in _IGNORED_DIRS and not item.startswith("."))
        for name in sorted(files):
            yield Path(current_root) / name


def _selected(relative: str, selected_paths: Set[str]) -> bool:
    if not selected_paths:
        return True
    return any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in selected_paths)


def _resolve_include(project_root: Path, source_file: str, include: str) -> Optional[str]:
    candidates = [project_root / include, project_root / Path(source_file).parent / include]
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(project_root).as_posix()
        except ValueError:
            continue
        if resolved.is_file():
            return relative
    return None


def _load_struts_configs(
    project_root: Path,
    initial_files: Sequence[str],
) -> tuple[List[PackageConfig], Dict[str, str], List[Diagnostic]]:
    packages: List[PackageConfig] = []
    constants: Dict[str, str] = {}
    diagnostics: List[Diagnostic] = []
    queue = list(sorted(initial_files))
    visited: Set[str] = set()
    while queue:
        file_path = queue.pop(0)
        if file_path in visited:
            continue
        visited.add(file_path)
        parsed = parse_struts_xml_file(str(project_root), file_path)
        packages.extend(parsed.packages)
        constants.update(parsed.constants)
        diagnostics.extend(parsed.diagnostics)
        for include in parsed.includes:
            resolved = _resolve_include(project_root, file_path, include)
            if resolved is None:
                diagnostics.append(
                    Diagnostic(
                        "struts.config.include_missing",
                        f"Unable to resolve included Struts configuration {include!r}",
                        "warning",
                        file_path,
                    )
                )
            elif resolved not in visited:
                queue.append(resolved)
    return packages, constants, diagnostics


def run_struts_analysis(
    *,
    root: str,
    project_id: str,
    project_name: str = "",
    selected_paths: Optional[Sequence[str]] = None,
) -> StrutsAnalysisResult:
    """Analyze the XML-first Apache Struts 2 semantic model for a project."""

    project_root = Path(root).resolve()
    display_name = project_name or project_id
    selected = {item.replace("\\", "/").strip("/") for item in selected_paths or () if item}
    web_xml_files: List[str] = []
    struts_xml_files: List[str] = []
    validation_files: List[str] = []
    java_files: List[str] = []
    convention_detected = False

    if not project_root.is_dir():
        return StrutsAnalysisResult(
            project_id=project_id,
            project_name=display_name,
            root=str(project_root),
            diagnostics=(Diagnostic("struts.root.invalid", "Project root is not a directory", "error", str(project_root)),),
            coverage_status="empty",
        )

    for path in _iter_files(project_root):
        relative = path.relative_to(project_root).as_posix()
        if not _selected(relative, selected):
            continue
        lower_name = path.name.lower()
        if lower_name == "web.xml":
            web_xml_files.append(relative)
        elif lower_name.endswith("-validation.xml"):
            validation_files.append(relative)
        elif lower_name == "struts.xml" or lower_name == "struts-plugin.xml" or (
            lower_name.startswith("struts-") and lower_name.endswith(".xml")
        ):
            struts_xml_files.append(relative)
        elif lower_name.endswith(".java"):
            java_files.append(relative)
        elif lower_name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            try:
                convention_detected = convention_detected or "struts2-convention-plugin" in path.read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                pass

    diagnostics: List[Diagnostic] = []
    packages, constants, config_diagnostics = _load_struts_configs(project_root, struts_xml_files)
    diagnostics.extend(config_diagnostics)
    web_filters: List[WebFilterConfig] = []
    for file_path in sorted(web_xml_files):
        parsed = parse_web_xml_file(str(project_root), file_path)
        web_filters.extend(parsed.filters)
        diagnostics.extend(parsed.diagnostics)
    validation_rules: List[ValidationRule] = []
    for file_path in sorted(validation_files):
        parsed = parse_validation_xml_file(str(project_root), file_path)
        validation_rules.extend(parsed.rules)
        diagnostics.extend(parsed.diagnostics)
    for file_path in sorted(java_files):
        parsed = parse_java_validation_hooks(str(project_root), file_path)
        validation_rules.extend(parsed.rules)
        diagnostics.extend(parsed.diagnostics)

    if convention_detected:
        diagnostics.append(
            Diagnostic(
                "struts.convention.partial",
                "Convention Plugin detected; annotation and classpath-derived routes are outside the XML-first MVP",
                "warning",
            )
        )

    resolution = resolve_struts_project(
        project_id=project_id,
        project_name=display_name,
        module_id=project_id,
        packages=packages,
        constants=constants,
        web_filters=web_filters,
        validation_rules=validation_rules,
    )
    diagnostics.extend(resolution.diagnostics)
    has_inputs = bool(struts_xml_files or web_xml_files or validation_files)
    has_errors = any(item.severity == "error" for item in diagnostics)
    has_partial = any(item.severity == "warning" for item in diagnostics)
    if not has_inputs:
        coverage = "empty"
    elif has_errors or has_partial or not packages:
        coverage = "partial"
    else:
        coverage = "complete"
    unique_diagnostics = {
        (item.code, item.message, item.severity, item.file_path): item for item in diagnostics
    }
    return StrutsAnalysisResult(
        project_id=project_id,
        project_name=display_name,
        root=str(project_root),
        semantic_facts=resolution.facts,
        relationships=resolution.relationships,
        diagnostics=tuple(
            sorted(unique_diagnostics.values(), key=lambda item: (item.file_path, item.code, item.message))
        ),
        coverage_status=coverage,
    )


__all__ = ["run_struts_analysis"]
