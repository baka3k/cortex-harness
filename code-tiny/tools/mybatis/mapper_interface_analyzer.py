from __future__ import annotations

import os
import re
import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from tools.java.java_analyzer import parse_java_file
from tools.mybatis.models import (
    Diagnostic,
    MyBatisAnnotationFact,
    MyBatisJavaPropertyFact,
    MyBatisMapperInterfaceFact,
    MyBatisMapperMethodFact,
    MyBatisMapperParameterFact,
    SourceSpan,
)
from tools.mybatis.detector import _EXCLUDED_DIRS
from tools.mybatis.parser_runtime import load_parser


_MYBATIS_ANNOTATION_PREFIX = "org.apache.ibatis.annotations."
_SQL_ANNOTATIONS = {
    "Select",
    "Insert",
    "Update",
    "Delete",
    "SelectProvider",
    "InsertProvider",
    "UpdateProvider",
    "DeleteProvider",
    "Results",
    "Result",
    "ResultMap",
    "Options",
    "Flush",
    "CacheNamespace",
    "CacheNamespaceRef",
    "ConstructorArgs",
    "Arg",
    "One",
    "Many",
    "MapKey",
}
_SPECIAL_PARAMETERS = {
    "RowBounds": "row_bounds",
    "org.apache.ibatis.session.RowBounds": "row_bounds",
    "ResultHandler": "result_handler",
    "org.apache.ibatis.session.ResultHandler": "result_handler",
}
@dataclass(frozen=True)
class MapperInterfaceAnalysis:
    interfaces: Tuple[MyBatisMapperInterfaceFact, ...]
    methods: Tuple[MyBatisMapperMethodFact, ...]
    parameters: Tuple[MyBatisMapperParameterFact, ...]
    java_properties: Tuple[MyBatisJavaPropertyFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


def analyze_mapper_interfaces(
    *,
    root: str,
    java_files: Sequence[str],
    project_id: str,
    project_name: str,
) -> MapperInterfaceAnalysis:
    interfaces: List[MyBatisMapperInterfaceFact] = []
    methods: List[MyBatisMapperMethodFact] = []
    parameters: List[MyBatisMapperParameterFact] = []
    diagnostics: List[Diagnostic] = []
    referenced_types: Set[str] = set()

    for rel_path in sorted(set(java_files)):
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            diagnostics.append(Diagnostic("mybatis.java.missing_file", "Mapper Java file is missing", "warning", rel_path))
            continue
        try:
            file_result = analyze_mapper_interface_file(root=root, rel_path=rel_path, project_id=project_id)
        except Exception as exc:
            diagnostics.append(Diagnostic("mybatis.java.parse_failed", str(exc), "error", rel_path))
            continue
        interfaces.extend(file_result.interfaces)
        methods.extend(file_result.methods)
        parameters.extend(file_result.parameters)
        diagnostics.extend(file_result.diagnostics)
        default_package = file_result.interfaces[0].package_name if file_result.interfaces else ""
        for method in file_result.methods:
            referenced_types.update(_referenced_type_candidates(method.return_type, default_package))
            for parameter_type in method.parameter_types:
                referenced_types.update(_referenced_type_candidates(parameter_type, default_package))

    properties = build_java_type_index(root=root, referenced_types=referenced_types, project_id=project_id)
    return MapperInterfaceAnalysis(
        interfaces=tuple(interfaces),
        methods=tuple(methods),
        parameters=tuple(parameters),
        java_properties=tuple(properties),
        diagnostics=tuple(diagnostics),
    )


def analyze_mapper_interface_file(*, root: str, rel_path: str, project_id: str) -> MapperInterfaceAnalysis:
    abs_path = os.path.join(root, rel_path)
    with open(abs_path, "rb") as handle:
        source_bytes = handle.read()
    parser = load_parser("java")
    tree = parser.parse(source_bytes)
    package_name = _package_name(tree.root_node, source_bytes)
    imports = _imports(tree.root_node, source_bytes)
    java_symbols = _java_symbol_maps(abs_path, root)

    interfaces: List[MyBatisMapperInterfaceFact] = []
    methods: List[MyBatisMapperMethodFact] = []
    parameters: List[MyBatisMapperParameterFact] = []
    diagnostics: List[Diagnostic] = []

    for node, class_path in _iter_type_declarations(tree.root_node, source_bytes, want={"interface_declaration"}):
        name = _decl_name(node, source_bytes)
        if not name:
            continue
        fqcn = _fqcn(package_name, class_path)
        class_symbol_id = java_symbols.class_ids.get(fqcn, f"class::{fqcn}")
        if not _interface_has_mybatis_evidence(node, source_bytes, imports, rel_path):
            continue
        annotations = tuple(_annotations(node, source_bytes, imports, rel_path))
        mapper_id = f"mybatis_mapper::{project_id}::{fqcn}"
        method_facts, param_facts, method_diags = _extract_methods(
            node=node,
            source_bytes=source_bytes,
            imports=imports,
            project_id=project_id,
            mapper_fqcn=fqcn,
            class_path=class_path,
            java_symbols=java_symbols,
            file_path=rel_path,
        )
        interfaces.append(
            MyBatisMapperInterfaceFact(
                stable_id=mapper_id,
                java_class_symbol_id=class_symbol_id,
                name=name,
                fqcn=fqcn,
                file_path=rel_path,
                source=_span(node, rel_path),
                package_name=package_name,
                type_parameters=tuple(_type_parameters(node, source_bytes)),
                extended_interfaces=tuple(_extends_interfaces(node, source_bytes)),
                modifiers=tuple(_modifiers(node, source_bytes)),
                imports=tuple(imports),
                annotations=annotations,
                methods=tuple(method_facts),
            )
        )
        methods.extend(method_facts)
        parameters.extend(param_facts)
        diagnostics.extend(method_diags)

    return MapperInterfaceAnalysis(tuple(interfaces), tuple(methods), tuple(parameters), (), tuple(diagnostics))


@dataclass(frozen=True)
class _JavaSymbols:
    class_ids: Dict[str, str]
    method_ids: Dict[Tuple[str, str, int, int], str]
    method_ids_fallback: Dict[Tuple[str, str, int], str]


def _java_symbol_maps(path: str, root: str) -> _JavaSymbols:
    functions, _, classes, _, _, _, _, _, _ = parse_java_file(path, root)
    class_ids = {item.qualified_name: item.symbol_id for item in classes}
    method_ids: Dict[Tuple[str, str, int, int], str] = {}
    fallback: Dict[Tuple[str, str, int], str] = {}
    for item in functions:
        if not item.class_name:
            continue
        key = (item.class_name, item.name, item.arity, item.start_line)
        method_ids[key] = item.symbol_id
        fallback.setdefault((item.class_name, item.name, item.arity), item.symbol_id)
    return _JavaSymbols(class_ids, method_ids, fallback)


def _extract_methods(
    *,
    node,
    source_bytes: bytes,
    imports: Sequence[str],
    project_id: str,
    mapper_fqcn: str,
    class_path: str,
    java_symbols: _JavaSymbols,
    file_path: str,
) -> Tuple[List[MyBatisMapperMethodFact], List[MyBatisMapperParameterFact], List[Diagnostic]]:
    body = node.child_by_field_name("body")
    method_nodes = [
        child
        for child in (body.named_children if body is not None else ())
        if child.type in {"method_declaration", "constructor_declaration"}
    ]
    name_counts: Dict[str, int] = {}
    for method_node in method_nodes:
        name = _method_name(method_node, source_bytes)
        if name and _is_xml_bindable_candidate(method_node, source_bytes, imports):
            name_counts[name] = name_counts.get(name, 0) + 1

    methods: List[MyBatisMapperMethodFact] = []
    parameters: List[MyBatisMapperParameterFact] = []
    diagnostics: List[Diagnostic] = []
    for method_node in method_nodes:
        name = _method_name(method_node, source_bytes)
        if not name:
            continue
        param_nodes = _parameter_nodes(method_node)
        param_types = tuple(_canonical_type(_parameter_type(param, source_bytes), imports) for param in param_nodes)
        return_type = _canonical_type(_return_type(method_node, source_bytes), imports)
        start_line = method_node.start_point[0] + 1
        java_arity = _java_analyzer_arity(param_nodes)
        java_symbol_id = java_symbols.method_ids.get(
            (class_path, name, java_arity, start_line),
            java_symbols.method_ids_fallback.get((class_path, name, java_arity), ""),
        )
        base_method_id = java_symbol_id or mapper_fqcn + "#" + name + "/" + str(len(param_nodes))
        stable_id = f"mybatis_method::{project_id}::{base_method_id}"
        if name_counts.get(name, 0) > 1:
            stable_id = f"{stable_id}::{_signature_suffix(param_types)}"
        annotations = tuple(_annotations(method_node, source_bytes, imports, file_path))
        modifiers = tuple(_modifiers(method_node, source_bytes))
        has_body = method_node.child_by_field_name("body") is not None
        bindable = _is_xml_bindable_candidate(method_node, source_bytes, imports)
        overload_count = name_counts.get(name, 0) if bindable else 0
        ambiguity = "ambiguous" if overload_count > 1 else "unique"
        if overload_count > 1:
            diagnostics.append(
                Diagnostic(
                    "mybatis.mapper_method.overloaded_statement_id",
                    f"Mapper statement id {name!r} has {overload_count} bindable overloads",
                    "warning",
                    file_path,
                    start_line,
                    method_node.end_point[0] + 1,
                )
            )
        method_params: List[MyBatisMapperParameterFact] = []
        for idx, param_node in enumerate(param_nodes):
            param = _parameter_fact(
                param_node=param_node,
                source_bytes=source_bytes,
                imports=imports,
                project_id=project_id,
                method_id=stable_id,
                position=idx,
                file_path=file_path,
            )
            method_params.append(param)
            parameters.append(param)
        methods.append(
            MyBatisMapperMethodFact(
                stable_id=stable_id,
                java_symbol_id=java_symbol_id,
                mapper_fqcn=mapper_fqcn,
                name=name,
                signature=f"{name}({', '.join(param_types)})",
                return_type=return_type,
                parameter_types=param_types,
                source=_span(method_node, file_path),
                bindable=bindable,
                overload_count=overload_count or 1,
                ambiguity_status=ambiguity,
                modifiers=modifiers,
                throws=tuple(_throws(method_node, source_bytes)),
                annotations=annotations,
                parameters=tuple(method_params),
                has_body=has_body,
            )
        )
    return methods, parameters, diagnostics


def _parameter_fact(*, param_node, source_bytes: bytes, imports: Sequence[str], project_id: str, method_id: str, position: int, file_path: str) -> MyBatisMapperParameterFact:
    name = _node_text(param_node.child_by_field_name("name"), source_bytes)
    raw_type = _parameter_type(param_node, source_bytes)
    canonical = _canonical_type(raw_type, imports)
    annotations = tuple(_annotations(param_node, source_bytes, imports, file_path))
    alias = ""
    for annotation in annotations:
        if annotation.resolved_name == f"{_MYBATIS_ANNOTATION_PREFIX}Param":
            alias = _first_string_literal(annotation.raw_arguments)
    return MyBatisMapperParameterFact(
        stable_id=f"{method_id}::param::{position}",
        mapper_method_id=method_id,
        name=name,
        position=position,
        java_type=raw_type,
        canonical_type=canonical,
        param_alias=alias,
        special_role=_SPECIAL_PARAMETERS.get(canonical, _SPECIAL_PARAMETERS.get(raw_type, "")),
        source=_span(param_node, file_path),
        annotations=annotations,
    )


def build_java_type_index(*, root: str, referenced_types: Iterable[str], project_id: str) -> Tuple[MyBatisJavaPropertyFact, ...]:
    wanted = {item for item in referenced_types if item}
    if not wanted:
        return ()
    parser = load_parser("java")
    properties: Dict[str, MyBatisJavaPropertyFact] = {}
    for abs_path, rel_path in _iter_java_files(root):
        with open(abs_path, "rb") as handle:
            source_bytes = handle.read()
        tree = parser.parse(source_bytes)
        package_name = _package_name(tree.root_node, source_bytes)
        for node, class_path in _iter_type_declarations(tree.root_node, source_bytes, want={"class_declaration", "record_declaration"}):
            fqcn = _fqcn(package_name, class_path)
            if fqcn not in wanted and _simple_type_name(fqcn) not in wanted:
                continue
            for prop_name, prop_type, source_kind, prop_node, readable, writable in _property_rows(node, source_bytes):
                stable_id = f"mybatis_java_property::{project_id}::{fqcn}::{prop_name}"
                next_fact = MyBatisJavaPropertyFact(
                    stable_id=stable_id,
                    java_type_fqcn=fqcn,
                    property_name=prop_name,
                    property_type=_canonical_type(prop_type, ()),
                    source_kind=source_kind,
                    source=_span(prop_node, rel_path),
                    readable=readable,
                    writable=writable,
                )
                existing = properties.get(stable_id)
                if existing is not None:
                    kinds = sorted(set(existing.source_kind.split(",")) | {source_kind})
                    next_fact = MyBatisJavaPropertyFact(
                        stable_id=stable_id,
                        java_type_fqcn=fqcn,
                        property_name=prop_name,
                        property_type=existing.property_type or next_fact.property_type,
                        source_kind=",".join(kinds),
                        source=existing.source,
                        readable=existing.readable or readable,
                        writable=existing.writable or writable,
                    )
                properties[stable_id] = next_fact
    return tuple(properties[key] for key in sorted(properties))


def _property_rows(node, source_bytes: bytes):
    if node.type == "record_declaration":
        params = node.child_by_field_name("parameters")
        if params is not None:
            for param in _parameter_nodes(params):
                name = _node_text(param.child_by_field_name("name"), source_bytes)
                yield name, _parameter_type(param, source_bytes), "record_component", param, True, True
    for child in _walk(node):
        if child.type == "field_declaration":
            type_text = _node_text(child.child_by_field_name("type"), source_bytes)
            declarator = child.child_by_field_name("declarator")
            name_node = declarator.child_by_field_name("name") if declarator is not None else None
            name = _node_text(name_node, source_bytes)
            if name:
                yield name, type_text, "field", child, True, True
        elif child.type == "method_declaration":
            name = _method_name(child, source_bytes)
            if not name:
                continue
            params = _parameter_nodes(child)
            return_type = _return_type(child, source_bytes)
            if not params and name.startswith("get") and len(name) > 3:
                yield _decap(name[3:]), return_type, "getter", child, True, False
            elif not params and name.startswith("is") and len(name) > 2 and return_type in {"boolean", "Boolean"}:
                yield _decap(name[2:]), return_type, "getter", child, True, False
            elif len(params) == 1 and name.startswith("set") and len(name) > 3:
                yield _decap(name[3:]), _parameter_type(params[0], source_bytes), "setter", child, False, True


def _iter_java_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".java"):
                abs_path = os.path.join(dirpath, name)
                yield abs_path, os.path.relpath(abs_path, root).replace("\\", "/")


def _iter_type_declarations(node, source_bytes: bytes, *, want: Set[str], stack: Tuple[str, ...] = ()):
    if node.type in {"class_declaration", "interface_declaration", "record_declaration"}:
        name = _decl_name(node, source_bytes)
        path = stack + ((name,) if name else ())
        if node.type in want:
            yield node, ".".join(path)
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                yield from _iter_type_declarations(child, source_bytes, want=want, stack=path)
        return
    for child in node.children:
        yield from _iter_type_declarations(child, source_bytes, want=want, stack=stack)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(node, source_bytes: bytes) -> str:
    if node is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore").strip()


def _span(node, file_path: str) -> SourceSpan:
    return SourceSpan(file_path, node.start_point[0] + 1, node.end_point[0] + 1, node.start_point[1] + 1, node.end_point[1] + 1)


def _package_name(root, source_bytes: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            text = _node_text(child, source_bytes)
            return text.removeprefix("package").rstrip(";").strip()
    return ""


def _imports(root, source_bytes: bytes) -> Tuple[str, ...]:
    rows: List[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            text = _node_text(child, source_bytes)
            text = re.sub(r"^import\s+static\s+", "", text)
            text = re.sub(r"^import\s+", "", text).rstrip(";").strip()
            rows.append(text)
    return tuple(rows)


def _decl_name(node, source_bytes: bytes) -> str:
    return _node_text(node.child_by_field_name("name"), source_bytes)


def _fqcn(package_name: str, class_path: str) -> str:
    return f"{package_name}.{class_path}" if package_name else class_path


def _modifiers(node, source_bytes: bytes) -> List[str]:
    mods = node.child_by_field_name("modifiers")
    if mods is None:
        for child in node.children:
            if child.type == "modifiers":
                mods = child
                break
    if mods is None:
        return []
    return [_node_text(child, source_bytes) for child in mods.children if child.type not in {"annotation", "marker_annotation"}]


def _annotations(node, source_bytes: bytes, imports: Sequence[str], file_path: str = "") -> List[MyBatisAnnotationFact]:
    results: List[MyBatisAnnotationFact] = []
    mods = node.child_by_field_name("modifiers")
    if mods is None:
        for child in node.children:
            if child.type == "modifiers":
                mods = child
                break
    if mods is None and node.type == "formal_parameter":
        mods = next((child for child in node.children if child.type == "modifiers"), None)
    if mods is None:
        return results
    for child in mods.children:
        if child.type not in {"annotation", "marker_annotation"}:
            continue
        name = _node_text(child.child_by_field_name("name"), source_bytes)
        args = _node_text(child.child_by_field_name("arguments"), source_bytes)
        results.append(MyBatisAnnotationFact(name=name, resolved_name=_resolve_annotation(name, imports), raw_arguments=args, source=_span(child, file_path)))
    return results


def _resolve_annotation(name: str, imports: Sequence[str]) -> str:
    if "." in name:
        return name
    for item in imports:
        if item.endswith(f".{name}"):
            return item
        if item == f"{_MYBATIS_ANNOTATION_PREFIX.rstrip('.')}.*":
            return f"{_MYBATIS_ANNOTATION_PREFIX}{name}"
    if name in _SQL_ANNOTATIONS or name == "Mapper" or name == "Param":
        return f"{_MYBATIS_ANNOTATION_PREFIX}{name}" if name != "Mapper" else "org.apache.ibatis.annotations.Mapper"
    return name


def _type_parameters(node, source_bytes: bytes) -> List[str]:
    child = next((item for item in node.children if item.type == "type_parameters"), None)
    if child is None:
        return []
    return [_node_text(item, source_bytes) for item in child.children if item.type == "type_parameter"]


def _extends_interfaces(node, source_bytes: bytes) -> List[str]:
    child = next((item for item in node.children if item.type == "extends_interfaces"), None)
    if child is None:
        return []
    type_list = next((item for item in child.children if item.type == "type_list"), child)
    return [_node_text(item, source_bytes) for item in type_list.named_children]


def _method_name(node, source_bytes: bytes) -> str:
    return _node_text(node.child_by_field_name("name"), source_bytes)


def _return_type(node, source_bytes: bytes) -> str:
    return _node_text(node.child_by_field_name("type"), source_bytes) or "void"


def _parameter_nodes(node) -> List:
    params = node.child_by_field_name("parameters") if hasattr(node, "child_by_field_name") else None
    if params is None and node.type == "formal_parameters":
        params = node
    if params is None:
        return []
    return [child for child in params.named_children if child.type in {"formal_parameter", "spread_parameter"}]


def _parameter_type(node, source_bytes: bytes) -> str:
    text = _node_text(node.child_by_field_name("type"), source_bytes)
    if not text and node.type == "spread_parameter":
        type_node = next(
            (
                child
                for child in node.named_children
                if child.type in {"type_identifier", "scoped_type_identifier", "generic_type"}
                or child.type.endswith("type")
            ),
            None,
        )
        text = _node_text(type_node, source_bytes)
    return f"{text}..." if node.type == "spread_parameter" and text and not text.endswith("...") else text


def _throws(node, source_bytes: bytes) -> List[str]:
    child = next((item for item in node.children if item.type == "throws"), None)
    if child is None:
        return []
    return [_node_text(item, source_bytes) for item in child.named_children]


def _canonical_type(raw: str, imports: Sequence[str]) -> str:
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if not raw:
        return ""
    suffix = ""
    base = raw
    if raw.endswith("..."):
        suffix = "..."
        base = raw[:-3]
    elif raw.endswith("[]"):
        suffix = "[]"
        base = raw[:-2]
    match = re.match(r"^([A-Za-z_][\w.]*)", base)
    if not match:
        return raw
    head = match.group(1)
    simple = head.rsplit(".", 1)[-1]
    resolved = head
    for item in imports:
        if item.endswith(f".{simple}"):
            resolved = item
            break
        if item.endswith(".*") and "." not in head and item.startswith("org.apache.ibatis."):
            resolved = f"{item[:-2]}.{simple}"
    return resolved + base[len(head) :] + suffix


def _simple_type_name(raw: str) -> str:
    text = re.sub(r"<.*>", "", raw or "").replace("[]", "").replace("...", "").strip()
    if not text or text in {"void", "boolean", "byte", "short", "int", "long", "float", "double", "char"}:
        return ""
    return text.rsplit(".", 1)[-1]


def _is_xml_bindable_candidate(node, source_bytes: bytes, imports: Sequence[str]) -> bool:
    annotations = _annotations(node, source_bytes, imports)
    has_mybatis_annotation = any(item.resolved_name.startswith(_MYBATIS_ANNOTATION_PREFIX) for item in annotations)
    modifiers = set(_modifiers(node, source_bytes))
    if has_mybatis_annotation:
        return True
    if "default" in modifiers or "static" in modifiers:
        return False
    return node.type == "method_declaration"


def _interface_has_mybatis_evidence(node, source_bytes: bytes, imports: Sequence[str], file_path: str) -> bool:
    annotations = _annotations(node, source_bytes, imports, file_path)
    if any(item.resolved_name == "org.apache.ibatis.annotations.Mapper" for item in annotations):
        return True
    body = node.child_by_field_name("body")
    if body is None:
        return False
    for child in body.named_children:
        if child.type != "method_declaration":
            continue
        method_annotations = _annotations(child, source_bytes, imports, file_path)
        if any(item.resolved_name.startswith(_MYBATIS_ANNOTATION_PREFIX) for item in method_annotations):
            return True
    return False


def _java_analyzer_arity(param_nodes: Sequence) -> int:
    return sum(1 for item in param_nodes if item.type == "formal_parameter")


def _signature_suffix(parameter_types: Sequence[str]) -> str:
    return hashlib.sha1("|".join(parameter_types).encode("utf-8")).hexdigest()[:12]


def _referenced_type_candidates(type_text: str, default_package: str = "") -> Set[str]:
    ignored = {
        "void",
        "boolean",
        "byte",
        "short",
        "int",
        "long",
        "float",
        "double",
        "char",
        "String",
        "List",
        "Map",
        "Set",
        "Collection",
        "Optional",
    }
    rows: Set[str] = set()
    for token in re.findall(r"[A-Za-z_][\w.]*", type_text or ""):
        simple = token.rsplit(".", 1)[-1]
        if simple in ignored:
            continue
        if "." in token or not default_package:
            rows.add(token)
        else:
            rows.add(f"{default_package}.{token}")
    return rows


def _first_string_literal(text: str) -> str:
    match = re.search(r'"([^"]*)"', text or "")
    return match.group(1) if match else ""


def _decap(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text
