from __future__ import annotations

from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote
from .vb6 import _VB6_FN_PATTERN


class VbaMessageDetector(BaseMessageDetector):
    parser_name = "vba"
    keywords: Set[str] = {
        "publish",
        "send",
        "notify",
        "dispatch",
        "raiseevent",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _VB6_FN_PATTERN.match((line_text or "").strip())
        return match.group(1) if match else None

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
        explanation = f"{callee_name}() inferred by vba detector"
        return message_name, receiver, payload[:400], explanation
