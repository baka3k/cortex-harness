from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ProcSqlStatement:
    operation: str
    targets: tuple[str, ...]
    host_variables: tuple[str, ...]
    raw_text: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


_EXEC_SQL_RE = re.compile(r"\bEXEC\s+SQL\b.*?;", re.IGNORECASE | re.DOTALL)


def extract_exec_sql_statements(source_text: str) -> list[ProcSqlStatement]:
    statements: list[ProcSqlStatement] = []
    for match in _EXEC_SQL_RE.finditer(source_text):
        raw_text = match.group(0)
        upper = raw_text.upper()
        operation_match = re.search(r"EXEC\s+SQL\s+([A-Z]+)", upper)
        targets = re.findall(
            r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+([A-Z0-9_.-]+)", upper
        )
        host_variables = re.findall(r":([A-Z0-9_-]+)", upper)
        start_line = source_text.count("\n", 0, match.start()) + 1
        end_line = start_line + raw_text.count("\n")
        statements.append(
            ProcSqlStatement(
                operation=operation_match.group(1) if operation_match else "",
                targets=tuple(dict.fromkeys(targets)),
                host_variables=tuple(dict.fromkeys(host_variables)),
                raw_text=raw_text,
                start_byte=len(source_text[: match.start()].encode("utf-8")),
                end_byte=len(source_text[: match.end()].encode("utf-8")),
                start_line=start_line,
                end_line=end_line,
            )
        )
    return statements