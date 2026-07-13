from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


SPRING_BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
SPRING_CONFIG_EXTS = (".properties", ".yml", ".yaml", ".json", ".xml")
SPRING_SOURCE_EXTS = (".java", ".kt", ".kts")

_SPRING_TEXT_MARKERS = (
    "org.springframework.boot",
    "spring-boot-starter",
    "org.springframework",
    "springframework",
    "io.spring.dependency-management",
)
_SPRING_SOURCE_MARKERS = (
    "@SpringBootApplication",
    "@EnableAutoConfiguration",
    "@RestController",
    "@Controller",
    "@Service",
    "@Repository",
    "@Component",
    "SpringApplication.run",
    "org.springframework.",
)
_ANDROID_MARKERS = (
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
)
_EXCLUDED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".gradle",
    ".idea",
    ".mvn",
    ".settings",
    ".cache",
    ".parcel-cache",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "bin",
    "build",
    "coverage",
    "dist",
    "gen",
    "generated",
    "node_modules",
    "out",
    "target",
})
_PROFILE_CONFIG_RE = re.compile(r"^application(?:-([A-Za-z0-9_.-]+))?\.(properties|ya?ml|json)$", re.IGNORECASE)


@dataclass(frozen=True)
class SpringDetectionResult:
    is_spring: bool
    module_root: str
    evidence: Tuple[str, ...]
    confidence: float


def safe_rel_path(path: str) -> str:
    return path.replace("\\", "/").strip("./")


def read_limited(path: str, limit: int = 128 * 1024) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def is_application_config_name(name: str) -> bool:
    return bool(_PROFILE_CONFIG_RE.match(name))


def infer_module_dir(rel_path: str) -> str:
    rel = safe_rel_path(rel_path)
    for prefix in ("src/main/", "src/test/", "src/integrationTest/", "src/androidTest/"):
        if rel.startswith(prefix):
            return ""
    for token in ("/src/main/", "/src/test/", "/src/integrationTest/", "/src/androidTest/"):
        idx = rel.find(token)
        if idx >= 0:
            return rel[:idx]
    return os.path.dirname(rel)


class SpringProjectDetector:
    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._module_cache: Dict[str, SpringDetectionResult] = {}
        self._android_cache: Dict[str, bool] = {}

    def detect_path(self, rel_path: str) -> SpringDetectionResult:
        rel = safe_rel_path(rel_path)
        module_dir = infer_module_dir(rel)
        key = module_dir or "."
        cached = self._module_cache.get(key)
        if cached is not None:
            return cached

        if self._module_has_android_evidence(module_dir):
            result = SpringDetectionResult(False, module_dir, ("android-module",), 0.0)
            self._module_cache[key] = result
            return result

        evidence: List[str] = []
        for build_name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            build_rel = f"{module_dir}/{build_name}" if module_dir else build_name
            build_abs = os.path.join(self.root, build_rel)
            if not os.path.isfile(build_abs):
                continue
            text = read_limited(build_abs).lower()
            for marker in _SPRING_TEXT_MARKERS:
                if marker.lower() in text:
                    evidence.append(f"{build_rel}:{marker}")

        evidence.extend(self._scan_module_source_evidence(module_dir))

        if is_application_config_name(os.path.basename(rel)):
            evidence.append(f"{rel}:application-config")

        confidence = min(1.0, 0.45 + 0.15 * len(evidence)) if evidence else 0.0
        result = SpringDetectionResult(bool(evidence), module_dir, tuple(dict.fromkeys(evidence)), confidence)
        self._module_cache[key] = result
        return result

    def discover_modules(self, languages: Sequence[str] = ("java", "kotlin")) -> List[Dict[str, object]]:
        modules: Dict[str, Dict[str, object]] = {}
        requested = set(languages)
        for abs_path, rel_path in self._iter_files():
            lower = rel_path.lower()
            name = os.path.basename(lower)
            if not (
                lower.endswith(SPRING_SOURCE_EXTS)
                or name in SPRING_BUILD_FILES
                or is_application_config_name(name)
            ):
                continue
            result = self.detect_path(rel_path)
            if not result.is_spring:
                continue
            module_key = result.module_root or "."
            bucket = modules.setdefault(
                module_key,
                {
                    "rel_path": module_key,
                    "languages": set(),
                    "build_files": set(),
                    "config_files": set(),
                    "evidence": set(),
                    "confidence": result.confidence,
                },
            )
            bucket["evidence"].update(result.evidence)  # type: ignore[union-attr]
            bucket["confidence"] = max(float(bucket["confidence"]), result.confidence)
            if lower.endswith(".java") and "java" in requested:
                bucket["languages"].add("java")  # type: ignore[union-attr]
            elif lower.endswith((".kt", ".kts")) and "kotlin" in requested:
                bucket["languages"].add("kotlin")  # type: ignore[union-attr]
            elif name in SPRING_BUILD_FILES:
                bucket["build_files"].add(rel_path)  # type: ignore[union-attr]
            elif is_application_config_name(name):
                bucket["config_files"].add(rel_path)  # type: ignore[union-attr]

        normalized: List[Dict[str, object]] = []
        for rel_path, item in sorted(modules.items()):
            normalized.append(
                {
                    "rel_path": rel_path,
                    "languages": tuple(sorted(item["languages"])),  # type: ignore[index]
                    "build_files": tuple(sorted(item["build_files"])),  # type: ignore[index]
                    "config_files": tuple(sorted(item["config_files"])),  # type: ignore[index]
                    "evidence": tuple(sorted(item["evidence"])),  # type: ignore[index]
                    "confidence": float(item["confidence"]),
                }
            )
        return normalized

    def _iter_files(self) -> Iterable[Tuple[str, str]]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                rel_path = safe_rel_path(os.path.relpath(abs_path, self.root))
                yield abs_path, rel_path

    def _module_has_android_evidence(self, module_dir: str) -> bool:
        key = module_dir or "."
        cached = self._android_cache.get(key)
        if cached is not None:
            return cached
        base = os.path.join(self.root, module_dir) if module_dir else self.root
        manifest = os.path.join(base, "src", "main", "AndroidManifest.xml")
        if os.path.isfile(manifest):
            self._android_cache[key] = True
            return True
        for gradle_name in ("build.gradle", "build.gradle.kts"):
            gradle = os.path.join(base, gradle_name)
            if not os.path.isfile(gradle):
                continue
            text = read_limited(gradle).lower()
            if any(marker in text for marker in _ANDROID_MARKERS):
                self._android_cache[key] = True
                return True
        self._android_cache[key] = False
        return False

    def _scan_module_source_evidence(self, module_dir: str) -> Tuple[str, ...]:
        base = os.path.join(self.root, module_dir) if module_dir else self.root
        src_root = os.path.join(base, "src")
        if not os.path.isdir(src_root):
            return ()
        evidence: List[str] = []
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
            for name in filenames:
                lower = name.lower()
                if not lower.endswith((".java", ".kt")):
                    continue
                abs_path = os.path.join(dirpath, name)
                rel_path = safe_rel_path(os.path.relpath(abs_path, self.root))
                text = read_limited(abs_path)
                for marker in _SPRING_SOURCE_MARKERS:
                    if marker in text:
                        evidence.append(f"{rel_path}:{marker}")
                        break
                if len(evidence) >= 50:
                    return tuple(evidence)
        return tuple(evidence)
