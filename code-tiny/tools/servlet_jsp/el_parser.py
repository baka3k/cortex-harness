from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from tools.servlet_jsp.models import Diagnostic, ResourceBudgets, SourceSpan


_KEYWORDS = {
    "and",
    "div",
    "empty",
    "eq",
    "false",
    "ge",
    "gt",
    "instanceof",
    "le",
    "lt",
    "mod",
    "ne",
    "not",
    "null",
    "or",
    "true",
}

_IMPLICIT_SCOPES = {
    "param": "parameter",
    "paramValues": "parameter",
    "requestScope": "request",
    "sessionScope": "session",
    "applicationScope": "application",
    "cookie": "cookie",
    "header": "header",
    "headerValues": "header",
}


@dataclass(frozen=True)
class ELToken:
    kind: str
    value: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ELFunctionReference:
    prefix: str
    name: str
    raw: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ELReference:
    root: str
    path: Tuple[str, ...]
    raw: str
    start_offset: int
    end_offset: int
    resolution_status: str = "unresolved"
    implicit_scope: str = ""

    @property
    def property_path(self) -> str:
        value = self.root
        for part in self.path:
            value += part if part.startswith("[") else f".{part}"
        return value


@dataclass(frozen=True)
class ELStateRead:
    implicit_object: str
    scope: str
    name: str
    raw: str
    dynamic: bool
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ELParseResult:
    raw: str
    body: str
    span: SourceSpan
    tokens: Tuple[ELToken, ...] = ()
    references: Tuple[ELReference, ...] = ()
    functions: Tuple[ELFunctionReference, ...] = ()
    state_reads: Tuple[ELStateRead, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    truncated: bool = False
    complete: bool = True

    @property
    def variables(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(item.root for item in self.references))

    @property
    def property_paths(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(item.property_path for item in self.references))

    @property
    def implicit_objects(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(item.implicit_object for item in self.state_reads))


def parse_el_expression(
    expression: str,
    *,
    file_path: str = "",
    start_line: int = 1,
    start_column: int = 1,
    budgets: Optional[ResourceBudgets] = None,
) -> ELParseResult:
    """Extract static references from one JSP EL expression without evaluating it."""

    effective = budgets or ResourceBudgets()
    raw = expression or ""
    diagnostics: List[Diagnostic] = []
    truncated = False
    encoded = raw.encode("utf-8", errors="surrogatepass")
    if len(encoded) > effective.max_el_bytes:
        bounded = encoded[: effective.max_el_bytes]
        while bounded:
            try:
                scanned = bounded.decode("utf-8", errors="strict")
                break
            except UnicodeDecodeError as exc:
                bounded = bounded[: exc.start]
        else:
            scanned = ""
        truncated = True
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.el.byte_budget",
                f"EL byte budget {effective.max_el_bytes} reached",
                "warning",
                file_path,
                start_line,
                start_line,
            )
        )
    else:
        scanned = raw

    body_offset = 0
    complete = True
    if scanned.startswith(("${", "#{")):
        body_offset = 2
        if scanned.endswith("}"):
            body = scanned[2:-1]
        else:
            body = scanned[2:]
            complete = False
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.el.unclosed",
                    "Unclosed EL expression",
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                )
            )
    else:
        body = scanned

    tokens, token_diagnostics, token_truncated, token_complete = _lex(
        body,
        body_offset=body_offset,
        file_path=file_path,
        start_line=start_line,
        max_tokens=effective.max_el_tokens,
        max_nesting=effective.max_el_nesting,
    )
    diagnostics.extend(token_diagnostics)
    truncated = truncated or token_truncated
    functions, function_token_indexes = _function_references(tokens, scanned)
    references = _references(tokens, scanned, function_token_indexes)
    state_reads = _state_reads(references)
    span = SourceSpan(
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + raw.count("\n"),
        start_column=start_column,
        end_column=start_column + len(raw) if "\n" not in raw else 1,
    )
    return ELParseResult(
        raw=raw,
        body=body,
        span=span,
        tokens=tuple(tokens),
        references=tuple(references),
        functions=tuple(functions),
        state_reads=tuple(state_reads),
        diagnostics=tuple(diagnostics),
        truncated=truncated,
        complete=complete and token_complete and not truncated,
    )


def _lex(
    body: str,
    *,
    body_offset: int,
    file_path: str,
    start_line: int,
    max_tokens: int,
    max_nesting: int,
) -> Tuple[List[ELToken], List[Diagnostic], bool, bool]:
    tokens: List[ELToken] = []
    diagnostics: List[Diagnostic] = []
    index = 0
    nesting: List[str] = []
    truncated = False
    complete = True
    while index < len(body):
        char = body[index]
        if char.isspace():
            index += 1
            continue
        if body.startswith("//", index):
            end = body.find("\n", index + 2)
            index = len(body) if end < 0 else end + 1
            continue
        if body.startswith("/*", index):
            end = body.find("*/", index + 2)
            if end < 0:
                complete = False
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.el.unclosed_comment",
                        "Unclosed comment in EL expression",
                        "warning",
                        file_path,
                        start_line,
                        start_line,
                    )
                )
                break
            index = end + 2
            continue
        start = index
        if char in {"'", '"'}:
            quote = char
            index += 1
            escaped = False
            while index < len(body):
                current = body[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    break
            else:
                complete = False
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.el.unclosed_string",
                        "Unclosed string literal in EL expression",
                        "warning",
                        file_path,
                        start_line,
                        start_line,
                    )
                )
            kind = "string"
        elif char.isalpha() or char in {"_", "$"}:
            index += 1
            while index < len(body) and (body[index].isalnum() or body[index] in {"_", "$"}):
                index += 1
            kind = "identifier"
        elif char.isdigit():
            index += 1
            while index < len(body) and (body[index].isalnum() or body[index] in {".", "_"}):
                index += 1
            kind = "number"
        else:
            pair = body[index : index + 2]
            if pair in {"==", "!=", "<=", ">=", "&&", "||", "->", "+=", "-=", "*=", "/=", "?."}:
                index += 2
            else:
                index += 1
            kind = "punctuation" if char in "()[]{}.,:?" else "operator"
        value = body[start:index]
        tokens.append(ELToken(kind, value, start + body_offset, index + body_offset))
        if value in {"(", "[", "{"}:
            nesting.append(value)
            if len(nesting) > max_nesting:
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.el.nesting_budget",
                        f"EL nesting budget {max_nesting} reached",
                        "warning",
                        file_path,
                        start_line,
                        start_line,
                    )
                )
                truncated = True
                break
        elif value in {")", "]", "}"}:
            expected = {")": "(", "]": "[", "}": "{"}[value]
            if not nesting or nesting[-1] != expected:
                complete = False
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.el.unexpected_closer",
                        f"Unexpected closing token {value!r} in EL expression",
                        "warning",
                        file_path,
                        start_line,
                        start_line,
                    )
                )
            else:
                nesting.pop()
        if len(tokens) >= max_tokens:
            if index < len(body):
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.el.token_budget",
                        f"EL token budget {max_tokens} reached",
                        "warning",
                        file_path,
                        start_line,
                        start_line,
                    )
                )
                truncated = True
            break
    if nesting and not truncated:
        complete = False
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.el.unbalanced_nesting",
                "Unbalanced grouping token in EL expression",
                "warning",
                file_path,
                start_line,
                start_line,
            )
        )
    return tokens, diagnostics, truncated, complete


def _function_references(
    tokens: List[ELToken], raw: str
) -> Tuple[List[ELFunctionReference], set[int]]:
    functions: List[ELFunctionReference] = []
    indexes: set[int] = set()
    for index in range(len(tokens) - 3):
        prefix, colon, name, opening = tokens[index : index + 4]
        if (
            prefix.kind == "identifier"
            and colon.value == ":"
            and name.kind == "identifier"
            and opening.value == "("
        ):
            indexes.update({index, index + 2})
            functions.append(
                ELFunctionReference(
                    prefix=prefix.value,
                    name=name.value,
                    raw=raw[prefix.start_offset : name.end_offset],
                    start_offset=prefix.start_offset,
                    end_offset=name.end_offset,
                )
            )
    return functions, indexes


def _references(
    tokens: List[ELToken], raw: str, function_token_indexes: set[int]
) -> List[ELReference]:
    references: List[ELReference] = []
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or token.value in _KEYWORDS or index in function_token_indexes:
            continue
        previous = tokens[index - 1].value if index else ""
        if previous in {".", "?.", ":"}:
            continue
        path: List[str] = []
        end_offset = token.end_offset
        cursor = index + 1
        dynamic = False
        while cursor < len(tokens):
            marker = tokens[cursor]
            if marker.value in {".", "?."} and cursor + 1 < len(tokens) and tokens[cursor + 1].kind == "identifier":
                part = tokens[cursor + 1]
                path.append(part.value)
                end_offset = part.end_offset
                cursor += 2
                continue
            if marker.value == "[":
                close = _matching_bracket(tokens, cursor)
                if close is None:
                    dynamic = True
                    end_offset = marker.end_offset
                    break
                inner = tokens[cursor + 1 : close]
                if len(inner) == 1 and inner[0].kind in {"string", "number"}:
                    index_value = _literal_index(inner[0])
                    path.append(f"[{index_value}]")
                else:
                    raw_inner = raw[marker.end_offset : tokens[close].start_offset].strip()
                    path.append(f"[{raw_inner}]")
                    dynamic = True
                end_offset = tokens[close].end_offset
                cursor = close + 1
                continue
            break
        implicit_scope = _IMPLICIT_SCOPES.get(token.value, "")
        references.append(
            ELReference(
                root=token.value,
                path=tuple(path),
                raw=raw[token.start_offset:end_offset],
                start_offset=token.start_offset,
                end_offset=end_offset,
                resolution_status=(
                    "resolved" if implicit_scope and path and not dynamic else "dynamic" if dynamic else "unresolved"
                ),
                implicit_scope=implicit_scope,
            )
        )
    return references


def _matching_bracket(tokens: List[ELToken], opening: int) -> Optional[int]:
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index].value == "[":
            depth += 1
        elif tokens[index].value == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _literal_index(token: ELToken) -> str:
    if token.kind == "string" and len(token.value) >= 2:
        return token.value[1:-1]
    return token.value


def _state_reads(references: List[ELReference]) -> List[ELStateRead]:
    reads: List[ELStateRead] = []
    for reference in references:
        if not reference.implicit_scope:
            continue
        name = ""
        dynamic = True
        if reference.path:
            first = reference.path[0]
            if first.startswith("[") and first.endswith("]"):
                name = first[1:-1]
            else:
                name = first
            dynamic = not bool(name) or any(char in name for char in "${}[]()")
        reads.append(
            ELStateRead(
                implicit_object=reference.root,
                scope=reference.implicit_scope,
                name=name,
                raw=reference.raw,
                dynamic=dynamic,
                start_offset=reference.start_offset,
                end_offset=reference.end_offset,
            )
        )
    return reads
