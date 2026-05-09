from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_SQL_PROC_PATTERN = re.compile(
    r"^\s*create\s+(?:or\s+replace\s+)?(?:procedure|function)\s+([A-Za-z_][A-Za-z0-9_.$]*)",
    re.IGNORECASE,
)


class SqlMessageDetector(BaseMessageDetector):
    parser_name = "sql"
    keywords: Set[str] = {
        "publish",
        "send",
        "notify",
        "post",
        "emit",
        "enqueue",
        "produce",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _SQL_PROC_PATTERN.match(line_text.strip())
        if match:
            return match.group(1)
        return None

    def extract_fields(self, callee_name: str, args: Sequence[str]):
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""
        message_name = (unquote(first) or first.strip() or callee_name)[:220]
        receiver = ""
        payload = second.strip() or first.strip()
        if looks_endpoint(second):
            receiver = second.strip()[:220]
        elif looks_endpoint(third):
            receiver = third.strip()[:220]
        explanation = f"{callee_name}() inferred by sql detector"
        return message_name, receiver, payload[:400], explanation
