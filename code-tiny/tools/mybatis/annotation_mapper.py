from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from tools.mybatis.models import (
    Diagnostic,
    MyBatisMapperMethodFact,
    MyBatisProviderFact,
    MyBatisResultMapFact,
    MyBatisResultMappingFact,
    MyBatisStatementFact,
    SourceSpan,
)


_SQL_ANNOTATIONS: Dict[str, str] = {
    "org.apache.ibatis.annotations.Select": "select",
    "org.apache.ibatis.annotations.Insert": "insert",
    "org.apache.ibatis.annotations.Update": "update",
    "org.apache.ibatis.annotations.Delete": "delete",
}
_PROVIDER_ANNOTATIONS: Dict[str, str] = {
    "org.apache.ibatis.annotations.SelectProvider": "select",
    "org.apache.ibatis.annotations.InsertProvider": "insert",
    "org.apache.ibatis.annotations.UpdateProvider": "update",
    "org.apache.ibatis.annotations.DeleteProvider": "delete",
}


@dataclass(frozen=True)
class AnnotationMapperAnalysis:
    statements: Tuple[MyBatisStatementFact, ...]
    result_maps: Tuple[MyBatisResultMapFact, ...]
    result_mappings: Tuple[MyBatisResultMappingFact, ...]
    providers: Tuple[MyBatisProviderFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


def analyze_annotation_mappers(
    *,
    mapper_methods: Sequence[MyBatisMapperMethodFact],
    project_id: str,
) -> AnnotationMapperAnalysis:
    statements: List[MyBatisStatementFact] = []
    result_maps: List[MyBatisResultMapFact] = []
    result_mappings: List[MyBatisResultMappingFact] = []
    providers: List[MyBatisProviderFact] = []
    diagnostics: List[Diagnostic] = []

    for method in mapper_methods:
        statement_attrs: Dict[str, str] = {"source_kind": "annotation", "java_method_id": method.stable_id}
        synthetic_result_map_id = ""
        for annotation in method.annotations:
            if annotation.resolved_name == "org.apache.ibatis.annotations.ResultMap":
                value = _first_annotation_value(annotation.raw_arguments)
                if value:
                    statement_attrs["resultMap"] = value
            elif annotation.resolved_name == "org.apache.ibatis.annotations.Results":
                result_map, mappings = _results_to_result_map(method, annotation.raw_arguments, project_id)
                if result_map is not None:
                    result_maps.append(result_map)
                    result_mappings.extend(mappings)
                    synthetic_result_map_id = result_map.result_map_id
        if synthetic_result_map_id and "resultMap" not in statement_attrs:
            statement_attrs["resultMap"] = synthetic_result_map_id

        for annotation in method.annotations:
            statement_kind = _SQL_ANNOTATIONS.get(annotation.resolved_name)
            if statement_kind:
                sql_text = _sql_from_annotation(annotation.raw_arguments)
                if not sql_text.strip():
                    diagnostics.append(
                        Diagnostic(
                            "mybatis.annotation.empty_sql",
                            f"{annotation.name} on {method.mapper_fqcn}.{method.name} has no static SQL literal",
                            "warning",
                            annotation.source.file_path,
                            annotation.source.start_line,
                            annotation.source.end_line,
                        )
                    )
                    continue
                stable_id = _statement_id(project_id, method, statement_kind)
                statements.append(
                    MyBatisStatementFact(
                        stable_id=stable_id,
                        namespace=method.mapper_fqcn,
                        statement_id=method.name,
                        statement_kind=statement_kind,
                        source=annotation.source,
                        database_id="annotation",
                        attributes={**statement_attrs, "annotation": annotation.resolved_name},
                        raw_body=sql_text,
                        expanded_body=sql_text,
                    )
                )
                continue

            provider_kind = _PROVIDER_ANNOTATIONS.get(annotation.resolved_name)
            if provider_kind:
                attrs = _named_values(annotation.raw_arguments)
                provider_id = _provider_id(project_id, method, provider_kind)
                providers.append(
                    MyBatisProviderFact(
                        stable_id=provider_id,
                        mapper_method_id=method.stable_id,
                        namespace=method.mapper_fqcn,
                        statement_id=method.name,
                        provider_kind=provider_kind,
                        source=annotation.source,
                        provider_type=attrs.get("type", attrs.get("value", "")),
                        provider_method=attrs.get("method", ""),
                        raw_arguments=annotation.raw_arguments,
                        attributes=attrs,
                    )
                )

    return AnnotationMapperAnalysis(
        tuple(statements),
        tuple(result_maps),
        tuple(result_mappings),
        tuple(providers),
        tuple(diagnostics),
    )


def _statement_id(project_id: str, method: MyBatisMapperMethodFact, statement_kind: str) -> str:
    return f"mybatis_stmt::{project_id}::{method.mapper_fqcn}::{method.name}::annotation:{statement_kind}"


def _provider_id(project_id: str, method: MyBatisMapperMethodFact, provider_kind: str) -> str:
    digest = hashlib.sha1(f"{method.stable_id}:{provider_kind}".encode("utf-8")).hexdigest()[:12]
    return f"mybatis_provider::{project_id}::{method.mapper_fqcn}::{method.name}::{provider_kind}:{digest}"


def _result_map_id(project_id: str, method: MyBatisMapperMethodFact) -> str:
    return f"mybatis_result_map::{project_id}::{method.mapper_fqcn}::{method.name}::annotationResults"


def _sql_from_annotation(raw_arguments: str) -> str:
    values = _string_literals(raw_arguments)
    text = " ".join(value.strip() for value in values if value.strip())
    return _strip_script_wrapper(text)


def _first_annotation_value(raw_arguments: str) -> str:
    values = _string_literals(raw_arguments)
    return values[0] if values else ""


def _string_literals(raw_arguments: str) -> List[str]:
    rows: List[str] = []
    for match in re.finditer(r'"(?:\\.|[^"\\])*"', raw_arguments or ""):
        literal = match.group(0)
        try:
            rows.append(str(ast.literal_eval(literal)))
        except Exception:
            rows.append(literal.strip('"'))
    return rows


def _strip_script_wrapper(text: str) -> str:
    stripped = (text or "").strip()
    match = re.fullmatch(r"(?is)<script>\s*(.*?)\s*</script>", stripped)
    return match.group(1).strip() if match else stripped


def _named_values(raw_arguments: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for key, identifier, literal in re.findall(r"(\w+)\s*=\s*(?:([A-Za-z_][\w.]*)(?:\.class)?|(\"(?:\\.|[^\"\\])*\"))", raw_arguments or ""):
        value = literal or identifier or ""
        if value.startswith('"'):
            try:
                value = str(ast.literal_eval(value))
            except Exception:
                value = value.strip('"')
        values[key] = value
    unnamed = _string_literals(raw_arguments)
    if unnamed and "value" not in values:
        values["value"] = unnamed[0]
    return values


def _results_to_result_map(
    method: MyBatisMapperMethodFact,
    raw_arguments: str,
    project_id: str,
) -> Tuple[MyBatisResultMapFact | None, Tuple[MyBatisResultMappingFact, ...]]:
    result_chunks = re.findall(r"@(?:org\.apache\.ibatis\.annotations\.)?Result\s*\((.*?)\)", raw_arguments or "", re.DOTALL)
    if not result_chunks:
        return None, ()
    map_id = _result_map_id(project_id, method)
    synthetic_name = f"{method.name}::annotationResults"
    mappings: List[MyBatisResultMappingFact] = []
    for index, chunk in enumerate(result_chunks):
        attrs = _named_values(chunk)
        property_name = attrs.get("property", "")
        column = attrs.get("column", "")
        mappings.append(
            MyBatisResultMappingFact(
                stable_id=f"mybatis_result_mapping::{map_id}::{index}",
                result_map_id=map_id,
                mapping_kind="result",
                source=method.source,
                property_name=property_name,
                column=column,
                java_type=attrs.get("javaType", ""),
                jdbc_type=attrs.get("jdbcType", ""),
                attributes=attrs,
            )
        )
    return (
        MyBatisResultMapFact(
            stable_id=map_id,
            namespace=method.mapper_fqcn,
            result_map_id=synthetic_name,
            source=method.source,
            java_type=_unwrap_collection(method.return_type),
            mappings=tuple(mappings),
        ),
        tuple(mappings),
    )


def _unwrap_collection(type_text: str) -> str:
    match = re.search(r"<\s*([A-Za-z_][\w.]*)\s*>", type_text or "")
    return match.group(1) if match else type_text
