"""Bounded static descriptor parsers."""

from .ant import parse_ant
from .android import parse_android_manifest, parse_android_resource
from .cmake import parse_cmake
from .gradle import parse_gradle_build, parse_gradle_settings
from .make import parse_make
from .manifest import parse_identity_manifest
from .maven import parse_maven
from .protobuf import parse_protobuf

__all__ = [
    "parse_ant",
    "parse_android_manifest",
    "parse_android_resource",
    "parse_cmake",
    "parse_gradle_build",
    "parse_gradle_settings",
    "parse_identity_manifest",
    "parse_make",
    "parse_maven",
    "parse_protobuf",
]
