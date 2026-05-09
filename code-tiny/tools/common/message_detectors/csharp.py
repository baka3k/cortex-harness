from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_CSHARP_FN_PATTERN = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|sealed|virtual|override|async|partial)\s+)*"
    r"(?:[\w<>\[\],?.]+\s+)+([A-Za-z_][\w]*)\s*\("
)


class CSharpMessageDetector(BaseMessageDetector):
    parser_name = "csharp"
    keywords: Set[str] = {
        "publish",
        "send",
        "sendasync",
        "emit",
        "post",
        "notify",
        "dispatch",
        "broadcast",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        match = _CSHARP_FN_PATTERN.match(line_text.strip())
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
        explanation = f"{callee_name}() inferred by csharp detector"
        return message_name, receiver, payload[:400], explanation
