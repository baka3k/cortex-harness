from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_DELPHI_FN_PATTERN = re.compile(
    r"^\s*(?:class\s+)?(?:procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_.]*)"
)


class DelphiMessageDetector(BaseMessageDetector):
    parser_name = "delphi"
    keywords: Set[str] = {
        "sendmessage",
        "postmessage",
        "publish",
        "dispatch",
        "notify",
        "broadcast",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _DELPHI_FN_PATTERN.match(line_text.strip())
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
        explanation = f"{callee_name}() inferred by delphi detector"
        return message_name, receiver, payload[:400], explanation
