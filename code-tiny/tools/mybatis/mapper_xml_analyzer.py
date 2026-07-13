from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Sequence, Tuple

from tools.mybatis.detector import classify_xml_text
from tools.mybatis.dynamic_sql import DYNAMIC_TAGS, dynamic_node
from tools.mybatis.models import (
    Diagnostic,
    MyBatisConfigFact,
    MyBatisDynamicSqlNodeFact,
    MyBatisIncludeFact,
    MyBatisResultMapFact,
    MyBatisResultMappingFact,
    MyBatisSqlFragmentFact,
    MyBatisStatementFact,
    MyBatisXmlDocumentFact,
    SourceSpan,
)
from tools.mybatis.parser_runtime import parse_xml_bytes


STATEMENT_TAGS = {"select", "insert", "update", "delete"}
RESULT_MAPPING_TAGS = {"id", "result", "association", "collection", "constructor", "arg", "idArg", "discriminator", "case"}
_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+([^>]+)>", re.IGNORECASE | re.DOTALL)
_PROPERTY_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass(frozen=True)
class MapperXmlAnalysis:
    documents: Tuple[MyBatisXmlDocumentFact, ...]
    statements: Tuple[MyBatisStatementFact, ...]
    fragments: Tuple[MyBatisSqlFragmentFact, ...]
    result_maps: Tuple[MyBatisResultMapFact, ...]
    result_mappings: Tuple[MyBatisResultMappingFact, ...]
    includes: Tuple[MyBatisIncludeFact, ...]
    dynamic_nodes: Tuple[MyBatisDynamicSqlNodeFact, ...]
    config_facts: Tuple[MyBatisConfigFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


def analyze_mapper_xml_files(*, root: str, xml_files: Sequence[str], project_id: str) -> MapperXmlAnalysis:
    documents: List[MyBatisXmlDocumentFact] = []
    statements: List[MyBatisStatementFact] = []
    fragments: List[MyBatisSqlFragmentFact] = []
    result_maps: List[MyBatisResultMapFact] = []
    result_mappings: List[MyBatisResultMappingFact] = []
    includes: List[MyBatisIncludeFact] = []
    dynamic_nodes: List[MyBatisDynamicSqlNodeFact] = []
    config_facts: List[MyBatisConfigFact] = []
    diagnostics: List[Diagnostic] = []
    fragment_index: Dict[str, List[_ElementView]] = {}

    parsed: List[Tuple[str, bytes, object, str, str]] = []
    for rel_path in sorted(set(xml_files)):
        path = os.path.join(root, rel_path)
        if not os.path.isfile(path):
            diagnostics.append(Diagnostic("mybatis.xml.missing_file", "XML file is missing", "warning", rel_path))
            continue
        with open(path, "rb") as handle:
            source = handle.read()
        tree = parse_xml_bytes(source)
        if tree.root_node.has_error:
            diagnostics.append(Diagnostic("mybatis.xml.parse_error", "XML parser reported syntax errors", "error", rel_path))
        root_element = _first_element(tree.root_node)
        if root_element is None:
            diagnostics.append(Diagnostic("mybatis.xml.no_root", "XML document has no root element", "error", rel_path))
            continue
        root_tag = _element_name(root_element, source)
        document_kind, _ = classify_xml_text(rel_path, source.decode("utf-8", errors="ignore"))
        namespace = _attrs(root_element, source).get("namespace", "")
        documents.append(
            MyBatisXmlDocumentFact(
                file_path=rel_path,
                document_kind=document_kind or root_tag,
                root_tag=root_tag,
                source=_span(root_element, rel_path),
                namespace=namespace,
                doctype=_doctype(source),
                parser_status="parse_error" if tree.root_node.has_error else "parsed",
            )
        )
        parsed.append((rel_path, source, root_element, root_tag, namespace))
        if root_tag == "mapper":
            for child in _child_elements(root_element, source):
                if _element_name(child, source) == "sql":
                    attrs = _attrs(child, source)
                    fragment_id = attrs.get("id", "")
                    if fragment_id:
                        fragment_index.setdefault(_qualified_ref(namespace, fragment_id), []).append(
                            _ElementView(rel_path, source, child, namespace, attrs.get("databaseId", ""))
                        )

    for rel_path, source, root_element, root_tag, namespace in parsed:
        if root_tag == "mapper":
            mapper_result = _analyze_mapper(root_element, source, rel_path, namespace, project_id, fragment_index)
            statements.extend(mapper_result.statements)
            fragments.extend(mapper_result.fragments)
            result_maps.extend(mapper_result.result_maps)
            result_mappings.extend(mapper_result.result_mappings)
            includes.extend(mapper_result.includes)
            dynamic_nodes.extend(mapper_result.dynamic_nodes)
            diagnostics.extend(mapper_result.diagnostics)
        elif root_tag == "configuration":
            config_facts.append(_analyze_config(root_element, source, rel_path, project_id))

    return MapperXmlAnalysis(
        tuple(documents),
        tuple(statements),
        tuple(fragments),
        tuple(result_maps),
        tuple(result_mappings),
        tuple(includes),
        tuple(dynamic_nodes),
        tuple(config_facts),
        tuple(diagnostics),
    )


@dataclass(frozen=True)
class _ElementView:
    file_path: str
    source: bytes
    node: object
    namespace: str
    database_id: str = ""


@dataclass(frozen=True)
class _MapperPartial:
    statements: Tuple[MyBatisStatementFact, ...]
    fragments: Tuple[MyBatisSqlFragmentFact, ...]
    result_maps: Tuple[MyBatisResultMapFact, ...]
    result_mappings: Tuple[MyBatisResultMappingFact, ...]
    includes: Tuple[MyBatisIncludeFact, ...]
    dynamic_nodes: Tuple[MyBatisDynamicSqlNodeFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


def _analyze_mapper(root_element, source: bytes, file_path: str, namespace: str, project_id: str, fragment_index: Dict[str, List[_ElementView]]) -> _MapperPartial:
    statements: List[MyBatisStatementFact] = []
    fragments: List[MyBatisSqlFragmentFact] = []
    result_maps: List[MyBatisResultMapFact] = []
    result_mappings: List[MyBatisResultMappingFact] = []
    includes: List[MyBatisIncludeFact] = []
    dynamic_nodes: List[MyBatisDynamicSqlNodeFact] = []
    diagnostics: List[Diagnostic] = []
    seen_statement_ids = set()

    for child in _child_elements(root_element, source):
        tag = _element_name(child, source)
        attrs = _attrs(child, source)
        if tag in STATEMENT_TAGS:
            statement_id = attrs.get("id", "")
            if not statement_id:
                diagnostics.append(Diagnostic("mybatis.xml.statement_missing_id", "Statement is missing id", "warning", file_path, child.start_point[0] + 1, child.end_point[0] + 1))
                continue
            duplicate_key = (statement_id, attrs.get("databaseId", ""))
            if duplicate_key in seen_statement_ids:
                diagnostics.append(Diagnostic("mybatis.xml.duplicate_statement", f"Duplicate statement id {statement_id!r}", "warning", file_path, child.start_point[0] + 1, child.end_point[0] + 1))
            seen_statement_ids.add(duplicate_key)
            stable_id = f"mybatis_stmt::{project_id}::{namespace}::{statement_id}::{attrs.get('databaseId') or 'default'}"
            expanded, stmt_includes, include_diags, nodes = _expand_body(child, source, file_path, namespace, stable_id, fragment_index, (), attrs.get("databaseId", ""))
            stmt_includes = _renumber_includes(stable_id, stmt_includes)
            nodes = _renumber_dynamic_nodes(stable_id, nodes)
            statements.append(
                MyBatisStatementFact(
                    stable_id=stable_id,
                    namespace=namespace,
                    statement_id=statement_id,
                    statement_kind=tag,
                    source=_span(child, file_path),
                    database_id=attrs.get("databaseId", ""),
                    attributes=attrs,
                    raw_body=_raw_body(child, source),
                    expanded_body=expanded,
                    includes=tuple(stmt_includes),
                    dynamic_nodes=tuple(nodes),
                )
            )
            includes.extend(stmt_includes)
            dynamic_nodes.extend(nodes)
            diagnostics.extend(include_diags)
        elif tag == "sql":
            fragment_id = attrs.get("id", "")
            if not fragment_id:
                continue
            stable_id = f"mybatis_fragment::{project_id}::{namespace}::{fragment_id}::{attrs.get('databaseId') or 'default'}"
            expanded, frag_includes, include_diags, nodes = _expand_body(child, source, file_path, namespace, stable_id, fragment_index, (), attrs.get("databaseId", ""))
            frag_includes = _renumber_includes(stable_id, frag_includes)
            nodes = _renumber_dynamic_nodes(stable_id, nodes)
            fragments.append(
                MyBatisSqlFragmentFact(
                    stable_id=stable_id,
                    namespace=namespace,
                    fragment_id=fragment_id,
                    source=_span(child, file_path),
                    database_id=attrs.get("databaseId", ""),
                    attributes=attrs,
                    raw_body=_raw_body(child, source),
                    expanded_body=expanded,
                    includes=tuple(frag_includes),
                )
            )
            includes.extend(frag_includes)
            dynamic_nodes.extend(nodes)
            diagnostics.extend(include_diags)
        elif tag == "resultMap":
            result_map, mappings = _result_map(child, source, file_path, namespace, project_id)
            result_maps.append(result_map)
            result_mappings.extend(mappings)

    return _MapperPartial(tuple(statements), tuple(fragments), tuple(result_maps), tuple(result_mappings), tuple(includes), tuple(dynamic_nodes), tuple(diagnostics))


def _result_map(node, source: bytes, file_path: str, namespace: str, project_id: str):
    attrs = _attrs(node, source)
    map_id = attrs.get("id", "")
    stable_id = f"mybatis_result_map::{project_id}::{namespace}::{map_id}"
    mappings: List[MyBatisResultMappingFact] = []
    ordinal = 0
    for child in _descendant_elements(node, source):
        tag = _element_name(child, source)
        if tag not in RESULT_MAPPING_TAGS:
            continue
        child_attrs = _attrs(child, source)
        mappings.append(
            MyBatisResultMappingFact(
                stable_id=f"mybatis_result_mapping::{stable_id}::{child.start_point[0] + 1}:{child.start_point[1] + 1}:{ordinal}",
                result_map_id=stable_id,
                mapping_kind=tag,
                source=_span(child, file_path),
                property_name=child_attrs.get("property", child_attrs.get("name", "")),
                column=child_attrs.get("column", ""),
                java_type=child_attrs.get("javaType", ""),
                jdbc_type=child_attrs.get("jdbcType", ""),
                nested_select=child_attrs.get("select", ""),
                nested_result_map=child_attrs.get("resultMap", ""),
                attributes=child_attrs,
            )
        )
        ordinal += 1
    return (
        MyBatisResultMapFact(
            stable_id=stable_id,
            namespace=namespace,
            result_map_id=map_id,
            source=_span(node, file_path),
            java_type=attrs.get("type", ""),
            extends=attrs.get("extends", ""),
            auto_mapping=attrs.get("autoMapping", ""),
            mappings=tuple(mappings),
        ),
        mappings,
    )


def _analyze_config(root_element, source: bytes, file_path: str, project_id: str) -> MyBatisConfigFact:
    properties: Dict[str, str] = {}
    settings: Dict[str, str] = {}
    type_aliases: Dict[str, str] = {}
    type_handlers: List[Dict[str, str]] = []
    plugins: List[Dict[str, str]] = []
    environments: List[Dict[str, str]] = []
    database_id_provider: Dict[str, str] = {}
    mappers: List[Dict[str, str]] = []
    for child in _child_elements(root_element, source):
        tag = _element_name(child, source)
        if tag == "properties":
            properties.update({key: value for key, value in _attrs(child, source).items() if key in {"resource", "url"}})
            properties.update(_property_children(child, source))
        elif tag == "settings":
            for setting in _child_elements(child, source):
                attrs = _attrs(setting, source)
                if attrs.get("name"):
                    settings[attrs["name"]] = attrs.get("value", "")
        elif tag == "typeAliases":
            for alias in _child_elements(child, source):
                attrs = _attrs(alias, source)
                if _element_name(alias, source) == "typeAlias" and attrs.get("alias"):
                    type_aliases[attrs["alias"]] = attrs.get("type", "")
                elif _element_name(alias, source) == "package" and attrs.get("name"):
                    type_aliases[f"package:{attrs['name']}"] = attrs["name"]
        elif tag == "mappers":
            for mapper in _child_elements(child, source):
                attrs = _attrs(mapper, source)
                if attrs:
                    mappers.append(attrs)
        elif tag == "typeHandlers":
            for handler in _child_elements(child, source):
                attrs = _attrs(handler, source)
                if attrs:
                    attrs["kind"] = _element_name(handler, source)
                    type_handlers.append(attrs)
        elif tag == "plugins":
            for plugin in _child_elements(child, source):
                attrs = _attrs(plugin, source)
                attrs.update({f"property:{k}": v for k, v in _property_children(plugin, source).items()})
                if attrs:
                    plugins.append(attrs)
        elif tag == "environments":
            for env in _child_elements(child, source):
                env_attrs = _attrs(env, source)
                row = {"id": env_attrs.get("id", "")}
                for env_child in _child_elements(env, source):
                    env_tag = _element_name(env_child, source)
                    attrs = _attrs(env_child, source)
                    if env_tag == "transactionManager":
                        row["transactionManager"] = attrs.get("type", "")
                    elif env_tag == "dataSource":
                        row["dataSource"] = attrs.get("type", "")
                        row.update({f"dataSource.{k}": v for k, v in _property_children(env_child, source).items()})
                environments.append(row)
        elif tag == "databaseIdProvider":
            database_id_provider.update(_attrs(child, source))
            database_id_provider.update({f"property:{k}": v for k, v in _property_children(child, source).items()})
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:16]
    return MyBatisConfigFact(
        stable_id=f"mybatis_config::{project_id}::{digest}",
        file_path=file_path,
        source=_span(root_element, file_path),
        properties=properties,
        settings=settings,
        type_aliases=type_aliases,
        type_handlers=tuple(type_handlers),
        plugins=tuple(plugins),
        environments=tuple(environments),
        database_id_provider=database_id_provider,
        mapper_registrations=tuple(mappers),
    )


def _expand_body(
    node,
    source: bytes,
    file_path: str,
    namespace: str,
    owner_id: str,
    fragment_index: Dict[str, List[_ElementView]],
    stack: Tuple[str, ...],
    database_id: str = "",
    inherited_props: Dict[str, str] | None = None,
    depth: int = 0,
    branch_role: str = "",
):
    props = dict(inherited_props or {})
    includes: List[MyBatisIncludeFact] = []
    diagnostics: List[Diagnostic] = []
    dynamic_nodes: List[MyBatisDynamicSqlNodeFact] = []
    parts: List[str] = []
    content = _content(node)
    if content is None:
        return "", includes, diagnostics, dynamic_nodes
    for child in content.children:
        if child.type in {"CharData", "CData"}:
            text = _substitute(_text(child, source), props)
            parts.append(text)
            if text.strip():
                dynamic_nodes.append(dynamic_node(owner_id=owner_id, tag="#text", node_kind="text", source=_span(child, file_path), order=len(dynamic_nodes), text=text))
        elif child.type == "CDSect":
            text = _substitute("".join(_text(grand, source) for grand in child.children if grand.type == "CData"), props)
            parts.append(text)
            if text.strip():
                dynamic_nodes.append(dynamic_node(owner_id=owner_id, tag="#cdata", node_kind="text", source=_span(child, file_path), order=len(dynamic_nodes), text=text))
        elif child.type == "EntityRef":
            text = html.unescape(_text(child, source))
            parts.append(text)
            if text.strip():
                dynamic_nodes.append(dynamic_node(owner_id=owner_id, tag="#entity", node_kind="text", source=_span(child, file_path), order=len(dynamic_nodes), text=text))
        elif child.type == "element":
            tag = _element_name(child, source)
            if tag == "include":
                attrs = _attrs(child, source)
                child_props = {**props, **_property_children(child, source)}
                refid = _substitute(attrs.get("refid", ""), child_props)
                resolved = _qualified_ref(namespace, refid)
                include_id = f"mybatis_include::{owner_id}::{child.start_point[0] + 1}:{child.start_point[1] + 1}:{len(includes)}"
                status = "resolved"
                target = _resolve_fragment(fragment_index, resolved, database_id)
                stack_key = f"{resolved}::{target.database_id if target else ''}"
                if stack_key in stack:
                    status = "cycle"
                    diagnostics.append(Diagnostic("mybatis.xml.include_cycle", f"Include cycle at {resolved}", "warning", file_path, child.start_point[0] + 1, child.end_point[0] + 1))
                elif depth >= 20:
                    status = "max_depth"
                    diagnostics.append(Diagnostic("mybatis.xml.include_depth", f"Include expansion depth exceeded at {resolved}", "warning", file_path, child.start_point[0] + 1, child.end_point[0] + 1))
                elif target is None:
                    status = "unresolved"
                    diagnostics.append(Diagnostic("mybatis.xml.include_unresolved", f"Unable to resolve include {refid!r}", "warning", file_path, child.start_point[0] + 1, child.end_point[0] + 1))
                includes.append(MyBatisIncludeFact(include_id, owner_id, refid, resolved, _span(child, file_path), child_props, status))
                if status == "resolved":
                    expanded, nested, nested_diags, nested_dynamic = _expand_body(
                        target.node,
                        target.source,
                        target.file_path,
                        target.namespace,
                        owner_id,
                        fragment_index,
                        stack + (stack_key,),
                        database_id or target.database_id,
                        child_props,
                        depth + 1,
                        branch_role,
                    )
                    parts.append(expanded)
                    dynamic_nodes.extend(nested_dynamic)
                    includes.extend(nested)
                    diagnostics.extend(nested_diags)
            else:
                role = tag if tag in {"when", "otherwise"} else branch_role
                if tag in DYNAMIC_TAGS:
                    dynamic_nodes.append(
                        dynamic_node(
                            owner_id=owner_id,
                            tag=tag,
                            node_kind="control",
                            source=_span(child, file_path),
                            order=len(dynamic_nodes),
                            attributes=_substitute_attrs(_attrs(child, source), props),
                            branch_role=role,
                        )
                    )
                expanded, nested, nested_diags, nested_dynamic = _expand_body(child, source, file_path, namespace, owner_id, fragment_index, stack, database_id, props, depth, role)
                parts.append(expanded)
                includes.extend(nested)
                diagnostics.extend(nested_diags)
                dynamic_nodes.extend(nested_dynamic)
    return "".join(parts), includes, diagnostics, dynamic_nodes


def _renumber_includes(owner_id: str, includes: List[MyBatisIncludeFact]) -> List[MyBatisIncludeFact]:
    rows: List[MyBatisIncludeFact] = []
    for order, include in enumerate(includes):
        source_digest = hashlib.sha1(include.source.file_path.encode("utf-8")).hexdigest()[:10]
        stable_id = f"mybatis_include::{owner_id}::{order}::{source_digest}:{include.source.start_line}:{include.source.start_column}"
        rows.append(replace(include, stable_id=stable_id))
    return rows


def _renumber_dynamic_nodes(owner_id: str, nodes: List[MyBatisDynamicSqlNodeFact]) -> List[MyBatisDynamicSqlNodeFact]:
    rows: List[MyBatisDynamicSqlNodeFact] = []
    for order, node in enumerate(nodes):
        source_digest = hashlib.sha1(node.source.file_path.encode("utf-8")).hexdigest()[:10]
        stable_id = f"mybatis_dynamic::{owner_id}::{order}::{source_digest}:{node.source.start_line}:{node.source.start_column}"
        rows.append(replace(node, stable_id=stable_id, order=order))
    return rows


def _resolve_fragment(fragment_index: Dict[str, List[_ElementView]], resolved: str, database_id: str) -> _ElementView | None:
    candidates = fragment_index.get(resolved, ())
    if not candidates:
        return None
    if database_id:
        for candidate in candidates:
            if candidate.database_id == database_id:
                return candidate
    for candidate in candidates:
        if not candidate.database_id:
            return candidate
    return candidates[0] if not database_id else None


def _first_element(node):
    if node.type == "element":
        return node
    for child in node.children:
        found = _first_element(child)
        if found is not None:
            return found
    return None


def _child_elements(node, source: bytes):
    content = _content(node)
    if content is None:
        return []
    return [child for child in content.named_children if child.type == "element"]


def _descendant_elements(node, source: bytes):
    for child in _child_elements(node, source):
        yield child
        yield from _descendant_elements(child, source)


def _content(node):
    return next((child for child in node.children if child.type == "content"), None)


def _element_name(node, source: bytes) -> str:
    tag = next((child for child in node.children if child.type in {"STag", "EmptyElemTag"}), None)
    if tag is None:
        return ""
    name = next((child for child in tag.children if child.type == "Name"), None)
    return _text(name, source)


def _attrs(node, source: bytes) -> Dict[str, str]:
    tag = next((child for child in node.children if child.type in {"STag", "EmptyElemTag"}), None)
    attrs: Dict[str, str] = {}
    if tag is None:
        return attrs
    for child in tag.children:
        if child.type != "Attribute":
            continue
        name = next((item for item in child.children if item.type == "Name"), None)
        value = next((item for item in child.children if item.type == "AttValue"), None)
        key = _text(name, source)
        raw = _text(value, source)
        value_text = raw[1:-1] if len(raw) >= 2 and raw[0] in {"'", '"'} else raw
        attrs[key] = html.unescape(value_text)
    return attrs


def _property_children(node, source: bytes) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for child in _child_elements(node, source):
        if _element_name(child, source) == "property":
            attrs = _attrs(child, source)
            if attrs.get("name"):
                props[attrs["name"]] = attrs.get("value", "")
    return props


def _raw_body(node, source: bytes) -> str:
    content = _content(node)
    return _text(content, source) if content is not None else ""


def _substitute(text: str, props: Dict[str, str]) -> str:
    return _PROPERTY_RE.sub(lambda match: props.get(match.group(1), match.group(0)), text or "")


def _substitute_attrs(attrs: Dict[str, str], props: Dict[str, str]) -> Dict[str, str]:
    return {key: _substitute(value, props) for key, value in attrs.items()}


def _qualified_ref(namespace: str, refid: str) -> str:
    return refid if "." in refid else f"{namespace}.{refid}" if namespace else refid


def _doctype(source: bytes) -> str:
    match = _DOCTYPE_RE.search(source.decode("utf-8", errors="ignore"))
    return match.group(0) if match else ""


def _span(node, file_path: str) -> SourceSpan:
    return SourceSpan(file_path, node.start_point[0] + 1, node.end_point[0] + 1, node.start_point[1] + 1, node.end_point[1] + 1)


def _text(node, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
