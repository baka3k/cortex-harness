#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config
from tools.common.project_scope import project_id_lookup_key

from tools.common.analyzer_cache import safe_cache_root
from tools.common.git_diff import (
    collect_changed_and_deleted,
    collect_git_diff_entries,
    collect_worktree_entries,
    discover_repository_scopes,
    write_manifest_paths,
)
from tools.common.incremental_sync_state import (
    backup_legacy_state,
    legacy_state_file_path,
    load_sync_state,
    mark_clean,
    mark_dirty,
    state_file_path,
)
from tools.common.source_inventory import (
    SourceChangedError,
    capture_source_inventory,
    diff_source_inventories,
    load_inventory_generation,
    preserve_inventory_prefixes,
    validate_inventory_unchanged,
    write_inventory_generation,
)
from tools.common.sync_scope import (
    LockBusyError,
    ProjectRunLock,
    read_lock_metadata,
    resolve_sync_cache_dir,
    scan_scope_id,
)
from tools.common.parse_quality import atomic_write_json
from tools.common.reliability import (
    ArtifactReference,
    FailureClass,
    FailureRecord,
    RunOutcome,
    RunPhase,
    RunResult,
    ReliabilityExitCode,
    exit_code_for,
    load_run_result,
)
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    normalize_graph_provider,
    prepare_graph_args,
)
from tools.graph.schema import CODE_GRAPH_SCHEMA, ensure_schema
from tools.graph.journal.config import (
    configure_journal_env,
    finalize_journal_from_env,
    journal_status_from_env,
    physical_target_from_env,
)
from tools.graph.journal.models import RunStatus
from tools.graph.journal.consumer import resume_journal
from tools.project_topology.registry import descriptor_spec_for_path
from tools.jp1.sniff import is_jp1_file
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
    writes_vectors: bool = True
    seeded_by: Tuple[str, ...] = ()


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
    "shell": AnalyzerConfig("shell", os.path.join(_ROOT_DIR, "tools", "shell", "shell_analyzer.py"), True),
    "jp1": AnalyzerConfig("jp1", os.path.join(_ROOT_DIR, "tools", "jp1", "jp1_analyzer.py"), True),
    "rust": AnalyzerConfig("rust", os.path.join(_ROOT_DIR, "tools", "rust", "rust_analyzer.py"), True),
    "swift": AnalyzerConfig("swift", os.path.join(_ROOT_DIR, "tools", "swift", "swift_analyzer.py"), True),
    "js": AnalyzerConfig("js", os.path.join(_ROOT_DIR, "tools", "js", "js_analyzer.py"), True),
    "ts": AnalyzerConfig("ts", os.path.join(_ROOT_DIR, "tools", "ts", "ts_analyzer.py"), True),
    "php": AnalyzerConfig("php", os.path.join(_ROOT_DIR, "tools", "php", "php_analyzer.py"), True),
    "csharp": AnalyzerConfig("csharp", os.path.join(_ROOT_DIR, "tools", "csharp", "csharp_analyzer.py"), True),
    "sql": AnalyzerConfig("sql", os.path.join(_ROOT_DIR, "tools", "sql", "sql_analyzer.py"), True),
    "plsql": AnalyzerConfig("plsql", os.path.join(_ROOT_DIR, "tools", "plsql", "plsql_analyzer.py"), True),
}

PROJECT_TOPOLOGY_ANALYZER = AnalyzerConfig(
    "project_topology",
    os.path.join(
        _ROOT_DIR,
        "tools",
        "project_topology",
        "topology_analyzer.py",
    ),
    True,
    writes_vectors=False,
)

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
    "fastapi_django": FrameworkAnalyzerConfig(
        "fastapi_django",
        os.path.join(_ROOT_DIR, "tools", "web_framework", "web_framework_analyzer.py"),
        True,
        ("python",),
        80,
        False,
        ("--framework", "fastapi_django"),
    ),
    "express_js": FrameworkAnalyzerConfig(
        "express_js",
        os.path.join(_ROOT_DIR, "tools", "web_framework", "web_framework_analyzer.py"),
        True,
        ("js",),
        81,
        False,
        ("--framework", "express_js"),
    ),
    "laravel": FrameworkAnalyzerConfig(
        "laravel",
        os.path.join(_ROOT_DIR, "tools", "web_framework", "web_framework_analyzer.py"),
        True,
        ("php",),
        82,
        False,
        ("--framework", "laravel"),
    ),
    "database_sql": FrameworkAnalyzerConfig(
        "database_sql",
        os.path.join(_ROOT_DIR, "tools", "database_schema", "database_schema_analyzer.py"),
        True,
        ("sql",),
        90,
        False,
        ("--dialect", "sql"),
    ),
    "database_plsql": FrameworkAnalyzerConfig(
        "database_plsql",
        os.path.join(_ROOT_DIR, "tools", "database_schema", "database_schema_analyzer.py"),
        True,
        ("plsql",),
        91,
        False,
        ("--dialect", "plsql"),
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
    "fastapi_django": {".py"},
    "express_js": {".js", ".jsx"},
    "laravel": {".php"},
    "database_sql": {".sql", ".ddl", ".dml", ".psql"},
    "database_plsql": {".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"},
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

_SHARED_VECTOR_CLI_PARSERS: Set[str] = {"dart", "go", "jp1", "perl", "rust", "shell", "swift"}
_SCAN_RESULT_VECTORS_RE = re.compile(r"(?m)^\[SCAN_RESULT\].*\bvectors=(\d+)\b")

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
    if ext in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".pc", ".pcc", ".rc", ".rc2"}:
        return "cplus"
    if ext in {".pas", ".dpr", ".inc"}:
        return "delphi"
    if ext == ".py":
        return "python"
    if ext == ".go":
        return "go"
    if ext in {".pl", ".pm", ".t"}:
        return "perl"
    if ext == ".sh":
        return "shell"
    if ext == ".txt" and is_jp1_file(os.path.join(classifier.root, path)):
        return "jp1"
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

    web_specs = {
        "fastapi_django": (
            (".py",),
            ("fastapi", "django.", "django ", "urlpatterns", "@app.get", "@router.get"),
            ("urls.py",),
        ),
        "express_js": (
            (".js", ".jsx"),
            ("express", "app.get(", "router.get(", "app.post(", "router.post("),
            ("routes.js", "router.js"),
        ),
        "laravel": (
            (".php",),
            ("illuminate\\", "route::", "extends controller"),
            ("routes.php", "web.php", "api.php"),
        ),
    }
    for framework, (extensions, markers, strong_names) in web_specs.items():
        candidates = {
            path for path in normalized_paths
            if os.path.splitext(path)[1].lower() in extensions
        }
        detected_evidence: List[str] = []
        for path in sorted(candidates):
            absolute = os.path.join(root, path)
            if not os.path.isfile(absolute):
                if os.path.basename(path).lower() in strong_names:
                    detected_evidence.append(f"{path}:strong-candidate")
                continue
            try:
                content = Path(absolute).read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if any(marker in content for marker in markers):
                detected_evidence.append(f"{path}:{framework}")
        if detected_evidence:
            grouped[framework].update(candidates)
            evidence[framework] = list(dict.fromkeys(detected_evidence))

    database_extensions = {
        "database_sql": _FRAMEWORK_CANDIDATE_EXTENSIONS["database_sql"],
        "database_plsql": _FRAMEWORK_CANDIDATE_EXTENSIONS["database_plsql"],
    }
    for framework, extensions in database_extensions.items():
        candidates = {
            path for path in normalized_paths
            if os.path.splitext(path)[1].lower() in extensions
        }
        grouped[framework].update(candidates)
        evidence[framework] = [f"{path}:{framework}" for path in sorted(candidates)]
    return grouped, evidence


def _run(cmd: List[str], *, cwd: str, verbose: bool, env: Optional[Dict[str, str]] = None) -> str:
    if verbose:
        print("[upsert] exec:", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    scan_result = ""
    output_tail = ""
    stderr_tail = ""
    max_output_tail_chars = 65_536

    def drain_stderr() -> None:
        nonlocal stderr_tail
        assert process.stderr is not None
        for line in process.stderr:
            stderr_tail = (stderr_tail + line[-max_output_tail_chars:])[-max_output_tail_chars:]

    stderr_thread = threading.Thread(target=drain_stderr, name="analyzer-stderr", daemon=True)
    stderr_thread.start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            if "[SCAN_RESULT]" in line:
                scan_result = line[-max_output_tail_chars:]
            output_tail = (output_tail + line[-max_output_tail_chars:])[-max_output_tail_chars:]
    except BaseException:
        process.terminate()
        process.wait()
        stderr_thread.join(timeout=1)
        raise
    return_code = process.wait()
    stderr_thread.join(timeout=1)
    if return_code:
        raise subprocess.CalledProcessError(
            return_code,
            cmd,
            output=output_tail,
            stderr=stderr_tail,
        )
    if stderr_tail:
        print(stderr_tail, end="", file=sys.stderr)
    return scan_result


def _scan_result_vector_count(output: Optional[str]) -> Optional[int]:
    """Extract the analyzer-reported vector count without parsing general logs."""
    match = _SCAN_RESULT_VECTORS_RE.search(output or "")
    return int(match.group(1)) if match else None


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
    graph_provider: Optional[str],
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: Optional[str],
    falkordb_path: Optional[str] = None,
    project_id: str,
    changed_paths: Sequence[str],
) -> Set[str]:
    if not changed_paths:
        return set()
    provider = normalize_graph_provider(graph_provider)
    config: Dict[str, Any]
    if provider == GraphProvider.FALKORDB:
        if not falkordb_path:
            from cortex_harness.storage import resolve_storage

            falkordb_path = str(resolve_storage(Path.cwd()).falkordb_code_path)
        config = {
            "path": falkordb_path,
            "graph": neo4j_db,
            "database": neo4j_db,
            "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
            "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
        }
    else:
        config = {
            "uri": neo4j_uri,
            "user": neo4j_user,
            "password": neo4j_password,
            "database": neo4j_db,
        }
    driver = await GraphDriverFactory.create_driver(provider, config)
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


async def _project_topology_bootstrap_needed(
    *,
    args: argparse.Namespace,
    project_id: str,
) -> bool:
    """Return True when the topology overlay is missing for this project.

    The incremental path only runs the topology analyzer when a build
    descriptor changed. A project ingested before the overlay existed (or one
    whose commits never touch descriptors) therefore never receives
    ``ProjectModule`` / ``BuildDescriptor`` facts, so the project-context tools
    return empty even though the rest of the graph is populated. Probing for
    any existing ``ProjectModule`` lets us bootstrap the overlay once on an
    otherwise no-op incremental run.
    """
    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    database = getattr(args, "neo4j_db", None) or getattr(args, "falkordb_graph", None)
    if provider == GraphProvider.NEO4J:
        config: Dict[str, Any] = {
            "uri": getattr(args, "neo4j_uri", None),
            "user": getattr(args, "neo4j_user", None),
            "password": getattr(args, "neo4j_password", None),
            "database": database,
        }
    else:
        graph_name = getattr(args, "falkordb_graph", None) or database
        config = {
            "path": getattr(args, "falkordb_path", None),
            "graph": graph_name,
            "database": graph_name,
            "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
            "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
        }
    try:
        driver = await GraphDriverFactory.create_driver(provider, config)
    except Exception:
        return False
    try:
        normalized = project_id_lookup_key(project_id)
        if normalized is None:
            return True
        rows, _, _ = await driver.execute_query(
            "MATCH (m:ProjectModule) "
            "WHERE m.project_id_normalized = $project_id_normalized "
            "RETURN count(m) AS total",
            {"project_id_normalized": normalized},
            database=database,
        )
        total = 0
        if rows:
            value = rows[0].get("total")
            if value is not None:
                total = int(value)
        return total == 0
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


async def _resume_configured_journal(args: argparse.Namespace, config: Any) -> int:
    """Drain a compatible unfinished run before starting source parsing."""

    if not config.required or not config.path.is_file():
        return 0
    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    database = getattr(args, "neo4j_db", None) or getattr(args, "falkordb_graph", None)
    if provider == GraphProvider.NEO4J:
        driver_config: Dict[str, Any] = {
            "uri": getattr(args, "neo4j_uri", None),
            "user": getattr(args, "neo4j_user", None),
            "password": getattr(args, "neo4j_password", None),
            "database": database,
        }
    else:
        driver_config = {
            "path": getattr(args, "falkordb_path", None),
            "graph": getattr(args, "falkordb_graph", None) or database,
            "database": database,
            "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
            "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
        }
    driver = await GraphDriverFactory.create_driver(provider, driver_config)
    try:
        return await resume_journal(config, driver)
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


def _selected_parsers(parsers_arg: str) -> Tuple[Set[str], bool]:
    text = (parsers_arg or "auto").strip().lower()
    if text == "auto":
        return set(ANALYZERS) | set(FRAMEWORK_ANALYZERS) | {"project_topology"}, True
    values = {item.strip() for item in text.split(",") if item.strip()}
    supported = set(ANALYZERS) | set(FRAMEWORK_ANALYZERS) | {"project_topology"}
    unsupported = sorted(values - supported)
    if unsupported:
        raise ValueError(f"Unsupported parser(s): {', '.join(unsupported)}")
    for framework in sorted(values & set(FRAMEWORK_ANALYZERS)):
        values.update(FRAMEWORK_ANALYZERS[framework].prerequisite_parsers)
    return values, False


def _build_analyzer_env(args: argparse.Namespace) -> Dict[str, str]:
    env = dict(os.environ)
    if getattr(args, "no_graph", False):
        # The sentinel is checked before provider/config normalization in every
        # child analyzer, so a project dev.json cannot silently re-enable writes.
        env["CORTEX_DISABLE_GRAPH"] = "1"
        env["CODE_GRAPH_PROVIDER"] = "neo4j"
        env["GRAPH_PROVIDER"] = "neo4j"
        for key in (
            "FALKORDB_PATH",
            "FALKORDB_GRAPH",
            "FALKORDB_DATABASE",
            "NEO4J_URI",
            "NEO4J_USER",
            "NEO4J_PASS",
            "NEO4J_DB",
        ):
            env.pop(key, None)
        return env
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
    if getattr(args, "falkordb_path", None):
        env["FALKORDB_PATH"] = str(args.falkordb_path)
    if getattr(args, "falkordb_graph", None):
        env["FALKORDB_GRAPH"] = str(args.falkordb_graph)
    if args.qdrant_url:
        env["QDRANT_CODE_PATH"] = args.qdrant_url
    if args.cache_dir:
        env["QDRANT_CACHE_DIR"] = args.cache_dir
    if args.embed_model:
        env["CODE_EMBEDDING_MODEL"] = args.embed_model
        env["EMBED_MODEL"] = args.embed_model
    if args.embed_device:
        env["EMBED_DEVICE"] = args.embed_device
    if args.embed_batch_size:
        env["EMBED_BATCH_SIZE"] = str(args.embed_batch_size)
    if args.max_embed_chars:
        env["MAX_EMBED_CHARS"] = str(args.max_embed_chars)
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
    embed_model: Optional[str] = None,
    embed_device: Optional[str] = None,
    embed_batch_size: Optional[int] = None,
    max_embed_chars: Optional[int] = None,
    parse_quality: str = "report",
    parse_quality_report: Optional[str] = None,
    parse_quality_max_files: int = 500,
    parse_quality_wall_seconds: int = 900,
    parse_quality_workers: int = 1,
    parse_quality_max_records: int = 10000,
    parse_quality_max_bytes: int = 8 * 1024 * 1024,
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
    if analyzer.parser == "cplus":
        cmd.extend(["--parse-quality", parse_quality])
        if parse_quality in {"report", "repair"}:
            cmd.append("--disable-compile-db-bootstrap")
        if parse_quality_report:
            cmd.extend(["--parse-quality-report", parse_quality_report])
        cmd.extend(
            [
                "--parse-quality-max-files",
                str(parse_quality_max_files),
                "--parse-quality-wall-seconds",
                str(parse_quality_wall_seconds),
                "--parse-quality-workers",
                str(parse_quality_workers),
                "--parse-quality-max-records",
                str(parse_quality_max_records),
                "--parse-quality-max-bytes",
                str(parse_quality_max_bytes),
            ]
        )
    if analyzer.parser in _SHARED_VECTOR_CLI_PARSERS:
        if embed_model:
            cmd.extend(["--embed-model", embed_model])
        if embed_device:
            cmd.extend(["--device", embed_device])
        if embed_batch_size:
            cmd.extend(["--batch-size", str(embed_batch_size)])
        if max_embed_chars:
            cmd.extend(["--max-embed-chars", str(max_embed_chars)])
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

_NEO4J_PROJECT_REPOSITORY_CONSTRAINTS = (
    "CREATE CONSTRAINT unique_project_id IF NOT EXISTS "
    "FOR (p:Project) REQUIRE p.project_id IS UNIQUE",
    "CREATE CONSTRAINT unique_repository_name IF NOT EXISTS "
    "FOR (r:Repository) REQUIRE r.name IS UNIQUE",
)

_NEO4J_DUPLICATE_TOPOLOGY_AUDIT = """
MATCH (p:Project)
WITH p.project_id AS identity, count(*) AS duplicates
WHERE identity IS NOT NULL AND duplicates > 1
RETURN 'Project' AS label, 'project_id' AS property, identity, duplicates
UNION ALL
MATCH (r:Repository)
WITH r.name AS identity, count(*) AS duplicates
WHERE identity IS NOT NULL AND duplicates > 1
RETURN 'Repository' AS label, 'name' AS property, identity, duplicates
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

    driver = await create_graph_driver_from_args(args)
    resolved_graph = (
        getattr(args, "neo4j_db", None)
        if provider == GraphProvider.NEO4J
        else getattr(args, "falkordb_graph", None) or getattr(args, "neo4j_db", None)
    )
    try:
        if provider == GraphProvider.NEO4J:
            duplicates, _, _ = await driver.execute_query(
                _NEO4J_DUPLICATE_TOPOLOGY_AUDIT,
                database=resolved_graph,
            )
            if duplicates:
                detail = ", ".join(
                    f"{row.get('label')}.{row.get('property')}={row.get('identity')!r} "
                    f"count={row.get('duplicates')}"
                    for row in duplicates[:20]
                )
                raise RuntimeError(
                    "duplicate graph identities block uniqueness constraints; "
                    f"repair explicitly before sync: {detail}"
                )
            for statement in _NEO4J_PROJECT_REPOSITORY_CONSTRAINTS:
                await driver.execute_query(statement, database=resolved_graph)
        schema_result = await ensure_schema(
            driver,
            CODE_GRAPH_SCHEMA,
            database=resolved_graph,
        )
        if getattr(args, "verbose", False):
            print(
                "[schema] ready manifest=%s fingerprint=%s indexes=%d verified=%d"
                % (
                    schema_result.manifest,
                    schema_result.fingerprint,
                    schema_result.required_count,
                    schema_result.verified_count,
                )
            )
        await driver.execute_query(
            _PROJECT_REPOSITORY_SETUP_QUERY,
            {
                "project_id": project_id,
                "project_name": project_name,
                "project_slug": _normalize_slug(project_name),
                "repo_name": repo_name,
            },
            database=resolved_graph,
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
    summary_root = os.path.join(resolve_sync_cache_dir(cache_dir, root), "incremental_sync_summaries")
    scope = scan_scope_id(project_id, root)
    invocation = f"{os.getpid()}_{uuid.uuid4().hex}"
    return os.path.join(summary_root, f"{_safe_segment(project_id)}_{scope}_{invocation}.json")


def _write_summary(path: str, payload: Dict[str, object]) -> None:
    target = Path(path).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, target)
                os.chmod(target, 0o600)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _redact_debug_text(text: str, *, roots: Sequence[str] = ()) -> str:
    redacted = text
    sensitive_roots = [str(Path.home()), *roots]
    for root in sorted({item for item in sensitive_roots if item}, key=len, reverse=True):
        redacted = redacted.replace(root, "<workspace>")
    secret_name = (
        r"[A-Za-z0-9_.-]*(?:password|passwd|pass|secret|token|api[_-]?key|cookie)"
        r"[A-Za-z0-9_.-]*"
    )
    redacted = re.sub(
        rf"(?i)(\b{secret_name}\s*[=:]\s*)([^\s,;]+)",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(
        rf"(?i)(--{secret_name}\s+)([^\s]+)",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(\bauthorization\s*:\s*(?:basic|bearer)\s+)([^\r\n]+)",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@",
        r"\1<redacted>@",
        redacted,
    )
    return redacted


def _write_debug_artifact(
    path: str,
    text: str,
    *,
    max_bytes: int = 1024 * 1024,
    roots: Sequence[str] = (),
) -> ArtifactReference:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _redact_debug_text(text, roots=roots).encode("utf-8", errors="replace")[-max_bytes:]
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return ArtifactReference(
        kind="debug",
        path=str(target),
        byte_count=len(encoded),
        item_count=1,
    )


def _failure_record_for_exception(
    exc: Exception,
    *,
    run_id: str,
    correlation_id: str,
    phase: RunPhase,
    artifacts: Sequence[ArtifactReference] = (),
) -> FailureRecord:
    message = str(exc) or type(exc).__name__
    lowered = message.casefold()
    retryable = False
    safe_action = "inspect the result artifact and correct the reported failure before retrying"
    if isinstance(exc, SourceChangedError):
        code = "source_changed_during_scan"
        failure_class = FailureClass.SOURCE_CHANGED
        safe_action = "restart discovery against the current source revision"
    elif isinstance(exc, TimeoutError):
        before_submission = getattr(exc, "submission_state", None) == "before_submission"
        code = (
            "operation_timeout_before_submission"
            if before_submission
            else "operation_timeout_submission_unknown"
        )
        failure_class = FailureClass.TIMEOUT if before_submission else FailureClass.AMBIGUOUS_MUTATION
        retryable = before_submission
        safe_action = (
            "retry within the configured budget"
            if before_submission
            else "inspect storage state and reconcile the run before any retry"
        )
    elif isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        code = "artifact_disk_full"
        failure_class = FailureClass.CAPACITY
        safe_action = "free disk space without deleting the active generation, then resume"
    elif isinstance(exc, ConnectionError):
        before_submission = getattr(exc, "submission_state", None) == "before_submission"
        code = "storage_unavailable" if before_submission else "storage_connection_state_unknown"
        failure_class = (
            FailureClass.STORAGE_UNAVAILABLE
            if before_submission
            else FailureClass.AMBIGUOUS_MUTATION
        )
        retryable = before_submission
        safe_action = (
            "restore the storage service, then retry within the configured budget"
            if before_submission
            else "reconcile storage effects before deciding whether a retry is safe"
        )
    elif isinstance(exc, BlockingIOError):
        code = "storage_lock_busy"
        failure_class = FailureClass.LOCK
        retryable = True
        safe_action = "retry within the lock-contention budget"
    elif isinstance(exc, (FileNotFoundError, PermissionError)):
        code = "storage_configuration_invalid"
        failure_class = FailureClass.CONFIGURATION
        safe_action = "correct the configured path or permissions before rerunning"
    elif isinstance(exc, OSError):
        code = "storage_os_error"
        failure_class = FailureClass.INTERNAL_DEFECT
        safe_action = "inspect the debug artifact before deciding whether a retry is safe"
    elif isinstance(exc, subprocess.CalledProcessError):
        child_stderr = str(exc.stderr or "")
        child_output = f"{exc.output or ''}\n{child_stderr}".casefold()
        if (
            "relationship batch integrity failure" in child_output
            or ("expected=" in child_output and "matched=" in child_output)
        ):
            code = "relationship_cardinality_mismatch"
            failure_class = FailureClass.INTEGRITY
            safe_action = "inspect unresolved endpoint evidence; do not retry until reconciled"
        elif "Traceback (most recent call last)" in child_stderr:
            code = "analyzer_internal_defect"
            failure_class = FailureClass.INTERNAL_DEFECT
            safe_action = "inspect the debug artifact and report the issue fingerprint"
        else:
            code = "analyzer_child_failed"
            failure_class = FailureClass.PARSER_ISOLATION
            safe_action = "inspect the child debug artifact and quarantine or correct the failing input"
    elif isinstance(exc, ValueError):
        code = "invalid_configuration"
        failure_class = FailureClass.CONFIGURATION
        safe_action = "correct the configuration or incompatible artifact before rerunning"
    elif "integrity" in lowered or "expected=" in lowered or "unresolved" in lowered:
        code = "storage_integrity_failure"
        failure_class = FailureClass.INTEGRITY
        safe_action = "inspect unresolved identities; do not retry until the mismatch is reconciled"
    elif "journal" in lowered:
        code = "journal_recovery_failure"
        failure_class = FailureClass.JOURNAL_RECOVERY
        safe_action = "run journal reconciliation and resume only a compatible run"
    else:
        code = "internal_defect"
        failure_class = FailureClass.INTERNAL_DEFECT
        safe_action = "inspect the debug artifact and report the issue fingerprint"
    details = {"exception_type": type(exc).__name__}
    if failure_class is FailureClass.INTERNAL_DEFECT:
        details["issue_fingerprint"] = hashlib.sha256(
            f"{type(exc).__name__}:{message}".encode("utf-8", errors="replace")
        ).hexdigest()[:16]
    return FailureRecord(
        code=code,
        failure_class=failure_class,
        phase=phase,
        component="incremental_sync",
        retryable=retryable,
        run_id=run_id,
        correlation_id=correlation_id,
        summary=message,
        safe_action=safe_action,
        artifact_references=tuple(artifacts),
        details=details,
    )


_SOURCE_EXTENSIONS: Set[str] = {
    ".cbl", ".cob", ".cpy", ".copy",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".pc", ".pcc", ".rc", ".rc2",
    ".pas", ".dpr", ".inc",
    ".py",
    ".go",
    ".pl", ".pm", ".t",
    ".sh",
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
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root_abs).replace("\\", "/")
            except ValueError:
                continue
            if (
                ext not in _SOURCE_EXTENSIONS
                and not lower.endswith(".gradle.kts")
                and descriptor_spec_for_path(rel) is None
                and not (ext == ".txt" and is_jp1_file(full))
            ):
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
    run_id = os.environ.get("CORTEX_RUN_ID") or uuid.uuid4().hex
    correlation_id = os.environ.get("CORTEX_CORRELATION_ID") or run_id
    current_phase = RunPhase.DISCOVERING
    failure_record: Optional[FailureRecord] = None
    result_artifacts: List[ArtifactReference] = []
    root = os.path.realpath(os.path.abspath(args.root))
    project_id = args.project_id or os.path.basename(root)
    project_name = args.project_name or project_id
    control_cache_dir = resolve_sync_cache_dir(args.cache_dir, root)
    scope_id = scan_scope_id(project_id, root)
    graph_ready = False if getattr(args, "no_graph", False) else prepare_graph_args(args)
    summary_path = args.summary_path or _default_summary_path(cache_dir=control_cache_dir, project_id=project_id, root=root)
    summary: Dict[str, object] = {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "phase": current_phase.value,
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
        "outcome": "running",
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
        "topology_overlays": [],
        "state_before": {},
        "state_after": {},
        "dirty_marked": False,
        "scope": {"id": scope_id, "root": root, "cache_dir": control_cache_dir},
        "lock": {},
        "change_detection": args.change_detection,
        "change_sources": {"committed": 0, "staged": 0, "unstaged": 0, "untracked": 0, "inventory": 0},
        "repositories": [],
        "coverage_warnings": [],
        "reconciliation": {"requested": bool(args.reconcile), "performed": False},
        "parse_quality": {
            "policy": args.parse_quality,
            "artifact": "",
            "artifacts": [],
            "aggregates": {},
        },
    }
    parse_quality_artifact_dir: Optional[str] = None
    parse_quality_manifest_path: Optional[str] = None
    parse_quality_manifest_entries: List[Dict[str, object]] = []
    if args.verbose and args.ignore_cache:
        print("[cache] ignore-cache enabled: analyzers will run with isolated cache scope")

    run_lock: Optional[ProjectRunLock] = None
    lock_path = ""
    lock_acquired = False
    state = None
    state_path = ""
    before_sha = str(args.before_sha or "")
    after_sha = str(args.after_sha or "")
    full_scan = bool(args.full_scan)
    current_inventory = None
    previous_inventory = None
    repository_state: Dict[str, Dict[str, object]] = {}
    working_tree_paths: Set[str] = set()
    try:
        if not os.path.isdir(root):
            raise ValueError(f"Root not found: {root}")
        if _is_git_repo(root) and str(args.after_sha or "HEAD") != "HEAD":
            requested_after = _normalize_sha(root, args.after_sha)
            checkout_head = _normalize_sha(root, "HEAD")
            if requested_after != checkout_head:
                raise ValueError(
                    "--after-sha must match the checked-out HEAD because analyzers read the current worktree"
                )

        lock_root = os.path.join(control_cache_dir, "incremental_sync_locks")
        lock_path = os.path.join(lock_root, f"{scope_id}.lock")
        run_lock = ProjectRunLock(
            lock_path,
            f"project_id={project_id}",
            scope_id,
            root,
            timeout_seconds=args.lock_timeout_seconds,
        )
        lock_wait_started = time.monotonic()
        try:
            run_lock.acquire()
            lock_acquired = True
            summary["lock"] = {
                "path": lock_path,
                "acquired": True,
                "wait_seconds": round(time.monotonic() - lock_wait_started, 6),
                "owner": read_lock_metadata(lock_path),
            }
            if args.verbose:
                print(f"[state] lock acquired: {lock_path}")
        except LockBusyError as exc:
            summary["status"] = "lock_busy"
            summary["outcome"] = "lock_busy"
            summary["lock"] = {
                "path": lock_path,
                "acquired": False,
                "wait_seconds": round(time.monotonic() - lock_wait_started, 6),
                "owner": read_lock_metadata(lock_path),
            }
            summary["error"] = str(exc)
            failure_record = FailureRecord(
                code="sync_lock_busy",
                failure_class=FailureClass.LOCK,
                phase=current_phase,
                component="incremental_sync",
                retryable=True,
                run_id=run_id,
                correlation_id=correlation_id,
                summary=str(exc),
                safe_action="wait for the active run to finish, then retry",
                details={"lock_path": lock_path},
            )
            print(f"[state] lock busy: {exc}", file=sys.stderr)
            return 2

        state_path = state_file_path(control_cache_dir, project_id, root)
        legacy_state_path = legacy_state_file_path(control_cache_dir, project_id, root)
        state_load_path = (
            legacy_state_path
            if not os.path.exists(state_path) and os.path.exists(legacy_state_path)
            else state_path
        )
        state = load_sync_state(state_load_path, project_id, root)
        if state.migration_required:
            backup = backup_legacy_state(state_load_path, state)
            summary["migration"] = {
                "from": state.migrated_from,
                "backup": backup,
                "strategy": "conservative_full_bootstrap",
            }
        summary["state_before"] = {
            "schema_version": state.schema_version,
            "dirty": bool(state.dirty),
            "last_good_sha": state.last_good_sha,
            "snapshot_id": state.snapshot_id,
            "inventory_path": state.inventory_path,
            "migration_required": state.migration_required,
            "last_error": state.last_error,
            "last_run_before": state.last_run_before,
            "last_run_after": state.last_run_after,
            "updated_at": state.updated_at,
        }

        if state.inventory_path:
            try:
                previous_inventory = load_inventory_generation(state.inventory_path)
            except (OSError, ValueError, KeyError) as exc:
                summary["coverage_warnings"].append(
                    {"code": "inventory_unavailable", "path": state.inventory_path, "error": str(exc)}
                )

        if not full_scan and (
            state.migration_required
            or previous_inventory is None
            or (not args.before_sha and not state.last_good_sha and _is_git_repo(root))
        ):
            full_scan = True
            summary["full_scan"] = True
            summary["bootstrap_full_scan"] = True
            if args.verbose:
                print("[bootstrap] no trustworthy content baseline; scanning all source files")

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
            except Exception as exc:
                raise RuntimeError(
                    f"graph schema/project setup failed before streaming: {exc}"
                ) from exc

        if args.strict:
            missing: List[str] = []
            if not graph_ready:
                missing.append("graph_store")
            if not args.qdrant_url:
                missing.append("qdrant_url")
            if missing:
                raise RuntimeError(f"strict mode missing required services: {', '.join(missing)}")

        git_available = _is_git_repo(root)
        effective_detection = args.change_detection
        root_after_sha = ""
        if git_available:
            try:
                root_after_sha = _normalize_sha(root, args.after_sha or "HEAD")
            except subprocess.CalledProcessError:
                if str(args.after_sha or "HEAD") != "HEAD":
                    raise
                effective_detection = "hash"
                summary["coverage_warnings"].append(
                    {"code": "git_unborn_hash_fallback", "path": root}
                )
            if root_after_sha:
                checkout_head = _normalize_sha(root, "HEAD")
                if root_after_sha != checkout_head:
                    raise ValueError(
                        "--after-sha must match the checked-out HEAD because analyzers read the current worktree"
                    )
        if effective_detection == "hybrid" and not git_available:
            effective_detection = "hash"
            summary["coverage_warnings"].append(
                {"code": "git_unavailable_hash_fallback", "path": root}
            )
        if effective_detection == "committed" and not git_available:
            raise ValueError("--change-detection=committed requires a Git repository")
        summary["change_detection_effective"] = effective_detection

        scopes = []
        topology_warnings: List[dict] = []
        ignored_prefixes: Set[str] = set()
        if git_available:
            discovered_scopes, discovered_warnings = discover_repository_scopes(
                root, recursive=True
            )
            if args.submodules == "recursive":
                scopes = discovered_scopes
                topology_warnings = discovered_warnings
            else:
                scopes = discovered_scopes[:1]
                ignored_prefixes = {
                    item.source_prefix
                    for item in discovered_scopes[1:]
                } | {
                    str(item.get("path") or "")
                    for item in discovered_warnings
                    if item.get("path")
                }
                summary["submodules_ignored"] = sorted(ignored_prefixes)
            summary["coverage_warnings"].extend(topology_warnings)
            if topology_warnings and args.strict:
                codes = ", ".join(sorted({item["code"] for item in topology_warnings}))
                raise RuntimeError(f"strict mode repository coverage warning: {codes}")
        unavailable_prefixes = {
            str(item.get("path") or "")
            for item in topology_warnings
            if item.get("code") == "submodule_uninitialized" and item.get("path")
        }
        unreadable_prefixes = {
            str(item.get("path") or "")
            for item in topology_warnings
            if item.get("code") == "submodule_unreadable" and item.get("path")
        }
        preserved_prefixes = unavailable_prefixes | ignored_prefixes
        for prefix in preserved_prefixes | unreadable_prefixes:
            if prefix in state.repositories:
                repository_state[prefix] = dict(state.repositories[prefix])

        def _under_preserved_prefix(path: str) -> bool:
            return any(
                path == prefix or path.startswith(prefix.rstrip("/") + "/")
                for prefix in preserved_prefixes
            )

        committed_candidates: Set[str] = set()
        worktree_candidates: Set[str] = set()
        committed_deleted: Set[str] = set()
        worktree_deleted: Set[str] = set()
        candidates_by_source: Dict[str, Set[str]] = {
            "committed": set(),
            "staged": set(),
            "unstaged": set(),
            "untracked": set(),
        }
        git_entry_count = 0
        baseline_missing_prefixes: Set[str] = set(unreadable_prefixes)

        def _scope_path(prefix: str, path: str) -> str:
            return path if prefix == "." else f"{prefix.rstrip('/')}/{path}"

        summary_rel_path = _normalize_project_path(root, summary_path)

        def _is_source_candidate(path: str) -> bool:
            normalized = path.replace("\\", "/")
            if summary_rel_path and normalized == summary_rel_path:
                return False
            parts = normalized.split("/")
            if any(part in _SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
                return False
            lower = normalized.lower()
            return (
                Path(lower).suffix in _SOURCE_EXTENSIONS
                or lower.endswith(".gradle.kts")
                or descriptor_spec_for_path(normalized) is not None
                or (Path(lower).suffix == ".txt" and is_jp1_file(os.path.join(root, normalized)))
            )

        for scope in scopes:
            current_sha = (
                root_after_sha
                if scope.source_prefix == "."
                else _normalize_sha(scope.root, "HEAD")
            )
            prior_repo = state.repositories.get(scope.source_prefix, {})
            prior_sha = str(prior_repo.get("last_good_sha") or "")
            if scope.source_prefix == ".":
                prior_sha = str(args.before_sha or prior_sha or state.last_good_sha or "")
                after_sha = current_sha
            repository_state[scope.source_prefix] = {
                "root": scope.root,
                "git_root": scope.git_root,
                "git_pathspec": scope.git_pathspec,
                "last_good_sha": current_sha,
            }
            scope_summary = {
                "source_prefix": scope.source_prefix,
                "root": scope.root,
                "git_root": scope.git_root,
                "git_pathspec": scope.git_pathspec,
                "before_sha": prior_sha,
                "after_sha": current_sha,
            }
            summary["repositories"].append(scope_summary)

            if not full_scan and effective_detection in {"hybrid", "committed"} and prior_sha:
                try:
                    committed_entries = collect_git_diff_entries(scope.root, prior_sha, current_sha)
                except subprocess.CalledProcessError as exc:
                    warning = {
                        "code": "repository_baseline_missing",
                        "path": scope.source_prefix,
                        "baseline": prior_sha,
                    }
                    summary["coverage_warnings"].append(warning)
                    if args.strict or effective_detection == "committed":
                        raise RuntimeError(
                            f"missing repository baseline: {scope.source_prefix} {prior_sha}"
                        ) from exc
                    baseline_missing_prefixes.add(scope.source_prefix)
                    committed_entries = []
                git_entry_count += len(committed_entries)
                changed, deleted = collect_changed_and_deleted(committed_entries)
                mapped_changed = {_scope_path(scope.source_prefix, item) for item in changed}
                mapped_deleted = {_scope_path(scope.source_prefix, item) for item in deleted}
                committed_candidates.update(mapped_changed)
                committed_deleted.update(mapped_deleted)
                candidates_by_source["committed"].update(mapped_changed | mapped_deleted)

            work_entries = (
                collect_worktree_entries(scope.root)
                if effective_detection in {"hybrid", "committed"}
                else []
            )
            work_entries = [
                item
                for item in work_entries
                if (item.new_path or item.old_path)
                and _is_source_candidate(
                    _scope_path(scope.source_prefix, item.new_path or item.old_path or "")
                )
            ]
            if scope.source_prefix == "." and args.submodules == "recursive":
                submodule_prefixes = [
                    item.source_prefix.rstrip("/") + "/"
                    for item in scopes
                    if item.source_prefix != "."
                ]
                work_entries = [
                    item
                    for item in work_entries
                    if not any(
                        (item.new_path or item.old_path or "").startswith(prefix)
                        for prefix in submodule_prefixes
                    )
                ]
            if effective_detection == "committed" and work_entries:
                raise RuntimeError(
                    "committed change detection requires a clean worktree; use --change-detection=hybrid"
                )
            if effective_detection == "hybrid":
                git_entry_count += len(work_entries)
                changed, deleted = collect_changed_and_deleted(work_entries)
                mapped_changed = {_scope_path(scope.source_prefix, item) for item in changed}
                mapped_deleted = {_scope_path(scope.source_prefix, item) for item in deleted}
                for item in work_entries:
                    raw_path = item.new_path or item.old_path
                    if raw_path:
                        candidates_by_source[item.source].add(
                            _scope_path(scope.source_prefix, raw_path)
                        )
                worktree_candidates.update(mapped_changed)
                worktree_deleted.update(mapped_deleted)
                working_tree_paths.update(mapped_changed | mapped_deleted)

        if git_available and not after_sha:
            after_sha = root_after_sha or "full_scan"
        if not git_available:
            after_sha = "full_scan"
        before_sha = "full_scan" if full_scan else str(args.before_sha or state.last_good_sha or after_sha)

        all_source_paths = _normalize_project_paths(root, _walk_all_source_files(root))
        all_source_paths = {
            path for path in all_source_paths if not _under_preserved_prefix(path)
        }
        if summary_rel_path:
            all_source_paths.discard(summary_rel_path)
        eligible_paths = set(all_source_paths)
        if previous_inventory is not None:
            eligible_paths.update(previous_inventory.entries)
        committed_candidates.intersection_update(eligible_paths)
        committed_deleted.intersection_update(eligible_paths)
        worktree_candidates.intersection_update(eligible_paths)
        worktree_deleted.intersection_update(eligible_paths)
        working_tree_paths.intersection_update(eligible_paths)
        for source, paths in candidates_by_source.items():
            paths.intersection_update(eligible_paths)
            summary["change_sources"][source] = len(paths)
        raw_prior_worktree_paths = set(state.working_tree_paths)
        working_tree_paths.update(
            path for path in raw_prior_worktree_paths if _under_preserved_prefix(path)
        )
        prior_worktree_paths = raw_prior_worktree_paths & eligible_paths
        force_hash_paths = set(committed_candidates) | set(worktree_candidates) | prior_worktree_paths
        if baseline_missing_prefixes:
            force_hash_paths.update(
                path
                for path in all_source_paths
                if any(
                    prefix == "."
                    or path == prefix
                    or path.startswith(prefix.rstrip("/") + "/")
                    for prefix in baseline_missing_prefixes
                )
            )
            summary["reconciliation"]["performed"] = True
        if full_scan or effective_detection == "hash" or args.reconcile or state.dirty:
            force_hash_paths = set(all_source_paths)
            summary["reconciliation"]["performed"] = bool(
                args.reconcile or state.dirty or effective_detection == "hash"
            )
        force_hash_paths = {
            path for path in force_hash_paths if not _under_preserved_prefix(path)
        }
        current_inventory = capture_source_inventory(
            root,
            all_source_paths,
            previous=previous_inventory,
            force_hash_paths=force_hash_paths,
        )
        current_inventory = preserve_inventory_prefixes(
            current_inventory, previous_inventory, preserved_prefixes
        )
        inventory_changed, inventory_deleted = diff_source_inventories(
            previous_inventory, current_inventory
        )
        summary["change_sources"]["inventory"] = len(inventory_changed) + len(inventory_deleted)

        if full_scan:
            changed_paths = set(all_source_paths)
            deleted_paths: Set[str] = set()
        elif effective_detection in {"hybrid", "hash"}:
            changed_paths = set(inventory_changed)
            deleted_paths = set(inventory_deleted)
        else:
            changed_paths = set(committed_candidates)
            deleted_paths = set(committed_deleted)

        summary["diff"] = {
            "entries": len(changed_paths) if full_scan else git_entry_count,
            "changed": len(changed_paths),
            "deleted": len(deleted_paths),
        }
        if args.verbose:
            print(
                "[diff] mode=%s entries=%d changed=%d deleted=%d"
                % (effective_detection, git_entry_count, len(changed_paths), len(deleted_paths))
            )

        summary["before_sha"] = before_sha
        summary["after_sha"] = after_sha

        if not changed_paths and not deleted_paths:
            validate_inventory_unchanged(root, current_inventory, force_hash_paths)
            unchanged_paths = _normalize_project_paths(root, _walk_all_source_files(root))
            unchanged_paths = {
                path for path in unchanged_paths if not _under_preserved_prefix(path)
            }
            if summary_rel_path:
                unchanged_paths.discard(summary_rel_path)
            expected_available_paths = {
                path
                for path in current_inventory.entries
                if not _under_preserved_prefix(path)
            }
            if unchanged_paths != expected_available_paths:
                raise SourceChangedError("source file set changed during no-change verification")
            inventory_path = write_inventory_generation(control_cache_dir, current_inventory)
            mark_clean(
                state_path,
                state,
                last_good_sha=after_sha if root_after_sha else state.last_good_sha,
                before_sha=before_sha,
                after_sha=after_sha,
                snapshot_id=current_inventory.snapshot_id,
                inventory_path=inventory_path,
                repositories=repository_state,
                working_tree_paths=sorted(working_tree_paths),
            )
            summary["state_after"] = {
                "dirty": False,
                "last_good_sha": state.last_good_sha,
                "snapshot_id": current_inventory.snapshot_id,
                "inventory_path": inventory_path,
                "last_error": "",
                "last_run_before": before_sha,
                "last_run_after": after_sha,
            }
            summary["status"] = "success"
            summary["outcome"] = "partial_coverage" if topology_warnings else "no_changes"
            print("[state] no changes detected; state marked clean")
            return 0

        impacted_paths: Set[str] = set()
        if not full_scan and graph_ready and changed_paths:
            summary["services"]["impact_expansion_used"] = True
            impacted_paths = await _query_impacted_files(
                graph_provider=getattr(args, "graph_provider", None),
                neo4j_uri=args.neo4j_uri,
                neo4j_user=args.neo4j_user,
                neo4j_password=args.neo4j_password,
                neo4j_db=args.neo4j_db,
                falkordb_path=getattr(args, "falkordb_path", None),
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
        topology_changed = {
            path
            for path in changed_paths | impacted_paths
            if descriptor_spec_for_path(path) is not None
        }
        topology_deleted = {
            path
            for path in deleted_paths
            if descriptor_spec_for_path(path) is not None
        }
        topology_bootstrap_needed = False
        if (
            not full_scan
            and "project_topology" in parser_filter
            and not topology_changed
            and not topology_deleted
            and graph_ready
        ):
            try:
                topology_bootstrap_needed = await _project_topology_bootstrap_needed(
                    args=args, project_id=project_id,
                )
            except Exception as exc:
                if args.verbose:
                    print(f"[topology] bootstrap probe failed: {exc}")
            if topology_bootstrap_needed and args.verbose:
                print("[topology] no ProjectModule facts found; scheduling bootstrap overlay")
        if not parser_auto_mode:
            for framework in parser_filter & set(FRAMEWORK_ANALYZERS):
                framework_grouped[framework].update(
                    path
                    for path in changed_paths | deleted_paths | impacted_paths
                    if _is_framework_candidate(framework, path)
                )

        artifact_token = (
            f"{current_inventory.snapshot_id[:12]}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        manifest_root = os.path.join(
            safe_cache_root(control_cache_dir, "incremental_sync_manifests", project_root=root),
            scope_id,
        )
        message_output_dir = args.message_output_dir or safe_cache_root(
            control_cache_dir,
            "message_scan_artifacts",
            project_root=root,
        )
        message_qdrant_collection = args.message_qdrant_collection or _message_collection_name(
            project_id, root, project_code=args.project_code
        )
        parse_quality_artifact_dir = os.path.join(
            safe_cache_root(
                control_cache_dir,
                "parse_quality_artifacts",
                project_root=root,
            ),
            scope_id,
            artifact_token,
        )
        parse_quality_manifest_path = os.path.join(parse_quality_artifact_dir, "manifest.json")
        parse_quality_summary = summary["parse_quality"]
        assert isinstance(parse_quality_summary, dict)
        parse_quality_summary["artifact"] = parse_quality_manifest_path
        summary["services"]["message_qdrant_collection"] = message_qdrant_collection
        env = _build_analyzer_env(args)
        env["CORTEX_RUN_ID"] = run_id
        env["CORTEX_CORRELATION_ID"] = correlation_id
        current_phase = RunPhase.PARSING
        summary["phase"] = current_phase.value
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

            changed_manifest = os.path.join(manifest_root, f"{parser}_changed_{artifact_token}.json")
            deleted_manifest = os.path.join(manifest_root, f"{parser}_deleted_{artifact_token}.json")
            write_manifest_paths(changed_manifest, parser_scan)
            write_manifest_paths(deleted_manifest, parser_deleted)

            parser_info: Dict[str, object] = {
                "parser": parser,
                "role": "primary",
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
                "writes_vectors": config.writes_vectors,
                "seeded_by": list(config.seeded_by),
                "vector_status": (
                    "pending" if config.writes_vectors and bool(args.qdrant_url) else "disabled"
                ),
                "vector_count": 0 if not args.qdrant_url else None,
                "message_scan_enabled": bool(args.sync_messages and parser in MESSAGE_ENABLED_PARSERS),
                "ignore_cache": bool(args.ignore_cache),
                "message_qdrant_collection": (
                    message_qdrant_collection if args.sync_messages and parser in MESSAGE_ENABLED_PARSERS else ""
                ),
            }
            parse_quality_report_path = (
                os.path.join(parse_quality_artifact_dir, "cplus.json")
                if parser == "cplus"
                and args.parse_quality != "off"
                and parse_quality_artifact_dir
                else None
            )
            if parse_quality_report_path:
                parser_info["parse_quality_artifact"] = parse_quality_report_path
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

            run_incrementally = bool(config.incremental_supported and not full_scan)
            if run_incrementally:
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
                    embed_model=args.embed_model,
                    embed_device=args.embed_device,
                    embed_batch_size=args.embed_batch_size,
                    max_embed_chars=args.max_embed_chars,
                    parse_quality=args.parse_quality,
                    parse_quality_report=parse_quality_report_path,
                    parse_quality_max_files=args.parse_quality_max_files,
                    parse_quality_wall_seconds=args.parse_quality_wall_seconds,
                    parse_quality_workers=args.parse_quality_workers,
                    parse_quality_max_records=args.parse_quality_max_records,
                    parse_quality_max_bytes=args.parse_quality_max_bytes,
                )
            else:
                reason = "requested" if full_scan else "fallback"
                print(f"[upsert] parser={parser} mode=full reason={reason}")
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
                    embed_model=args.embed_model,
                    embed_device=args.embed_device,
                    embed_batch_size=args.embed_batch_size,
                    max_embed_chars=args.max_embed_chars,
                    parse_quality=args.parse_quality,
                    parse_quality_report=parse_quality_report_path,
                    parse_quality_max_files=args.parse_quality_max_files,
                    parse_quality_wall_seconds=args.parse_quality_wall_seconds,
                    parse_quality_workers=args.parse_quality_workers,
                    parse_quality_max_records=args.parse_quality_max_records,
                    parse_quality_max_bytes=args.parse_quality_max_bytes,
                )
            parser_info["command"] = cmd
            parser_env = dict(env)
            journal_config = None
            if parser_env.get("CORTEX_DISABLE_GRAPH", "").casefold() not in {
                "1", "true", "yes", "on"
            }:
                journal_config = configure_journal_env(
                    parser_env,
                    root=root,
                    project_id=project_id,
                    parser=parser,
                    source_revision=after_sha or current_inventory.snapshot_id,
                    source_snapshot=current_inventory.snapshot_id,
                    physical_target=physical_target_from_env(parser_env),
                    cache_dir=control_cache_dir,
                    mode=parser_env.get(
                        "CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"
                    ),
                    generation=artifact_token,
                )
                parser_info["journal_path"] = str(journal_config.path)
            try:
                if journal_config is not None:
                    recovered = await _resume_configured_journal(args, journal_config)
                    if recovered:
                        parser_info["journal_recovered_batches"] = recovered
                analyzer_output = _run(
                    cmd, cwd=_ROOT_DIR, verbose=args.verbose, env=parser_env
                )
                journal_status = finalize_journal_from_env(parser_env)
                if journal_status is not None:
                    parser_info["journal_status"] = journal_status.value
                    parser_info["journal"] = journal_status_from_env(parser_env)
                    if journal_status is not RunStatus.DRAINED:
                        raise RuntimeError(
                            f"parser '{parser}' graph journal did not drain: "
                            f"{journal_status.value}"
                        )
            except Exception as exc:
                parser_info["status"] = "failed"
                parser_info["error"] = str(exc)
                if parser_info["vector_status"] == "pending":
                    parser_info["vector_status"] = "failed"
                raise
            else:
                parser_info["status"] = "success"
                reported_vector_count = _scan_result_vector_count(analyzer_output)
                if reported_vector_count is not None:
                    parser_info["vector_count"] = reported_vector_count
                if parser_info["vector_status"] == "pending":
                    parser_info["vector_status"] = "success"
                if parse_quality_report_path and os.path.isfile(parse_quality_report_path):
                    try:
                        with open(parse_quality_report_path, "r", encoding="utf-8") as handle:
                            quality_payload = json.load(handle)
                        quality_aggregates = dict(quality_payload.get("aggregates") or {})
                        parser_info["parse_quality_aggregates"] = quality_aggregates
                        parse_quality_summary = summary["parse_quality"]
                        assert isinstance(parse_quality_summary, dict)
                        parse_quality_summary["aggregates"] = quality_aggregates
                        artifacts = parse_quality_summary["artifacts"]
                        assert isinstance(artifacts, list)
                        artifacts.append(parse_quality_report_path)
                        parse_quality_manifest_entries.append(
                            {
                                "parser": parser,
                                "path": os.path.basename(parse_quality_report_path),
                                "aggregates": quality_aggregates,
                            }
                        )
                    except (OSError, ValueError, TypeError) as exc:
                        parser_info["parse_quality_error"] = str(exc)
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
                ],
                "vector_status": "disabled",
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

            changed_manifest = os.path.join(manifest_root, f"{framework}_changed_{artifact_token}.json")
            deleted_manifest = os.path.join(manifest_root, f"{framework}_deleted_{artifact_token}.json")
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
            framework_env = dict(env)
            if framework_env.get("CORTEX_DISABLE_GRAPH", "").casefold() not in {
                "1", "true", "yes", "on"
            }:
                framework_journal = configure_journal_env(
                    framework_env,
                    root=root,
                    project_id=project_id,
                    parser=framework,
                    source_revision=after_sha or current_inventory.snapshot_id,
                    source_snapshot=current_inventory.snapshot_id,
                    physical_target=physical_target_from_env(framework_env),
                    cache_dir=control_cache_dir,
                    mode=framework_env.get(
                        "CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"
                    ),
                    generation=artifact_token,
                )
                framework_info["journal_path"] = str(framework_journal.path)
            framework_started = time.time()
            try:
                _run(cmd, cwd=_ROOT_DIR, verbose=args.verbose, env=framework_env)
                framework_status = finalize_journal_from_env(framework_env)
                if framework_status is not None:
                    framework_info["journal_status"] = framework_status.value
                    framework_info["journal"] = journal_status_from_env(
                        framework_env
                    )
                    if framework_status is not RunStatus.DRAINED:
                        raise RuntimeError(
                            f"framework '{framework}' graph journal did not drain: "
                            f"{framework_status.value}"
                        )
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

        topology_summaries: List[Dict[str, object]] = []
        if "project_topology" in parser_filter and (
            full_scan
            or topology_changed
            or topology_deleted
            or topology_bootstrap_needed
        ):
            topology_changed_manifest = os.path.join(
                manifest_root, f"project_topology_changed_{artifact_token}.json"
            )
            topology_deleted_manifest = os.path.join(
                manifest_root, f"project_topology_deleted_{artifact_token}.json"
            )
            write_manifest_paths(topology_changed_manifest, topology_changed)
            write_manifest_paths(topology_deleted_manifest, topology_deleted)
            topology_info: Dict[str, object] = {
                "parser": "project_topology",
                "role": "topology_overlay",
                "changed": len(topology_changed),
                "deleted": len(topology_deleted),
                "bootstrap": bool(topology_bootstrap_needed),
                "incremental_supported": True,
                "writes_vectors": False,
                "vector_status": "disabled",
                "status": "pending",
                "error": "",
                "started_at": _now_iso(),
                "finished_at": None,
                "duration_seconds": None,
                "changed_manifest": topology_changed_manifest,
                "deleted_manifest": topology_deleted_manifest,
            }
            topology_summaries.append(topology_info)
            topology_started = time.time()
            cmd = _build_analyzer_cmd(
                python_bin=args.python_bin,
                analyzer=PROJECT_TOPOLOGY_ANALYZER,
                root=root,
                project_id=project_id,
                project_name=project_name,
                before_sha=before_sha,
                after_sha=after_sha,
                changed_manifest=(
                    topology_changed_manifest if not full_scan else None
                ),
                deleted_manifest=(
                    topology_deleted_manifest if not full_scan else None
                ),
                qdrant_collection=None,
                message_scan_enabled=False,
                message_output_dir=None,
                message_qdrant_collection=None,
                incremental=not full_scan,
                verbose=args.verbose,
                ignore_cache=bool(args.ignore_cache),
            )
            topology_info["command"] = cmd
            topology_env = dict(env)
            if topology_env.get("CORTEX_DISABLE_GRAPH", "").casefold() not in {
                "1", "true", "yes", "on"
            }:
                topology_journal = configure_journal_env(
                    topology_env,
                    root=root,
                    project_id=project_id,
                    parser="project_topology",
                    source_revision=after_sha or current_inventory.snapshot_id,
                    source_snapshot=current_inventory.snapshot_id,
                    physical_target=physical_target_from_env(topology_env),
                    cache_dir=control_cache_dir,
                    mode=topology_env.get(
                        "CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"
                    ),
                    generation=artifact_token,
                )
                topology_info["journal_path"] = str(topology_journal.path)
            topology_mode = (
                "full" if full_scan
                else "bootstrap" if topology_bootstrap_needed
                else "incremental"
            )
            print(
                "[overlay] project_topology changed=%d deleted=%d mode=%s"
                % (
                    len(topology_changed),
                    len(topology_deleted),
                    topology_mode,
                )
            )
            try:
                _run(cmd, cwd=_ROOT_DIR, verbose=args.verbose, env=topology_env)
                topology_status = finalize_journal_from_env(topology_env)
                if topology_status is not None:
                    topology_info["journal_status"] = topology_status.value
                    topology_info["journal"] = journal_status_from_env(topology_env)
                    if topology_status is not RunStatus.DRAINED:
                        raise RuntimeError(
                            "project topology graph journal did not drain: "
                            f"{topology_status.value}"
                        )
            except Exception as exc:
                topology_info["status"] = "failed"
                topology_info["error"] = str(exc)
                raise
            else:
                topology_info["status"] = "success"
                executed_parsers.append("project_topology")
            finally:
                topology_info["finished_at"] = _now_iso()
                topology_info["duration_seconds"] = round(
                    time.time() - topology_started, 6
                )

        summary["primary_parsers"] = parser_summaries
        summary["framework_overlays"] = framework_summaries
        summary["topology_overlays"] = topology_summaries
        summary["parsers"] = (
            parser_summaries + framework_summaries + topology_summaries
        )
        current_phase = RunPhase.VERIFYING_GENERATION
        summary["phase"] = current_phase.value
        verification_paths = (
            set(current_inventory.entries)
            if full_scan or effective_detection == "hash" or args.reconcile
            else (changed_paths | impacted_paths)
        )
        verification_paths = {
            path for path in verification_paths if not _under_preserved_prefix(path)
        }
        validate_inventory_unchanged(root, current_inventory, verification_paths)
        post_source_paths = _normalize_project_paths(root, _walk_all_source_files(root))
        post_source_paths = {
            path for path in post_source_paths if not _under_preserved_prefix(path)
        }
        if summary_rel_path:
            post_source_paths.discard(summary_rel_path)
        expected_available_paths = {
            path
            for path in current_inventory.entries
            if not _under_preserved_prefix(path)
        }
        if post_source_paths != expected_available_paths:
            raise SourceChangedError("source file set changed during scan")
        current_phase = RunPhase.PUBLISHING
        summary["phase"] = current_phase.value
        inventory_path = write_inventory_generation(control_cache_dir, current_inventory)
        mark_clean(
            state_path,
            state,
            last_good_sha=after_sha if root_after_sha else state.last_good_sha,
            before_sha=before_sha,
            after_sha=after_sha,
            snapshot_id=current_inventory.snapshot_id,
            inventory_path=inventory_path,
            repositories=repository_state,
            working_tree_paths=sorted(working_tree_paths),
        )
        summary["state_after"] = {
            "dirty": False,
            "last_good_sha": state.last_good_sha,
            "snapshot_id": current_inventory.snapshot_id,
            "inventory_path": inventory_path,
            "last_error": "",
            "last_run_before": before_sha,
            "last_run_after": after_sha,
        }
        summary["status"] = "success"
        summary["outcome"] = "partial_coverage" if topology_warnings else "scanned"
        print(
            "[state] summary changed=%d deleted=%d impacted=%d parsers=%d"
            % (len(changed_paths), len(deleted_paths), len(impacted_paths), len(executed_parsers))
        )
        print("[state] incremental sync completed successfully")
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["outcome"] = "source_changed" if isinstance(exc, SourceChangedError) else "failed"
        summary["error"] = str(exc)
        debug_artifact: Optional[ArtifactReference] = None
        provisional = _failure_record_for_exception(
            exc,
            run_id=run_id,
            correlation_id=correlation_id,
            phase=current_phase,
        )
        if provisional.failure_class is FailureClass.INTERNAL_DEFECT or isinstance(
            exc, subprocess.CalledProcessError
        ):
            try:
                debug_text = traceback.format_exc()
                if isinstance(exc, subprocess.CalledProcessError):
                    debug_text = (
                        f"command: {' '.join(str(item) for item in exc.cmd)}\n"
                        f"exit_code: {exc.returncode}\n"
                        f"stdout_tail:\n{exc.output or ''}\n"
                        f"stderr_tail:\n{exc.stderr or ''}\n"
                    )
                debug_artifact = _write_debug_artifact(
                    f"{summary_path}.debug.log", debug_text, roots=(root,)
                )
                result_artifacts.append(debug_artifact)
            except OSError:
                debug_artifact = None
        failure_record = _failure_record_for_exception(
            exc,
            run_id=run_id,
            correlation_id=correlation_id,
            phase=current_phase,
            artifacts=((debug_artifact,) if debug_artifact else ()),
        )
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
        if parse_quality_manifest_path and parse_quality_artifact_dir:
            try:
                atomic_write_json(
                    parse_quality_manifest_path,
                    {
                        "schema_version": "1",
                        "policy": args.parse_quality,
                        "artifacts": parse_quality_manifest_entries,
                    },
                    allowed_root=parse_quality_artifact_dir,
                    max_bytes=1024 * 1024,
                )
                print(f"[parse-quality] run artifact: {parse_quality_manifest_path}", flush=True)
            except Exception as exc:  # pragma: no cover - filesystem edge cases
                print(f"[parse-quality] failed writing run artifact: {exc}", file=sys.stderr)
        if parse_quality_manifest_path and os.path.exists(parse_quality_manifest_path):
            result_artifacts.append(
                ArtifactReference(kind="parse_quality", path=parse_quality_manifest_path)
            )
        if failure_record is not None:
            if failure_record.failure_class is FailureClass.AMBIGUOUS_MUTATION:
                result_outcome = RunOutcome.AMBIGUOUS
            else:
                result_outcome = (
                    RunOutcome.FAILED_RETRYABLE
                    if failure_record.retryable
                    else RunOutcome.FAILED_TERMINAL
                )
        elif summary.get("status") == "success":
            legacy_outcome = str(summary.get("outcome") or "")
            if legacy_outcome == "no_changes":
                result_outcome = RunOutcome.NO_CHANGES
            elif legacy_outcome == "partial_coverage":
                result_outcome = RunOutcome.SUCCESS_WITH_QUARANTINE
            else:
                result_outcome = RunOutcome.SUCCESS
        else:
            failure_record = FailureRecord(
                code="incomplete_run_result",
                failure_class=FailureClass.INTERNAL_DEFECT,
                phase=current_phase,
                component="incremental_sync",
                retryable=False,
                run_id=run_id,
                correlation_id=correlation_id,
                summary=str(summary.get("error") or "run ended without a terminal status"),
                safe_action="inspect the summary artifact before rerunning",
            )
            result_outcome = RunOutcome.FAILED_TERMINAL
        run_result = RunResult(
            run_id=run_id,
            correlation_id=correlation_id,
            outcome=result_outcome,
            phase=current_phase,
            component="incremental_sync",
            failure=failure_record,
            artifacts=tuple(result_artifacts),
            started_at=str(summary.get("started_at") or ""),
            finished_at=str(summary.get("finished_at") or ""),
        )
        summary["run_result"] = run_result.to_dict()
        try:
            _write_summary(summary_path, summary)
            if args.verbose:
                print(f"[state] summary json: {summary_path}")
        except Exception as exc:  # pragma: no cover - filesystem edge cases
            print(f"[state] failed writing summary json: {exc}", file=sys.stderr)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reliable incremental Neo4j/Qdrant sync using Git candidates plus SHA-256 inventory")
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
        "--change-detection",
        choices=("hybrid", "committed", "hash"),
        default=os.environ.get("INCREMENTAL_CHANGE_DETECTION", "hybrid"),
        help="hybrid (default): Git candidates + SHA-256 verification; committed: clean Git commits only; hash: full content comparison",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=float(os.environ.get("INCREMENTAL_LOCK_TIMEOUT_SECONDS", "10")),
        help="Seconds to wait for the same project/root scan scope lock (exit 2 when busy)",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Force a full SHA-256 reconciliation without forcing full analyzer mode",
    )
    parser.add_argument(
        "--submodules",
        choices=("recursive", "ignore", "off"),
        default=os.environ.get("INCREMENTAL_SUBMODULES", "recursive"),
        help="Recursively scan initialized Git submodules or explicitly ignore them ('off' is an alias)",
    )
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
        "--reliability-mode",
        choices=("observe", "required"),
        default=os.environ.get("CORTEX_RELIABILITY_MODE", "observe"),
        help="Observe preserves legacy exits; required uses the typed reliability exit map.",
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
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Disable graph setup and graph writes for this sync invocation.",
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_CODE_PATH"))
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL") or os.environ.get("EMBED_MODEL"),
    )
    parser.add_argument("--embed-device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=int(os.environ.get("EMBED_BATCH_SIZE", "4")),
    )
    parser.add_argument(
        "--max-embed-chars",
        type=int,
        default=int(os.environ.get("MAX_EMBED_CHARS", "4000")),
    )
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
    parser.add_argument(
        "--parse-quality",
        choices=("off", "report", "repair"),
        default=os.environ.get("PARSE_QUALITY_POLICY", "report"),
    )
    parser.add_argument("--parse-quality-max-files", type=int, default=500)
    parser.add_argument("--parse-quality-wall-seconds", type=int, default=900)
    parser.add_argument(
        "--parse-quality-workers",
        type=int,
        default=max(1, min(4, (os.cpu_count() or 2) // 2)),
    )
    parser.add_argument("--parse-quality-max-records", type=int, default=10000)
    parser.add_argument("--parse-quality-max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if min(
        args.parse_quality_max_files,
        args.parse_quality_wall_seconds,
        args.parse_quality_workers,
        args.parse_quality_max_records,
        args.parse_quality_max_bytes,
    ) <= 0:
        parser.error("parse-quality limits must be positive")
    return args


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.summary_path:
        root = os.path.realpath(os.path.abspath(args.root))
        project_id = args.project_id or os.path.basename(root)
        args.summary_path = _default_summary_path(
            cache_dir=args.cache_dir,
            project_id=project_id,
            root=root,
        )
    legacy_exit = await _run_incremental(args)
    if args.reliability_mode != "required":
        return legacy_exit
    try:
        result = load_run_result(args.summary_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(
            "[failure] code=result_artifact_unavailable phase=finished "
            f"summary={type(exc).__name__} artifact={args.summary_path} "
            "action=inspect filesystem capacity and permissions",
            file=sys.stderr,
        )
        return int(ReliabilityExitCode.INTERNAL_DEFECT)
    return exit_code_for(result, observe_only=False)


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
