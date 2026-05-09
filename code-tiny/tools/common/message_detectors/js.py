from __future__ import annotations

import re
from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote

_JS_FUNCTION_DECL = re.compile(r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_JS_ARROW_ASSIGN = re.compile(
    r"^\s*(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
)
_JS_METHOD_DECL = re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{")


class JsMessageDetector(BaseMessageDetector):
    parser_name = "js"
    keywords: Set[str] = {
        "publish",
        "send",
        "emit",
        "post",
        "notify",
        "dispatch",
        "enqueue",
        "produce",
        "broadcast",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        stripped = line_text.strip()
        for pattern in (_JS_FUNCTION_DECL, _JS_ARROW_ASSIGN, _JS_METHOD_DECL):
            match = pattern.match(stripped)
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
        explanation = f"{callee_name}() inferred by js detector"
        return message_name, receiver, payload[:400], explanation
