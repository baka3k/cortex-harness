from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from tools.mybatis.detector import read_limited
from tools.mybatis.mapper_xml_analyzer import _attrs, _child_elements, _descendant_elements, _element_name, _first_element, _span
from tools.mybatis.models import (
    Diagnostic,
    MyBatisCacheFact,
    MyBatisConfigFact,
    MyBatisExtensionFact,
    MyBatisSpringBridgeFact,
    SourceSpan,
)
from tools.mybatis.parser_runtime import parse_xml_bytes


_SPRING_BRIDGE_CLASSES = {
    "org.mybatis.spring.SqlSessionFactoryBean": "sql_session_factory",
    "org.mybatis.spring.SqlSessionTemplate": "sql_session_template",
    "org.mybatis.spring.mapper.MapperScannerConfigurer": "mapper_scanner",
    "org.mybatis.spring.mapper.MapperFactoryBean": "mapper_factory",
    "SqlSessionFactoryBean": "sql_session_factory",
    "SqlSessionTemplate": "sql_session_template",
    "MapperScannerConfigurer": "mapper_scanner",
    "MapperFactoryBean": "mapper_factory",
}
_JAVA_BRIDGE_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"@MapperScan\s*\((?P<args>[^)]*)\)", re.DOTALL), "mapper_scan"),
    (re.compile(r"\bSqlSessionFactoryBean\b"), "sql_session_factory"),
    (re.compile(r"\bSqlSessionTemplate\b"), "sql_session_template"),
    (re.compile(r"\bMapperScannerConfigurer\b"), "mapper_scanner"),
    (re.compile(r"\bMapperFactoryBean\b"), "mapper_factory"),
)
_EXTENSION_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBaseTypeHandler\s*<|\bextends\s+BaseTypeHandler\b|@MappedTypes\b|@MappedJdbcTypes\b"), "type_handler"),
    (re.compile(r"\bimplements\s+Interceptor\b|@Intercepts\b|@Signature\b"), "plugin"),
)


@dataclass(frozen=True)
class SpringBridgeAnalysis:
    bridge_facts: Tuple[MyBatisSpringBridgeFact, ...]
    extension_facts: Tuple[MyBatisExtensionFact, ...]
    cache_facts: Tuple[MyBatisCacheFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


def analyze_optional_extensions(
    *,
    root: str,
    java_files: Sequence[str],
    xml_files: Sequence[str],
    config_facts: Sequence[MyBatisConfigFact],
    project_id: str,
) -> SpringBridgeAnalysis:
    bridge_facts: List[MyBatisSpringBridgeFact] = []
    extension_facts: List[MyBatisExtensionFact] = []
    cache_facts: List[MyBatisCacheFact] = []
    diagnostics: List[Diagnostic] = []

    for config in config_facts:
        for index, handler in enumerate(config.type_handlers):
            extension_facts.append(_config_extension(project_id, config, "type_handler", handler, index))
        for index, plugin in enumerate(config.plugins):
            extension_facts.append(_config_extension(project_id, config, "plugin", plugin, index))

    for rel_path in sorted(set(java_files)):
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            continue
        text = read_limited(abs_path)
        bridge_facts.extend(_java_bridge_facts(project_id, rel_path, text))
        extension_facts.extend(_java_extension_facts(project_id, rel_path, text))

    for rel_path in sorted(set(xml_files)):
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            continue
        with open(abs_path, "rb") as handle:
            source = handle.read()
        try:
            tree = parse_xml_bytes(source)
        except Exception as exc:
            diagnostics.append(Diagnostic("mybatis.bridge.xml_parse_failed", str(exc), "warning", rel_path))
            continue
        root_element = _first_element(tree.root_node)
        if root_element is None:
            continue
        root_tag = _element_name(root_element, source)
        if root_tag == "mapper":
            namespace = _attrs(root_element, source).get("namespace", "")
            cache_facts.extend(_mapper_cache_facts(project_id, root_element, source, rel_path, namespace))
        elif root_tag in {"beans", "beans:beans"}:
            bridge_facts.extend(_spring_xml_bridge_facts(project_id, root_element, source, rel_path))

    return SpringBridgeAnalysis(tuple(bridge_facts), tuple(extension_facts), tuple(cache_facts), tuple(diagnostics))


def _config_extension(
    project_id: str,
    config: MyBatisConfigFact,
    kind: str,
    attrs: Dict[str, str],
    index: int,
) -> MyBatisExtensionFact:
    java_type = attrs.get("handler", attrs.get("interceptor", attrs.get("type", "")))
    name = java_type or attrs.get("javaType", kind)
    return MyBatisExtensionFact(
        stable_id=f"mybatis_extension::{project_id}::{kind}::{_digest(config.file_path, str(index), name)}",
        extension_kind=kind,
        name=name,
        java_type=java_type,
        source=config.source,
        attributes=dict(attrs),
    )


def _java_bridge_facts(project_id: str, rel_path: str, text: str) -> List[MyBatisSpringBridgeFact]:
    rows: List[MyBatisSpringBridgeFact] = []
    for pattern, kind in _JAVA_BRIDGE_PATTERNS:
        for index, match in enumerate(pattern.finditer(text or "")):
            line = _line_for_index(text, match.start())
            attrs: Dict[str, str] = {"source_kind": "java"}
            if "args" in match.groupdict():
                attrs["arguments"] = match.group("args").strip()
            rows.append(
                MyBatisSpringBridgeFact(
                    stable_id=f"mybatis_spring_bridge::{project_id}::{kind}::{_digest(rel_path, str(line), str(index))}",
                    bridge_kind=kind,
                    name=kind,
                    source=SourceSpan(rel_path, line, line),
                    attributes=attrs,
                )
            )
    return rows


def _java_extension_facts(project_id: str, rel_path: str, text: str) -> List[MyBatisExtensionFact]:
    rows: List[MyBatisExtensionFact] = []
    class_name = _java_class_name(text)
    for pattern, kind in _EXTENSION_PATTERNS:
        for index, match in enumerate(pattern.finditer(text or "")):
            line = _line_for_index(text, match.start())
            rows.append(
                MyBatisExtensionFact(
                    stable_id=f"mybatis_extension::{project_id}::{kind}::{_digest(rel_path, str(line), str(index))}",
                    extension_kind=kind,
                    name=class_name or kind,
                    java_type=class_name,
                    source=SourceSpan(rel_path, line, line),
                    attributes={"source_kind": "java", "evidence": match.group(0)[:120]},
                )
            )
    return rows


def _spring_xml_bridge_facts(project_id: str, root_element, source: bytes, rel_path: str) -> List[MyBatisSpringBridgeFact]:
    rows: List[MyBatisSpringBridgeFact] = []
    for index, elem in enumerate(_descendant_elements(root_element, source)):
        tag = _element_name(elem, source).split(":")[-1]
        attrs = _attrs(elem, source)
        class_name = attrs.get("class", "")
        bridge_kind = _SPRING_BRIDGE_CLASSES.get(class_name, "")
        if tag == "scan" and "mybatis" in _text_around(source, elem).lower():
            bridge_kind = "mapper_scan"
        if not bridge_kind:
            continue
        rows.append(
            MyBatisSpringBridgeFact(
                stable_id=f"mybatis_spring_bridge::{project_id}::{bridge_kind}::{_digest(rel_path, str(index), class_name)}",
                bridge_kind=bridge_kind,
                name=attrs.get("id", class_name or bridge_kind),
                target=class_name,
                source=_span(elem, rel_path),
                attributes=dict(attrs),
            )
        )
        for child in _child_elements(elem, source):
            child_attrs = _attrs(child, source)
            prop_name = child_attrs.get("name", "")
            if prop_name in {"mapperLocations", "configLocation", "basePackage", "sqlSessionFactoryBeanName"}:
                rows.append(
                    MyBatisSpringBridgeFact(
                        stable_id=f"mybatis_spring_bridge::{project_id}::{prop_name}::{_digest(rel_path, str(index), prop_name)}",
                        bridge_kind=prop_name,
                        name=prop_name,
                        target=child_attrs.get("value", child_attrs.get("ref", "")),
                        source=_span(child, rel_path),
                        attributes=dict(child_attrs),
                    )
                )
    return rows


def _mapper_cache_facts(project_id: str, root_element, source: bytes, rel_path: str, namespace: str) -> List[MyBatisCacheFact]:
    rows: List[MyBatisCacheFact] = []
    for index, child in enumerate(_child_elements(root_element, source)):
        tag = _element_name(child, source)
        if tag not in {"cache", "cache-ref"}:
            continue
        attrs = _attrs(child, source)
        rows.append(
            MyBatisCacheFact(
                stable_id=f"mybatis_cache::{project_id}::{namespace}::{tag}:{_digest(rel_path, str(index), attrs.get('namespace', ''))}",
                namespace=namespace,
                cache_kind=tag,
                target_namespace=attrs.get("namespace", ""),
                source=_span(child, rel_path),
                attributes=attrs,
            )
        )
    return rows


def _java_class_name(text: str) -> str:
    package = ""
    package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text or "")
    if package_match:
        package = package_match.group(1)
    class_match = re.search(r"\b(?:class|interface|record)\s+([A-Za-z_]\w*)", text or "")
    if not class_match:
        return ""
    return f"{package}.{class_match.group(1)}" if package else class_match.group(1)


def _line_for_index(text: str, index: int) -> int:
    return (text or "").count("\n", 0, index) + 1


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _text_around(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
