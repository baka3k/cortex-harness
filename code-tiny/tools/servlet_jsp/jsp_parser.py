from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Tuple, Union

from tools.servlet_jsp.el_parser import ELParseResult, ELStateRead, parse_el_expression
from tools.servlet_jsp.models import Diagnostic, ResourceBudgets, SourceSpan
from tools.servlet_jsp.parser_runtime import parse_xml_bytes
from tools.servlet_jsp.path_resolver import read_bounded_file, resolve_project_path


_TAG_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")
_ATTRIBUTE_NAME_RE = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:-]*")
_JSTL_CORE_URIS = {
    "http://java.sun.com/jsp/jstl/core",
    "http://xmlns.jcp.org/jsp/jstl/core",
    "jakarta.tags.core",
}
_JSTL_FORMAT_URIS = {
    "http://java.sun.com/jsp/jstl/fmt",
    "http://xmlns.jcp.org/jsp/jstl/fmt",
    "jakarta.tags.fmt",
}
_JSTL_FUNCTION_URIS = {
    "http://java.sun.com/jsp/jstl/functions",
    "http://xmlns.jcp.org/jsp/jstl/functions",
    "jakarta.tags.functions",
}
_JSP_XML_URIS = {
    "http://java.sun.com/JSP/Page",
    "http://xmlns.jcp.org/JSP/Page",
    "jakarta.tags.jsp",
}
_RESOURCE_ATTRIBUTES = {
    "img": "src",
    "script": "src",
    "link": "href",
    "iframe": "src",
    "source": "src",
    "video": "src",
    "audio": "src",
    "input": "src",
}


@dataclass(frozen=True)
class JspRegion:
    kind: str
    name: str
    raw: str
    attributes: Dict[str, str]
    span: SourceSpan
    self_closing: bool = False
    malformed: bool = False
    taglib_uri: str = ""
    semantic_kind: str = ""


@dataclass(frozen=True)
class JspExpression:
    kind: str
    raw: str
    span: SourceSpan
    el: Optional[ELParseResult] = None

    @property
    def references(self):
        return self.el.references if self.el else ()

    @property
    def functions(self):
        return self.el.functions if self.el else ()


@dataclass(frozen=True)
class JspTarget:
    kind: str
    raw_value: str
    resolved_path: str
    classification: str
    resolution_status: str
    span: SourceSpan
    method: str = ""
    source_name: str = ""
    dynamic: bool = False


@dataclass(frozen=True)
class JspDependency:
    source_path: str
    target_path: str
    kind: str
    dynamic: bool
    resolution_status: str
    span: SourceSpan
    raw_target: str = ""


@dataclass(frozen=True)
class JspScriptletOperation:
    kind: str
    scope: str
    name: str
    raw: str
    resolution_status: str
    span: SourceSpan


@dataclass(frozen=True)
class IncludeCycle:
    files: Tuple[str, ...]


@dataclass(frozen=True)
class JspParseResult:
    file_path: str
    syntax: str
    regions: Tuple[JspRegion, ...] = ()
    directives: Tuple[JspRegion, ...] = ()
    actions: Tuple[JspRegion, ...] = ()
    tags: Tuple[JspRegion, ...] = ()
    expressions: Tuple[JspExpression, ...] = ()
    scriptlet_operations: Tuple[JspScriptletOperation, ...] = ()
    taglibs: Dict[str, str] = field(default_factory=dict)
    targets: Tuple[JspTarget, ...] = ()
    dependencies: Tuple[JspDependency, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    truncated: bool = False
    complete: bool = True

    @property
    def state_reads(self) -> Tuple[ELStateRead, ...]:
        return tuple(read for expression in self.expressions if expression.el for read in expression.el.state_reads)


class _LineMap:
    def __init__(self, text: str, file_path: str) -> None:
        self.file_path = file_path
        self.starts = [0]
        self.starts.extend(index + 1 for index, char in enumerate(text) if char == "\n")

    def span(self, start: int, end: int) -> SourceSpan:
        start_line_index = bisect.bisect_right(self.starts, start) - 1
        end_position = max(start, end - 1)
        end_line_index = bisect.bisect_right(self.starts, end_position) - 1
        return SourceSpan(
            self.file_path,
            start_line_index + 1,
            end_line_index + 1,
            start - self.starts[start_line_index] + 1,
            end_position - self.starts[end_line_index] + 2,
        )


def parse_jsp_file(
    root: str,
    file_path: str,
    *,
    budgets: Optional[ResourceBudgets] = None,
) -> JspParseResult:
    """Parse a JSP/JSPX/JSP fragment through a bounded, root-confined read."""

    effective = budgets or ResourceBudgets()
    resolution = resolve_project_path(root, file_path, require_exists=True)
    if resolution.status != "resolved":
        return _failed_result(
            file_path,
            "servlet_jsp.jsp.path_rejected",
            resolution.message or f"Unable to read JSP file: {resolution.status}",
        )
    try:
        payload, byte_truncated = read_bounded_file(resolution.absolute_path, effective.max_source_bytes)
    except OSError as exc:
        return _failed_result(resolution.relative_path, "servlet_jsp.jsp.read_error", str(exc))
    diagnostics: List[Diagnostic] = []
    if byte_truncated:
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.jsp.byte_budget",
                f"JSP byte budget {effective.max_source_bytes} reached",
                "warning",
                resolution.relative_path,
            )
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.jsp.legacy_encoding",
                "JSP is not UTF-8; decoded as ISO-8859-1",
                "info",
                resolution.relative_path,
            )
        )
    syntax = "jspx" if resolution.relative_path.lower().endswith(".jspx") else "classic"
    if syntax == "jspx":
        try:
            tree = parse_xml_bytes(payload)
            if tree.root_node.has_error:
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.jspx.xml_syntax",
                        "JSPX parsed with XML syntax errors; recoverable lexical regions were retained",
                        "warning",
                        resolution.relative_path,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.jspx.xml_parser_error",
                    str(exc),
                    "error",
                    resolution.relative_path,
                )
            )
    regions, expressions, scan_diagnostics, region_truncated = _scan_regions(
        text, resolution.relative_path, effective
    )
    diagnostics.extend(scan_diagnostics)
    normalized, taglibs = _normalize_regions(regions, syntax)
    known_expression_spans = {(item.span.start_line, item.span.start_column, item.kind) for item in expressions}
    for region in normalized:
        expression_kind = "jsp_expression" if region.kind == "expression" else ""
        key = (region.span.start_line, region.span.start_column, expression_kind)
        if expression_kind and key not in known_expression_spans:
            expressions.append(JspExpression(expression_kind, region.raw, region.span))
    line_map = _LineMap(text, resolution.relative_path)
    targets, dependencies = _extract_targets(
        root, resolution.relative_path, normalized, line_map
    )
    scriptlet_operations, scriptlet_targets = _extract_scriptlet_operations(
        root, resolution.relative_path, normalized
    )
    targets.extend(scriptlet_targets)
    directives = tuple(item for item in normalized if item.kind == "directive")
    actions = tuple(item for item in normalized if item.kind == "start_tag" and item.semantic_kind)
    tags = tuple(item for item in normalized if item.kind == "start_tag")
    truncated = byte_truncated or region_truncated or any(
        expression.el and expression.el.truncated for expression in expressions
    )
    for expression in expressions:
        if expression.el:
            diagnostics.extend(expression.el.diagnostics)
    diagnostics = diagnostics[: effective.max_diagnostics_per_file]
    return JspParseResult(
        file_path=resolution.relative_path,
        syntax=syntax,
        regions=tuple(normalized),
        directives=directives,
        actions=actions,
        tags=tags,
        expressions=tuple(expressions),
        scriptlet_operations=tuple(scriptlet_operations),
        taglibs=dict(sorted(taglibs.items())),
        targets=tuple(targets),
        dependencies=tuple(dependencies),
        diagnostics=tuple(diagnostics),
        truncated=truncated,
        complete=not truncated and not any(item.severity == "error" for item in diagnostics),
    )


def build_include_graph(results: Iterable[JspParseResult]) -> Dict[str, Tuple[str, ...]]:
    graph: Dict[str, set[str]] = {}
    for result in results:
        graph.setdefault(result.file_path, set())
        for dependency in result.dependencies:
            if not dependency.dynamic and dependency.resolution_status == "resolved" and dependency.target_path:
                graph.setdefault(dependency.source_path, set()).add(dependency.target_path)
                graph.setdefault(dependency.target_path, set())
    return {key: tuple(sorted(value)) for key, value in sorted(graph.items())}


def detect_include_cycles(
    items: Iterable[Union[JspParseResult, JspDependency]],
) -> Tuple[IncludeCycle, ...]:
    materialized = list(items)
    if not materialized:
        return ()
    if isinstance(materialized[0], JspParseResult):
        graph = build_include_graph(item for item in materialized if isinstance(item, JspParseResult))
    else:
        graph_sets: Dict[str, set[str]] = {}
        for item in materialized:
            if not isinstance(item, JspDependency) or item.dynamic or item.resolution_status != "resolved":
                continue
            graph_sets.setdefault(item.source_path, set()).add(item.target_path)
            graph_sets.setdefault(item.target_path, set())
        graph = {key: tuple(sorted(value)) for key, value in graph_sets.items()}
    return _strongly_connected_cycles(graph)


def include_cycle_diagnostics(cycles: Iterable[IncludeCycle]) -> Tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            "servlet_jsp.jsp.include_cycle",
            "JSP include cycle: " + " -> ".join(cycle.files + (cycle.files[0],)),
            "warning",
            cycle.files[0],
        )
        for cycle in sorted(cycles, key=lambda item: item.files)
        if cycle.files
    )


def _failed_result(file_path: str, code: str, message: str) -> JspParseResult:
    return JspParseResult(
        file_path=file_path,
        syntax="jspx" if file_path.lower().endswith(".jspx") else "classic",
        diagnostics=(Diagnostic(code, message, "error", file_path),),
        complete=False,
    )


def _scan_regions(
    text: str, file_path: str, budgets: ResourceBudgets
) -> Tuple[List[JspRegion], List[JspExpression], List[Diagnostic], bool]:
    regions: List[JspRegion] = []
    expressions: List[JspExpression] = []
    diagnostics: List[Diagnostic] = []
    line_map = _LineMap(text, file_path)
    cursor = 0
    template_start = 0
    truncated = False

    def add(region: JspRegion) -> bool:
        nonlocal truncated
        if len(regions) >= budgets.max_jsp_regions:
            truncated = True
            if not any(item.code == "servlet_jsp.jsp.region_budget" for item in diagnostics):
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.jsp.region_budget",
                        f"JSP region budget {budgets.max_jsp_regions} reached",
                        "warning",
                        file_path,
                        region.span.start_line,
                        region.span.end_line,
                    )
                )
            return False
        regions.append(region)
        return True

    def flush_template(end: int) -> bool:
        nonlocal template_start
        if end <= template_start:
            return True
        raw = text[template_start:end]
        return add(JspRegion("template", "", raw, {}, line_map.span(template_start, end)))

    while cursor < len(text):
        next_cursor = _next_special(text, cursor)
        if next_cursor < 0:
            break
        cursor = next_cursor
        if not flush_template(cursor):
            break
        if text.startswith("<%--", cursor):
            close = text.find("--%>", cursor + 5)
            end = len(text) if close < 0 else close + 4
            malformed = close < 0
            if malformed:
                diagnostics.append(_malformed_diagnostic(file_path, line_map.span(cursor, end), "JSP comment"))
            if not add(JspRegion("comment", "", text[cursor:end], {}, line_map.span(cursor, end), malformed=malformed)):
                break
        elif text.startswith("<%", cursor):
            marker_length, kind = _jsp_region_kind(text, cursor)
            close = _find_jsp_close(text, cursor + marker_length)
            malformed = close < 0
            end = _recovery_boundary(text, cursor + marker_length) if malformed else close + 2
            if malformed:
                diagnostics.append(_malformed_diagnostic(file_path, line_map.span(cursor, end), kind))
            raw = text[cursor:end]
            name = ""
            attributes: Dict[str, str] = {}
            if kind == "directive":
                name, attributes = _parse_directive(raw)
            if not add(JspRegion(kind, name, raw, attributes, line_map.span(cursor, end), malformed=malformed)):
                break
            if kind == "expression":
                expressions.append(JspExpression("jsp_expression", raw, line_map.span(cursor, end)))
        elif text.startswith("<!--", cursor):
            close = text.find("-->", cursor + 4)
            end = len(text) if close < 0 else close + 3
            malformed = close < 0
            if malformed:
                diagnostics.append(_malformed_diagnostic(file_path, line_map.span(cursor, end), "HTML comment"))
            if not add(JspRegion("comment", "", text[cursor:end], {}, line_map.span(cursor, end), malformed=malformed)):
                break
        elif text.startswith(("${", "#{"), cursor):
            close = _find_el_close(text, cursor + 2)
            malformed = close < 0
            end = _recovery_boundary(text, cursor + 2) if malformed else close + 1
            raw = text[cursor:end]
            el = parse_el_expression(
                raw,
                file_path=file_path,
                start_line=line_map.span(cursor, end).start_line,
                start_column=line_map.span(cursor, end).start_column,
                budgets=budgets,
            )
            if not add(JspRegion("el", "", raw, {}, line_map.span(cursor, end), malformed=malformed)):
                break
            expressions.append(JspExpression("el", raw, line_map.span(cursor, end), el))
        elif text[cursor] == "<" and _looks_like_tag(text, cursor):
            close = _find_tag_close(text, cursor + 1)
            malformed = close < 0
            end = _recovery_boundary(text, cursor + 1) if malformed else close + 1
            raw = text[cursor:end]
            kind, name, attributes, self_closing = _parse_tag(raw)
            if malformed:
                diagnostics.append(_malformed_diagnostic(file_path, line_map.span(cursor, end), "tag"))
            if not add(JspRegion(kind, name, raw, attributes, line_map.span(cursor, end), self_closing, malformed)):
                break
            if kind == "start_tag":
                expressions.extend(_expressions_in_region(raw, cursor, line_map, budgets))
        else:
            cursor += 1
            template_start = min(template_start, cursor)
            continue
        cursor = end
        template_start = end
        if len(diagnostics) >= budgets.max_diagnostics_per_file:
            truncated = True
            break
    if not truncated:
        flush_template(len(text))
    return regions, expressions, diagnostics, truncated


def _next_special(text: str, start: int) -> int:
    candidates = [value for value in (text.find("<", start), text.find("${", start), text.find("#{", start)) if value >= 0]
    return min(candidates) if candidates else -1


def _jsp_region_kind(text: str, start: int) -> Tuple[int, str]:
    if text.startswith("<%@", start):
        return 3, "directive"
    if text.startswith("<%!", start):
        return 3, "declaration"
    if text.startswith("<%=", start):
        return 3, "expression"
    return 2, "scriptlet"


def _find_jsp_close(text: str, start: int) -> int:
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = start
    while index < len(text) - 1:
        pair = text[index : index + 2]
        char = text[index]
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if pair == "*/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if pair == "%>":
            return index
        if pair == "//":
            line_comment = True
            index += 2
            continue
        if pair == "/*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
        index += 1
    return -1


def _find_el_close(text: str, start: int) -> int:
    depth = 1
    quote = ""
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _find_tag_close(text: str, start: int) -> int:
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == ">":
            return index
    return -1


def _recovery_boundary(text: str, start: int) -> int:
    newline = text.find("\n", start)
    search_start = newline + 1 if newline >= 0 else start
    candidates = [value for value in (text.find("<", search_start), text.find("${", search_start), text.find("#{", search_start)) if value >= 0]
    return min(candidates) if candidates else len(text)


def _looks_like_tag(text: str, start: int) -> bool:
    if start + 1 >= len(text):
        return False
    char = text[start + 1]
    return char.isalpha() or char in {"/", "!", "?", "_"}


def _parse_directive(raw: str) -> Tuple[str, Dict[str, str]]:
    content = raw[3:-2] if raw.endswith("%>") else raw[3:]
    match = _TAG_NAME_RE.search(content)
    if not match:
        return "", {}
    return match.group(0), _parse_attributes(content, match.end())


def _parse_tag(raw: str) -> Tuple[str, str, Dict[str, str], bool]:
    content = raw[1:-1] if raw.endswith(">") else raw[1:]
    stripped = content.lstrip()
    if stripped.startswith(("!", "?")):
        return "declaration", "", {}, stripped.rstrip().endswith("/")
    is_end = stripped.startswith("/")
    if is_end:
        stripped = stripped[1:].lstrip()
    match = _TAG_NAME_RE.match(stripped)
    if not match:
        return "template", "", {}, False
    return (
        "end_tag" if is_end else "start_tag",
        match.group(0),
        {} if is_end else _parse_attributes(stripped, match.end()),
        stripped.rstrip().endswith("/"),
    )


def _parse_attributes(content: str, start: int) -> Dict[str, str]:
    attributes: Dict[str, str] = {}
    cursor = start
    while cursor < len(content):
        while cursor < len(content) and (content[cursor].isspace() or content[cursor] == "/"):
            cursor += 1
        match = _ATTRIBUTE_NAME_RE.match(content, cursor)
        if not match:
            cursor += 1
            continue
        name = match.group(0)
        cursor = match.end()
        while cursor < len(content) and content[cursor].isspace():
            cursor += 1
        value = ""
        if cursor < len(content) and content[cursor] == "=":
            cursor += 1
            while cursor < len(content) and content[cursor].isspace():
                cursor += 1
            if cursor < len(content) and content[cursor] in {"'", '"'}:
                quote = content[cursor]
                cursor += 1
                value_start = cursor
                escaped = False
                while cursor < len(content):
                    if escaped:
                        escaped = False
                    elif content[cursor] == "\\":
                        escaped = True
                    elif content[cursor] == quote:
                        break
                    cursor += 1
                value = content[value_start:cursor]
                cursor += cursor < len(content)
            else:
                value_start = cursor
                while cursor < len(content) and not content[cursor].isspace() and content[cursor] not in ">/":
                    cursor += 1
                value = content[value_start:cursor]
        attributes[name] = value
    return attributes


def _expressions_in_region(
    raw: str, absolute_start: int, line_map: _LineMap, budgets: ResourceBudgets
) -> List[JspExpression]:
    expressions: List[JspExpression] = []
    cursor = 0
    while cursor < len(raw):
        positions = [value for value in (raw.find("${", cursor), raw.find("#{", cursor)) if value >= 0]
        if not positions:
            break
        start = min(positions)
        close = _find_el_close(raw, start + 2)
        end = len(raw) if close < 0 else close + 1
        value = raw[start:end]
        span = line_map.span(absolute_start + start, absolute_start + end)
        expressions.append(
            JspExpression(
                "el",
                value,
                span,
                parse_el_expression(
                    value,
                    file_path=line_map.file_path,
                    start_line=span.start_line,
                    start_column=span.start_column,
                    budgets=budgets,
                ),
            )
        )
        cursor = end
    return expressions


def _normalize_regions(regions: List[JspRegion], syntax: str) -> Tuple[List[JspRegion], Dict[str, str]]:
    taglibs: Dict[str, str] = {}
    for region in regions:
        if region.kind == "directive" and region.name.lower() == "taglib":
            prefix = region.attributes.get("prefix", "")
            uri = region.attributes.get("uri", "") or region.attributes.get("tagdir", "")
            if prefix and uri:
                taglibs[prefix] = uri
        if region.kind == "start_tag":
            for name, value in region.attributes.items():
                if name == "xmlns":
                    taglibs[""] = value
                elif name.startswith("xmlns:"):
                    taglibs[name.split(":", 1)[1]] = value
    normalized: List[JspRegion] = []
    for region in regions:
        if region.kind not in {"start_tag", "end_tag"} or not region.name:
            normalized.append(region)
            continue
        prefix, local = _split_tag_name(region.name)
        uri = taglibs.get(prefix, "")
        if syntax == "jspx" and uri in _JSP_XML_URIS and local.startswith("directive."):
            directive_name = local.split(".", 1)[1]
            normalized.append(replace(region, kind="directive", name=directive_name, taglib_uri=uri))
            if directive_name == "taglib":
                declared_prefix = region.attributes.get("prefix", "")
                declared_uri = region.attributes.get("uri", "")
                if declared_prefix and declared_uri:
                    taglibs[declared_prefix] = declared_uri
            continue
        semantic = ""
        if (prefix == "jsp" and not uri) or uri in _JSP_XML_URIS:
            semantic = f"jsp_{local.lower()}"
        elif uri in _JSTL_CORE_URIS:
            semantic = f"jstl_core_{local.lower()}"
        elif uri in _JSTL_FORMAT_URIS:
            semantic = f"jstl_format_{local.lower()}"
        elif uri in _JSTL_FUNCTION_URIS:
            semantic = f"jstl_function_{local.lower()}"
        normalized.append(replace(region, taglib_uri=uri, semantic_kind=semantic))
    if syntax == "jspx":
        normalized = _normalize_jspx_islands(normalized)
    return normalized, taglibs


def _normalize_jspx_islands(regions: List[JspRegion]) -> List[JspRegion]:
    normalized = list(regions)
    island_kinds = {
        "jsp_declaration": "declaration",
        "jsp_expression": "expression",
        "jsp_scriptlet": "scriptlet",
    }
    for index, region in enumerate(normalized):
        target_kind = island_kinds.get(region.semantic_kind, "")
        if region.kind != "start_tag" or not target_kind:
            continue
        depth = 1
        for close_index in range(index + 1, len(normalized)):
            candidate = normalized[close_index]
            if candidate.name != region.name:
                continue
            if candidate.kind == "start_tag" and not candidate.self_closing:
                depth += 1
            elif candidate.kind == "end_tag":
                depth -= 1
                if depth == 0:
                    for content_index in range(index + 1, close_index):
                        content = normalized[content_index]
                        if content.kind == "template":
                            normalized[content_index] = replace(content, kind=target_kind, name=region.name)
                    break
    return normalized


def _extract_targets(
    root: str,
    file_path: str,
    regions: List[JspRegion],
    line_map: _LineMap,
) -> Tuple[List[JspTarget], List[JspDependency]]:
    del line_map
    targets: List[JspTarget] = []
    dependencies: List[JspDependency] = []
    for region in regions:
        if region.kind == "directive" and region.name.lower() == "include":
            _add_target(root, file_path, targets, dependencies, "include", region.attributes.get("file", ""), region.span, "translation_include")
            continue
        if region.kind != "start_tag":
            continue
        prefix, local = _split_tag_name(region.name)
        local_lower = local.lower()
        if region.semantic_kind.startswith("jsp_"):
            if local_lower == "include":
                _add_target(root, file_path, targets, dependencies, "include", region.attributes.get("page", ""), region.span, "runtime_include")
            elif local_lower == "forward":
                _add_target(root, file_path, targets, dependencies, "forward", region.attributes.get("page", ""), region.span)
        elif region.taglib_uri in _JSTL_CORE_URIS:
            value = region.attributes.get("url", "") or region.attributes.get("value", "")
            if local_lower == "import":
                _add_target(root, file_path, targets, dependencies, "include", value, region.span, "jstl_import")
            elif local_lower == "redirect":
                _add_target(root, file_path, targets, dependencies, "redirect", value, region.span)
            elif local_lower == "url":
                _add_target(root, file_path, targets, dependencies, "link", value, region.span)
        elif not prefix:
            if local_lower == "form":
                _add_target(
                    root,
                    file_path,
                    targets,
                    dependencies,
                    "form",
                    region.attributes.get("action", ""),
                    region.span,
                    method=(region.attributes.get("method", "get") or "get").upper(),
                )
            elif local_lower == "a":
                _add_target(root, file_path, targets, dependencies, "link", region.attributes.get("href", ""), region.span)
            elif local_lower in _RESOURCE_ATTRIBUTES:
                _add_target(root, file_path, targets, dependencies, "resource", region.attributes.get(_RESOURCE_ATTRIBUTES[local_lower], ""), region.span)
    return targets, dependencies


def _add_target(
    root: str,
    file_path: str,
    targets: List[JspTarget],
    dependencies: List[JspDependency],
    kind: str,
    raw_value: str,
    span: SourceSpan,
    dependency_kind: str = "",
    method: str = "",
    force_dynamic: bool = False,
) -> None:
    value = (raw_value or "").strip()
    if not value:
        return
    dynamic = force_dynamic or _is_dynamic(value)
    if dynamic:
        classification, status, resolved = "dynamic", "dynamic", ""
    elif value.startswith("//"):
        classification, status, resolved = "external", "external", ""
    elif value.startswith("#"):
        classification, status, resolved = "fragment", "resolved", ""
    elif value.startswith("?"):
        classification, status, resolved = "current", "resolved", file_path
    else:
        classification = "context_relative" if value.startswith("/") else "relative"
        resolution = resolve_project_path(
            root,
            value,
            base_file=file_path,
            web_root_relative=value.startswith("/"),
            require_exists=False,
        )
        status = resolution.status
        resolved = resolution.relative_path
        if status == "external":
            classification = "external"
        elif status in {"rejected", "invalid"}:
            classification = "rejected"
    targets.append(JspTarget(kind, value, resolved, classification, status, span, method, kind, dynamic))
    if dependency_kind:
        dependencies.append(JspDependency(file_path, resolved, dependency_kind, dynamic, status, span, value))


def _extract_scriptlet_operations(
    root: str, file_path: str, regions: List[JspRegion]
) -> Tuple[List[JspScriptletOperation], List[JspTarget]]:
    operations: List[JspScriptletOperation] = []
    targets: List[JspTarget] = []
    argument = r"(?P<arg>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^)]*)"
    dispatcher_re = re.compile(
        r"getRequestDispatcher\s*\(\s*" + argument + r"\s*\)\s*(?:\.\s*(?P<op>forward|include)\s*\()?",
        re.DOTALL,
    )
    redirect_re = re.compile(r"sendRedirect\s*\(\s*" + argument + r"\s*\)", re.DOTALL)
    state_re = re.compile(
        r"(?P<receiver>request(?:\s*\.\s*getSession\s*\(\s*\))?|session|application|servletContext)\s*\.\s*"
        r"(?P<method>getParameter|getAttribute|setAttribute)\s*\(\s*" + argument,
        re.DOTALL,
    )
    for region in regions:
        if region.kind != "scriptlet":
            continue
        if region.raw.startswith("<%"):
            raw = region.raw[2:-2] if region.raw.endswith("%>") else region.raw[2:]
        else:
            raw = region.raw
        for match in dispatcher_re.finditer(raw):
            value, status = _literal_argument(match.group("arg"))
            operation = match.group("op") or "dispatcher"
            operations.append(JspScriptletOperation(operation, "request", value, match.group(0), status, region.span))
            if operation in {"forward", "include"}:
                temporary_dependencies: List[JspDependency] = []
                _add_target(
                    root,
                    file_path,
                    targets,
                    temporary_dependencies,
                    operation,
                    value or match.group("arg"),
                    region.span,
                    force_dynamic=status != "resolved",
                )
        for match in redirect_re.finditer(raw):
            value, status = _literal_argument(match.group("arg"))
            operations.append(JspScriptletOperation("redirect", "response", value, match.group(0), status, region.span))
            temporary_dependencies = []
            _add_target(
                root,
                file_path,
                targets,
                temporary_dependencies,
                "redirect",
                value or match.group("arg"),
                region.span,
                force_dynamic=status != "resolved",
            )
        for match in state_re.finditer(raw):
            receiver = re.sub(r"\s+", "", match.group("receiver"))
            method = match.group("method")
            value, status = _literal_argument(match.group("arg"))
            scope = "session" if receiver == "session" or "getSession" in receiver else "request"
            if receiver in {"application", "servletContext"}:
                scope = "application"
            operation = "write_state" if method == "setAttribute" else "read_state"
            if method == "getParameter":
                scope = "parameter"
            operations.append(JspScriptletOperation(operation, scope, value, match.group(0), status, region.span))
        if re.search(r"\bgetCookies\s*\(", raw):
            operations.append(JspScriptletOperation("read_state", "cookie", "", "getCookies()", "dynamic", region.span))
        for match in re.finditer(r"new\s+Cookie\s*\(\s*([^,)]+)", raw):
            value, status = _literal_argument(match.group(1))
            operations.append(JspScriptletOperation("write_state", "cookie", value, match.group(0), status, region.span))
    return operations, targets


def _literal_argument(raw: str) -> Tuple[str, str]:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1], "resolved"
    return "", "dynamic"


def _split_tag_name(name: str) -> Tuple[str, str]:
    prefix, separator, local = name.partition(":")
    return (prefix, local) if separator else ("", prefix)


def _is_dynamic(value: str) -> bool:
    return any(marker in value for marker in ("${", "#{", "<%=", "<%"))


def _malformed_diagnostic(file_path: str, span: SourceSpan, kind: str) -> Diagnostic:
    return Diagnostic(
        "servlet_jsp.jsp.unclosed_region",
        f"Unclosed {kind}; parser recovered at the next safe boundary",
        "warning",
        file_path,
        span.start_line,
        span.end_line,
    )


def _strongly_connected_cycles(graph: Dict[str, Tuple[str, ...]]) -> Tuple[IncludeCycle, ...]:
    nodes = sorted(set(graph).union(*(set(values) for values in graph.values()))) if graph else []
    visited: set[str] = set()
    finish: List[str] = []
    for start in nodes:
        if start in visited:
            continue
        stack: List[Tuple[str, bool]] = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for neighbor in reversed(graph.get(node, ())):
                if neighbor not in visited:
                    stack.append((neighbor, False))
    reverse: Dict[str, List[str]] = {node: [] for node in nodes}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    assigned: set[str] = set()
    cycles: List[IncludeCycle] = []
    for start in reversed(finish):
        if start in assigned:
            continue
        component: List[str] = []
        stack = [start]
        assigned.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in reverse.get(node, ()):
                if neighbor not in assigned:
                    assigned.add(neighbor)
                    stack.append(neighbor)
        if len(component) > 1 or (component and component[0] in graph.get(component[0], ())):
            cycles.append(IncludeCycle(tuple(sorted(component))))
    return tuple(sorted(cycles, key=lambda item: item.files))
