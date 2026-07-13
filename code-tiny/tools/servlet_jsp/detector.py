from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from tools.servlet_jsp.models import stable_digest
from tools.servlet_jsp.path_resolver import normalize_relative_path, read_bounded_file


SERVLET_JSP_BUILD_FILES = frozenset({
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "build.xml",
})
SERVLET_JSP_VIEW_EXTS = (".jsp", ".jspx", ".jspf")
SERVLET_JSP_SOURCE_EXTS = (".java",)
SERVLET_JSP_PROPERTIES_EXTS = (".properties",)
SERVLET_JSP_STATIC_EXTS = (".html", ".htm", ".css", ".js")

_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".gradle", ".idea", ".mvn", ".settings", ".cache",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", "bin",
    "build", "coverage", "dist", "gen", "generated", "node_modules", "out", "target", "vendor",
})
_ANDROID_MARKERS = (
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
)
_JAVA_STRONG_RE = re.compile(
    r"(?:javax|jakarta)\.servlet\.|@(?:WebServlet|WebFilter|WebListener)\b|"
    r"\bextends\s+HttpServlet\b|\bimplements\s+(?:Filter|ServletContextListener|HttpSessionListener|ServletRequestListener)\b"
)
_DESCRIPTOR_ROOT_RE = re.compile(r"<\s*(?:[A-Za-z_][\w.-]*:)?web-app\b", re.IGNORECASE)
_DESCRIPTOR_CONTENT_RE = re.compile(
    r"<\s*(?:[A-Za-z_][\w.-]*:)?(?:servlet|servlet-mapping|filter|filter-mapping|listener|jsp-config)\b",
    re.IGNORECASE,
)
_JSP_RE = re.compile(r"<%@|<jsp:|\$\{|#\{|<\w+:\w+", re.IGNORECASE)
_BUILD_MARKERS = (
    "jakarta.servlet",
    "javax.servlet",
    "servlet-api",
    "jakarta.servlet-api",
    "jsp-api",
    "jakarta.servlet.jsp",
    "tomcat-embed-jasper",
)


@dataclass(frozen=True)
class ServletJspDetectionResult:
    is_servlet_jsp: bool
    module_root: str
    module_id: str
    artifact_kind: str
    evidence: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    confidence: float


def infer_module_dir(rel_path: str) -> str:
    rel = normalize_relative_path(rel_path)
    for prefix in ("src/main/", "src/test/", "src/integrationTest/"):
        if rel.startswith(prefix):
            return ""
    for token in ("/src/main/", "/src/test/", "/src/integrationTest/"):
        index = rel.find(token)
        if index >= 0:
            return rel[:index]
    if "/WEB-INF/" in rel:
        return rel.split("/WEB-INF/", 1)[0]
    return os.path.dirname(rel)


def module_id_for_path(module_path: str) -> str:
    normalized = normalize_relative_path(module_path) or "."
    return f"servlet_jsp_module::{stable_digest(normalized)}"


class ServletJspProjectDetector:
    def __init__(self, root: str, read_limit: int = 256 * 1024) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self.read_limit = read_limit
        self._module_cache: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], float, bool]] = {}

    def detect_path(self, rel_path: str) -> ServletJspDetectionResult:
        rel = normalize_relative_path(rel_path)
        module_root = infer_module_dir(rel)
        module_id = module_id_for_path(module_root)
        if self._module_is_android(module_root):
            return ServletJspDetectionResult(False, module_root, module_id, "", ("android-module",), (), 0.0)
        kind, direct, supporting = self._classify(rel)
        module_evidence, module_supporting, module_confidence, module_is_web = self._module_evidence(module_root)
        if not direct and not module_is_web:
            return ServletJspDetectionResult(False, module_root, module_id, kind, (), tuple(supporting), 0.0)
        evidence = tuple(dict.fromkeys([*direct, *module_evidence]))
        support = tuple(dict.fromkeys([*supporting, *module_supporting]))
        confidence = max(module_confidence, _confidence(evidence, support))
        return ServletJspDetectionResult(bool(evidence), module_root, module_id, kind, evidence, support, confidence)

    def discover_modules(self) -> List[Dict[str, object]]:
        modules: Dict[str, Dict[str, object]] = {}
        for _, rel in self._iter_files():
            kind, direct, supporting = self._classify(rel)
            if not kind:
                continue
            result = self.detect_path(rel)
            if not result.is_servlet_jsp:
                continue
            key = result.module_root or "."
            bucket = modules.setdefault(
                key,
                {
                    "module_id": result.module_id,
                    "rel_path": key,
                    "java_files": set(),
                    "descriptor_files": set(),
                    "jsp_files": set(),
                    "properties_files": set(),
                    "build_files": set(),
                    "static_files": set(),
                    "evidence": set(),
                    "confidence": 0.0,
                },
            )
            if kind == "java":
                bucket["java_files"].add(rel)
            elif kind == "web_xml":
                bucket["descriptor_files"].add(rel)
            elif kind in {"jsp", "jspx", "jsp_fragment"}:
                bucket["jsp_files"].add(rel)
            elif kind == "properties":
                bucket["properties_files"].add(rel)
            elif kind == "build":
                bucket["build_files"].add(rel)
            elif kind == "static":
                bucket["static_files"].add(rel)
            bucket["evidence"].update(result.evidence)
            bucket["evidence"].update(direct)
            bucket["evidence"].update(supporting)
            bucket["confidence"] = max(float(bucket["confidence"]), result.confidence)
        normalized: List[Dict[str, object]] = []
        for key in sorted(modules):
            bucket = modules[key]
            for name in (
                "java_files", "descriptor_files", "jsp_files", "properties_files", "build_files", "static_files", "evidence"
            ):
                bucket[name] = tuple(sorted(bucket[name]))
            normalized.append(bucket)
        return normalized

    def _classify(self, rel_path: str) -> Tuple[str, List[str], List[str]]:
        lower = rel_path.lower()
        name = os.path.basename(lower)
        text = self._read_text(rel_path)
        direct: List[str] = []
        supporting: List[str] = []
        kind = ""
        if lower.endswith(".java"):
            kind = "java"
            if _JAVA_STRONG_RE.search(text):
                direct.append(f"{rel_path}:servlet-api-java")
        elif name == "web.xml":
            kind = "web_xml"
            if _DESCRIPTOR_ROOT_RE.search(text) and _DESCRIPTOR_CONTENT_RE.search(text):
                direct.append(f"{rel_path}:web-app-descriptor")
            else:
                supporting.append(f"{rel_path}:web-xml-name")
        elif lower.endswith(SERVLET_JSP_VIEW_EXTS):
            kind = "jspx" if lower.endswith(".jspx") else "jsp_fragment" if lower.endswith(".jspf") else "jsp"
            if _JSP_RE.search(text) or lower.endswith((".jsp", ".jspx", ".jspf")):
                direct.append(f"{rel_path}:jsp-view")
        elif lower.endswith(SERVLET_JSP_PROPERTIES_EXTS):
            kind = "properties"
            supporting.append(f"{rel_path}:properties")
        elif name in SERVLET_JSP_BUILD_FILES:
            kind = "build"
            normalized = text.lower()
            hits = [marker for marker in _BUILD_MARKERS if marker in normalized]
            supporting.extend(f"{rel_path}:build:{marker}" for marker in hits)
        elif lower.endswith(SERVLET_JSP_STATIC_EXTS):
            kind = "static"
            supporting.append(f"{rel_path}:static-target")
        return kind, direct, supporting

    def _module_evidence(self, module_root: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], float, bool]:
        key = normalize_relative_path(module_root)
        cached = self._module_cache.get(key)
        if cached is not None:
            return cached
        evidence: List[str] = []
        supporting: List[str] = []
        prefix = f"{key}/" if key else ""
        for _, rel in self._iter_files():
            if key and not rel.startswith(prefix):
                continue
            if not key and "/src/main/" in rel:
                continue
            kind, direct, support = self._classify(rel)
            if kind:
                evidence.extend(direct)
                supporting.extend(support)
        unique_evidence = tuple(dict.fromkeys(evidence))
        unique_support = tuple(dict.fromkeys(supporting))
        result = (unique_evidence, unique_support, _confidence(unique_evidence, unique_support), bool(unique_evidence))
        self._module_cache[key] = result
        return result

    def _module_is_android(self, module_root: str) -> bool:
        base = os.path.join(self.root, module_root) if module_root else self.root
        if os.path.isfile(os.path.join(base, "src", "main", "AndroidManifest.xml")):
            return True
        for name in ("build.gradle", "build.gradle.kts"):
            path = os.path.join(base, name)
            if not os.path.isfile(path):
                continue
            try:
                data, _ = read_bounded_file(path, 64 * 1024)
            except OSError:
                continue
            if any(marker in data.decode("utf-8", errors="ignore") for marker in _ANDROID_MARKERS):
                return True
        return False

    def _read_text(self, rel_path: str) -> str:
        path = os.path.join(self.root, normalize_relative_path(rel_path))
        try:
            data, _ = read_bounded_file(path, self.read_limit)
        except OSError:
            return ""
        return data.decode("utf-8", errors="ignore")

    def _iter_files(self) -> Iterable[Tuple[str, str]]:
        for current, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in _EXCLUDED_DIRS)
            for name in sorted(files):
                absolute = os.path.join(current, name)
                rel = normalize_relative_path(os.path.relpath(absolute, self.root))
                yield absolute, rel


def _confidence(evidence: Sequence[str], supporting: Sequence[str]) -> float:
    if not evidence:
        return 0.0
    return min(1.0, 0.72 + 0.08 * min(len(evidence) - 1, 2) + 0.03 * min(len(supporting), 3))

