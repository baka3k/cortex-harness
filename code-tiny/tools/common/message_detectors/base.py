from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence, Set, Tuple

MessageFields = Tuple[str, str, str, str]

_STRING_PATTERN = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'')
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_:.]*")


def unquote(text: str) -> Optional[str]:
    match = _STRING_PATTERN.search(text or "")
    if not match:
        return None
    value = match.group(1) if match.group(1) is not None else match.group(2)
    return (value or "").strip()


def looks_endpoint(text: str) -> bool:
    token = (text or "").strip()
    if not token:
        return False
    if unquote(token):
        return True
    if len(token) > 120:
        return False
    if token.lower() in {"null", "true", "false", "this", "self"}:
        return False
    return bool(_IDENTIFIER_PATTERN.fullmatch(token))


class BaseMessageDetector(ABC):
    parser_name: str = ""
    keywords: Set[str] = set()

    @abstractmethod
    def extract_sender(self, line_text: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def extract_fields(self, callee_name: str, args: Sequence[str]) -> MessageFields:
        raise NotImplementedError


class GenericMessageDetector(BaseMessageDetector):
    parser_name = "generic"
    keywords = {
        "emit",
        "publish",
        "send",
        "post",
        "notify",
        "dispatch",
        "broadcast",
        "sendmessage",
        "postmessage",
    }

    def extract_sender(self, line_text: str) -> Optional[str]:
        del line_text
        return None

    def extract_fields(self, callee_name: str, args: Sequence[str]) -> MessageFields:
        lower = callee_name.lower()
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""
        third = args[2] if len(args) > 2 else ""
        message_name = unquote(first) or first.strip() or callee_name
        message_name = re.sub(r"\s+", " ", message_name)[:220]
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
        explanation = f"{callee_name}() inferred by generic detector"
        return message_name, receiver, payload[:400], explanation
