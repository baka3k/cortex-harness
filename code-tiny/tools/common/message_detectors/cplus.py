from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_C_STYLE_FN_PATTERN = re.compile(
    r"^\s*(?:template\s*<[^>]+>\s*)?(?:[\w:\[\]<>*&~]+\s+){0,8}([A-Za-z_~][\w:]*)\s*\([^;{}]*\)\s*(?:const\b)?\s*(?:\{|$)"
)


class CPlusMessageDetector(BaseMessageDetector):
    parser_name = "cplus"
    keywords: Set[str] = {
        "emit",
        "publish",
        "send",
        "postmessage",
        "sendmessage",
        "dispatch",
        "notify",
        "broadcast",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _C_STYLE_FN_PATTERN.match(line_text.strip())
        return match.group(1) if match else None

    def extract_fields(self, callee_name: str, args: Sequence[str]):
        lower = callee_name.lower()
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""

        message_name = (unquote(first) or first.strip() or callee_name)[:220]
        receiver = ""
        payload = second.strip() or first.strip()
        if lower in {"sendmessage", "postmessage"}:
            receiver = first.strip()
            payload = second.strip() or third.strip()
            if not unquote(first) and second:
                message_name = (unquote(second) or message_name)[:220]
        elif looks_endpoint(second):
            receiver = second.strip()[:220]
        elif looks_endpoint(third):
            receiver = third.strip()[:220]
        explanation = f"{callee_name}() inferred by cplus detector"
        return message_name, receiver, payload[:400], explanation
