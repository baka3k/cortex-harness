from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


MYBATIS_BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
MYBATIS_SOURCE_EXTS = (".java", ".kt", ".kts")
MYBATIS_XML_EXTS = (".xml",)

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
_ANDROID_MARKERS = (
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
)
_MYBATIS_BUILD_MARKERS = (
    "org.mybatis",
    "mybatis",
    "mybatis-spring",
    "mybatis-spring-boot-starter",
)
_MYBATIS_JAVA_MARKERS = (
    "org.apache.ibatis.annotations.Mapper",
    "org.apache.ibatis.annotations.Select",
    "org.apache.ibatis.annotations.Insert",
    "org.apache.ibatis.annotations.Update",
    "org.apache.ibatis.annotations.Delete",
    "org.apache.ibatis.annotations.Results",
    "org.apache.ibatis.annotations.ResultMap",
    "@Mapper",
    "@Select",
    "@Insert",
    "@Update",
    "@Delete",
    "@Results",
    "@ResultMap",
)
_MYBATIS_SPRING_MARKERS = (
    "org.mybatis.spring.SqlSessionFactoryBean",
    "org.mybatis.spring.mapper.MapperScannerConfigurer",
    "SqlSessionFactoryBean",
    "MapperScannerConfigurer",
    "mybatis:scan",
)
_MAPPER_ROOT_RE = re.compile(r"<\s*mapper\b[^>]*\bnamespace\s*=", re.IGNORECASE | re.DOTALL)
_CONFIG_ROOT_RE = re.compile(r"<\s*configuration\b", re.IGNORECASE)
_XML_DECL_OR_MISC_RE = re.compile(r"\A\s*(?:<\?xml\b[^?]*\?>\s*|<!--.*?-->\s*|<!DOCTYPE\b[^>]*>\s*)*", re.IGNORECASE | re.DOTALL)
_FIRST_ELEMENT_RE = re.compile(r"<\s*([A-Za-z_][\w:.-]*)\b", re.DOTALL)
_MYBATIS_CONFIG_CHILD_RE = re.compile(
    r"<\s*(properties|settings|typeAliases|typeHandlers|objectFactory|objectWrapperFactory|"
    r"reflectorFactory|plugins|environments|databaseIdProvider|mappers)\b",
    re.IGNORECASE,
)
_MYBATIS_DTD_RE = re.compile(r"mybatis\.org//DTD\s+(?:Mapper|Config)", re.IGNORECASE)


@dataclass(frozen=True)
class MyBatisDetectionResult:
    is_mybatis: bool
    module_root: str
    artifact_kind: str
    evidence: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    confidence: float


def safe_rel_path(path: str) -> str:
    return path.replace("\\", "/").strip("./")


def read_limited(path: str, limit: int = 256 * 1024) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


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


class MyBatisProjectDetector:
    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._module_cache: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], float]] = {}
        self._android_cache: Dict[str, bool] = {}

    def detect_path(self, rel_path: str) -> MyBatisDetectionResult:
        rel = safe_rel_path(rel_path)
        module_dir = infer_module_dir(rel)
        if self._module_has_android_evidence(module_dir):
            return MyBatisDetectionResult(False, module_dir, "", ("android-module",), (), 0.0)

        abs_path = os.path.join(self.root, rel)
        text = read_limited(abs_path)
        lower = rel.lower()
        name = os.path.basename(lower)
        evidence: List[str] = []
        supporting: List[str] = []
        artifact_kind = ""

        if lower.endswith(MYBATIS_XML_EXTS):
            xml_kind, xml_evidence = classify_xml_text(rel, text)
            if xml_kind:
                artifact_kind = xml_kind
                evidence.extend(xml_evidence)
            elif name.endswith("mapper.xml"):
                supporting.append(f"{rel}:mapper-xml-name")
        elif lower.endswith(MYBATIS_SOURCE_EXTS):
            java_evidence = _source_evidence(rel, text)
            if java_evidence:
                artifact_kind = "java_mapper"
                evidence.extend(java_evidence)
            elif name.endswith("mapper.java") or name.endswith("mapper.kt"):
                supporting.append(f"{rel}:mapper-source-name")
        elif name in MYBATIS_BUILD_FILES:
            build_evidence = _marker_evidence(rel, text.lower(), _MYBATIS_BUILD_MARKERS)
            if build_evidence:
                artifact_kind = "build"
                evidence.extend(build_evidence)

        direct_evidence = tuple(dict.fromkeys(evidence))
        module_evidence, module_supporting, module_confidence = self._module_evidence(module_dir)
        supporting.extend(module_supporting)
        supporting = list(dict.fromkeys(supporting))
        if not direct_evidence:
            return MyBatisDetectionResult(False, module_dir, "", (), tuple(supporting), 0.0)

        evidence.extend(module_evidence)
        evidence = list(dict.fromkeys(evidence))
        confidence = max(module_confidence, _confidence(evidence, supporting))
        return MyBatisDetectionResult(True, module_dir, artifact_kind, tuple(evidence), tuple(supporting), confidence)

    def discover_modules(self, languages: Sequence[str] = ("java", "kotlin")) -> List[Dict[str, object]]:
        modules: Dict[str, Dict[str, object]] = {}
        requested = set(languages)
        for abs_path, rel_path in self._iter_files():
            lower = rel_path.lower()
            name = os.path.basename(lower)
            if not (
                lower.endswith(MYBATIS_XML_EXTS)
                or lower.endswith(MYBATIS_SOURCE_EXTS)
                or name in MYBATIS_BUILD_FILES
            ):
                continue
            result = self.detect_path(rel_path)
            if not result.is_mybatis:
                continue
            module_key = result.module_root or "."
            bucket = modules.setdefault(
                module_key,
                {
                    "rel_path": module_key,
                    "mapper_xml_files": set(),
                    "config_xml_files": set(),
                    "java_files": set(),
                    "build_files": set(),
                    "spring_config_files": set(),
                    "evidence": set(),
                    "confidence": result.confidence,
                },
            )
            bucket["evidence"].update(result.evidence)  # type: ignore[union-attr]
            bucket["confidence"] = max(float(bucket["confidence"]), result.confidence)
            if result.artifact_kind == "mapper_xml":
                bucket["mapper_xml_files"].add(rel_path)  # type: ignore[union-attr]
            elif result.artifact_kind == "config_xml":
                bucket["config_xml_files"].add(rel_path)  # type: ignore[union-attr]
            elif result.artifact_kind == "spring_xml":
                bucket["spring_config_files"].add(rel_path)  # type: ignore[union-attr]
            elif result.artifact_kind == "java_mapper" and lower.endswith(".java") and "java" in requested:
                bucket["java_files"].add(rel_path)  # type: ignore[union-attr]
            elif result.artifact_kind == "java_mapper" and lower.endswith((".kt", ".kts")) and "kotlin" in requested:
                bucket["java_files"].add(rel_path)  # type: ignore[union-attr]
            elif result.artifact_kind == "build" and name in MYBATIS_BUILD_FILES:
                bucket["build_files"].add(rel_path)  # type: ignore[union-attr]

        normalized: List[Dict[str, object]] = []
        for rel_path, item in sorted(modules.items()):
            normalized.append(
                {
                    "rel_path": rel_path,
                    "mapper_xml_files": tuple(sorted(item["mapper_xml_files"])),  # type: ignore[index]
                    "config_xml_files": tuple(sorted(item["config_xml_files"])),  # type: ignore[index]
                    "java_files": tuple(sorted(item["java_files"])),  # type: ignore[index]
                    "build_files": tuple(sorted(item["build_files"])),  # type: ignore[index]
                    "spring_config_files": tuple(sorted(item["spring_config_files"])),  # type: ignore[index]
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
                yield abs_path, safe_rel_path(os.path.relpath(abs_path, self.root))

    def _module_evidence(self, module_dir: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], float]:
        key = module_dir or "."
        cached = self._module_cache.get(key)
        if cached is not None:
            return cached

        if self._module_has_android_evidence(module_dir):
            result = ((), ("android-module",), 0.0)
            self._module_cache[key] = result
            return result

        evidence: List[str] = []
        supporting: List[str] = []
        base = os.path.join(self.root, module_dir) if module_dir else self.root
        for build_name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            rel = f"{module_dir}/{build_name}" if module_dir else build_name
            path = os.path.join(base, build_name)
            if os.path.isfile(path):
                evidence.extend(_marker_evidence(rel, read_limited(path).lower(), _MYBATIS_BUILD_MARKERS))

        src_root = os.path.join(base, "src")
        if os.path.isdir(src_root):
            for dirpath, dirnames, filenames in os.walk(src_root):
                dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
                for name in filenames:
                    rel = safe_rel_path(os.path.relpath(os.path.join(dirpath, name), self.root))
                    lower = rel.lower()
                    text = read_limited(os.path.join(dirpath, name))
                    if lower.endswith(".xml"):
                        kind, xml_evidence = classify_xml_text(rel, text)
                        evidence.extend(xml_evidence)
                        if not kind and name.lower().endswith("mapper.xml"):
                            supporting.append(f"{rel}:mapper-xml-name")
                    elif lower.endswith(MYBATIS_SOURCE_EXTS):
                        evidence.extend(_source_evidence(rel, text))
                        if name.lower().endswith(("mapper.java", "mapper.kt")):
                            supporting.append(f"{rel}:mapper-source-name")
                    if len(evidence) >= 80:
                        confidence = _confidence(evidence, supporting)
                        result = (tuple(dict.fromkeys(evidence)), tuple(dict.fromkeys(supporting)), confidence)
                        self._module_cache[key] = result
                        return result

        confidence = _confidence(evidence, supporting)
        result = (tuple(dict.fromkeys(evidence)), tuple(dict.fromkeys(supporting)), confidence)
        self._module_cache[key] = result
        return result

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
            if os.path.isfile(gradle):
                text = read_limited(gradle).lower()
                if any(marker in text for marker in _ANDROID_MARKERS):
                    self._android_cache[key] = True
                    return True
        self._android_cache[key] = False
        return False


def classify_xml_text(rel_path: str, text: str) -> Tuple[str, List[str]]:
    evidence: List[str] = []
    root_tag = _first_element_name(text)
    if root_tag == "mapper" and _MAPPER_ROOT_RE.search(text):
        evidence.append(f"{rel_path}:mapper-root-namespace")
        return "mapper_xml", evidence
    if _MYBATIS_DTD_RE.search(text):
        evidence.append(f"{rel_path}:mybatis-dtd")
        if "DTD Mapper" in text or "<mapper" in text:
            return "mapper_xml", evidence
        if "DTD Config" in text or root_tag == "configuration" or _CONFIG_ROOT_RE.search(text):
            return "config_xml", evidence
    spring_evidence = _marker_evidence(rel_path, text, _MYBATIS_SPRING_MARKERS)
    if spring_evidence:
        return "spring_xml", spring_evidence
    if root_tag == "configuration" and ("mybatis" in text.lower() or _MYBATIS_CONFIG_CHILD_RE.search(text)):
        evidence.append(f"{rel_path}:mybatis-configuration-root")
        return "config_xml", evidence
    return "", evidence


def _first_element_name(text: str) -> str:
    match = _XML_DECL_OR_MISC_RE.match(text or "")
    start = match.end() if match else 0
    element = _FIRST_ELEMENT_RE.match(text[start:])
    return element.group(1).split(":")[-1].lower() if element else ""


def _source_evidence(rel_path: str, text: str) -> List[str]:
    return _marker_evidence(rel_path, text, _MYBATIS_JAVA_MARKERS)


def _marker_evidence(rel_path: str, text: str, markers: Sequence[str]) -> List[str]:
    return [f"{rel_path}:{marker}" for marker in markers if marker in text]


def _confidence(evidence: Sequence[str], supporting: Sequence[str]) -> float:
    if not evidence:
        return 0.0
    return min(1.0, 0.55 + 0.12 * len(evidence) + 0.04 * len(supporting))
