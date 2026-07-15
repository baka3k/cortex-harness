from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from .identity import module_id, normalize_relative_path
from .safe_formats import local_name, parse_xml_file, read_bounded_text


IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".settings", ".cache", ".venv",
    "venv", "__pycache__", "bin", "obj", "build", "dist", "out", "target",
    "node_modules", "packages",
}


@dataclass(frozen=True)
class ModuleDetection:
    framework: str
    module_path: str
    module_id: str
    detected: bool
    evidence: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    confidence: float
    artifacts: Tuple[str, ...]


def discover_project_roots(root: str) -> tuple[str, ...]:
    root_path = Path(root).resolve()
    modules: set[str] = set()
    for current, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS and not name.startswith("."))
        if any(name.lower().endswith((".csproj", ".vbproj")) for name in filenames):
            modules.add(normalize_relative_path(os.path.relpath(current, root_path)) or ".")
    if not modules:
        modules.add(".")
    return tuple(sorted(modules))


def iter_module_files(root: str, module_path: str) -> Iterable[str]:
    base = Path(root).resolve() if module_path in {"", "."} else Path(root).resolve() / module_path
    if not base.is_dir():
        return ()
    values: list[str] = []
    for current, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in IGNORED_DIRS
            and not name.startswith(".")
            and not _contains_project_file(Path(current) / name)
        )
        for filename in sorted(filenames):
            absolute = Path(current) / filename
            if absolute.is_symlink():
                continue
            values.append(normalize_relative_path(os.path.relpath(absolute, Path(root).resolve())))
    return tuple(values)


def _contains_project_file(path: Path) -> bool:
    try:
        return any(
            child.name.lower().endswith((".csproj", ".vbproj"))
            for child in path.iterdir()
            if child.is_file()
        )
    except OSError:
        return False


def infer_deleted_module_path(framework: str, path: str) -> str:
    """Recover a removed module root from a deleted root-level marker."""

    normalized = normalize_relative_path(path)
    name = os.path.basename(normalized).lower()
    marker = name.endswith((".csproj", ".vbproj"))
    if framework == "aspnet_core":
        marker = marker or name in {"program.cs", "startup.cs"} or (
            name.startswith("appsettings") and name.endswith(".json")
        )
    elif framework == "aspnet_framework":
        marker = marker or name in {"web.config", "global.asax", "packages.config"}
    if not marker:
        return ""
    return normalize_relative_path(os.path.dirname(normalized)) or "."


def detect_modules(root: str, framework: str) -> tuple[ModuleDetection, ...]:
    detections: list[ModuleDetection] = []
    for module_path in discover_project_roots(root):
        files = tuple(iter_module_files(root, module_path))
        strong: list[str] = []
        supporting: list[str] = []
        for path in files:
            lower = path.lower()
            name = os.path.basename(lower)
            if lower.endswith(".csproj"):
                try:
                    tree, _, _ = parse_xml_file(root, path)
                    sdk = str(tree.attrib.get("Sdk") or "").lower()
                    values = " ".join(
                        str(element.text or "") + " " + " ".join(str(v) for v in element.attrib.values())
                        for element in tree.iter()
                    ).lower()
                except (OSError, ValueError):
                    sdk, values = "", ""
                if framework == "aspnet_core":
                    if "microsoft.net.sdk.web" in sdk:
                        strong.append(f"{path}:web-sdk")
                    if "microsoft.aspnetcore.app" in values or "microsoft.aspnetcore" in values:
                        strong.append(f"{path}:aspnet-core-reference")
                else:
                    if "system.web" in values or "349c5851-65df-11da-9384-00065b846f21" in values:
                        strong.append(f"{path}:legacy-web-project")
                    if re.search(r"<TargetFrameworkVersion>v(?:2|3|4)", values, re.IGNORECASE):
                        supporting.append(f"{path}:legacy-target")
            elif framework == "aspnet_core":
                if name.startswith("appsettings") and name.endswith(".json"):
                    supporting.append(f"{path}:appsettings")
                elif lower.endswith((".cshtml", ".razor")):
                    supporting.append(f"{path}:razor")
                elif name in {"program.cs", "startup.cs"}:
                    try:
                        text, _, _ = read_bounded_text(root, path, 256 * 1024)
                    except OSError:
                        text = ""
                    if "Microsoft.AspNetCore" in text or re.search(r"\bWebApplication\s*\.\s*CreateBuilder\b", text):
                        supporting.append(f"{path}:host-bootstrap")
            else:
                if name == "web.config":
                    try:
                        text, _, _ = read_bounded_text(root, path, 512 * 1024)
                    except OSError:
                        text = ""
                    if re.search(r"<\s*system\.web(?:Server)?\b", text, re.IGNORECASE):
                        strong.append(f"{path}:system-web-config")
                elif name == "global.asax" or lower.endswith((".aspx", ".ascx", ".master", ".asmx", ".ashx")):
                    strong.append(f"{path}:legacy-web-artifact")
                elif "/app_start/" in f"/{lower}":
                    supporting.append(f"{path}:app-start")
                elif name == "packages.config":
                    supporting.append(f"{path}:packages-config")
        detected = bool(strong) or len(supporting) >= 2
        confidence = min(1.0, (0.75 if strong else 0.0) + 0.12 * len(supporting)) if detected else 0.0
        detections.append(
            ModuleDetection(
                framework=framework,
                module_path=module_path,
                module_id=module_id(framework, module_path),
                detected=detected,
                evidence=tuple(sorted(set(strong))),
                supporting_evidence=tuple(sorted(set(supporting))),
                confidence=confidence,
                artifacts=files,
            )
        )
    return tuple(detections)
