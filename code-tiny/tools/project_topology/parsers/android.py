"""Secure Android manifest and resource XML semantic extraction."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any, Dict, Tuple

from ..models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    DiagnosticCode,
    ParseDepth,
    safe_summary,
)
from .common import MAX_XML_DEPTH, MAX_XML_NODES, evidence, module_path_for_file


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
_REFERENCE_RE = re.compile(r"(?<![\w])([@?][+*]?[A-Za-z0-9_.:-]+/[A-Za-z0-9_.-]+)")
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")
_SECRET_NAME_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|credential|private[_-]?key)",
    re.IGNORECASE,
)


def _android(node: ET.Element, name: str) -> str:
    return node.attrib.get(ANDROID_NS + name, "")


def _safe_xml(
    *,
    path: str,
    text: str,
    module_path: str,
) -> Tuple[ET.Element | None, Tuple[AnalysisDiagnostic, ...]]:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return None, (
            AnalysisDiagnostic(
                DiagnosticCode.XML_UNSAFE,
                "DOCTYPE and entity declarations are not permitted in Android XML.",
                severity="error",
                file_path=path,
                module_path=module_path,
            ),
        )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return None, (
            AnalysisDiagnostic(
                DiagnosticCode.MALFORMED_DESCRIPTOR,
                f"Malformed Android XML: {exc}",
                severity="error",
                file_path=path,
                module_path=module_path,
            ),
        )
    count = 0
    max_depth = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        max_depth = max(max_depth, depth)
        if count > MAX_XML_NODES or max_depth > MAX_XML_DEPTH:
            return None, (
                AnalysisDiagnostic(
                    DiagnosticCode.LIMIT_EXCEEDED,
                    "Android XML exceeds the safe node/depth limit.",
                    severity="error",
                    file_path=path,
                    module_path=module_path,
                    details={
                        "nodes": count,
                        "depth": max_depth,
                        "node_limit": MAX_XML_NODES,
                        "depth_limit": MAX_XML_DEPTH,
                    },
                ),
            )
        stack.extend((child, depth + 1) for child in list(node))
    return root, ()


def _source_set(path: str) -> str:
    parts = PurePosixPath(path).parts
    try:
        index = parts.index("src")
        return parts[index + 1]
    except (ValueError, IndexError):
        return ""


def parse_android_manifest(
    *,
    project_id: str,
    path: str,
    text: str,
) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    marker = "/src/"
    if marker in path:
        module_path = path.split(marker, 1)[0] or "."
    root, xml_diagnostics = _safe_xml(
        path=path, text=text, module_path=module_path
    )
    diagnostics = list(xml_diagnostics)
    properties: Dict[str, Any] = {
        "source_set": _source_set(path),
        "permissions": [],
        "features": [],
        "queries": [],
        "instrumentation": [],
        "metadata_keys": [],
        "components": [],
    }
    if root is not None:
        properties["package"] = root.attrib.get("package", "")
        properties["version_code"] = _android(root, "versionCode")
        properties["version_name"] = _android(root, "versionName")
        uses_sdk = root.find("uses-sdk")
        if uses_sdk is not None:
            properties["sdk"] = {
                "min": _android(uses_sdk, "minSdkVersion"),
                "target": _android(uses_sdk, "targetSdkVersion"),
                "max": _android(uses_sdk, "maxSdkVersion"),
            }
        for node in root:
            tag = node.tag.rsplit("}", 1)[-1]
            if tag in {"uses-permission", "permission"}:
                properties["permissions"].append(
                    {
                        "name": _android(node, "name"),
                        "kind": tag,
                        "max_sdk": _android(node, "maxSdkVersion"),
                    }
                )
            elif tag == "uses-feature":
                properties["features"].append(
                    {
                        "name": _android(node, "name"),
                        "required": _android(node, "required"),
                        "gl_es_version": _android(node, "glEsVersion"),
                    }
                )
            elif tag == "queries":
                for query in node:
                    properties["queries"].append(
                        {
                            "kind": query.tag.rsplit("}", 1)[-1],
                            "name": _android(query, "name"),
                            "package": query.attrib.get("package", ""),
                        }
                    )
            elif tag == "instrumentation":
                properties["instrumentation"].append(
                    {
                        "name": _android(node, "name"),
                        "target_package": _android(node, "targetPackage"),
                    }
                )
        application = root.find("application")
        if application is not None:
            properties["application"] = {
                name: _android(application, name)
                for name in (
                    "name",
                    "label",
                    "theme",
                    "icon",
                    "debuggable",
                    "allowBackup",
                    "networkSecurityConfig",
                )
                if _android(application, name)
            }
            for metadata in application.findall("meta-data"):
                name = _android(metadata, "name")
                if name:
                    properties["metadata_keys"].append(name)
            for node in application:
                tag = node.tag.rsplit("}", 1)[-1]
                if tag not in {
                    "activity",
                    "activity-alias",
                    "service",
                    "receiver",
                    "provider",
                }:
                    continue
                component = {
                    "kind": tag,
                    "name": _android(node, "name"),
                    "target_activity": _android(node, "targetActivity"),
                    "exported": _android(node, "exported") or "unknown",
                    "enabled": _android(node, "enabled") or "default",
                    "permission": _android(node, "permission"),
                    "process": _android(node, "process"),
                    "intent_filters": [],
                }
                for intent_filter in node.findall("intent-filter"):
                    item = {
                        "actions": [
                            _android(child, "name")
                            for child in intent_filter.findall("action")
                            if _android(child, "name")
                        ],
                        "categories": [
                            _android(child, "name")
                            for child in intent_filter.findall("category")
                            if _android(child, "name")
                        ],
                        "data": [],
                    }
                    for data in intent_filter.findall("data"):
                        item["data"].append(
                            {
                                key: _android(data, key)
                                for key in (
                                    "scheme",
                                    "host",
                                    "port",
                                    "path",
                                    "pathPrefix",
                                    "pathPattern",
                                    "mimeType",
                                )
                                if _android(data, key)
                            }
                        )
                    component["intent_filters"].append(item)
                properties["components"].append(component)
        if _PLACEHOLDER_RE.search(text):
            diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.UNRESOLVED_REFERENCE,
                    "Manifest placeholders were retained without build-time resolution.",
                    file_path=path,
                    module_path=module_path,
                )
            )
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.ANDROID_MANIFEST,
        role=DescriptorRole.FRAMEWORK,
        parser="android_manifest",
        parse_depth=ParseDepth.SEMANTIC if root is not None else ParseDepth.IDENTITY,
        summary=safe_summary(
            f"Android manifest with {len(properties['components'])} components"
        ),
        properties=properties,
        confidence=(
            ConfidenceLevel.LOW
            if root is None
            else ConfidenceLevel.MEDIUM
            if diagnostics
            else ConfidenceLevel.HIGH
        ),
        evidence=evidence(path),
        diagnostics=tuple(diagnostics),
    )
    return DescriptorParseOutput(
        descriptor=descriptor, diagnostics=tuple(diagnostics)
    )


def _qualifier(path: str) -> str:
    for part in PurePosixPath(path).parts:
        if part.startswith(
            ("values", "layout", "navigation", "menu", "xml", "drawable")
        ):
            return part
    return ""


def parse_android_resource(
    *,
    project_id: str,
    path: str,
    text: str,
) -> DescriptorParseOutput:
    module_path = path.split("/src/", 1)[0] if "/src/" in path else module_path_for_file(path)
    root, xml_diagnostics = _safe_xml(
        path=path, text=text, module_path=module_path
    )
    diagnostics = list(xml_diagnostics)
    values = []
    views = []
    references = sorted(set(_REFERENCE_RE.findall(text)))
    redacted = False
    if root is not None:
        if root.tag.rsplit("}", 1)[-1] == "resources":
            for node in root:
                resource_type = node.tag.rsplit("}", 1)[-1]
                name = node.attrib.get("name", "")
                secret = bool(_SECRET_NAME_RE.search(name))
                redacted = redacted or secret
                values.append(
                    {
                        "type": resource_type,
                        "name": name,
                        "parent": node.attrib.get("parent", ""),
                        "item_count": len(list(node)),
                        "value": "[redacted]" if secret else safe_summary(node.text, limit=120),
                    }
                )
        else:
            stack = [(root, "")]
            while stack:
                node, parent_id = stack.pop()
                node_id = _android(node, "id")
                views.append(
                    {
                        "type": node.tag.rsplit("}", 1)[-1],
                        "id": node_id,
                        "parent_id": parent_id,
                        "name": _android(node, "name"),
                        "destination": _android(node, "destination"),
                        "uri": _android(node, "uri"),
                    }
                )
                stack.extend((child, node_id or parent_id) for child in reversed(list(node)))
    if redacted:
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.SECRET_REDACTED,
                "Secret-like Android resource values were redacted.",
                file_path=path,
                module_path=module_path,
            )
        )
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.RESOURCE,
        role=DescriptorRole.RESOURCE,
        parser="android_resource",
        parse_depth=ParseDepth.SEMANTIC if root is not None else ParseDepth.IDENTITY,
        secret_bearing=redacted,
        redacted=redacted,
        summary=safe_summary(
            f"Android resource {path} with {len(values) or len(views)} facts"
        ),
        properties={
            "qualifier": _qualifier(path),
            "values": values,
            "views": views,
            "references": references,
        },
        confidence=ConfidenceLevel.LOW if root is None else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=tuple(diagnostics),
    )
    return DescriptorParseOutput(
        descriptor=descriptor, diagnostics=tuple(diagnostics)
    )


__all__ = ["parse_android_manifest", "parse_android_resource"]
