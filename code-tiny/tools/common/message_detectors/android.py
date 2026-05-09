from __future__ import annotations

from typing import Optional, Sequence, Set

from .base import BaseMessageDetector, looks_endpoint, unquote
from .kotlin import KotlinMessageDetector


class AndroidMessageDetector(KotlinMessageDetector):
    parser_name = "android"
    keywords: Set[str] = {
        "emit",
        "publish",
        "post",
        "send",
        "sendbroadcast",
        "registerreceiver",
        "startactivity",
        "startservice",
        "sendmessage",
        "notify",
    }

    def extract_fields(self, callee_name: str, args: Sequence[str]):
        lower = callee_name.lower()
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""

        message_name = (unquote(first) or first.strip() or callee_name)[:220]
        receiver = ""
        payload = second.strip() or first.strip()

        if lower in {"startactivity", "startservice"}:
            receiver = first.strip() or second.strip()
            payload = second.strip() or third.strip()
        elif lower == "registerreceiver":
            receiver = first.strip() or second.strip()
        elif lower in {"sendbroadcast", "publish", "emit", "post", "notify"}:
            if looks_endpoint(second):
                receiver = second.strip()[:220]
            elif looks_endpoint(third):
                receiver = third.strip()[:220]
        elif looks_endpoint(second):
            receiver = second.strip()[:220]

        explanation = f"{callee_name}() inferred by android detector"
        return message_name, receiver, payload[:400], explanation
