from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.analyzer_cache import safe_cache_root
from tools.common.cloc_stats import collect_cloc_stats, normalize_cloc_payload, write_cloc_stats_to_neo4j
from tools.common.git_diff import load_manifest_paths
from tools.common.message_scan import default_message_collection_name, run_message_scan_pipeline
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.android import android_common
from tools.java import java_analyzer as java_base

# Import shared Android dataclasses
AndroidManifestDef = android_common.AndroidManifestDef
AndroidComponentDef = android_common.AndroidComponentDef


def _scan_android_manifest_files(root: str) -> List[str]:
    """Scan for AndroidManifest.xml files using shared utility."""
    skip_patterns = getattr(java_base, "_SCAN_SKIP_DIRS", set())
    return android_common._scan_android_manifest_files(root, skip_dirs=skip_patterns)


def _parse_android_manifest(path: str, root: str) -> Tuple[AndroidManifestDef, List[AndroidComponentDef]]:
    """Parse AndroidManifest.xml using shared utility."""
    return android_common._parse_android_manifest(path, root)


def _extract_string_literals(text: str) -> List[str]:
    """Extract string literals using shared utility."""
    return android_common._extract_string_literals(text)


def _extract_class_refs(text: str) -> List[str]:
    """Extract class references using shared utility."""
    return android_common._extract_class_refs(text)


def _extract_register_receiver_target(arg_text: str) -> Optional[str]:
    """Extract receiver class name using shared utility."""
    return android_common._extract_register_receiver_target(arg_text)


def _extract_intentfilter_actions(text: str) -> List[str]:
    """Extract Intent filter actions using shared utility."""
    return android_common._extract_intentfilter_actions(text)


def _extract_action_constants(text: str) -> List[str]:
    """Extract action constants using shared utility."""
    return android_common._extract_action_constants(text)


def _extract_action_values(text: str) -> List[str]:
    """Extract all action values using shared utility."""
    return android_common._extract_action_values(text)


def _extract_balanced_args(text: str, open_paren_index: int) -> Tuple[Optional[str], Optional[int]]:
    """Extract balanced arguments using shared utility."""
    return android_common._extract_balanced_args(text, open_paren_index)


def _iter_named_calls(text: str, call_names: set[str]) -> Iterable[Tuple[str, str]]:
    """Iterate named calls using shared utility."""
    return android_common._iter_named_calls(text, call_names)


def _iter_member_calls(text: str, method_names: set[str]) -> Iterable[Tuple[str, str, str]]:
    """Iterate member calls using shared utility."""
    return android_common._iter_member_calls(text, method_names)


def _manifest_symbol_id(rel_path: str) -> str:
    """Generate a unique symbol ID for an AndroidManifest file."""
    return android_common._manifest_symbol_id(rel_path)


def _intent_action_symbol_id(action: str) -> str:
    """Generate a unique symbol ID for an Intent action."""
    return android_common._intent_action_symbol_id(action)


def _is_hidden_rel_path(path: str) -> bool:
    parts = [part for part in (path or "").replace("\\", "/").split("/") if part]
    return any(part.startswith(".") for part in parts)


def _collect_android_events_from_function(function_code: str, function_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if not function_code:
        return events

    text = function_code
    intent_vars: Dict[str, Dict[str, List[str]]] = {}
    filter_vars: Dict[str, List[str]] = {}

    for match in re.finditer(r"\b(?:Intent|var)\s+(\w+)\s*=\s*new\s+Intent\s*\(", text):
        var_name = match.group(1)
        open_paren = text.find("(", match.start())
        args_text, _ = _extract_balanced_args(text, open_paren)
        if args_text is None:
            continue
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        for action in _extract_action_values(args_text):
            if action not in intent_vars[var_name]["actions"]:
                intent_vars[var_name]["actions"].append(action)
        for target in _extract_class_refs(args_text):
            if target not in intent_vars[var_name]["targets"]:
                intent_vars[var_name]["targets"].append(target)

    for match in re.finditer(r"\b(?:IntentFilter|var)\s+(\w+)\s*=\s*new\s+IntentFilter\s*\(", text):
        var_name = match.group(1)
        open_paren = text.find("(", match.start())
        args_text, _ = _extract_balanced_args(text, open_paren)
        if args_text is None:
            continue
        filter_vars[var_name] = _extract_intentfilter_actions(args_text)

    for var_name, _, args_text in _iter_member_calls(text, {"addAction"}):
        filter_vars.setdefault(var_name, [])
        for action in _extract_action_values(args_text):
            if action not in filter_vars[var_name]:
                filter_vars[var_name].append(action)

    for var_name, _, args_text in _iter_member_calls(text, {"setAction"}):
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        for action in _extract_action_values(args_text):
            if action not in intent_vars[var_name]["actions"]:
                intent_vars[var_name]["actions"].append(action)

    for var_name, _, args_text in _iter_member_calls(text, {"setClass", "setClassName"}):
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        for target in _extract_class_refs(args_text):
            if target not in intent_vars[var_name]["targets"]:
                intent_vars[var_name]["targets"].append(target)

    android_calls = {
        "startActivity",
        "startActivityForResult",
        "startService",
        "startForegroundService",
        "sendBroadcast",
        "sendOrderedBroadcast",
        "sendStickyBroadcast",
        "registerReceiver",
    }

    for callee, args_text in _iter_named_calls(text, android_calls):
        implied_actions: List[str] = []
        implied_targets: List[str] = []

        for var_name, meta in intent_vars.items():
            if re.search(rf"\\b{re.escape(var_name)}\\b", args_text):
                implied_actions.extend(meta.get("actions", []))
                implied_targets.extend(meta.get("targets", []))

        if callee in {"startActivity", "startActivityForResult", "startService", "startForegroundService"}:
            events.append(
                {
                    "event_type": "start_component",
                    "function_id": function_id,
                    "actions": _extract_action_values(args_text) + implied_actions,
                    "targets": _extract_class_refs(args_text) + implied_targets,
                    "receiver": None,
                }
            )
        elif callee in {"sendBroadcast", "sendOrderedBroadcast", "sendStickyBroadcast"}:
            events.append(
                {
                    "event_type": "send_broadcast",
                    "function_id": function_id,
                    "actions": _extract_action_values(args_text) + implied_actions,
                    "targets": _extract_class_refs(args_text) + implied_targets,
                    "receiver": None,
                }
            )
        elif callee == "registerReceiver":
            all_actions = _extract_action_values(args_text)
            for filter_var, filter_actions in filter_vars.items():
                if re.search(rf"\\b{re.escape(filter_var)}\\b", args_text):
                    for action in filter_actions:
                        if action not in all_actions:
                            all_actions.append(action)
            receiver_target = _extract_register_receiver_target(args_text)
            events.append(
                {
                    "event_type": "register_receiver",
                    "function_id": function_id,
                    "actions": all_actions,
                    "targets": _extract_class_refs(args_text),
                    "receiver": receiver_target,
                }
            )

    return events


def _iter_chunks(rows: List[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(rows), batch_size):
        yield rows[i : i + batch_size]


async def _write_batched(driver: Any, database: Optional[str], query: str, rows: List[Dict[str, Any]], batch_size: int) -> None:
    if not rows:
        return
    for batch in _iter_chunks(rows, batch_size):
        await driver.execute_query(query, {"rows": batch}, database)


def _resolve_class_id(
    target: str,
    source_package: Optional[str],
    class_index_by_qualified: Dict[str, str],
    class_index_by_simple: Dict[str, List[str]],
) -> Optional[str]:
    if not target:
        return None
    candidate = target.strip().replace("$", ".")
    if candidate.endswith(".class"):
        candidate = candidate[:-6]
    if candidate in class_index_by_qualified:
        return class_index_by_qualified[candidate]
    if candidate.startswith(".") and source_package:
        resolved = f"{source_package}{candidate}"
        if resolved in class_index_by_qualified:
            return class_index_by_qualified[resolved]
    if "." not in candidate and source_package:
        resolved = f"{source_package}.{candidate}"
        if resolved in class_index_by_qualified:
            return class_index_by_qualified[resolved]
    short_name = candidate.split(".")[-1]
    options = class_index_by_simple.get(short_name, [])
    if options:
        return options[0]
    return None


async def enrich_android_java_graph(
    root: str,
    code_writer: Optional[LanguageCodeWriter],
    parse_cache_root: str,
    parse_cache: bool,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    verbose: bool,
    incremental: bool,
    changed_files: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    changed_set = {item.replace("\\", "/") for item in (changed_files or []) if item}

    all_java_files = [
        path
        for path in java_base._scan_java_files(root)
        if not _is_hidden_rel_path(os.path.relpath(path, root))
    ]
    all_manifest_files = _scan_android_manifest_files(root)

    rel_to_abs = {
        os.path.relpath(path, root).replace("\\", "/"): path for path in all_java_files
    }
    all_rel_paths = list(rel_to_abs.keys())

    selected_rel_paths: set[str]
    if incremental:
        changed_existing = {path for path in changed_set if path in rel_to_abs}
        deps_by_file = java_base._collect_java_import_graph(all_java_files, root)
        impacted = java_base._expand_impacted_files_by_imports(changed_existing, deps_by_file)
        selected_rel_paths = changed_existing | impacted
    else:
        selected_rel_paths = set(all_rel_paths)

    # LAZY LOADING OPTIMIZATION: Only load payloads for selected files
    # This reduces memory usage significantly for large projects with incremental mode
    # where only a small subset of files are actually being analyzed.
    selected_payloads: List[Dict[str, Any]] = []
    payload_by_rel: Dict[str, Dict[str, Any]] = {}
    for rel_path in selected_rel_paths:
        if rel_path not in rel_to_abs:
            continue
        abs_path = rel_to_abs[rel_path]
        payload = java_base._load_or_parse_payload(
            abs_path,
            root,
            parse_cache_root,
            parse_cache,
        )
        payload_by_rel[rel_path] = payload
        selected_payloads.append(payload)

    class_index_by_qualified: Dict[str, str] = {}
    class_index_by_simple: Dict[str, List[str]] = {}
    package_by_file: Dict[str, Optional[str]] = {}

    for rel_path, payload in payload_by_rel.items():
        file_def = payload.get("file_def") or {}
        package_by_file[rel_path] = file_def.get("package_name")
        for class_def in payload.get("classes", []):
            qualified_name = class_def.get("qualified_name")
            symbol_id = class_def.get("symbol_id")
            if not qualified_name or not symbol_id:
                continue
            class_index_by_qualified[qualified_name] = symbol_id
            simple_name = class_def.get("name", "").split(".")[-1]
            class_index_by_simple.setdefault(simple_name, [])
            if symbol_id not in class_index_by_simple[simple_name]:
                class_index_by_simple[simple_name].append(symbol_id)

    manifest_defs: List[AndroidManifestDef] = []
    component_defs: List[AndroidComponentDef] = []
    for manifest_path in all_manifest_files:
        manifest_def, components = _parse_android_manifest(manifest_path, root)
        manifest_defs.append(manifest_def)
        component_defs.extend(components)

    intent_action_to_components: Dict[str, List[str]] = {}
    for component in component_defs:
        for action in component.intent_actions:
            if component.component_type == "receiver":
                intent_action_to_components.setdefault(action, []).append(component.symbol_id)

    event_relations: List[Dict[str, Any]] = []
    intent_actions: Dict[str, str] = {}

    for payload in selected_payloads:
        file_def = payload.get("file_def") or {}
        file_path = file_def.get("file_path") or ""
        source_package = file_def.get("package_name")
        for func in payload.get("functions", []):
            function_id = func.get("symbol_id")
            function_code = func.get("code") or ""
            if not function_id:
                continue
            events = _collect_android_events_from_function(function_code, function_id)
            for event in events:
                event_type = event.get("event_type")
                actions = event.get("actions") or []
                targets = event.get("targets") or []
                receiver = event.get("receiver")

                for action in actions:
                    if not action:
                        continue
                    action_id = android_common._intent_action_symbol_id(action)
                    intent_actions[action_id] = action
                    rel_type = "STARTS_INTENT"
                    if event_type == "send_broadcast":
                        rel_type = "SENDS_BROADCAST"
                    elif event_type == "register_receiver":
                        rel_type = "REGISTERS_RECEIVER"
                    event_relations.append(
                        {
                            "source_label": "Function",
                            "source_id": function_id,
                            "target_label": "AndroidIntentAction",
                            "target_id": action_id,
                            "rel_type": rel_type,
                            "props": {
                                "event_type": event_type or "",
                                "file_path": file_path,
                            },
                        }
                    )
                    for component_id in intent_action_to_components.get(action, []):
                        event_relations.append(
                            {
                                "source_label": "AndroidIntentAction",
                                "source_id": action_id,
                                "target_label": "AndroidComponent",
                                "target_id": component_id,
                                "rel_type": "DISPATCHES_TO",
                                "props": {},
                            }
                        )

                for target in targets:
                    class_id = _resolve_class_id(
                        target,
                        source_package,
                        class_index_by_qualified,
                        class_index_by_simple,
                    )
                    if not class_id:
                        continue
                    event_relations.append(
                        {
                            "source_label": "Function",
                            "source_id": function_id,
                            "target_label": "Class",
                            "target_id": class_id,
                            "rel_type": "STARTS_COMPONENT",
                            "props": {
                                "event_type": event_type or "",
                                "file_path": file_path,
                            },
                        }
                    )

                if receiver:
                    class_id = _resolve_class_id(
                        receiver,
                        source_package,
                        class_index_by_qualified,
                        class_index_by_simple,
                    )
                    if class_id:
                        event_relations.append(
                            {
                                "source_label": "Function",
                                "source_id": function_id,
                                "target_label": "Class",
                                "target_id": class_id,
                                "rel_type": "REGISTERS_RECEIVER",
                                "props": {
                                    "event_type": event_type or "",
                                    "file_path": file_path,
                                },
                            }
                        )

    if code_writer is None:
        return {
            "manifests": len(manifest_defs),
            "components": len(component_defs),
            "intent_actions": len(intent_actions),
            "event_relations": len(event_relations),
        }

    driver = code_writer.driver
    database = code_writer.database
    batch_size = max(1, min(code_writer.batch_size, 1000))

    manifest_rows: List[Dict[str, Any]] = []
    component_rows: List[Dict[str, Any]] = []
    action_rows: List[Dict[str, Any]] = []
    directory_rows: List[Dict[str, Any]] = []
    relation_rows: List[Dict[str, Any]] = []

    file_paths_for_tree: List[str] = []
    for payload in selected_payloads:
        file_def = payload.get("file_def") or {}
        file_path = file_def.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            file_paths_for_tree.append(file_path)
    for manifest in manifest_defs:
        if manifest.file_path:
            file_paths_for_tree.append(manifest.file_path)
    for component in component_defs:
        if component.file_path:
            file_paths_for_tree.append(component.file_path)
    directory_paths_for_tree = android_common._scan_android_directory_paths(root)
    directory_rows, directory_relations = android_common._build_directory_nodes_and_relations(
        file_paths=file_paths_for_tree,
        directory_paths=directory_paths_for_tree,
        project_id=project_id,
        project_name=project_name,
        language=language,
        repo=repo,
        build_system=build_system,
    )
    relation_rows.extend(directory_relations)

    for manifest in manifest_defs:
        manifest_rows.append(
            {
                "id": manifest.symbol_id,
                "package_name": manifest.package_name,
                "file_path": manifest.file_path,
                "path": manifest.file_path,
                "start_line": manifest.start_line,
                "end_line": manifest.end_line,
                "code": manifest.code,
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            }
        )
        relation_rows.append(
            {
                "source_id": project_id,
                "target_id": manifest.symbol_id,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )

    for component in component_defs:
        component_rows.append(
            {
                "id": component.symbol_id,
                "name": component.name,
                "component_type": component.component_type,
                "class_name": component.class_name,
                "exported": component.exported,
                "process": component.process,
                "permission": component.permission,
                "enabled": component.enabled,
                "direct_boot_aware": component.direct_boot_aware,
                "target_activity": component.target_activity,
                "intent_actions": component.intent_actions,
                "intent_categories": component.intent_categories,
                "intent_data": component.intent_data,
                "file_path": component.file_path,
                "path": component.file_path,
                "start_line": component.start_line,
                "end_line": component.end_line,
                "code": component.code,
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            }
        )
        relation_rows.append(
            {
                "source_id": project_id,
                "target_id": component.symbol_id,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )
        manifest_id = _manifest_symbol_id(component.file_path)
        relation_rows.append(
            {
                "source_id": manifest_id,
                "target_id": component.symbol_id,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )
        if component.class_name:
            class_id = _resolve_class_id(
                component.class_name,
                None,
                class_index_by_qualified,
                class_index_by_simple,
            )
            if class_id:
                relation_rows.append(
                    {
                        "source_id": component.symbol_id,
                        "target_id": class_id,
                        "rel_type": "MAPS_TO_CLASS",
                        "properties": {},
                    }
                )
        for action in component.intent_actions:
            action_id = _intent_action_symbol_id(action)
            intent_actions[action_id] = action
            relation_rows.append(
                {
                    "source_id": component.symbol_id,
                    "target_id": action_id,
                    "rel_type": "DECLARES_INTENT_ACTION",
                    "properties": {},
                }
            )

    for action_id, action in intent_actions.items():
        action_rows.append(
            {
                "id": action_id,
                "action": action,
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            }
        )
        relation_rows.append(
            {
                "source_id": project_id,
                "target_id": action_id,
                "rel_type": "CONTAINS",
                "properties": {},
            }
        )

    for rel in event_relations:
        relation_rows.append(
            {
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "rel_type": rel["rel_type"],
                "properties": rel.get("props") or {},
            }
        )

    await _write_batched(
        driver,
        database,
        """
        UNWIND $rows AS row
        MERGE (d:Directory {id: row.id})
        SET d.name = row.name,
            d.path = row.path,
            d.depth = row.depth,
            d.project_id = row.project_id,
            d.project_name = row.project_name,
            d.language = row.language,
            d.repo = row.repo,
            d.build_system = row.build_system,
            d.updated_at = datetime()
        """,
        directory_rows,
        batch_size,
    )

    await _write_batched(
        driver,
        database,
        """
        UNWIND $rows AS row
        MERGE (m:AndroidManifest {id: row.id})
        SET m.package_name = row.package_name,
            m.file_path = row.file_path,
            m.path = row.path,
            m.start_line = row.start_line,
            m.end_line = row.end_line,
            m.code = row.code,
            m.project_id = row.project_id,
            m.project_name = row.project_name,
            m.language = row.language,
            m.repo = row.repo,
            m.build_system = row.build_system,
            m.updated_at = datetime()
        """,
        manifest_rows,
        batch_size,
    )

    await _write_batched(
        driver,
        database,
        """
        UNWIND $rows AS row
        MERGE (c:AndroidComponent {id: row.id})
        SET c.name = row.name,
            c.component_type = row.component_type,
            c.class_name = row.class_name,
            c.exported = row.exported,
            c.process = row.process,
            c.permission = row.permission,
            c.enabled = row.enabled,
            c.direct_boot_aware = row.direct_boot_aware,
            c.target_activity = row.target_activity,
            c.intent_actions = row.intent_actions,
            c.intent_categories = row.intent_categories,
            c.intent_data = row.intent_data,
            c.file_path = row.file_path,
            c.path = row.path,
            c.start_line = row.start_line,
            c.end_line = row.end_line,
            c.code = row.code,
            c.project_id = row.project_id,
            c.project_name = row.project_name,
            c.language = row.language,
            c.repo = row.repo,
            c.build_system = row.build_system,
            c.updated_at = datetime()
        """,
        component_rows,
        batch_size,
    )

    await _write_batched(
        driver,
        database,
        """
        UNWIND $rows AS row
        MERGE (a:AndroidIntentAction {id: row.id})
        SET a.action = row.action,
            a.project_id = row.project_id,
            a.project_name = row.project_name,
            a.language = row.language,
            a.repo = row.repo,
            a.build_system = row.build_system,
            a.updated_at = datetime()
        """,
        action_rows,
        batch_size,
    )

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rel in relation_rows:
        by_type.setdefault(rel["rel_type"], []).append(rel)

    for rel_type, rows in by_type.items():
        query = (
            "UNWIND $rows AS row "
            "MATCH (a {id: row.source_id}), (b {id: row.target_id}) "
            f"MERGE (a)-[r:{rel_type}]->(b) "
            "SET r += row.properties"
        )
        await _write_batched(driver, database, query, rows, batch_size)

    if verbose:
        print(
            "[android-java] manifests=%d components=%d intent_actions=%d relations=%d"
            % (len(manifest_rows), len(component_rows), len(action_rows), len(relation_rows))
        )

    return {
        "manifests": len(manifest_rows),
        "components": len(component_rows),
        "intent_actions": len(action_rows),
        "event_relations": len(event_relations),
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Android Java call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Android Java sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION", "android_java_functions"),
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.set_defaults(enable_message_scan=True)
    parser.add_argument(
        "--enable-message-scan",
        dest="enable_message_scan",
        action="store_true",
        help="Enable message scan and sync (default)",
    )
    parser.add_argument(
        "--disable-message-scan",
        dest="enable_message_scan",
        action="store_false",
        help="Disable message scan and sync",
    )
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--keep-cache", action="store_true")
    parser.add_argument("--disable-parse-cache", action="store_true")
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore local caches for this run (parse cache, Neo4j/Qdrant resume state).",
    )
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true", help="Enable incremental ingestion mode")
    parser.add_argument(
        "--changed-files-manifest",
        help="JSON/TXT manifest of changed+impacted file paths (relative to --root)",
    )
    parser.add_argument(
        "--deleted-files-manifest",
        help="JSON/TXT manifest of deleted file paths (relative to --root)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    code_writer: Optional[LanguageCodeWriter] = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        driver = await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
        )
        code_writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )

    qdrant_writer = None
    embedder = None
    if args.qdrant_url:
        embedder = java_base.CodeEmbedder(
            args.embed_model,
            args.device,
            args.max_embed_chars,
            args.chunk_embed,
            fallback_cache_base_dir=args.cache_dir,
            project_root=args.root,
            verbose=args.verbose,
        )
        qdrant_writer = java_base.QdrantWriter(
            args.qdrant_url,
            args.qdrant_collection,
            vector_size=embedder.vector_size,
            timeout=args.qdrant_timeout,
            retries=args.qdrant_retries,
            retry_sleep=args.qdrant_retry_sleep,
        )

    parse_cache = not args.disable_parse_cache
    effective_cache_dir = args.cache_dir
    if args.ignore_cache:
        run_cache_root = safe_cache_root(effective_cache_dir, "java_analyzer", project_root=args.root)
        effective_cache_dir = os.path.join(
            run_cache_root,
            "ignore_runs",
            f"run_{int(time.time() * 1000)}",
        )
        os.makedirs(effective_cache_dir, exist_ok=True)
        parse_cache = False
        args.disable_neo4j_resume = True
        args.keep_cache = False
        if args.verbose:
            print(
                "[cache] ignore-cache enabled; using isolated cache dir: %s"
                % effective_cache_dir
            )

    changed_manifest_files: List[str] = []
    deleted_manifest_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_manifest_files = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_manifest_files = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        if args.verbose:
            print(
                "[diff] incremental manifests changed=%d deleted=%d"
                % (len(changed_manifest_files), len(deleted_manifest_files))
            )

    neo4j_state_path = None
    if not args.disable_neo4j_resume and not args.incremental:
        cache_root = safe_cache_root(effective_cache_dir, "java_analyzer", project_root=args.root)
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    elif args.incremental and args.verbose:
        print("[state] incremental mode disables neo4j resume state")

    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "android-java"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or "gradle"
    commit_sha = args.commit_sha_after or ""
    commit_sha_before = args.commit_sha_before or ""

    message_qdrant_collection = (
        args.message_qdrant_collection
        or default_message_collection_name(args.qdrant_collection)
    )

    if code_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            await write_cloc_stats_to_neo4j(
                driver=code_writer.driver,
                database=code_writer.database,
                project_id=project_id,
                project_name=project_name,
                root=args.root,
                repo=repo,
                language=language,
                stats=cloc_stats,
            )
            if args.verbose:
                print("[cloc] Stats stored in Neo4j")
        elif args.verbose:
            print("[cloc] Skipped (cloc not available or failed)")

    cache_root = safe_cache_root(effective_cache_dir, "java_analyzer", project_root=args.root)
    parse_cache_root = os.path.join(cache_root, "parse")
    os.makedirs(parse_cache_root, exist_ok=True)

    try:
        if args.dry_run:
            java_files = java_base._scan_java_files(args.root)
            if args.incremental and changed_manifest_files:
                manifest_set = set(changed_manifest_files)
                java_files = [
                    file_path
                    for file_path in java_files
                    if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
                ]
                print(
                    "Dry run (incremental): %d Android Java files selected (manifest=%d)"
                    % (len(java_files), len(changed_manifest_files))
                )
            else:
                print(f"Dry run: {len(java_files)} Android Java files found")
            return 0

        await java_base.build_call_graph(
            args.root,
            code_writer=code_writer,
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=effective_cache_dir,
            keep_cache=args.keep_cache,
            parse_cache=parse_cache,
            neo4j_batch_size=args.neo4j_batch_size,
            neo4j_state_path=neo4j_state_path,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            verbose=args.verbose,
            incremental=args.incremental,
            changed_files=changed_manifest_files,
            deleted_files=deleted_manifest_files,
            commit_sha=commit_sha,
            commit_sha_before=commit_sha_before,
        )

        android_summary = await enrich_android_java_graph(
            root=args.root,
            code_writer=code_writer,
            parse_cache_root=parse_cache_root,
            parse_cache=parse_cache,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            verbose=args.verbose,
            incremental=args.incremental,
            changed_files=changed_manifest_files,
        )
        print(
            "[android] parser=android_java manifests=%s components=%s actions=%s event_relations=%s"
            % (
                android_summary.get("manifests", 0),
                android_summary.get("components", 0),
                android_summary.get("intent_actions", 0),
                android_summary.get("event_relations", 0),
            )
        )

        if args.enable_message_scan:
            message_summary = await run_message_scan_pipeline(
                root=args.root,
                parser="java",
                project_id=project_id,
                project_name=project_name,
                language=language,
                repo=repo,
                build_system=build_system,
                incremental=args.incremental,
                changed_files=changed_manifest_files,
                deleted_files=deleted_manifest_files,
                driver=driver,
                neo4j_database=args.neo4j_db,
                qdrant_url=args.qdrant_url,
                qdrant_collection=message_qdrant_collection if args.qdrant_url else None,
                qdrant_vector_size=embedder.vector_size if embedder else 1024,
                embed_texts=embedder.embed if embedder else None,
                output_dir=args.message_output_dir,
                cache_dir=effective_cache_dir,
                commit_sha_before=commit_sha_before,
                commit_sha_after=commit_sha,
                qdrant_batch_size=args.qdrant_batch_size,
                qdrant_timeout=args.qdrant_timeout,
                qdrant_retries=args.qdrant_retries,
                qdrant_retry_sleep=args.qdrant_retry_sleep,
                verbose=args.verbose,
            )
            print(
                "[message] parser=%s count=%s neo4j=%s qdrant=%s collection=%s artifact=%s"
                % (
                    message_summary.get("parser"),
                    message_summary.get("message_count"),
                    message_summary.get("neo4j_upserted"),
                    message_summary.get("qdrant_upserted"),
                    message_summary.get("qdrant_collection"),
                    message_summary.get("artifact_path"),
                )
            )
    finally:
        if driver:
            close_result = driver.close()
            if hasattr(close_result, "__await__"):
                await close_result

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
