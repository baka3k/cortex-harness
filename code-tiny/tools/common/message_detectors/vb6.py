from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_VB6_FN_PATTERN = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Static\s+|Global\s+)?(?:Sub|Function|Property\s+Get|Property\s+Set|Property\s+Let)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


class Vb6MessageDetector(BaseMessageDetector):
    parser_name = "vb6"
    keywords: Set[str] = {
        "sendmessage",
        "postmessage",
        "publish",
        "send",
        "notify",
        "dispatch",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _VB6_FN_PATTERN.match((line_text or "").strip())
        return match.group(1) if match else None

    def extract_fields(self, callee_name: str, args: Sequence[str]):
        lower = (callee_name or "").lower()
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""
        message_name = (unquote(first) or first.strip() or callee_name)[:220]
        receiver = ""
        payload = second.strip() or first.strip()
        if lower in {"sendmessage", "postmessage"}:
            receiver = first.strip()[:220]
            payload = second.strip() or third.strip()
            if not unquote(first) and second:
                message_name = (unquote(second) or message_name)[:220]
        elif looks_endpoint(second):
            receiver = second.strip()[:220]
        explanation = f"{callee_name}() inferred by vb6 detector"
        return message_name, receiver, payload[:400], explanation
