"""Shared Android analysis utilities for Java & Kotlin analyzers.

This module contains common dataclasses and utility functions used by both
android_java_analyzer.py and android_kotlin_analyzer.py to eliminate code
duplication and ensure consistent behavior.

Extracted from android_java_analyzer.py and android_kotlin_analyzer.py
on 2026-04-03 as part of code optimization effort.
"""

from __future__ import annotations

import fnmatch
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ============================================================================
# Shared Dataclasses
# ============================================================================

@dataclass
class AndroidManifestDef:
    """Represents an AndroidManifest.xml file."""
    symbol_id: str
    package_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidComponentDef:
    """Represents an Android component (Activity, Service, Receiver, Provider)."""
    symbol_id: str
    name: str
    component_type: str
    class_name: Optional[str]
    exported: Optional[bool]
    process: Optional[str]
    permission: Optional[str]
    enabled: Optional[bool]
    direct_boot_aware: Optional[bool]
    target_activity: Optional[str]
    intent_actions: List[str]
    intent_categories: List[str]
    intent_data: List[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidResourceDef:
    """Represents an Android resource (string, drawable, layout, etc.)."""
    symbol_id: str
    name: str
    res_type: str
    file_path: str
    qualifier: str
    summary: str = ""
    note: str = ""


@dataclass
class GradleModuleDef:
    """Represents a Gradle module."""
    symbol_id: str
    name: str
    module_path: str
    module_type: str
    namespace: Optional[str]
    application_id: Optional[str]
    file_path: str
    summary: str = ""
    note: str = ""


@dataclass
class GradleDependencyDef:
    """Represents a Gradle dependency."""
    symbol_id: str
    coordinate: str
    group: Optional[str]
    artifact: Optional[str]
    version: Optional[str]
    summary: str = ""
    note: str = ""


@dataclass
class AndroidAnnotationDef:
    """Represents an Android annotation."""
    symbol_id: str
    name: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidNavRouteDef:
    """Represents a navigation route."""
    symbol_id: str
    route: str
    file_path: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidIntentActionDef:
    """Represents an Intent action."""
    symbol_id: str
    action: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidHandlerMessageDef:
    """Represents a Handler message."""
    symbol_id: str
    token: str
    summary: str = ""
    note: str = ""


# ============================================================================
# Shared Constants
# ============================================================================

# Common directories to skip during Android source scanning
_ANDROID_SKIP_DIRS = {
    # Version control
    ".git", ".hg", ".svn",

    # Gradle
    ".gradle", "gradleCache", ".mvn",

    # IDE
    ".idea", ".vscode", ".settings", ".capture", ".eclipse",

    # Android specific
    ".android", ".cxx", ".externalNativeBuild",

    # Build outputs
    "build", "out", "bin", "dist", "buildSrc",

    # Generated
    "gen", "generated",

    # Node (mixed projects)
    "node_modules", "dist",

    # Cache
    ".cache", ".parcel-cache", "__pycache__",

    # Testing
    "coverage", ".test-results", "test-results", "junit", "androidTest",

    # Lint
    "lint-results", "lint-baseline.xml",

    # Temporary
    "tmp", "temp", ".tmp", "tmpdir",

    # OS specific
    ".DS_Store", "Thumbs.db",

    # Misc project files
    ".project", ".classpath", "*.iml", "*.ipr", "*.iws",

    # Compiled/outputs
    "*.class", "*.apk", "*.aab", "*.ap_",

    # Gradle wrapper
    "mvnw", "mvnw.cmd", "gradlew", "gradlew.bat",
}


# ============================================================================
# Shared Utility Functions
# ============================================================================

def _is_skipped_android_name(name: str) -> bool:
    """Check if a directory/file name should be skipped during scanning."""
    if name.startswith("."):
        return True
    for pattern in _ANDROID_SKIP_DIRS:
        if name == pattern or fnmatch.fnmatch(name, pattern):
            return True
    return False


def _android_attr(elem: ET.Element, name: str) -> Optional[str]:
    """Extract an Android attribute from an XML element, handling namespaces."""
    return (
        elem.get(name)
        or elem.get(f"{{http://schemas.android.com/apk/res/android}}{name}")
        or elem.get(f"android:{name}")
    )


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    """Parse a boolean value from string, handling various formats."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    return None


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _resolve_android_class_name(name: Optional[str], package_name: Optional[str]) -> Optional[str]:
    """Resolve a relative Android class name to fully qualified name."""
    if not name:
        return None
    if name.startswith("."):
        return f"{package_name}{name}" if package_name else name[1:]
    if "." in name:
        return name
    return f"{package_name}.{name}" if package_name else name


def _display_android_component_name(raw_name: Optional[str], class_name: Optional[str]) -> str:
    """Return a stable UI-friendly component name (never starts with '.')."""
    if class_name:
        simple = class_name.strip().split(".")[-1]
        if simple:
            return simple
    value = (raw_name or "").strip()
    if value.startswith("."):
        return value[1:]
    return value


def _manifest_symbol_id(rel_path: str) -> str:
    """Generate a unique symbol ID for an AndroidManifest file."""
    return f"manifest::{rel_path}"


def _component_symbol_id(component_type: str, class_name: Optional[str], rel_path: str, line: int) -> str:
    """Generate a unique symbol ID for an Android component."""
    base = class_name or "unknown"
    return f"component::{component_type}:{base}@{rel_path}:{line}"


def _resource_symbol_id(res_type: str, name: str) -> str:
    """Generate a unique symbol ID for an Android resource."""
    return f"resource::{res_type}/{name}"


def _module_symbol_id(module_path: str) -> str:
    """Generate a unique symbol ID for a Gradle module."""
    return f"module::{module_path}"


def _dependency_symbol_id(coordinate: str) -> str:
    """Generate a unique symbol ID for a Gradle dependency."""
    return f"dependency::{coordinate}"


def _annotation_symbol_id(name: str) -> str:
    """Generate a unique symbol ID for an annotation."""
    return f"annotation::{name}"


def _nav_route_symbol_id(route: str) -> str:
    """Generate a unique symbol ID for a navigation route."""
    return f"nav_route::{route}"


def _intent_action_symbol_id(action: str) -> str:
    """Generate a unique symbol ID for an Intent action."""
    return f"intent_action::{action}"


def _handler_message_symbol_id(token: str) -> str:
    """Generate a unique symbol ID for a Handler message."""
    return f"handler_message::{token}"


def _extract_string_literals(text: str) -> List[str]:
    """Extract string literals from code, handling escape sequences."""
    if not text:
        return []
    pattern = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
    values: List[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        value = bytes(match.group(1), "utf-8").decode("unicode_escape")
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _extract_class_refs(text: str) -> List[str]:
    """Extract class references from code (e.g., MyClass.class)."""
    refs: List[str] = []
    seen: set[str] = set()
    # Match patterns like "MyClass.class" or "com.example.MyClass.class"
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_$.]*)\s*\.class\b", text):
        candidate = match.group(1).replace("$", ".")
        if candidate not in seen:
            seen.add(candidate)
            refs.append(candidate)
    # Match ComponentName usage
    for match in re.finditer(r"new\s+ComponentName\s*\([^\)]*?,\s*\"([^\"]+)\"\s*\)", text):
        candidate = match.group(1)
        if candidate not in seen:
            seen.add(candidate)
            refs.append(candidate)
    return refs


def _extract_register_receiver_target(arg_text: str) -> Optional[str]:
    """Extract receiver class name from registerReceiver call."""
    match = re.search(r"new\s+([A-Za-z_][A-Za-z0-9_$.]*)\s*\(", arg_text)
    if match:
        return match.group(1).replace("$", ".")
    return None


def _extract_intentfilter_actions(text: str) -> List[str]:
    """Extract Intent actions from IntentFilter construction."""
    return _extract_action_values(text)


def _extract_action_constants(text: str) -> List[str]:
    """Extract Intent action constants (e.g., Intent.ACTION_VIEW)."""
    constants: List[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.ACTION_[A-Za-z0-9_]+\b")
    for match in pattern.finditer(text):
        value = match.group(0)
        if value not in seen:
            seen.add(value)
            constants.append(value)
    return constants


def _extract_action_values(text: str) -> List[str]:
    """Extract all action values from text (strings and constants)."""
    values: List[str] = []
    seen: set[str] = set()
    for value in _extract_string_literals(text) + _extract_action_constants(text):
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _extract_balanced_args(text: str, open_paren_index: int) -> Tuple[Optional[str], Optional[int]]:
    """
    Extract balanced arguments from a function call.
    Returns (args_text, closing_paren_index) or (None, None) if failed.
    """
    if open_paren_index < 0 or open_paren_index >= len(text) or text[open_paren_index] != "(":
        return None, None
    depth = 0
    in_single = False
    in_double = False
    escaped = False
    for index in range(open_paren_index, len(text)):
        ch = text[index]
        if escaped:
            escaped = False
            continue
        if in_single:
            if ch == "\\":
                escaped = True
            elif ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            continue
        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_index + 1 : index], index
    return None, None


def _iter_named_calls(text: str, call_names: set[str]) -> Iterable[Tuple[str, str]]:
    """
    Iterate over named function calls in text.
    Yields (function_name, args_text) tuples.
    """
    if not call_names:
        return
    name_part = "|".join(re.escape(name) for name in sorted(call_names, key=len, reverse=True))
    pattern = re.compile(rf"\b({name_part})\s*\(")
    for match in pattern.finditer(text):
        open_paren = text.find("(", match.start())
        args_text, _ = _extract_balanced_args(text, open_paren)
        if args_text is None:
            continue
        yield match.group(1), args_text


def _iter_member_calls(text: str, method_names: set[str]) -> Iterable[Tuple[str, str, str]]:
    """
    Iterate over member method calls in text.
    Yields (receiver, method_name, args_text) tuples.
    """
    if not method_names:
        return
    name_part = "|".join(re.escape(name) for name in sorted(method_names, key=len, reverse=True))
    pattern = re.compile(rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*({name_part})\s*\(")
    for match in pattern.finditer(text):
        open_paren = text.find("(", match.start())
        args_text, _ = _extract_balanced_args(text, open_paren)
        if args_text is None:
            continue
        yield match.group(1), match.group(2), args_text


def _parse_android_manifest(path: str, root: str) -> Tuple[AndroidManifestDef, List[AndroidComponentDef]]:
    """
    Parse an AndroidManifest.xml file and extract manifest and component definitions.
    Returns (manifest_def, list of component_defs).
    """
    rel_path = os.path.relpath(path, root).replace("\\", "/")
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read()
    end_line = content.count("\n") + 1
    package_name = None
    components: List[AndroidComponentDef] = []

    try:
        tree = ET.parse(path)
        root_elem = tree.getroot()
        package_name = root_elem.get("package")

        for elem in root_elem.iter():
            tag = _strip_ns(elem.tag)
            if tag not in {"activity", "activity-alias", "service", "receiver", "provider"}:
                continue
            raw_name = _android_attr(elem, "name")
            class_name = _resolve_android_class_name(raw_name, package_name)
            display_name = _display_android_component_name(raw_name, class_name)
            exported = _parse_bool(_android_attr(elem, "exported"))
            enabled = _parse_bool(_android_attr(elem, "enabled"))
            direct_boot = _parse_bool(_android_attr(elem, "directBootAware"))
            process = _android_attr(elem, "process")
            permission = _android_attr(elem, "permission")
            target_activity = _android_attr(elem, "targetActivity")
            start_line = getattr(elem, "sourceline", 0) or 0
            code = ET.tostring(elem, encoding="unicode")
            intent_actions: List[str] = []
            intent_categories: List[str] = []
            intent_data: List[str] = []

            for child in list(elem):
                if _strip_ns(child.tag) != "intent-filter":
                    continue
                for sub in list(child):
                    sub_tag = _strip_ns(sub.tag)
                    if sub_tag == "action":
                        action_name = _android_attr(sub, "name")
                        if action_name:
                            intent_actions.append(action_name)
                    elif sub_tag == "category":
                        category_name = _android_attr(sub, "name")
                        if category_name:
                            intent_categories.append(category_name)
                    elif sub_tag == "data":
                        data_parts: List[str] = []
                        for key in (
                            "scheme",
                            "host",
                            "port",
                            "path",
                            "pathPrefix",
                            "pathPattern",
                            "mimeType",
                        ):
                            value = _android_attr(sub, key)
                            if value:
                                data_parts.append(f"{key}={value}")
                        if data_parts:
                            intent_data.append(",".join(data_parts))

            component_id = _component_symbol_id(tag, class_name, rel_path, start_line)
            components.append(
                AndroidComponentDef(
                    symbol_id=component_id,
                    name=display_name,
                    component_type=tag,
                    class_name=class_name,
                    exported=exported,
                    process=process,
                    permission=permission,
                    enabled=enabled,
                    direct_boot_aware=direct_boot,
                    target_activity=target_activity,
                    intent_actions=intent_actions,
                    intent_categories=intent_categories,
                    intent_data=intent_data,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=start_line,
                    code=code,
                )
            )
    except ET.ParseError:
        pass

    manifest_def = AndroidManifestDef(
        symbol_id=_manifest_symbol_id(rel_path),
        package_name=package_name,
        file_path=rel_path,
        start_line=1,
        end_line=end_line,
        code=content,
    )
    return manifest_def, components


def _scan_android_manifest_files(root: str, skip_dirs: Optional[set] = None) -> List[str]:
    """
    Scan for AndroidManifest.xml files in the given root directory.
    Optionally skips directories specified in skip_dirs (uses _ANDROID_SKIP_DIRS if None).
    """
    if skip_dirs is None:
        skip_dirs = _ANDROID_SKIP_DIRS

    manifests: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _is_skipped_android_name_custom(name, skip_dirs)]
        for name in filenames:
            if _is_skipped_android_name_custom(name, skip_dirs):
                continue
            if name == "AndroidManifest.xml":
                manifests.append(os.path.join(dirpath, name))
    return sorted(manifests)


def _is_skipped_android_name_custom(name: str, skip_dirs: set) -> bool:
    """Check if a directory/file name should be skipped (custom skip_dirs version)."""
    if name.startswith("."):
        return True
    for pattern in skip_dirs:
        if name == pattern or fnmatch.fnmatch(name, pattern):
            return True
    return False


def _normalize_rel_path(path: str) -> str:
    """Normalize to slash-separated relative path."""
    normalized = (path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _scan_android_directory_paths(root: str, skip_dirs: Optional[set] = None) -> List[str]:
    """Scan the project tree and return relative directory paths (excluding root)."""
    if skip_dirs is None:
        skip_dirs = _ANDROID_SKIP_DIRS

    directory_paths: List[str] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _is_skipped_android_name_custom(name, skip_dirs)]
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir in {".", ""}:
            continue
        normalized = _normalize_rel_path(rel_dir)
        if normalized:
            directory_paths.append(normalized)
    return sorted(set(directory_paths))


def _directory_symbol_id(dir_path: str) -> str:
    """Generate a stable symbol ID for directory nodes."""
    return f"dir::{dir_path}"


def _build_directory_nodes_and_relations(
    *,
    file_paths: Iterable[str],
    directory_paths: Optional[Iterable[str]] = None,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build Directory nodes and CONTAINS edges for a file tree."""
    normalized_files: List[str] = []
    for file_path in file_paths:
        rel = _normalize_rel_path(file_path)
        if rel:
            normalized_files.append(rel)
    normalized_files = sorted(set(normalized_files))

    dir_paths: set[str] = set()
    for dir_path in directory_paths or []:
        normalized = _normalize_rel_path(dir_path)
        if not normalized:
            continue
        current = normalized
        while current:
            dir_paths.add(current)
            if "/" not in current:
                break
            current = current.rsplit("/", 1)[0]

    for rel in normalized_files:
        current = _normalize_rel_path(os.path.dirname(rel))
        while current:
            dir_paths.add(current)
            if "/" not in current:
                break
            current = current.rsplit("/", 1)[0]

    directory_rows: List[Dict[str, Any]] = []
    for dir_path in sorted(dir_paths):
        parts = [part for part in dir_path.split("/") if part]
        directory_rows.append(
            {
                "id": _directory_symbol_id(dir_path),
                "name": parts[-1] if parts else dir_path,
                "path": dir_path,
                "depth": len(parts),
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            }
        )

    relation_seen: set[Tuple[str, str]] = set()
    relation_rows: List[Dict[str, Any]] = []

    for dir_path in sorted(dir_paths):
        child_id = _directory_symbol_id(dir_path)
        parent_dir = dir_path.rsplit("/", 1)[0] if "/" in dir_path else ""
        parent_id = _directory_symbol_id(parent_dir) if parent_dir else project_id
        key = (parent_id, child_id)
        if key in relation_seen:
            continue
        relation_seen.add(key)
        relation_rows.append(
            {
                "source_id": parent_id,
                "target_id": child_id,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )

    for rel in normalized_files:
        parent_dir = _normalize_rel_path(os.path.dirname(rel))
        if not parent_dir:
            continue
        source_id = _directory_symbol_id(parent_dir)
        key = (source_id, rel)
        if key in relation_seen:
            continue
        relation_seen.add(key)
        relation_rows.append(
            {
                "source_id": source_id,
                "target_id": rel,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )

    return directory_rows, relation_rows
