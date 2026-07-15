from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from tools.servlet_jsp.parser_runtime import parse_java_bytes
from tools.struts.models import Diagnostic, SourceSpan, ValidationData, ValidationRule


_TYPE_NODES = {"class_declaration", "record_declaration", "enum_declaration"}


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    return _node_text(name_node, source).strip() if name_node is not None else ""


def parse_java_validation_hooks(root: str, file_path: str) -> ValidationData:
    project_root = Path(root).resolve()
    candidate = Path(file_path)
    absolute = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    try:
        relative = absolute.relative_to(project_root).as_posix()
        source = absolute.read_bytes()
    except (OSError, ValueError) as exc:
        return ValidationData(
            diagnostics=(Diagnostic("struts.java.read_error", str(exc), "error", str(file_path)),)
        )
    try:
        tree = parse_java_bytes(source)
    except Exception as exc:  # noqa: BLE001
        return ValidationData(
            diagnostics=(Diagnostic("struts.java.parser_unavailable", str(exc), "warning", relative),)
        )

    diagnostics: List[Diagnostic] = []
    if tree.root_node.has_error:
        diagnostics.append(
            Diagnostic("struts.java.parse_error", "Java source contains Tree-sitter parse errors", "warning", relative)
        )
    rules: List[ValidationRule] = []

    def walk(node, type_stack: Tuple[str, ...] = ()) -> None:
        next_stack = type_stack
        if node.type in _TYPE_NODES:
            type_name = _name(node, source)
            if type_name:
                next_stack = type_stack + (type_name,)
        if node.type == "method_declaration" and next_stack and _name(node, source) == "validate":
            rules.append(
                ValidationRule(
                    target=next_stack[-1],
                    method="",
                    validator_type="validate_method",
                    message="Java validate() hook",
                    source=SourceSpan(
                        relative,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    ),
                )
            )
        for child in node.children:
            walk(child, next_stack)

    walk(tree.root_node)
    return ValidationData(rules=tuple(rules), diagnostics=tuple(diagnostics))


__all__ = ["parse_java_validation_hooks"]
