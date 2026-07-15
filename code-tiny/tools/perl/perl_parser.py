"""Scope-aware, side-effect-free Perl extraction over Tree-sitter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .models import (
    ANALYZER_VERSION,
    Diagnostic,
    DocumentationRecord,
    FileRecord,
    ImportRecord,
    ParsedFile,
    ReferenceRecord,
    SourceSpan,
    SymbolRecord,
    redact_text,
    stable_id,
)
from .parser_runtime import capabilities, error_nodes, new_parser


_VARIABLE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(\$(?:[A-Za-z_][A-Za-z0-9_:]*|[^A-Za-z0-9_\s]))|"
    r"([@%][A-Za-z_][A-Za-z0-9_:]*)"
    r")"
)
_STATIC_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$")
_CALL_NODE_TYPES = {
    "function_call_expression",
    "ambiguous_function_call_expression",
    "method_call_expression",
    "coderef_call_expression",
    "func0op_call_expression",
}
_CONDITIONAL_ANCESTORS = {
    "if_statement",
    "unless_statement",
    "conditional_expression",
    "while_statement",
    "for_statement",
    "given_statement",
}


def _node_text(node: Optional[Any], source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _span(node: Any) -> SourceSpan:
    return SourceSpan(
        start_line=node.start_point[0] + 1,
        start_column=node.start_point[1] + 1,
        end_line=node.end_point[0] + 1,
        end_column=node.end_point[1] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


def _descendants(node: Any, node_types: Sequence[str]) -> List[Any]:
    allowed = set(node_types)
    found: List[Any] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in allowed:
            found.append(current)
        stack.extend(reversed(current.children))
    return found


def _first_descendant(node: Any, node_types: Sequence[str]) -> Optional[Any]:
    allowed = set(node_types)
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        if current.type in allowed:
            return current
        stack.extend(reversed(current.children))
    return None


def _field_or_descendant(node: Any, field: str, node_types: Sequence[str]) -> Optional[Any]:
    candidate = node.child_by_field_name(field)
    return candidate if candidate is not None else _first_descendant(node, node_types)


def _is_conditional(node: Any) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in _CONDITIONAL_ANCESTORS:
            return True
        parent = parent.parent
    return False


def _normalized_package(text: str) -> str:
    return re.sub(r"\s+", "", text or "") or "main"


def _argument_count(node: Any) -> int:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return 0
    return sum(1 for child in arguments.named_children if child.type not in {"comment", "pod"})


def _subroutine_arity(node: Any, source: bytes) -> int:
    signature = _first_descendant(node, ("signature",))
    if signature is not None:
        return len(
            _descendants(
                signature,
                ("mandatory_parameter", "optional_parameter", "slurpy_parameter"),
            )
        )
    prototype = _first_descendant(node, ("prototype",))
    if prototype is None:
        return 0
    text = _node_text(prototype, source)
    return len(re.findall(r"[\$@%*&]", text))


def _leading_documentation(node: Any, source: bytes, limit: int) -> str:
    parts: List[str] = []
    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in {"comment", "pod"}:
        parts.append(_node_text(sibling, source).strip())
        sibling = sibling.prev_named_sibling
    return redact_text("\n".join(reversed(parts)), limit)


@dataclass
class _Collector:
    project_id: str
    file_path: str
    source: bytes
    include_docs: bool
    max_snippet_chars: int
    max_doc_chars: int
    symbols: List[SymbolRecord]
    imports: List[ImportRecord]
    references: List[ReferenceRecord]
    documentation: List[DocumentationRecord]
    diagnostics: List[Diagnostic]

    def symbol_id(self, package: str, scope: str, kind: str, fq_name: str) -> str:
        return stable_id(self.project_id, self.file_path, package, scope, kind, fq_name)

    def add_package(self, node: Any, package: str) -> SymbolRecord:
        symbol = SymbolRecord(
            symbol_id=self.symbol_id(package, "", "package", package),
            name=package.rsplit("::", 1)[-1],
            kind="package",
            fq_name=package,
            file_path=self.file_path,
            span=_span(node),
            package=package,
            code=redact_text(_node_text(node, self.source), self.max_snippet_chars),
            documentation=(
                _leading_documentation(node, self.source, self.max_doc_chars)
                if self.include_docs
                else ""
            ),
        )
        self.symbols.append(symbol)
        return symbol

    def add_subroutine(self, node: Any, package: str, outer_scope: Tuple[str, ...]) -> SymbolRecord:
        name_node = _field_or_descendant(node, "name", ("bareword",))
        name = _node_text(name_node, self.source).strip() or f"anonymous@L{node.start_point[0] + 1}"
        qualified = name if "::" in name else f"{package}::{name}"
        scope = "::".join((package, *outer_scope))
        prototype_node = _first_descendant(node, ("prototype", "signature"))
        attributes_node = node.child_by_field_name("attributes")
        if attributes_node is None:
            attributes_node = _first_descendant(node, ("attrlist",))
        attributes = tuple(
            sorted(
                {
                    _node_text(item, self.source).strip()
                    for item in _descendants(attributes_node, ("attribute",))
                    if _node_text(item, self.source).strip()
                }
            )
        ) if attributes_node is not None else ()
        symbol = SymbolRecord(
            symbol_id=self.symbol_id(package, scope, "subroutine", qualified),
            name=name,
            kind="subroutine",
            fq_name=qualified,
            file_path=self.file_path,
            span=_span(node),
            package=package,
            scope=scope,
            declaration_kind="named",
            arity=_subroutine_arity(node, self.source),
            prototype=_node_text(prototype_node, self.source).strip(),
            attributes=attributes,
            code=redact_text(_node_text(node, self.source), self.max_snippet_chars),
            documentation=(
                _leading_documentation(node, self.source, self.max_doc_chars)
                if self.include_docs
                else ""
            ),
        )
        self.symbols.append(symbol)
        return symbol

    def add_variables(
        self,
        node: Any,
        package: str,
        scope: Tuple[str, ...],
        declaration_kind: str,
    ) -> None:
        raw = _node_text(node, self.source)
        names = list(
            dict.fromkeys(
                next((part for part in match if part), "")
                for match in _VARIABLE_RE.findall(raw)
            )
        )
        names = [name for name in names if name]
        for name in names:
            if declaration_kind == "our":
                fq_name = f"{package}::{name}"
                scope_text = package
                scope_kind = "package"
            else:
                scope_text = "::".join((package, *scope))
                fq_name = f"{scope_text}::{name}@L{node.start_point[0] + 1}"
                scope_kind = "dynamic-local" if declaration_kind == "local" else "lexical"
            self.symbols.append(
                SymbolRecord(
                    symbol_id=self.symbol_id(package, scope_text, "variable", fq_name),
                    name=name,
                    kind="variable",
                    fq_name=fq_name,
                    file_path=self.file_path,
                    span=_span(node),
                    package=package,
                    scope=scope_text,
                    declaration_kind=f"{declaration_kind}:{scope_kind}",
                    code=redact_text(raw, self.max_snippet_chars),
                )
            )

    def add_use(self, node: Any, package: str) -> None:
        raw = _node_text(node, self.source).strip()
        first_token = next((child.type for child in node.children if child.type in {"use", "no"}), "use")
        module_node = node.child_by_field_name("module")
        module = _normalized_package(_node_text(module_node, self.source)) if module_node is not None else ""
        is_dynamic = not bool(module and _STATIC_MODULE_RE.fullmatch(module))
        import_id = self.symbol_id(
            package,
            package,
            "import",
            f"{first_token}:{module or raw}@L{node.start_point[0] + 1}",
        )
        self.imports.append(
            ImportRecord(
                import_id=import_id,
                kind=first_token,
                module=module,
                raw_text=redact_text(raw, self.max_snippet_chars),
                file_path=self.file_path,
                span=_span(node),
                is_dynamic=is_dynamic,
                is_conditional=_is_conditional(node),
            )
        )

    def add_require(self, node: Any, package: str) -> None:
        raw = _node_text(node, self.source).strip()
        target = raw[len("require") :].strip().rstrip(";").strip()
        target = target.strip("'\"")
        is_static = bool(_STATIC_MODULE_RE.fullmatch(target))
        self.imports.append(
            ImportRecord(
                import_id=self.symbol_id(
                    package,
                    package,
                    "import",
                    f"require:{target or raw}@L{node.start_point[0] + 1}",
                ),
                kind="require",
                module=target if is_static else "",
                raw_text=redact_text(raw, self.max_snippet_chars),
                file_path=self.file_path,
                span=_span(node),
                is_dynamic=not is_static,
                is_conditional=_is_conditional(node),
            )
        )

    def add_reference(
        self,
        node: Any,
        package: str,
        active_subroutine: Optional[SymbolRecord],
    ) -> None:
        raw = _node_text(node, self.source).strip()
        source_id = active_subroutine.symbol_id if active_subroutine else ""
        source_name = active_subroutine.fq_name if active_subroutine else package
        target_name = ""
        kind = "dynamic"
        confidence = 0.1
        status = "dynamic"
        reason = "runtime target"

        if node.type in {"function_call_expression", "ambiguous_function_call_expression", "func0op_call_expression"}:
            function_node = node.child_by_field_name("function")
            if function_node is None:
                function_node = _first_descendant(node, ("function", "bareword"))
            target_name = _node_text(function_node, self.source).strip()
            if target_name:
                kind = "qualified" if "::" in target_name else "direct"
                confidence = 1.0 if kind == "qualified" else 0.8
                status = "unresolved"
                reason = "awaiting project-local resolution"
        elif node.type == "method_call_expression":
            method_node = node.child_by_field_name("method")
            if method_node is None:
                method_node = _first_descendant(node, ("method",))
            invocant_node = node.child_by_field_name("invocant")
            method = _node_text(method_node, self.source).strip()
            invocant = _node_text(invocant_node, self.source).strip()
            target_name = f"{invocant}->{method}" if invocant else method
            kind = "method"
            confidence = 0.35
            status = "unresolved"
            reason = "Perl method dispatch is runtime-dependent"
        elif node.type == "coderef_call_expression":
            target_name = raw.split("->", 1)[0].strip()
            kind = "coderef"
            reason = "coderef target is dynamic"

        if not target_name:
            return
        ref_name = f"{source_name}->{target_name}:{kind}@{node.start_point[0] + 1}:{node.start_point[1] + 1}"
        self.references.append(
            ReferenceRecord(
                ref_id=self.symbol_id(package, source_name, "reference", ref_name),
                source_symbol_id=source_id,
                source_name=source_name,
                target_name=target_name,
                kind=kind,
                file_path=self.file_path,
                span=_span(node),
                confidence=confidence,
                resolution_status=status,
                reason=reason,
                raw_text=redact_text(raw, self.max_snippet_chars),
            )
        )

    def add_eval_reference(self, node: Any, package: str, active_subroutine: Optional[SymbolRecord]) -> None:
        source_id = active_subroutine.symbol_id if active_subroutine else ""
        source_name = active_subroutine.fq_name if active_subroutine else package
        raw = _node_text(node, self.source).strip()
        self.references.append(
            ReferenceRecord(
                ref_id=self.symbol_id(
                    package,
                    source_name,
                    "reference",
                    f"eval@{node.start_point[0] + 1}:{node.start_point[1] + 1}",
                ),
                source_symbol_id=source_id,
                source_name=source_name,
                target_name="eval",
                kind="eval",
                file_path=self.file_path,
                span=_span(node),
                confidence=0.0,
                resolution_status="dynamic",
                reason="eval is never statically resolved",
                raw_text=redact_text(raw, self.max_snippet_chars),
            )
        )

    def visit(
        self,
        node: Any,
        package: str,
        scope: Tuple[str, ...] = (),
        active_subroutine: Optional[SymbolRecord] = None,
    ) -> None:
        if node.type == "subroutine_declaration_statement":
            subroutine = self.add_subroutine(node, package, scope)
            for child in node.children:
                self.visit(child, package, (*scope, subroutine.name), subroutine)
            return
        if node.type == "variable_declaration":
            raw = _node_text(node, self.source).lstrip()
            declaration = "our" if raw.startswith("our") else "my"
            self.add_variables(node, package, scope, declaration)
        elif node.type == "localization_expression":
            self.add_variables(node, package, scope, "local")
        elif node.type == "use_statement":
            self.add_use(node, package)
        elif node.type == "require_expression":
            self.add_require(node, package)
        elif node.type in _CALL_NODE_TYPES:
            self.add_reference(node, package, active_subroutine)
        elif node.type == "eval_expression":
            self.add_eval_reference(node, package, active_subroutine)

        for child in node.children:
            self.visit(child, package, scope, active_subroutine)


class PerlTreeSitterParser:
    """Extract normalized structural facts from one Perl source file."""

    def __init__(self, *, max_snippet_chars: int = 4000, max_doc_chars: int = 8000) -> None:
        self.max_snippet_chars = max(0, max_snippet_chars)
        self.max_doc_chars = max(0, max_doc_chars)

    def parse_bytes(
        self,
        *,
        project_id: str,
        file_path: str,
        source: bytes,
        include_docs: bool = False,
        truncated: bool = False,
    ) -> ParsedFile:
        parser = new_parser()
        tree = parser.parse(source)
        root = tree.root_node
        grammar = capabilities()
        errors = error_nodes(root)
        diagnostics: List[Diagnostic] = []
        try:
            source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    code="perl.encoding.invalid_utf8",
                    severity="warning",
                    message="Invalid UTF-8 bytes were replaced in extracted text.",
                    file_path=file_path,
                    details=(("start", str(exc.start)), ("end", str(exc.end))),
                )
            )
        for node in errors[:100]:
            diagnostics.append(
                Diagnostic(
                    code="perl.parser.error_node",
                    severity="warning",
                    message="Tree-sitter recovered from malformed or unsupported Perl syntax.",
                    file_path=file_path,
                    span=_span(node),
                    details=(("node_type", node.type),),
                )
            )
        if len(errors) > 100:
            diagnostics.append(
                Diagnostic(
                    code="perl.parser.error_budget",
                    severity="warning",
                    message=f"Parser diagnostics truncated after 100 of {len(errors)} errors.",
                    file_path=file_path,
                )
            )

        collector = _Collector(
            project_id=project_id,
            file_path=file_path,
            source=source,
            include_docs=include_docs,
            max_snippet_chars=self.max_snippet_chars,
            max_doc_chars=self.max_doc_chars,
            symbols=[],
            imports=[],
            references=[],
            documentation=[],
            diagnostics=diagnostics,
        )
        package = "main"
        package_seen = False
        for child in root.children:
            if child.type == "package_statement":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    name_node = _first_descendant(child, ("package",))
                package = _normalized_package(_node_text(name_node, source))
                collector.add_package(child, package)
                package_seen = True
                continue
            if include_docs and child.type in {"comment", "pod"}:
                raw = _node_text(child, source)
                bounded = redact_text(raw, self.max_doc_chars)
                collector.documentation.append(
                    DocumentationRecord(
                        kind=child.type,
                        text=bounded,
                        file_path=file_path,
                        span=_span(child),
                        truncated=len(raw) > len(bounded),
                    )
                )
            collector.visit(child, package)

        if not package_seen and source.strip():
            pseudo_span = SourceSpan(1, 1, 1, 1, 0, 0)
            collector.symbols.append(
                SymbolRecord(
                    symbol_id=collector.symbol_id("main", "", "package", "main"),
                    name="main",
                    kind="package",
                    fq_name="main",
                    file_path=file_path,
                    span=pseudo_span,
                    package="main",
                    declaration_kind="implicit",
                )
            )

        coverage = "empty" if not source else ("partial" if errors or diagnostics or truncated else "complete")
        parse_status = "empty" if not source else ("partial" if coverage == "partial" else "ok")
        file_record = FileRecord(
            file_path=file_path,
            language="perl",
            parser_version=ANALYZER_VERSION,
            grammar_version=grammar.grammar_version,
            parse_status=parse_status,
            coverage=coverage,
            content_sha256=hashlib.sha256(source).hexdigest(),
            size_bytes=len(source),
            line_count=source.count(b"\n") + (1 if source else 0),
            error_count=len(errors),
            truncated=truncated,
        )
        dedupe = lambda items, key: tuple(sorted({key(item): item for item in items}.values(), key=key))
        return ParsedFile(
            file=file_record,
            symbols=dedupe(collector.symbols, lambda item: (item.file_path, item.span, item.symbol_id)),
            imports=dedupe(collector.imports, lambda item: (item.file_path, item.span, item.import_id)),
            references=dedupe(collector.references, lambda item: (item.file_path, item.span, item.ref_id)),
            documentation=dedupe(
                collector.documentation,
                lambda item: (item.file_path, item.span, item.kind),
            ),
            diagnostics=tuple(sorted(collector.diagnostics)),
        )
