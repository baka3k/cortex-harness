"""Flutter project discovery based on pub package metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

import yaml


@dataclass(frozen=True)
class FlutterProject:
    root: Path
    package_name: str
    sdk_constraint: str
    flutter_constraint: str
    pubspec: Mapping[str, Any]
    evidence: Tuple[str, ...]


def _sdk_dependency(value: Any) -> bool:
    return isinstance(value, Mapping) and str(value.get("sdk", "")).strip().lower() == "flutter"


def load_pubspec(root: str | Path) -> Mapping[str, Any] | None:
    project_root = Path(root).expanduser().resolve()
    pubspec_path = project_root / "pubspec.yaml"
    if not pubspec_path.is_file():
        return None
    try:
        value = yaml.safe_load(pubspec_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot parse pubspec {pubspec_path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"pubspec must contain a YAML mapping: {pubspec_path}")
    return dict(value)


def project_package_name(root: str | Path) -> str:
    project_root = Path(root).expanduser().resolve()
    pubspec = load_pubspec(project_root)
    return str(pubspec.get("name") or project_root.name) if pubspec is not None else project_root.name


def detect_flutter_project(root: str | Path) -> FlutterProject | None:
    project_root = Path(root).expanduser().resolve()
    value = load_pubspec(project_root)
    if value is None:
        return None
    dependencies = value.get("dependencies", {})
    if not isinstance(dependencies, Mapping) or not _sdk_dependency(dependencies.get("flutter")):
        return None
    environment = value.get("environment", {})
    if not isinstance(environment, Mapping):
        environment = {}
    flutter_section = value.get("flutter", {})
    evidence = ["dependencies.flutter.sdk=flutter"]
    if isinstance(flutter_section, Mapping):
        evidence.append("flutter-section")
    return FlutterProject(
        root=project_root,
        package_name=str(value.get("name") or project_root.name),
        sdk_constraint=str(environment.get("sdk", "")),
        flutter_constraint=str(environment.get("flutter", "")),
        pubspec=dict(value),
        evidence=tuple(evidence),
    )
