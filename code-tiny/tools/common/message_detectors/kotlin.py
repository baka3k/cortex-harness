from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_KOTLIN_FN_PATTERN = re.compile(r"^\s*(?:suspend\s+)?fun\s+([A-Za-z_][\w.]*)\s*\(")
_JAVA_LIKE_FN_PATTERN = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|open|override|abstract|final|suspend|inline)\s+)*"
    r"(?:[\w<>\[\],?.]+\s+)+([A-Za-z_][\w]*)\s*\("
)


class KotlinMessageDetector(BaseMessageDetector):
    parser_name = "kotlin"
    keywords: Set[str] = {
        "emit",
        "publish",
        "post",
        "send",
        "sendbroadcast",
        "registerreceiver",
        "sendmessage",
        "notify",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        stripped = line_text.strip()
        match = _KOTLIN_FN_PATTERN.match(stripped)
        if match:
            return match.group(1)
        match = _JAVA_LIKE_FN_PATTERN.match(stripped)
        return match.group(1) if match else None

    def extract_fields(self, callee_name: str, args: Sequence[str]):
        lower = callee_name.lower()
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""
        message_name = (unquote(first) or first.strip() or callee_name)[:220]
        receiver = ""
        payload = second.strip() or first.strip()
        if lower == "registerreceiver":
            receiver = first.strip() or second.strip()
        elif looks_endpoint(second):
            receiver = second.strip()[:220]
        elif looks_endpoint(third):
            receiver = third.strip()[:220]
        explanation = f"{callee_name}() inferred by kotlin detector"
        return message_name, receiver, payload[:400], explanation
