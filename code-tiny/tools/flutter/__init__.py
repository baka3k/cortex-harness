"""Dart and Flutter analysis support for CortexHarness."""

from .detector import FlutterProject, detect_flutter_project
from .protocol import PROTOCOL_MAJOR, PROTOCOL_VERSION, ProtocolError, parse_jsonl

__all__ = [
    "FlutterProject",
    "PROTOCOL_MAJOR",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "detect_flutter_project",
    "parse_jsonl",
]
