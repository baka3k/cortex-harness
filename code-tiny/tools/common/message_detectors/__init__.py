from __future__ import annotations

from typing import Dict

from .android import AndroidMessageDetector
from .base import BaseMessageDetector, GenericMessageDetector
from .cplus import CPlusMessageDetector
from .csharp import CSharpMessageDetector
from .delphi import DelphiMessageDetector
from .js import JsMessageDetector
from .java import JavaMessageDetector
from .kotlin import KotlinMessageDetector
from .php import PhpMessageDetector
from .plsql import PlSqlMessageDetector
from .python import PythonMessageDetector
from .sql import SqlMessageDetector
from .ts import TsMessageDetector
from .vb6 import Vb6MessageDetector
from .vba import VbaMessageDetector
from .vbnet import VbNetMessageDetector
from .vbscript import VbScriptMessageDetector

_DETECTORS: Dict[str, BaseMessageDetector] = {
    "cplus": CPlusMessageDetector(),
    "delphi": DelphiMessageDetector(),
    "java": JavaMessageDetector(),
    "csharp": CSharpMessageDetector(),
    "kotlin": KotlinMessageDetector(),
    "android": AndroidMessageDetector(),
    "python": PythonMessageDetector(),
    "js": JsMessageDetector(),
    "ts": TsMessageDetector(),
    "php": PhpMessageDetector(),
    "sql": SqlMessageDetector(),
    "plsql": PlSqlMessageDetector(),
    "vbnet": VbNetMessageDetector(),
    "vb6": Vb6MessageDetector(),
    "vba": VbaMessageDetector(),
    "vbscript": VbScriptMessageDetector(),
}
_FALLBACK = GenericMessageDetector()


def get_detector(parser: str) -> BaseMessageDetector:
    return _DETECTORS.get((parser or "").strip().lower(), _FALLBACK)


def has_specific_detector(parser: str) -> bool:
    return (parser or "").strip().lower() in _DETECTORS


def supported_parsers() -> set[str]:
    return set(_DETECTORS.keys())
