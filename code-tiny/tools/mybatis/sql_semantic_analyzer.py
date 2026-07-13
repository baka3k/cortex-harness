from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from tools.mybatis.models import (
    Diagnostic,
    MyBatisSqlColumnFact,
    MyBatisSqlJoinFact,
    MyBatisSqlParameterFact,
    MyBatisSqlStatementSemanticFact,
    MyBatisSqlTableFact,
    MyBatisStatementFact,
    SourceSpan,
)
from tools.sql.sql_analyzer import _get_sql_parser


_BOUND_PARAM_RE = re.compile(r"#\{\s*([^,}]+?)\s*(?:,\s*([^}]+?))?\s*\}")
_TEXT_PARAM_RE = re.compile(r"\$\{\s*([^}]+?)\s*\}")


@dataclass(frozen=True)
class SqlSemanticAnalysis:
    statements: Tuple[MyBatisSqlStatementSemanticFact, ...]
    tables: Tuple[MyBatisSqlTableFact, ...]
    columns: Tuple[MyBatisSqlColumnFact, ...]
    joins: Tuple[MyBatisSqlJoinFact, ...]
    parameters: Tuple[MyBatisSqlParameterFact, ...]
    diagnostics: Tuple[Diagnostic, ...]


@dataclass(frozen=True)
class _ParamToken:
    token: str
    parameter_kind: str
    name: str
    options: Dict[str, str]
    position: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class _DynamicSegment:
    start_byte: int
    end_byte: int
    dynamic_node_ids: Tuple[str, ...]
    branch_roles: Tuple[str, ...]


def analyze_sql_semantics(*, statements: Sequence[MyBatisStatementFact], project_id: str) -> SqlSemanticAnalysis:
    semantic_statements: List[MyBatisSqlStatementSemanticFact] = []
    tables: List[MyBatisSqlTableFact] = []
    columns: List[MyBatisSqlColumnFact] = []
    joins: List[MyBatisSqlJoinFact] = []
    parameters: List[MyBatisSqlParameterFact] = []
    diagnostics: List[Diagnostic] = []

    try:
        parser = _get_sql_parser()
    except Exception as exc:
        for stmt in statements:
            normalized, tokens = _normalize_placeholders(stmt.expanded_body)
            sql_fact = _statement_fact(stmt, normalized, "parser_unavailable", 0, "", bool([t for t in tokens if t.parameter_kind == "textual"]))
            semantic_statements.append(sql_fact)
            parameters.extend(_parameter_facts(sql_fact.stable_id, stmt.source, stmt.expanded_body, tokens, _dynamic_segments(stmt)))
            diagnostics.append(Diagnostic("mybatis.sql.parser_unavailable", str(exc), "error", stmt.source.file_path, stmt.source.start_line, stmt.source.end_line))
        return SqlSemanticAnalysis(tuple(semantic_statements), tuple(tables), tuple(columns), tuple(joins), tuple(parameters), tuple(diagnostics))

    for stmt in statements:
        normalized, tokens = _normalize_placeholders(stmt.expanded_body)
        source_bytes = normalized.encode("utf-8")
        tree = parser.parse(source_bytes)
        error_count = _count_errors(tree.root_node)
        crud = _crud(tree.root_node)
        parser_status = "parsed"
        if error_count:
            parser_status = "partial"
        elif not crud:
            parser_status = "unsupported"
        has_textual = any(token.parameter_kind == "textual" for token in tokens)
        sql_fact = _statement_fact(stmt, normalized, parser_status, error_count, crud, has_textual)
        semantic_statements.append(sql_fact)
        dynamic_segments = _dynamic_segments(stmt)
        parameters.extend(_parameter_facts(sql_fact.stable_id, stmt.source, stmt.expanded_body, tokens, dynamic_segments))

        if crud and stmt.statement_kind and crud != stmt.statement_kind:
            diagnostics.append(
                Diagnostic(
                    "mybatis.sql.crud_mismatch",
                    f"XML statement tag {stmt.statement_kind!r} does not match SQL CRUD {crud!r}",
                    "warning",
                    stmt.source.file_path,
                    stmt.source.start_line,
                    stmt.source.end_line,
                )
            )

        cte_names = _cte_names(tree.root_node, source_bytes)
        tables.extend(_table_facts(sql_fact.stable_id, stmt.source, tree.root_node, source_bytes, cte_names, dynamic_segments))
        joins.extend(_join_facts(sql_fact.stable_id, stmt.source, tree.root_node, source_bytes, dynamic_segments))
        columns.extend(_column_facts(sql_fact.stable_id, stmt.source, tree.root_node, source_bytes, dynamic_segments))

    return SqlSemanticAnalysis(tuple(semantic_statements), tuple(tables), tuple(columns), tuple(joins), tuple(parameters), tuple(diagnostics))


def _normalize_placeholders(sql: str) -> Tuple[str, List[_ParamToken]]:
    tokens: List[_ParamToken] = []

    def bound(match: re.Match[str]) -> str:
        token = _sized_token("mbp", len(tokens), len(match.group(0)))
        replacement = _fit_token(token, match.group(0))
        tokens.append(_ParamToken(replacement.strip(), "bound", match.group(1).strip(), _parse_options(match.group(2) or ""), len(tokens), match.start(), match.end()))
        return replacement

    def textual(match: re.Match[str]) -> str:
        token = _sized_token("mbt", len(tokens), len(match.group(0)))
        replacement = _fit_token(token, match.group(0))
        tokens.append(_ParamToken(replacement.strip(), "textual", match.group(1).strip(), {}, len(tokens), match.start(), match.end()))
        return replacement

    normalized = _BOUND_PARAM_RE.sub(bound, sql or "")
    normalized = _TEXT_PARAM_RE.sub(textual, normalized)
    return normalized, tokens


def _parse_options(text: str) -> Dict[str, str]:
    options: Dict[str, str] = {}
    for item in [part.strip() for part in text.split(",") if part.strip()]:
        if "=" in item:
            key, value = item.split("=", 1)
            options[key.strip()] = value.strip()
        else:
            options[item] = ""
    return options


def _statement_fact(stmt: MyBatisStatementFact, normalized: str, parser_status: str, error_count: int, crud: str, has_textual: bool) -> MyBatisSqlStatementSemanticFact:
    return MyBatisSqlStatementSemanticFact(
        stable_id=f"mybatis_sql_stmt::{stmt.stable_id}",
        owner_statement_id=stmt.stable_id,
        source=stmt.source,
        crud=crud,
        xml_statement_kind=stmt.statement_kind,
        database_id=stmt.database_id,
        raw_sql=stmt.expanded_body,
        normalized_sql=normalized,
        parser_status=parser_status,
        parser_error_count=error_count,
        has_textual_substitution=has_textual,
        confidence=0.65 if parser_status != "parsed" or has_textual else 1.0,
    )


def _parameter_facts(sql_statement_id: str, source: SourceSpan, sql: str, tokens: Sequence[_ParamToken], dynamic_segments: Sequence[_DynamicSegment]) -> List[MyBatisSqlParameterFact]:
    rows: List[MyBatisSqlParameterFact] = []
    for token in tokens:
        dynamic_ids, branch_roles = _provenance_for_range(token.start_byte, token.end_byte, dynamic_segments)
        rows.append(
            MyBatisSqlParameterFact(
                stable_id=f"mybatis_sql_param::{sql_statement_id}::{token.position}",
                sql_statement_id=sql_statement_id,
                token=token.token,
                parameter_kind=token.parameter_kind,
                source=_span_from_bytes(source, sql, token.start_byte, token.end_byte),
                name=token.name,
                options=dict(token.options),
                position=token.position,
                dynamic_node_ids=dynamic_ids,
                branch_roles=branch_roles,
            )
        )
    return rows


def _table_facts(sql_statement_id: str, owner_source: SourceSpan, root, source_bytes: bytes, cte_names: Sequence[str], dynamic_segments: Sequence[_DynamicSegment]) -> List[MyBatisSqlTableFact]:
    rows: List[MyBatisSqlTableFact] = []
    delete_from_nodes = _delete_from_nodes(root)

    def add(node, role: str, is_cte: bool = False) -> None:
        parts = _relation_parts(node, source_bytes)
        if not parts:
            return
        raw_name, alias, source_node = parts
        normalized = _normalize_identifier(raw_name)
        dynamic = _is_dynamic_token(raw_name)
        dynamic_ids, branch_roles = _provenance_for_node(source_node, dynamic_segments)
        catalog, schema = _catalog_schema(raw_name)
        stable_id = _stable("mybatis_sql_table", sql_statement_id, len(rows), role, raw_name, alias)
        rows.append(
            MyBatisSqlTableFact(
                stable_id=stable_id,
                sql_statement_id=sql_statement_id,
                raw_name=raw_name,
                normalized_name=normalized,
                role=role,
                source=_offset_span(owner_source, source_node),
                alias=alias,
                catalog=catalog,
                schema=schema,
                is_cte=is_cte or normalized in cte_names,
                is_dynamic=dynamic,
                dynamic_node_ids=dynamic_ids,
                branch_roles=branch_roles,
                resolution_status="dynamic" if dynamic else "resolved",
            )
        )

    for cte in _nodes(root, "cte"):
        name_node = _first_child(cte, {"identifier"})
        if name_node is not None:
            add(name_node, "cte_definition", True)
    for insert in _nodes(root, "insert"):
        target = _first_child(insert, {"object_reference"})
        if target is not None:
            add(target, "write")
    for update in _nodes(root, "update"):
        target = _first_child(update, {"relation", "object_reference"})
        if target is not None:
            add(target, "write")
    for delete_from in delete_from_nodes:
        target = _first_child(delete_from, {"object_reference", "relation"})
        if target is not None:
            add(target, "write")
    for from_node in _nodes(root, "from"):
        if any(from_node == item for item in delete_from_nodes):
            continue
        for target in _direct_children(from_node, {"relation", "object_reference"}):
            add(target, "read")
        for join in [child for child in from_node.children if child.type == "join"]:
            relation = _first_child(join, {"relation", "object_reference"})
            if relation is not None:
                add(relation, "read")
    return rows


def _join_facts(sql_statement_id: str, owner_source: SourceSpan, root, source_bytes: bytes, dynamic_segments: Sequence[_DynamicSegment]) -> List[MyBatisSqlJoinFact]:
    rows: List[MyBatisSqlJoinFact] = []
    for join in _nodes(root, "join"):
        relation = _first_child(join, {"relation", "object_reference"})
        parts = _relation_parts(relation, source_bytes) if relation is not None else None
        raw_name, alias = (parts[0], parts[1]) if parts else ("", "")
        condition = _join_condition(join, source_bytes)
        join_type = _join_type(join, source_bytes)
        dynamic_ids, branch_roles = _provenance_for_node(join, dynamic_segments)
        rows.append(
            MyBatisSqlJoinFact(
                stable_id=_stable("mybatis_sql_join", sql_statement_id, len(rows), join_type, raw_name, alias),
                sql_statement_id=sql_statement_id,
                source=_offset_span(owner_source, join),
                join_type=join_type,
                right_table=raw_name,
                right_alias=alias,
                condition=condition,
                dynamic_node_ids=dynamic_ids,
                branch_roles=branch_roles,
                resolution_status="dynamic" if _is_dynamic_token(raw_name) else "resolved",
            )
        )
    return rows


def _column_facts(sql_statement_id: str, owner_source: SourceSpan, root, source_bytes: bytes, dynamic_segments: Sequence[_DynamicSegment]) -> List[MyBatisSqlColumnFact]:
    rows: List[MyBatisSqlColumnFact] = []

    def add(raw: str, role: str, node, expression: str = "") -> None:
        raw = raw.strip()
        if not raw or _skip_identifier(raw):
            return
        qualifier, name = _split_qualifier(raw)
        dynamic_ids, branch_roles = _provenance_for_node(node, dynamic_segments)
        rows.append(
            MyBatisSqlColumnFact(
                stable_id=_stable("mybatis_sql_column", sql_statement_id, len(rows), role, raw),
                sql_statement_id=sql_statement_id,
                raw_name=raw,
                normalized_name=_normalize_identifier(name),
                role=role,
                source=_offset_span(owner_source, node),
                qualifier=qualifier,
                table_ref=qualifier,
                expression=expression or raw,
                dynamic_node_ids=dynamic_ids,
                branch_roles=branch_roles,
                resolution_status="dynamic" if _is_dynamic_token(raw) else "unresolved",
            )
        )

    for select in _nodes(root, "select"):
        for expr in [child for child in select.children if child.type == "select_expression"]:
            for term in [child for child in expr.named_children if child.type in {"term", "field", "column", "object_reference"}]:
                for col in _column_like_nodes(term):
                    add(_text(col, source_bytes), "projection", col, _text(term, source_bytes))
    for insert in _nodes(root, "insert"):
        lists = [child for child in insert.children if child.type == "list"]
        if lists:
            for col in _nodes(lists[0], "column"):
                add(_text(col, source_bytes), "insert", col)
    for assignment in _nodes(root, "assignment"):
        target = _first_child(assignment, {"field", "column", "identifier"})
        if target is not None:
            add(_text(target, source_bytes), "assignment", target, _text(assignment, source_bytes))
    for where in _nodes(root, "where"):
        for col in _column_like_nodes(where):
            add(_text(col, source_bytes), "predicate", col, _text(where, source_bytes))
    for join in _nodes(root, "join"):
        condition = _join_condition(join, source_bytes)
        for col in _column_like_nodes(join):
            if _node_is_under_type(col, "relation"):
                continue
            add(_text(col, source_bytes), "join", col, condition)
    for order in _nodes(root, "order_by"):
        for col in _column_like_nodes(order):
            add(_text(col, source_bytes), "ordering", col, _text(order, source_bytes))
    for group in _nodes(root, "group_by"):
        for col in _column_like_nodes(group):
            add(_text(col, source_bytes), "grouping", col, _text(group, source_bytes))
    return rows


def _crud(root) -> str:
    for node in _main_statement_children(root):
        if node.type in {"select", "insert", "update", "delete"}:
            return node.type
    for node in _nodes(root, {"select", "insert", "update", "delete"}):
        return node.type
    return ""


def _cte_names(root, source_bytes: bytes) -> Tuple[str, ...]:
    rows: List[str] = []
    for cte in _nodes(root, "cte"):
        name = _first_child(cte, {"identifier"})
        if name is not None:
            rows.append(_normalize_identifier(_text(name, source_bytes)))
    return tuple(rows)


def _delete_from_nodes(root) -> List[object]:
    rows = []
    for stmt in _nodes(root, "statement"):
        children = list(stmt.children)
        for index, child in enumerate(children):
            if child.type == "from" and any(prev.type == "delete" for prev in children[:index]):
                rows.append(child)
    return rows


def _main_statement_children(root) -> List[object]:
    statement = _first_child(root, {"statement"})
    if statement is None:
        return []
    return [child for child in statement.named_children if child.type != "cte"]


def _relation_parts(node, source_bytes: bytes) -> Tuple[str, str, object] | None:
    if node is None:
        return None
    if node.type in {"identifier", "object_reference"}:
        return _text(node, source_bytes), "", node
    object_ref = _first_child(node, {"object_reference"})
    if object_ref is None:
        return _text(node, source_bytes), "", node
    alias = ""
    seen_ref = False
    for child in node.named_children:
        if child == object_ref:
            seen_ref = True
            continue
        if seen_ref and child.type == "identifier":
            alias = _text(child, source_bytes)
            break
    return _text(object_ref, source_bytes), alias, object_ref


def _join_condition(join, source_bytes: bytes) -> str:
    seen_on = False
    parts: List[str] = []
    for child in join.children:
        if child.type == "keyword_on":
            seen_on = True
            continue
        if seen_on:
            parts.append(_text(child, source_bytes))
    return _normalize_ws(" ".join(parts))


def _join_type(join, source_bytes: bytes) -> str:
    text = _text(join, source_bytes).lower()
    before_join = text.split("join", 1)[0].strip()
    return _normalize_ws(before_join + " join") if before_join else "join"


def _catalog_schema(raw_name: str) -> Tuple[str, str]:
    parts = [_strip_quotes(part) for part in raw_name.split(".")]
    if len(parts) >= 3:
        return parts[-3], parts[-2]
    if len(parts) == 2:
        return "", parts[0]
    return "", ""


def _split_qualifier(raw: str) -> Tuple[str, str]:
    parts = raw.split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1]), parts[-1]
    return "", raw


def _column_like_nodes(node) -> List[object]:
    rows: List[object] = []
    for child in _nodes(node, {"column", "field", "object_reference"}):
        if child.type in {"field", "object_reference"} and _is_relation_object_reference(child):
            continue
        if _node_is_under_type(child, "invocation"):
            continue
        if child.type == "field" and _text_node_type_parent(child) in {"list"}:
            continue
        rows.append(child)
    deduped: List[object] = []
    seen = set()
    for row in rows:
        key = (row.start_byte, row.end_byte)
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def _is_relation_object_reference(node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"relation", "insert", "update", "from", "join", "cte"}:
            return True
        if parent.type in {"where", "select", "assignment", "order_by", "group_by", "binary_expression"}:
            return False
        parent = parent.parent
    return False


def _text_node_type_parent(node) -> str:
    parent = getattr(node, "parent", None)
    return parent.type if parent is not None else ""


def _node_is_under_type(node, node_type: str) -> bool:
    parent = getattr(node, "parent", None)
    while parent is not None:
        if parent.type == node_type:
            return True
        parent = parent.parent
    return False


def _skip_identifier(value: str) -> bool:
    lower = value.lower()
    return lower.startswith("mbp") or lower.startswith("mbt")


def _is_dynamic_token(value: str) -> bool:
    return (value or "").lower().startswith("mbt")


def _normalize_identifier(value: str) -> str:
    return _strip_quotes(value).lower()


def _strip_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and ((text[0] == text[-1] and text[0] in {'"', "'", "`"}) or (text[0] == "[" and text[-1] == "]")):
        return text[1:-1]
    return text


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _fit_token(token: str, original: str) -> str:
    if len(token) >= len(original):
        return token[: len(original)]
    return token + (" " * (len(original) - len(token)))


def _sized_token(prefix: str, ordinal: int, width: int) -> str:
    base = f"{prefix}{ordinal}"
    if len(base) >= width:
        return base[:width]
    return base + ("x" * (width - len(base)))


def _dynamic_segments(stmt: MyBatisStatementFact) -> Tuple[_DynamicSegment, ...]:
    segments: List[_DynamicSegment] = []
    controls = [item for item in stmt.dynamic_nodes if item.node_kind == "control"]
    cursor = 0
    for node in stmt.dynamic_nodes:
        if node.tag != "#text" or not node.text:
            continue
        index = stmt.expanded_body.find(node.text, cursor)
        if index < 0:
            index = stmt.expanded_body.find(node.text)
        if index < 0:
            continue
        start_byte = len(stmt.expanded_body[:index].encode("utf-8"))
        end_byte = start_byte + len(node.text.encode("utf-8"))
        cursor = index + len(node.text)
        owning_controls = [control for control in controls if _span_contains(control.source, node.source)]
        if not owning_controls:
            continue
        segments.append(
            _DynamicSegment(
                start_byte=start_byte,
                end_byte=end_byte,
                dynamic_node_ids=tuple(control.stable_id for control in owning_controls),
                branch_roles=tuple(dict.fromkeys(control.branch_role for control in owning_controls if control.branch_role)),
            )
        )
    return tuple(segments)


def _provenance_for_node(node, dynamic_segments: Sequence[_DynamicSegment]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    return _provenance_for_range(node.start_byte, node.end_byte, dynamic_segments)


def _provenance_for_range(start_byte: int, end_byte: int, dynamic_segments: Sequence[_DynamicSegment]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    dynamic_ids: List[str] = []
    branch_roles: List[str] = []
    for segment in dynamic_segments:
        if start_byte < segment.end_byte and end_byte > segment.start_byte:
            dynamic_ids.extend(item for item in segment.dynamic_node_ids if item not in dynamic_ids)
            branch_roles.extend(item for item in segment.branch_roles if item not in branch_roles)
    return tuple(dynamic_ids), tuple(branch_roles)


def _span_contains(outer: SourceSpan, inner: SourceSpan) -> bool:
    if outer.file_path != inner.file_path:
        return False
    outer_start = (outer.start_line, outer.start_column)
    outer_end = (outer.end_line, outer.end_column)
    inner_start = (inner.start_line, inner.start_column)
    inner_end = (inner.end_line, inner.end_column)
    return outer_start <= inner_start and inner_end <= outer_end


def _nodes(node, node_types) -> Iterable:
    wanted = {node_types} if isinstance(node_types, str) else set(node_types)
    if node.type in wanted:
        yield node
    for child in node.children:
        yield from _nodes(child, wanted)


def _first_child(node, node_types):
    wanted = set(node_types)
    if node is None:
        return None
    for child in node.named_children:
        if child.type in wanted:
            return child
    return None


def _direct_children(node, node_types):
    wanted = set(node_types)
    return [child for child in node.named_children if child.type in wanted]


def _count_errors(node) -> int:
    total = 1 if node.type == "ERROR" or getattr(node, "is_error", False) else 0
    for child in node.children:
        total += _count_errors(child)
    return total


def _offset_span(owner_source: SourceSpan, node) -> SourceSpan:
    return SourceSpan(
        owner_source.file_path,
        owner_source.start_line + node.start_point[0],
        owner_source.start_line + node.end_point[0],
        node.start_point[1] + 1,
        node.end_point[1] + 1,
    )


def _span_from_bytes(owner_source: SourceSpan, text: str, start_byte: int, end_byte: int) -> SourceSpan:
    source_bytes = (text or "").encode("utf-8")
    before_start = source_bytes[:start_byte].decode("utf-8", errors="ignore")
    through_end = source_bytes[:end_byte].decode("utf-8", errors="ignore")
    start_line_offset = before_start.count("\n")
    end_line_offset = through_end.count("\n")
    start_line = owner_source.start_line + start_line_offset
    end_line = owner_source.start_line + end_line_offset
    if start_line_offset == 0:
        start_column = owner_source.start_column + len(before_start.rsplit("\n", 1)[-1])
    else:
        start_column = len(before_start.rsplit("\n", 1)[-1]) + 1
    if end_line_offset == 0:
        end_column = owner_source.start_column + len(through_end.rsplit("\n", 1)[-1])
    else:
        end_column = len(through_end.rsplit("\n", 1)[-1]) + 1
    return SourceSpan(owner_source.file_path, start_line, end_line, start_column, end_column)


def _stable(prefix: str, owner_id: str, ordinal: int, *parts: str) -> str:
    digest = hashlib.sha1("|".join([owner_id, str(ordinal), *parts]).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}::{owner_id}::{ordinal}::{digest}"


def _text(node, source_bytes: bytes) -> str:
    if node is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
