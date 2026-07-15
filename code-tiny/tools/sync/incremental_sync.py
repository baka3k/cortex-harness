#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import fcntl
except Exception:  # pragma: no cover - non-posix environments
    fcntl = None

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config

from tools.common.analyzer_cache import safe_cache_root
from tools.common.git_diff import (
    collect_changed_and_deleted,
    collect_git_diff_entries,
    write_manifest_paths,
)
from tools.common.incremental_sync_state import (
    load_sync_state,
    mark_clean,
    mark_dirty,
    state_file_path,
)
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.cli import add_graph_provider_args, normalize_graph_provider, prepare_graph_args
from tools.vb.vb_path_classifier import VBPathClassifier
from tools.ts.ts_project_detector import detect_project_type as _detect_ts_project_type

_ANDROID_PLUGIN_MARKERS = (
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
)


@dataclass(frozen=True)
class AnalyzerConfig:
    parser: str
    script_path: str
    incremental_supported: bool
    extra_args: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameworkAnalyzerConfig:
    framework: str
    script_path: str
    incremental_supported: bool
    prerequisite_parsers: Tuple[str, ...]
    order: int
    writes_vectors: bool = False
    extra_args: Tuple[str, ...] = ()


ANALYZERS: Dict[str, AnalyzerConfig] = {
    "cobol": AnalyzerConfig("cobol", os.path.join(_ROOT_DIR, "tools", "cobol", "cobol_analyzer.py"), True),
    "dart": AnalyzerConfig(
        "dart", os.path.join(_ROOT_DIR, "tools", "flutter", "flutter_analyzer.py"), True, ("--mode", "dart")
    ),
    "cplus": AnalyzerConfig("cplus", os.path.join(_ROOT_DIR, "tools", "cplus", "cplus_analyzer.py"), True),
    "delphi": AnalyzerConfig("delphi", os.path.join(_ROOT_DIR, "tools", "delphi", "delphi_analyzer.py"), True),
    "java": AnalyzerConfig("java", os.path.join(_ROOT_DIR, "tools", "java", "java_analyzer.py"), True),
    "kotlin": AnalyzerConfig("kotlin", os.path.join(_ROOT_DIR, "tools", "kotlin", "kotlin_analyzer.py"), True),
    "android": AnalyzerConfig("android", os.path.join(_ROOT_DIR, "tools", "android", "android_kotlin_analyzer.py"), True),
    "vbnet": AnalyzerConfig("vbnet", os.path.join(_ROOT_DIR, "tools", "vb", "vbnet_analyzer.py"), True),
    "vb6": AnalyzerConfig("vb6", os.path.join(_ROOT_DIR, "tools", "vb", "vb6_analyzer.py"), True),
    "vba": AnalyzerConfig("vba", os.path.join(_ROOT_DIR, "tools", "vb", "vba_analyzer.py"), True),
    "vbscript": AnalyzerConfig("vbscript", os.path.join(_ROOT_DIR, "tools", "vb", "vbscript_analyzer.py"), True),
    "python": AnalyzerConfig("python", os.path.join(_ROOT_DIR, "tools", "python", "python_analyzer.py"), True),
    "go": AnalyzerConfig("go", os.path.join(_ROOT_DIR, "tools", "go", "go_analyzer.py"), True),
    "perl": AnalyzerConfig("perl", os.path.join(_ROOT_DIR, "tools", "perl", "perl_analyzer.py"), True),
    "rust": AnalyzerConfig("rust", os.path.join(_ROOT_DIR, "tools", "rust", "rust_analyzer.py"), True),
    "swift": AnalyzerConfig("swift", os.path.join(_ROOT_DIR, "tools", "swift", "swift_analyzer.py"), True),
    "js": AnalyzerConfig("js", os.path.join(_ROOT_DIR, "tools", "js", "js_analyzer.py"), True),
    "ts": AnalyzerConfig("ts", os.path.join(_ROOT_DIR, "tools", "ts", "ts_analyzer.py"), True),
    "php": AnalyzerConfig("php", os.path.join(_ROOT_DIR, "tools", "php", "php_analyzer.py"), True),
    "csharp": AnalyzerConfig("csharp", os.path.join(_ROOT_DIR, "tools", "csharp", "csharp_analyzer.py"), True),
    "sql": AnalyzerConfig("sql", os.path.join(_ROOT_DIR, "tools", "sql", "sql_analyzer.py"), True),
    "plsql": AnalyzerConfig("plsql", os.path.join(_ROOT_DIR, "tools", "plsql", "plsql_analyzer.py"), True),
}

FRAMEWORK_ANALYZERS: Dict[str, FrameworkAnalyzerConfig] = {
    "spring": FrameworkAnalyzerConfig(
        "spring", os.path.join(_ROOT_DIR, "tools", "spring", "spring_analyzer.py"), True,
        ("java", "kotlin"), 10,
    ),
    "servlet_jsp": FrameworkAnalyzerConfig(
        "servlet_jsp", os.path.join(_ROOT_DIR, "tools", "servlet_jsp", "servlet_jsp_analyzer.py"), True,
        ("java",), 20,
    ),
    "mybatis": FrameworkAnalyzerConfig(
        "mybatis", os.path.join(_ROOT_DIR, "tools", "mybatis", "mybatis_analyzer.py"), True,
        ("java", "kotlin"), 30,
    ),
    "struts": FrameworkAnalyzerConfig(
        "struts", os.path.join(_ROOT_DIR, "tools", "struts", "struts_analyzer.py"), True,
        ("java",), 40,
    ),
    "flutter": FrameworkAnalyzerConfig(
        "flutter", os.path.join(_ROOT_DIR, "tools", "flutter", "flutter_analyzer.py"), True,
        ("dart",), 50, False, ("--mode", "flutter"),
    ),
    "aspnet_framework": FrameworkAnalyzerConfig(
        "aspnet_framework",
        os.path.join(_ROOT_DIR, "tools", "aspnet_framework", "aspnet_framework_analyzer.py"),
        True,
        ("csharp",),
        60,
    ),
    "aspnet_core": FrameworkAnalyzerConfig(
        "aspnet_core",
        os.path.join(_ROOT_DIR, "tools", "aspnet_core", "aspnet_core_analyzer.py"),
        True,
        ("csharp",),
        70,
    ),
}

_FRAMEWORK_CANDIDATE_EXTENSIONS: Dict[str, Set[str]] = {
    "spring": {".java", ".kt", ".kts", ".xml", ".properties", ".yml", ".yaml", ".json", ".gradle"},
    "servlet_jsp": {".java", ".jsp", ".jspx", ".jspf", ".tag", ".tagx", ".xml", ".properties", ".gradle"},
    "mybatis": {".java", ".kt", ".kts", ".xml", ".gradle"},
    "struts": {".java", ".xml", ".properties", ".yml", ".yaml", ".gradle"},
    "flutter": {
        ".dart", ".arb", ".json", ".xml", ".plist", ".gradle", ".properties", ".yml", ".yaml",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ttf", ".otf",
    },
    "aspnet_framework": {
        ".cs", ".csproj", ".sln", ".config", ".asax", ".aspx", ".ascx", ".master",
        ".asmx", ".ashx", ".cshtml", ".resx",
    },
    "aspnet_core": {".cs", ".csproj", ".sln", ".cshtml", ".razor", ".json", ".config"},
}

_FRAMEWORK_BUILD_FILES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
}

MESSAGE_ENABLED_PARSERS: Set[str] = {
    "cplus",
    "delphi",
    "java",
    "csharp",
    "kotlin",
    "android",
    "vbnet",
    "vb6",
    "vba",
    "vbscript",
    "python",
    "swift",
    "js",
    "ts",
    "php",
    "sql",
    "plsql",
}

_TS_BACKEND_SCRIPT = os.path.join(_ROOT_DIR, "tools", "ts", "ts_backend_analyzer.py")
_TS_FRONTEND_SCRIPT = os.path.join(_ROOT_DIR, "tools", "ts", "ts_analyzer.py")


def _resolve_ts_analyzer(root: str) -> AnalyzerConfig:
    """Pick ts_backend_analyzer or ts_analyzer based on project type detection."""
    result = _detect_ts_project_type(root)
    project_type = result.project_type
    if project_type in ("backend", "fullstack"):
        script = _TS_BACKEND_SCRIPT
    else:
        script = _TS_FRONTEND_SCRIPT
    print(f"[ts-detect] project_type={project_type} framework={result.framework} -> {os.path.basename(script)}")
    return AnalyzerConfig("ts", script, True)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"


def _normalize_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "project"


def _project_scope_token(project_id: str, root: str) -> str:
    project = _safe_segment(project_id)
    digest = hashlib.sha1(os.path.realpath(os.path.abspath(root)).encode("utf-8")).hexdigest()[:10]
    return f"{project}_{digest}"


def _code_collection_name(
    project_id: str,
    root: str,
    parser: str,
    project_code: Optional[str] = None,
) -> str:
    """Build the per-parser code collection name.

    Both schemes preserve the same root-path hash (``sha1[:10]``) so a
    project with multiple source roots for the same parser produces
    distinct collections in both legacy and per-project modes — there is
    no information loss across the migration.

    Per-project shape:  ``{slug}-code-{parser}-{root_hash}``
    Legacy shape:       ``{safe(id)}_{root_hash}__{parser}_functions``
    """
    digest = hashlib.sha1(
        os.path.realpath(os.path.abspath(root)).encode("utf-8")
    ).hexdigest()[:_ROOT_HASH_LEN]
    if _per_project_scheme_active():
        code = _per_project_require_code(project_code)
        slug = _per_project_slug(code)
        parser_token = _per_project_validate_parser(parser)
        # Must match qdrant_naming.collection_name(role=ROLE_CODE,
        # parser=parser, root_hash=digest) byte-for-byte. The parity test
        # in tests/test_incremental_sync_naming.py guards against drift.
        return f"{slug}-code-{parser_token}-{digest}"
    parser_token = _safe_segment(parser)
    scope = f"{_safe_segment(project_id)}_{digest}"
    name = f"{scope}__{parser_token}_functions"
    return name[:255]


# Per-project collection scheme — keep these constants in lock-step with
# ``hyper_pack_core.qdrant_naming``.  Duplication is deliberate: this script
# is shipped inside ``hyper-dev`` and must not assume the parent repo is on
# sys.path.  A contract test (``tests/test_incremental_sync_naming.py``)
# pins the two implementations together.
_COLLECTION_SCHEME_ENV = "HYPERPACK_COLLECTION_SCHEME"
_COLLECTION_SCHEME_PER_PROJECT = "per_project"
_PROJECT_CODE_RE_LOCAL = re.compile(r"^[A-Z0-9][A-Z0-9\-]{1,19}$")
_PARSER_TOKEN_RE_LOCAL = re.compile(r"^[a-z0-9_]+$")
_ROOT_HASH_LEN = 10  # sha1 hex prefix; matches qdrant_naming root_hash bounds [6,16]


def _per_project_scheme_active() -> bool:
    return (
        os.environ.get(_COLLECTION_SCHEME_ENV, "").strip().lower()
        == _COLLECTION_SCHEME_PER_PROJECT
    )


def _per_project_slug(project_code: str) -> str:
    code = (project_code or "").strip()
    if not _PROJECT_CODE_RE_LOCAL.match(code):
        raise ValueError(
            "project_code must be 2-20 uppercase alphanumeric characters "
            f"with hyphens (e.g., HP-UI, PROJ01); got: {project_code!r}"
        )
    return code.lower()


def _per_project_validate_parser(parser: str) -> str:
    token = (parser or "").strip()
    if not _PARSER_TOKEN_RE_LOCAL.match(token):
        raise ValueError(
            "parser must contain only [a-z0-9_] for per-project collection "
            f"naming; got: {parser!r}. Update the parser token to match "
            "hyper_pack_core.qdrant_naming."
        )
    return token


def _per_project_require_code(project_code: Optional[str]) -> str:
    if not project_code or not project_code.strip():
        raise ValueError(
            "project_code is required when "
            f"{_COLLECTION_SCHEME_ENV}={_COLLECTION_SCHEME_PER_PROJECT}; "
            "pass --project-code or PROJECT_CODE env."
        )
    return project_code


def _message_collection_name(
    project_id: str, root: str, project_code: Optional[str] = None
) -> str:
    del root
    if _per_project_scheme_active():
        code = _per_project_require_code(project_code)
        return f"{_per_project_slug(code)}-messages"
    name = f"{_safe_segment(project_id)}_mess"
    return name[:255]


def _normalize_project_path(root: str, raw_path: str) -> Optional[str]:
    text = (raw_path or "").strip()
    if not text:
        return None
    normalized_root = os.path.realpath(os.path.abspath(root))
    path_text = text.replace("\\", "/")
    if os.path.isabs(path_text):
        abs_path = os.path.realpath(path_text)
        try:
            rel = os.path.relpath(abs_path, normalized_root)
        except ValueError:
            return None
        rel = rel.replace("\\", "/")
        if rel.startswith("../") or rel == "..":
            return None
        return rel
    rel = os.path.normpath(path_text).replace("\\", "/")
    if rel == ".":
        return None
    if rel.startswith("../") or rel == "..":
        return None
    return rel


def _normalize_project_paths(root: str, paths: Iterable[str]) -> Set[str]:
    normalized: Set[str] = set()
    for item in paths:
        rel = _normalize_project_path(root, item)
        if rel:
            normalized.add(rel)
    return normalized


class _ProjectRunLock:
    def __init__(self, lock_path: str, description: str) -> None:
        self.lock_path = lock_path
        self.description = description
        self._handle = None
        self._exclusive_created = False

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        if fcntl:
            handle = open(self.lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise RuntimeError(
                    f"another incremental sync is running for {self.description} (lock: {self.lock_path})"
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} started_at={time.time():.0f}\n")
            handle.flush()
            self._handle = handle
            return

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.lock_path, flags, 0o644)
        except FileExistsError as exc:
            raise RuntimeError(
                f"another incremental sync is running for {self.description} (lock: {self.lock_path})"
            ) from exc
        os.write(fd, f"pid={os.getpid()} started_at={time.time():.0f}\n".encode("utf-8"))
        self._handle = fd
        self._exclusive_created = True

    def release(self) -> None:
        if self._handle is None:
            return
        if fcntl and hasattr(self._handle, "fileno"):
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
        else:
            os.close(int(self._handle))
            if self._exclusive_created:
                try:
                    os.remove(self.lock_path)
                except FileNotFoundError:
                    pass
        self._handle = None


class _AndroidPathClassifier:
    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._manifest_cache: Dict[str, bool] = {}
        self._gradle_cache: Dict[str, bool] = {}

    def is_android_path(self, rel_path: str) -> bool:
        rel = rel_path.replace("\\", "/")
        lower = rel.lower()
        name = os.path.basename(lower)
        if name == "androidmanifest.xml":
            return True
        if name.endswith(".gradle") or name.endswith(".gradle.kts"):
            return True
        if "/src/main/res/" in lower:
            return True
        if lower.endswith(".xml") and "/res/" in lower and "/src/" in lower:
            return True
        ext = os.path.splitext(lower)[1]
        if ext not in {".java", ".kt", ".kts", ".xml"}:
            return False
        module_dir = self._module_dir_from_path(rel)
        if module_dir and self._module_has_android_manifest(module_dir):
            return True
        file_dir = os.path.dirname(rel)
        return self._has_android_gradle_ancestor(file_dir)

    def _module_dir_from_path(self, rel_path: str) -> Optional[str]:
        parts = [part for part in rel_path.split("/") if part]
        for idx in range(len(parts) - 1):
            if parts[idx] == "src":
                return "/".join(parts[:idx])
        return None

    def _module_has_android_manifest(self, module_dir: str) -> bool:
        key = module_dir or "."
        cached = self._manifest_cache.get(key)
        if cached is not None:
            return cached
        candidate = os.path.join(self.root, module_dir, "src", "main", "AndroidManifest.xml")
        found = os.path.isfile(candidate)
        self._manifest_cache[key] = found
        return found

    def _has_android_gradle_ancestor(self, rel_dir: str) -> bool:
        text = rel_dir.replace("\\", "/").strip("/")
        probe = text
        while True:
            key = probe or "."
            cached = self._gradle_cache.get(key)
            if cached is None:
                cached = self._detect_android_gradle(probe)
                self._gradle_cache[key] = cached
            if cached:
                return True
            if not probe:
                return False
            probe = probe.rsplit("/", 1)[0] if "/" in probe else ""

    def _detect_android_gradle(self, rel_dir: str) -> bool:
        base = os.path.join(self.root, rel_dir) if rel_dir else self.root
        for gradle_name in ("build.gradle", "build.gradle.kts"):
            gradle_path = os.path.join(base, gradle_name)
            if not os.path.isfile(gradle_path):
                continue
            try:
                with open(gradle_path, "r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read(65536).lower()
            except OSError:
                continue
            if any(marker in content for marker in _ANDROID_PLUGIN_MARKERS):
                return True
        return False


def _select_parser_for_path(path: str, classifier: _AndroidPathClassifier, vb_classifier: VBPathClassifier) -> Optional[str]:
    rel = path.replace("\\", "/")
    lower = rel.lower()
    name = os.path.basename(lower)
    ext = os.path.splitext(lower)[1]

    if ext in {".cbl", ".cob", ".cpy", ".copy"}:
        return "cobol"
    if ext in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".rc", ".rc2"}:
        return "cplus"
    if ext in {".pas", ".dpr", ".inc"}:
        return "delphi"
    if ext == ".py":
        return "python"
    if ext == ".go":
        return "go"
    if ext in {".pl", ".pm", ".t"}:
        return "perl"
    if ext == ".rs":
        return "rust"
    if ext == ".swift":
        return "swift"
    if ext == ".dart":
        return "dart"
    if ext in {".js", ".jsx"}:
        return "js"
    if ext in {".ts", ".tsx"}:
        return "ts"
    if ext == ".php":
        return "php"
    if ext == ".cs":
        return "csharp"
    if ext == ".sql":
        return "sql"
    if ext in {".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"}:
        return "plsql"
    if ext in {".vb", ".vbproj", ".vbp", ".vbw", ".frx", ".bas", ".cls", ".frm", ".vbs", ".wsf", ".asp"}:
        return vb_classifier.select_parser_for_path(rel)

    if name.endswith(".gradle") or name.endswith(".gradle.kts"):
        return "android"
    if ext == ".xml":
        return "android" if classifier.is_android_path(rel) else None
    if ext == ".java":
        return "android" if classifier.is_android_path(rel) else "java"
    if ext in {".kt", ".kts"}:
        return "android" if classifier.is_android_path(rel) else "kotlin"
    return None


def _group_paths_by_parser(paths: Iterable[str], *, root: str) -> Dict[str, Set[str]]:
    grouped: Dict[str, Set[str]] = {}
    classifier = _AndroidPathClassifier(root)
    vb_classifier = VBPathClassifier(root)
    for path in paths:
        parser = _select_parser_for_path(path, classifier, vb_classifier)
        if not parser:
            continue
        grouped.setdefault(parser, set()).add(path)
    return grouped


def _is_framework_candidate(framework: str, path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    name = os.path.basename(lower)
    ext = os.path.splitext(lower)[1]
    return name in _FRAMEWORK_BUILD_FILES or ext in _FRAMEWORK_CANDIDATE_EXTENSIONS[framework]


def _path_in_module(path: str, module_root: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    module = (module_root or ".").replace("\\", "/").strip("/")
    return module in {"", "."} or normalized == module or normalized.startswith(module + "/")


def _group_paths_by_framework(paths: Iterable[str], *, root: str) -> Tuple[Dict[str, Set[str]], Dict[str, List[str]]]:
    """Route candidate paths to every detected framework overlay.

    Primary ownership remains exclusive.  Framework routing is deliberately
    many-to-many and uses each framework detector as the final authority.
    """

    normalized_paths = {path.replace("\\", "/") for path in paths if path}
    grouped: Dict[str, Set[str]] = {name: set() for name in FRAMEWORK_ANALYZERS}
    evidence: Dict[str, List[str]] = {name: [] for name in FRAMEWORK_ANALYZERS}
    if not normalized_paths:
        return grouped, evidence

    from tools.mybatis.detector import MyBatisProjectDetector
    from tools.servlet_jsp.detector import ServletJspProjectDetector
    from tools.spring.detector import SpringProjectDetector

    detectors = {
        "spring": SpringProjectDetector(root),
        "servlet_jsp": ServletJspProjectDetector(root),
        "mybatis": MyBatisProjectDetector(root),
    }
    for framework, detector in detectors.items():
        candidates = {path for path in normalized_paths if _is_framework_candidate(framework, path)}
        if not candidates:
            continue
        modules = detector.discover_modules()
        module_roots = [str(item.get("rel_path") or ".") for item in modules]
        for item in modules:
            evidence[framework].extend(str(value) for value in item.get("evidence", ()) if value)
        for path in candidates:
            if any(_path_in_module(path, module) for module in module_roots):
                grouped[framework].add(path)
                continue
            result = detector.detect_path(path)
            detected = bool(
                getattr(result, "is_spring", False)
                or getattr(result, "is_servlet_jsp", False)
                or getattr(result, "is_mybatis", False)
            )
            if detected:
                grouped[framework].add(path)
                evidence[framework].extend(str(value) for value in getattr(result, "evidence", ()) if value)

        # Deleted artifacts cannot be read. Strong framework-specific names
        # still need to reach the analyzer so it can apply cleanup/tombstones.
        for path in candidates - grouped[framework]:
            name = os.path.basename(path).lower()
            if (
                (framework == "mybatis" and (name.endswith("mapper.xml") or "mybatis" in name))
                or (framework == "servlet_jsp" and (name == "web.xml" or name.endswith((".jsp", ".jspx", ".jspf", ".tag", ".tagx"))))
                or (framework == "spring" and name.startswith("application") and name.endswith((".properties", ".yml", ".yaml")))
            ):
                grouped[framework].add(path)
                evidence[framework].append(f"{path}:strong-candidate")

        evidence[framework] = list(dict.fromkeys(evidence[framework]))

    struts_candidates = {path for path in normalized_paths if _is_framework_candidate("struts", path)}
    struts_evidence: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")]
        for filename in filenames:
            lower_name = filename.lower()
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, root).replace("\\", "/")
            if lower_name == "struts.xml" or lower_name == "struts-plugin.xml" or (
                lower_name.startswith("struts-") and lower_name.endswith(".xml")
            ):
                struts_evidence.append(f"{rel_path}:struts-config")
            elif lower_name in _FRAMEWORK_BUILD_FILES:
                try:
                    content = Path(full_path).read_text(encoding="utf-8", errors="ignore").lower()
                except OSError:
                    continue
                if "struts2" in content or "org.apache.struts" in content:
                    struts_evidence.append(f"{rel_path}:struts-dependency")
    if struts_evidence:
        grouped["struts"].update(struts_candidates)
        evidence["struts"] = list(dict.fromkeys(struts_evidence))
    else:
        strong = {
            path for path in struts_candidates
            if os.path.basename(path).lower() in {"struts.xml", "struts-plugin.xml"}
            or os.path.basename(path).lower().endswith("-validation.xml")
        }
        grouped["struts"].update(strong)
        evidence["struts"] = [f"{path}:strong-candidate" for path in sorted(strong)]

    flutter_candidates = {path for path in normalized_paths if _is_framework_candidate("flutter", path)}
    from tools.flutter.detector import detect_flutter_project

    try:
        flutter_project = detect_flutter_project(Path(root))
    except (OSError, ValueError):
        flutter_project = None
    if flutter_project is not None:
        grouped["flutter"].update(flutter_candidates)
        evidence["flutter"] = list(getattr(flutter_project, "evidence", ()))
    else:
        strong = {path for path in flutter_candidates if os.path.basename(path).lower() == "pubspec.yaml"}
        grouped["flutter"].update(strong)
        evidence["flutter"] = [f"{path}:strong-candidate" for path in sorted(strong)]

    from tools.aspnet_core.detector import AspNetCoreDetector, is_strong_deleted_candidate as is_core_deleted
    from tools.aspnet_framework.detector import (
        AspNetFrameworkDetector,
        is_strong_deleted_candidate as is_framework_deleted,
    )

    aspnet_detectors = {
        "aspnet_framework": (AspNetFrameworkDetector(root), is_framework_deleted),
        "aspnet_core": (AspNetCoreDetector(root), is_core_deleted),
    }
    for framework, (detector, deleted_candidate) in aspnet_detectors.items():
        candidates = {path for path in normalized_paths if _is_framework_candidate(framework, path)}
        modules = detector.discover_modules()
        for module in modules:
            evidence[framework].extend((*module.evidence, *module.supporting_evidence))
            grouped[framework].update(
                path for path in candidates if _path_in_module(path, module.module_path)
            )
        for path in candidates - grouped[framework]:
            detection = detector.detect_path(path)
            if detection is not None:
                grouped[framework].add(path)
                evidence[framework].extend((*detection.evidence, *detection.supporting_evidence))
            elif deleted_candidate(path) and not os.path.exists(os.path.join(root, path)):
                grouped[framework].add(path)
                evidence[framework].append(f"{path}:strong-candidate")
        evidence[framework] = list(dict.fromkeys(evidence[framework]))
    return grouped, evidence


def _run(cmd: List[str], *, cwd: str, verbose: bool, env: Optional[Dict[str, str]] = None) -> None:
    if verbose:
        print("[upsert] exec:", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def _normalize_sha(root: str, ref: str) -> str:
    return (
        subprocess.check_output(["git", "-C", root, "rev-parse", ref], text=True, stderr=subprocess.DEVNULL).strip()
    )


# Well-known SHA-1 of an empty git tree object — same value in every repo
# (sha1("tree 0\x00")). git diff accepts it as a tree-ish without the object
# needing to exist in the database, so it's the standard way to express
# "diff against nothing" when HEAD is a root commit with no parent.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _detect_default_before(root: str, after_sha: str) -> str:
    """Return the parent of after_sha, or the empty tree if after_sha is the root commit.

    Falling back to the empty tree makes the very first sync on a freshly
    initialized repo (only one commit, no parent) behave the same as a full
    bootstrap — every file in HEAD shows up as 'added' in the diff.
    """
    try:
        return _normalize_sha(root, f"{after_sha}^")
    except subprocess.CalledProcessError:
        return _EMPTY_TREE_SHA


async def _query_impacted_files(
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: Optional[str],
    project_id: str,
    changed_paths: Sequence[str],
) -> Set[str]:
    if not changed_paths:
        return set()
    driver = await GraphDriverFactory.create_driver(
        GraphProvider.NEO4J,
        {
            "uri": neo4j_uri,
            "user": neo4j_user,
            "password": neo4j_password,
            "database": neo4j_db,
        },
    )
    try:
        deps_query = """
        MATCH (src:File)-[r]->(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["INCLUDES", "DEPENDS_ON", "USES", "USES_TYPE", "EXTENDS", "IMPLEMENTS", "INHERITS", "MIXES_IN"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        """
        caller_query = """
        MATCH (src:File)-[:CONTAINS]->(:Function)-[r]->(:Function)<-[:CONTAINS]-(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        """
        type_query = """
        MATCH (src:File)-[:CONTAINS]->(srcNode)-[r]->(dstNode)<-[:CONTAINS]-(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["USES_TYPE", "EXTENDS", "IMPLEMENTS", "INHERITS", "MIXES_IN", "DEPENDS_ON_TYPE"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        """
        impacted: Set[str] = set()
        for query in (deps_query, caller_query, type_query):
            records, _, _ = await driver.execute_query(
                query,
                {"project_id": project_id, "changed_paths": list(changed_paths)},
                database=neo4j_db,
            )
            for row in records:
                file_path = str(row.get("file_path") or "").strip().replace("\\", "/")
                if file_path:
                    impacted.add(file_path)
        return impacted
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


def _selected_parsers(parsers_arg: str) -> Tuple[Set[str], bool]:
    text = (parsers_arg or "auto").strip().lower()
    if text == "auto":
        return set(ANALYZERS) | set(FRAMEWORK_ANALYZERS), True
    values = {item.strip() for item in text.split(",") if item.strip()}
    supported = set(ANALYZERS) | set(FRAMEWORK_ANALYZERS)
    unsupported = sorted(values - supported)
    if unsupported:
        raise ValueError(f"Unsupported parser(s): {', '.join(unsupported)}")
    for framework in sorted(values & set(FRAMEWORK_ANALYZERS)):
        values.update(FRAMEWORK_ANALYZERS[framework].prerequisite_parsers)
    return values, False


def _build_analyzer_env(args: argparse.Namespace) -> Dict[str, str]:
    env = dict(os.environ)
    if getattr(args, "graph_provider", None):
        env["CODE_GRAPH_PROVIDER"] = args.graph_provider
        env["GRAPH_PROVIDER"] = args.graph_provider
    if args.neo4j_uri:
        env["NEO4J_URI"] = args.neo4j_uri
    if args.neo4j_user:
        env["NEO4J_USER"] = args.neo4j_user
    if args.neo4j_password:
        env["NEO4J_PASS"] = args.neo4j_password
    if args.neo4j_db:
        env["NEO4J_DB"] = args.neo4j_db
    if getattr(args, "falkordb_uri", None):
        env["FALKORDB_URI"] = args.falkordb_uri
    if getattr(args, "falkordb_host", None):
        env["FALKORDB_HOST"] = str(args.falkordb_host)
    if getattr(args, "falkordb_port", None):
        env["FALKORDB_PORT"] = str(args.falkordb_port)
    if getattr(args, "falkordb_user", None):
        env["FALKORDB_USER"] = str(args.falkordb_user)
    if getattr(args, "falkordb_password", None):
        env["FALKORDB_PASSWORD"] = str(args.falkordb_password)
    if getattr(args, "falkordb_graph", None):
        env["FALKORDB_GRAPH"] = str(args.falkordb_graph)
    if getattr(args, "falkordb_ssl", False):
        env["FALKORDB_SSL"] = "1"
    if args.qdrant_url:
        env["QDRANT_URL"] = args.qdrant_url
    if args.cache_dir:
        env["QDRANT_CACHE_DIR"] = args.cache_dir
    return env


def _build_analyzer_cmd(
    *,
    python_bin: str,
    analyzer: AnalyzerConfig,
    root: str,
    project_id: str,
    project_name: str,
    before_sha: str,
    after_sha: str,
    changed_manifest: Optional[str],
    deleted_manifest: Optional[str],
    qdrant_collection: Optional[str],
    message_scan_enabled: bool,
    message_output_dir: Optional[str],
    message_qdrant_collection: Optional[str],
    incremental: bool,
    verbose: bool,
    ignore_cache: bool = False,
) -> List[str]:
    cmd = [
        python_bin,
        analyzer.script_path,
        "--root",
        root,
        "--project-id",
        project_id,
        "--project-name",
        project_name,
        "--commit-sha-before",
        before_sha,
        "--commit-sha-after",
        after_sha,
    ]
    cmd.extend(analyzer.extra_args)
    if qdrant_collection:
        cmd.extend(["--qdrant-collection", qdrant_collection])
    if incremental:
        cmd.append("--incremental")
        if changed_manifest:
            cmd.extend(["--changed-files-manifest", changed_manifest])
        if deleted_manifest:
            cmd.extend(["--deleted-files-manifest", deleted_manifest])
    if ignore_cache:
        cmd.append("--ignore-cache")
    if message_scan_enabled:
        cmd.append("--enable-message-scan")
        if message_output_dir:
            cmd.extend(["--message-output-dir", message_output_dir])
        if message_qdrant_collection:
            cmd.extend(["--message-qdrant-collection", message_qdrant_collection])
    else:
        cmd.append("--disable-message-scan")
    if verbose:
        cmd.append("--verbose")
    return cmd


_PROJECT_REPOSITORY_SETUP_QUERY = """
MERGE (p:Project {project_id: $project_id})
ON CREATE SET
    p.name       = $project_name,
    p.slug       = $project_slug,
    p.created_at = timestamp()
ON MATCH SET
    p.name       = $project_name,
    p.slug       = $project_slug
WITH p
MERGE (r:Repository {name: $repo_name})
ON CREATE SET
    r.id          = $repo_name,
    r.project_id  = $project_id,
    r.created_at  = timestamp()
ON MATCH SET
    r.id          = $repo_name
WITH p, r
MERGE (p)-[:HAS_REPOSITORY]->(r)
"""


async def _ensure_project_repository_graph(
    *,
    args: argparse.Namespace,
    root: str,
    project_id: str,
    project_name: str,
) -> None:
    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    repo_name = f"{project_name}/{os.path.basename(root)}"

    if provider == GraphProvider.NEO4J:
        setup_script = os.path.join(_ROOT_DIR, "scripts", "setup_graph_project.py")
        if not os.path.isfile(setup_script):
            return
        setup_cmd = [
            sys.executable,
            setup_script,
            "--project-id",
            project_id,
            "--project-name",
            project_name,
            "--source-path",
            root,
            "--repo-name",
            repo_name,
            "--neo4j-uri",
            args.neo4j_uri,
            "--neo4j-user",
            args.neo4j_user,
            "--neo4j-password",
            args.neo4j_password,
            "--neo4j-db",
            args.neo4j_db,
        ]
        subprocess.run(setup_cmd, cwd=_ROOT_DIR, check=True, capture_output=True, text=True)
        return

    driver = await GraphDriverFactory.create_driver(
        provider,
        {
            "uri": getattr(args, "falkordb_uri", None),
            "host": getattr(args, "falkordb_host", None),
            "port": getattr(args, "falkordb_port", None),
            "user": getattr(args, "falkordb_user", None),
            "password": getattr(args, "falkordb_password", None),
            "database": getattr(args, "falkordb_graph", None) or getattr(args, "neo4j_db", None),
            "graph": getattr(args, "falkordb_graph", None) or getattr(args, "neo4j_db", None),
            "ssl": bool(getattr(args, "falkordb_ssl", False)),
        },
    )
    try:
        await driver.create_indexes(
            [
                {"label": "Project", "property": "project_id"},
                {"label": "Repository", "property": "name"},
            ],
            database=getattr(args, "neo4j_db", None),
        )
        await driver.execute_query(
            _PROJECT_REPOSITORY_SETUP_QUERY,
            {
                "project_id": project_id,
                "project_name": project_name,
                "project_slug": _normalize_slug(project_name),
                "repo_name": repo_name,
            },
            database=getattr(args, "neo4j_db", None),
        )
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_summary_path(
    *,
    cache_dir: Optional[str],
    project_id: str,
    root: str,
) -> str:
    summary_root = safe_cache_root(cache_dir, "incremental_sync_summaries", project_root=root)
    return os.path.join(summary_root, f"{_safe_segment(project_id)}_{int(time.time())}.json")


def _write_summary(path: str, payload: Dict[str, object]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        import json

        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)


_SOURCE_EXTENSIONS: Set[str] = {
    ".cbl", ".cob", ".cpy", ".copy",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".rc", ".rc2",
    ".pas", ".dpr", ".inc",
    ".py",
    ".go",
    ".pl", ".pm", ".t",
    ".rs", ".swift", ".dart", ".arb",
    ".js", ".jsx",
    ".ts", ".tsx",
    ".php",
    ".cs",
    ".sql",
    ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc",
    ".vb", ".vbproj", ".vbp", ".vbw", ".frx", ".bas", ".cls", ".frm", ".vbs", ".wsf", ".asp",
    ".java", ".kt", ".kts",
    ".xml", ".gradle",
    ".properties", ".yml", ".yaml", ".json",
    ".jsp", ".jspx", ".jspf", ".tag", ".tagx",
}

_SKIP_DIRS: Set[str] = {
    "node_modules", ".git", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", ".venv", "venv", ".idea", ".vscode",
    "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".cache", ".gradle", ".dart_tool",
}


def _walk_all_source_files(root: str) -> Set[str]:
    found: Set[str] = set()
    root_abs = os.path.realpath(os.path.abspath(root))
    for dirpath, dirnames, filenames in os.walk(root_abs, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            lower = fname.lower()
            ext = os.path.splitext(lower)[1]
            if ext not in _SOURCE_EXTENSIONS and not lower.endswith(".gradle.kts"):
                continue
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root_abs).replace("\\", "/")
            except ValueError:
                continue
            found.add(rel)
    return found


def _is_git_repo(root: str) -> bool:
    try:
        result = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return result.lower() == "true"
    except Exception:
        return False


async def _run_incremental(args: argparse.Namespace) -> int:
    started_monotonic = time.time()
    root = os.path.abspath(args.root)
    project_id = args.project_id or os.path.basename(root)
    project_name = args.project_name or project_id
    graph_ready = prepare_graph_args(args)
    summary_path = args.summary_path or _default_summary_path(cache_dir=args.cache_dir, project_id=project_id, root=root)
    summary: Dict[str, object] = {
        "project_id": project_id,
        "project_name": project_name,
        "root": root,
        "strict_mode": bool(args.strict),
        "ignore_cache": bool(args.ignore_cache),
        "full_scan": bool(args.full_scan),
        "bootstrap_full_scan": False,
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_seconds": None,
        "status": "running",
        "error": "",
        "before_sha": "",
        "after_sha": "",
        "services": {
            "graph_ready": graph_ready,
            "neo4j_ready": graph_ready,
            "qdrant_ready": bool(args.qdrant_url),
            "impact_expansion_used": False,
            "message_sync_enabled": bool(args.sync_messages),
            "message_qdrant_collection": args.message_qdrant_collection or "",
        },
        "diff": {"entries": 0, "changed": 0, "deleted": 0},
        "impact": {"expanded_impacted": 0},
        "parsers": [],
        "primary_parsers": [],
        "framework_overlays": [],
        "state_before": {},
        "state_after": {},
        "dirty_marked": False,
    }
    if args.verbose and args.ignore_cache:
        print("[cache] ignore-cache enabled: analyzers will run with isolated cache scope")

    run_lock: Optional[_ProjectRunLock] = None
    lock_path = ""
    lock_acquired = False
    state = None
    state_path = ""
    before_sha = str(args.before_sha or "")
    after_sha = str(args.after_sha or "")
    full_scan = bool(args.full_scan)
    try:
        if not os.path.isdir(root):
            raise ValueError(f"Root not found: {root}")

        if not full_scan:
            try:
                inside = subprocess.check_output(
                    ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except Exception as exc:  # pragma: no cover - depends on local git
                raise ValueError(f"Root is not a git repository: {root}") from exc
            if inside.lower() != "true":
                raise ValueError(f"Root is not a git repository: {root}")

        lock_root = safe_cache_root(args.cache_dir, "incremental_sync_locks", project_root=root)
        lock_path = os.path.join(lock_root, f"{_safe_segment(project_id)}.lock")
        run_lock = _ProjectRunLock(lock_path, f"project_id={project_id}")
        try:
            run_lock.acquire()
            lock_acquired = True
            if args.verbose:
                print(f"[state] lock acquired: {lock_path}")
        except RuntimeError as exc:
            summary["status"] = "lock_busy"
            summary["error"] = str(exc)
            print(f"[state] lock busy: {exc}", file=sys.stderr)
            return 2

        state_path = state_file_path(args.cache_dir, project_id, root)
        state = load_sync_state(state_path, project_id, root)
        summary["state_before"] = {
            "dirty": bool(state.dirty),
            "last_good_sha": state.last_good_sha,
            "last_error": state.last_error,
            "last_run_before": state.last_run_before,
            "last_run_after": state.last_run_after,
            "updated_at": state.updated_at,
        }

        if not full_scan and not args.before_sha and not state.last_good_sha:
            full_scan = True
            summary["full_scan"] = True
            summary["bootstrap_full_scan"] = True
            if args.verbose:
                print("[bootstrap] no incremental baseline; scanning all source files")

        if graph_ready:
            try:
                await _ensure_project_repository_graph(
                    args=args,
                    root=root,
                    project_id=project_id,
                    project_name=project_name,
                )
                if args.verbose:
                    print("[setup] Project+Repository nodes ensured")
            except subprocess.CalledProcessError as exc:
                print(f"[setup] WARNING: setup_graph_project failed (non-fatal): {exc.stderr}", file=sys.stderr)
            except Exception as exc:
                print(f"[setup] WARNING: Project+Repository setup failed (non-fatal): {exc}", file=sys.stderr)

        if args.strict:
            missing: List[str] = []
            if not graph_ready:
                missing.append("graph_store")
            if not args.qdrant_url:
                missing.append("qdrant_url")
            if missing:
                raise RuntimeError(f"strict mode missing required services: {', '.join(missing)}")

        if full_scan:
            after_sha = _normalize_sha(root, args.after_sha or "HEAD") if _is_git_repo(root) else "full_scan"
            before_sha = "full_scan"
            if args.verbose:
                print("[full-scan] walking filesystem (git-independent)")
            changed_paths = _normalize_project_paths(root, _walk_all_source_files(root))
            deleted_paths: Set[str] = set()
            summary["diff"] = {
                "entries": len(changed_paths),
                "changed": len(changed_paths),
                "deleted": 0,
            }
            if args.verbose:
                print("[full-scan] found %d source files" % len(changed_paths))
        else:
            after_sha = _normalize_sha(root, args.after_sha or "HEAD")
            before_sha = args.before_sha or state.last_good_sha
            if not before_sha:
                before_sha = _detect_default_before(root, after_sha)
            before_sha = _normalize_sha(root, before_sha)

            if args.verbose:
                print(
                    "[state] dirty=%s last_good_sha=%s before=%s after=%s"
                    % (state.dirty, state.last_good_sha or "-", before_sha[:12], after_sha[:12])
                )

            entries = collect_git_diff_entries(root, before_sha, after_sha)
            changed_paths, deleted_paths = collect_changed_and_deleted(entries)
            changed_paths = _normalize_project_paths(root, changed_paths)
            deleted_paths = _normalize_project_paths(root, deleted_paths)
            summary["diff"] = {
                "entries": len(entries),
                "changed": len(changed_paths),
                "deleted": len(deleted_paths),
            }
            if args.verbose:
                print(
                    "[diff] entries=%d changed=%d deleted=%d"
                    % (len(entries), len(changed_paths), len(deleted_paths))
                )

        summary["before_sha"] = before_sha
        summary["after_sha"] = after_sha

        if not changed_paths and not deleted_paths:
            if state is not None:
                mark_clean(
                    state_path,
                    state,
                    last_good_sha=after_sha,
                    before_sha=before_sha,
                    after_sha=after_sha,
                )
                summary["state_after"] = {
                    "dirty": False,
                    "last_good_sha": after_sha,
                    "last_error": "",
                    "last_run_before": before_sha,
                    "last_run_after": after_sha,
                }
            summary["status"] = "success"
            print("[state] no changes detected; state marked clean")
            return 0

        impacted_paths: Set[str] = set()
        if not full_scan and graph_ready and changed_paths:
            summary["services"]["impact_expansion_used"] = True
            impacted_paths = await _query_impacted_files(
                neo4j_uri=args.neo4j_uri,
                neo4j_user=args.neo4j_user,
                neo4j_password=args.neo4j_password,
                neo4j_db=args.neo4j_db,
                project_id=project_id,
                changed_paths=sorted(changed_paths),
            )
            impacted_paths = _normalize_project_paths(root, impacted_paths)
        elif args.verbose:
            print("[impact] graph store missing; skip graph-based impact expansion")

        summary["impact"] = {"expanded_impacted": len(impacted_paths)}
        if args.verbose:
            print("[impact] expanded_impacted=%d" % len(impacted_paths))

        parser_filter, parser_auto_mode = _selected_parsers(args.parsers)
        changed_by_parser = _group_paths_by_parser(changed_paths, root=root)
        deleted_by_parser = _group_paths_by_parser(deleted_paths, root=root)
        impacted_by_parser = _group_paths_by_parser(impacted_paths, root=root)
        framework_grouped, framework_evidence = _group_paths_by_framework(
            changed_paths | deleted_paths | impacted_paths,
            root=root,
        )
        if not parser_auto_mode:
            for framework in parser_filter & set(FRAMEWORK_ANALYZERS):
                framework_grouped[framework].update(
                    path
                    for path in changed_paths | deleted_paths | impacted_paths
                    if _is_framework_candidate(framework, path)
                )

        manifest_root = safe_cache_root(args.cache_dir, "incremental_sync_manifests", project_root=root)
        message_output_dir = args.message_output_dir or safe_cache_root(
            args.cache_dir,
            "message_scan_artifacts",
            project_root=root,
        )
        message_qdrant_collection = args.message_qdrant_collection or _message_collection_name(
            project_id, root, project_code=args.project_code
        )
        summary["services"]["message_qdrant_collection"] = message_qdrant_collection
        env = _build_analyzer_env(args)
        executed_parsers: List[str] = []
        parser_summaries: List[Dict[str, object]] = []
        for parser, config in ANALYZERS.items():
            if parser not in parser_filter:
                continue
            # For TS, dynamically pick frontend vs backend analyzer based on project structure.
            if parser == "ts":
                config = _resolve_ts_analyzer(root)
            parser_changed = set(changed_by_parser.get(parser, set()))
            parser_deleted = set(deleted_by_parser.get(parser, set()))
            parser_impacted = set(impacted_by_parser.get(parser, set()))
            parser_scan = parser_changed | parser_impacted

            if not parser_scan and not parser_deleted:
                continue

            changed_manifest = os.path.join(manifest_root, f"{parser}_changed_{after_sha[:12]}.json")
            deleted_manifest = os.path.join(manifest_root, f"{parser}_deleted_{after_sha[:12]}.json")
            write_manifest_paths(changed_manifest, parser_scan)
            write_manifest_paths(deleted_manifest, parser_deleted)

            parser_info: Dict[str, object] = {
                "parser": parser,
                "changed": len(parser_changed),
                "impacted": len(parser_impacted),
                "scan": len(parser_scan),
                "deleted": len(parser_deleted),
                "incremental_supported": bool(config.incremental_supported),
                "status": "pending",
                "error": "",
                "started_at": _now_iso(),
                "finished_at": None,
                "duration_seconds": None,
                "changed_manifest": changed_manifest,
                "deleted_manifest": deleted_manifest,
                "qdrant_collection": _code_collection_name(
                    project_id, root, parser, project_code=args.project_code
                ),
                "message_scan_enabled": bool(args.sync_messages and parser in MESSAGE_ENABLED_PARSERS),
                "ignore_cache": bool(args.ignore_cache),
                "message_qdrant_collection": (
                    message_qdrant_collection if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else ""
                ),
            }
            parser_started = time.time()
            parser_summaries.append(parser_info)

            print(
                "[impact] parser=%s changed=%d impacted=%d scan=%d deleted=%d"
                % (parser, len(parser_changed), len(parser_impacted), len(parser_scan), len(parser_deleted))
            )
            if args.sync_messages and parser not in MESSAGE_ENABLED_PARSERS:
                print(f"[message] parser={parser} skip (message detector not enabled for this parser)")

            if not config.incremental_supported and not args.allow_full_fallback:
                if parser_auto_mode:
                    print(
                        "[impact] parser=%s skipped (incremental unsupported; use --allow-full-fallback to force full)"
                        % parser
                    )
                    parser_info["status"] = "skipped"
                    parser_info["finished_at"] = _now_iso()
                    parser_info["duration_seconds"] = round(time.time() - parser_started, 6)
                    continue
                raise RuntimeError(
                    f"parser '{parser}' has no incremental mode yet; rerun with --allow-full-fallback or exclude parser."
                )

            if config.incremental_supported:
                cmd = _build_analyzer_cmd(
                    python_bin=args.python_bin,
                    analyzer=config,
                    root=root,
                    project_id=project_id,
                    project_name=project_name,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    changed_manifest=changed_manifest,
                    deleted_manifest=deleted_manifest,
                    qdrant_collection=str(parser_info["qdrant_collection"]),
                    message_scan_enabled=bool(args.sync_messages and parser in MESSAGE_ENABLED_PARSERS),
                    message_output_dir=message_output_dir if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else None,
                    message_qdrant_collection=message_qdrant_collection if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else None,
                    incremental=True,
                    verbose=args.verbose,
                    ignore_cache=bool(args.ignore_cache),
                )
            else:
                print(f"[upsert] parser={parser} fallback=full")
                cmd = _build_analyzer_cmd(
                    python_bin=args.python_bin,
                    analyzer=config,
                    root=root,
                    project_id=project_id,
                    project_name=project_name,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    changed_manifest=None,
                    deleted_manifest=None,
                    qdrant_collection=str(parser_info["qdrant_collection"]),
                    message_scan_enabled=bool(args.sync_messages and parser in MESSAGE_ENABLED_PARSERS),
                    message_output_dir=message_output_dir if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else None,
                    message_qdrant_collection=message_qdrant_collection if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else None,
                    incremental=False,
                    verbose=args.verbose,
                    ignore_cache=bool(args.ignore_cache),
                )
            parser_info["command"] = cmd
            try:
                _run(cmd, cwd=_ROOT_DIR, verbose=args.verbose, env=env)
            except Exception as exc:
                parser_info["status"] = "failed"
                parser_info["error"] = str(exc)
                raise
            else:
                parser_info["status"] = "success"
                executed_parsers.append(parser)
            finally:
                parser_info["finished_at"] = _now_iso()
                parser_info["duration_seconds"] = round(time.time() - parser_started, 6)

        framework_summaries: List[Dict[str, object]] = []
        for framework, framework_config in sorted(
            FRAMEWORK_ANALYZERS.items(), key=lambda item: (item[1].order, item[0])
        ):
            if framework not in parser_filter:
                continue
            routed = set(framework_grouped.get(framework, set()))
            framework_changed = routed & changed_paths
            framework_deleted = routed & deleted_paths
            framework_impacted = routed & impacted_paths
            framework_scan = framework_changed | framework_impacted
            framework_info: Dict[str, object] = {
                "parser": framework,
                "framework": framework,
                "role": "overlay",
                "changed": len(framework_changed),
                "impacted": len(framework_impacted),
                "scan": len(framework_scan),
                "deleted": len(framework_deleted),
                "incremental_supported": framework_config.incremental_supported,
                "prerequisite_parsers": list(framework_config.prerequisite_parsers),
                "writes_vectors": framework_config.writes_vectors,
                "qdrant_collection": "",
                "semantic_seed_collections": [
                    _code_collection_name(project_id, root, parser, project_code=args.project_code)
                    for parser in framework_config.prerequisite_parsers
                    if parser in {"java", "kotlin"}
                ],
                "detector_evidence": framework_evidence.get(framework, []),
                "status": "pending",
                "error": "",
                "started_at": _now_iso(),
                "finished_at": None,
                "duration_seconds": None,
            }
            framework_summaries.append(framework_info)
            if not framework_scan and not framework_deleted:
                framework_info["status"] = "skipped"
                framework_info["skip_reason"] = "no framework evidence in changed/deleted paths"
                framework_info["finished_at"] = _now_iso()
                framework_info["duration_seconds"] = 0.0
                continue

            changed_manifest = os.path.join(manifest_root, f"{framework}_changed_{after_sha[:12]}.json")
            deleted_manifest = os.path.join(manifest_root, f"{framework}_deleted_{after_sha[:12]}.json")
            write_manifest_paths(changed_manifest, framework_scan)
            write_manifest_paths(deleted_manifest, framework_deleted)
            framework_info["changed_manifest"] = changed_manifest
            framework_info["deleted_manifest"] = deleted_manifest
            print(
                "[overlay] framework=%s changed=%d impacted=%d scan=%d deleted=%d"
                % (
                    framework,
                    len(framework_changed),
                    len(framework_impacted),
                    len(framework_scan),
                    len(framework_deleted),
                )
            )
            cmd = _build_analyzer_cmd(
                python_bin=args.python_bin,
                analyzer=AnalyzerConfig(
                    framework,
                    framework_config.script_path,
                    framework_config.incremental_supported,
                    framework_config.extra_args,
                ),
                root=root,
                project_id=project_id,
                project_name=project_name,
                before_sha=before_sha,
                after_sha=after_sha,
                changed_manifest=changed_manifest if not full_scan else None,
                deleted_manifest=deleted_manifest if not full_scan else None,
                qdrant_collection=None,
                message_scan_enabled=False,
                message_output_dir=None,
                message_qdrant_collection=None,
                incremental=not full_scan,
                verbose=args.verbose,
                ignore_cache=bool(args.ignore_cache),
            )
            framework_info["command"] = cmd
            framework_started = time.time()
            try:
                _run(cmd, cwd=_ROOT_DIR, verbose=args.verbose, env=env)
            except Exception as exc:
                framework_info["status"] = "failed"
                framework_info["error"] = str(exc)
                raise
            else:
                framework_info["status"] = "success"
                executed_parsers.append(framework)
            finally:
                framework_info["finished_at"] = _now_iso()
                framework_info["duration_seconds"] = round(time.time() - framework_started, 6)

        summary["primary_parsers"] = parser_summaries
        summary["framework_overlays"] = framework_summaries
        summary["parsers"] = parser_summaries + framework_summaries
        mark_clean(
            state_path,
            state,
            last_good_sha=after_sha,
            before_sha=before_sha,
            after_sha=after_sha,
        )
        summary["state_after"] = {
            "dirty": False,
            "last_good_sha": after_sha,
            "last_error": "",
            "last_run_before": before_sha,
            "last_run_after": after_sha,
        }
        summary["status"] = "success"
        print(
            "[state] summary changed=%d deleted=%d impacted=%d parsers=%d"
            % (len(changed_paths), len(deleted_paths), len(impacted_paths), len(executed_parsers))
        )
        print("[state] incremental sync completed successfully")
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        dirty_marked = False
        if lock_acquired and state is not None and state_path:
            mark_dirty(
                state_path,
                state,
                error=str(exc),
                before_sha=str(before_sha or ""),
                after_sha=str(after_sha or ""),
            )
            dirty_marked = True
            summary["state_after"] = {
                "dirty": True,
                "last_good_sha": state.last_good_sha,
                "last_error": str(exc),
                "last_run_before": str(before_sha or ""),
                "last_run_after": str(after_sha or ""),
            }
        summary["dirty_marked"] = dirty_marked
        if dirty_marked:
            print(f"[state] marked dirty: {exc}", file=sys.stderr)
        else:
            print(f"[state] failed before dirty-state update: {exc}", file=sys.stderr)
        return 1
    finally:
        if run_lock and lock_acquired:
            run_lock.release()
            if args.verbose:
                print(f"[state] lock released: {lock_path}")
        summary["finished_at"] = _now_iso()
        summary["duration_seconds"] = round(time.time() - started_monotonic, 6)
        try:
            _write_summary(summary_path, summary)
            if args.verbose:
                print(f"[state] summary json: {summary_path}")
        except Exception as exc:  # pragma: no cover - filesystem edge cases
            print(f"[state] failed writing summary json: {exc}", file=sys.stderr)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental Neo4j/Qdrant sync driven by git diff")
    parser.add_argument("--root", required=True, help="Repository root")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument(
        "--project-code",
        default=os.environ.get("PROJECT_CODE"),
        help=(
            "Project code (2-20 uppercase alphanumeric + hyphens). Required "
            "when HYPERPACK_COLLECTION_SCHEME=per_project so per-project "
            "Qdrant collection names can be derived (e.g. 'NEXT' -> "
            "'next-messages')."
        ),
    )
    parser.add_argument("--before-sha", default=os.environ.get("GIT_COMMIT_SHA_BEFORE"))
    parser.add_argument("--after-sha", default=os.environ.get("GIT_COMMIT_SHA_AFTER", "HEAD"))
    parser.add_argument("--parsers", default="auto", help="auto or comma-separated parser list")
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Pass --ignore-cache to analyzer runs (isolated cache per run, no local resume).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=str(os.environ.get("INCREMENTAL_STRICT", "")).lower() in {"1", "true", "yes", "on"},
        help="Fail fast if required services (Neo4j/Qdrant) are not configured",
    )
    parser.add_argument(
        "--summary-path",
        default=os.environ.get("INCREMENTAL_SUMMARY_PATH"),
        help="Optional JSON summary output path (default under .cache/incremental_sync_summaries)",
    )
    parser.add_argument(
        "--allow-full-fallback",
        action="store_true",
        help="Allow full analyzer fallback for parsers without incremental support",
    )
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--sync-messages",
        action=argparse.BooleanOptionalAction,
        default=str(os.environ.get("SYNC_MESSAGES", "1")).lower() not in {"0", "false", "no", "off"},
        help="Enable/disable message scan sync in analyzer runs",
    )
    parser.add_argument(
        "--message-output-dir",
        default=os.environ.get("MESSAGE_OUTPUT_DIR"),
        help="Optional directory for per-project message JSON artifacts",
    )
    parser.add_argument(
        "--message-qdrant-collection",
        default=os.environ.get("MESSAGE_QDRANT_COLLECTION"),
        help="Optional Qdrant collection override for messages (default: <project_scope>_mess)",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Scan all files from scratch (ignore git state / last_good_sha). Equivalent to diffing against an empty tree.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    return await _run_incremental(args)


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(asyncio.run(main()))
