from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from tools.vb.vb_path_classifier import VBPathClassifier

_ANDROID_PLUGIN_MARKERS = (
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
)

SUPPORTED_PARSERS: Set[str] = {
    "cplus",
    "delphi",
    "java",
    "kotlin",
    "android",
    "vbnet",
    "vb6",
    "vba",
    "vbscript",
    "python",
    "js",
    "ts",
    "php",
    "csharp",
    "sql",
    "plsql",
}

SQL_SCANNER_EXTS: Tuple[str, ...] = (".sql", ".ddl", ".dml", ".psql")
PLSQL_SCANNER_EXTS: Tuple[str, ...] = (
    ".sql",
    ".pls",
    ".plsql",
    ".pks",
    ".pkb",
    ".pkg",
    ".pck",
    ".spc",
    ".spb",
    ".trg",
    ".fnc",
)

_PLSQL_STRONG_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcreate\s+(?:or\s+replace\s+)?package(?:\s+body)?\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:or\s+replace\s+)?trigger\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:or\s+replace\s+)?(?:procedure|function)\b", re.IGNORECASE),
    re.compile(r"\bdbms_scheduler\s*\.\s*create_job\b", re.IGNORECASE),
    re.compile(r"\blanguage\s+plpgsql\b", re.IGNORECASE),
    re.compile(r"\bdeclare\b[\s\S]{0,160}\bbegin\b", re.IGNORECASE),
)
_SQL_STRONG_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcreate\s+table\b", re.IGNORECASE),
    re.compile(r"\balter\s+table\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:unique\s+)?index\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
)

_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"--[^\n]*")


@dataclass(frozen=True)
class SqlOwnerDecision:
    owner: str
    plsql_score: int
    sql_score: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class BuildOwnerResult:
    owned_by_parser: Dict[str, Set[str]]
    deleted_by_parser: Dict[str, Set[str]]
    sql_decisions: Dict[str, SqlOwnerDecision]
    unassigned: Set[str]


def _safe_rel_path(path: str) -> str:
    return path.replace("\\", "/")


class _AndroidPathClassifier:
    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._module_cache: Dict[str, bool] = {}
        self._manifest_cache: Dict[str, bool] = {}
        self._gradle_cache: Dict[str, bool] = {}

    def is_android_path(self, rel_path: str) -> bool:
        rel = _safe_rel_path(rel_path).strip("./")
        if not rel:
            return False
        lower = rel.lower()
        if "/src/androidtest/" in lower or "/src/test/" in lower:
            return True
        if "/src/main/res/" in lower or "/src/main/manifest/" in lower:
            return True
        name = os.path.basename(lower)
        if name == "androidmanifest.xml":
            return True
        if lower.endswith(".gradle") or lower.endswith(".gradle.kts"):
            module_dir = os.path.dirname(rel)
            if module_dir and self._gradle_declares_android(module_dir):
                return True
        module_dir = self._infer_module_dir(rel)
        if module_dir:
            if self._module_has_android_manifest(module_dir):
                return True
            if self._module_has_android_gradle(module_dir):
                return True
        return False

    def _infer_module_dir(self, rel: str) -> str:
        normalized = _safe_rel_path(rel)
        for token in ("/src/main/", "/src/test/", "/src/androidTest/"):
            idx = normalized.find(token)
            if idx > 0:
                return normalized[:idx]
        return os.path.dirname(normalized)

    def _module_has_android_manifest(self, module_dir: str) -> bool:
        key = _safe_rel_path(module_dir)
        cached = self._manifest_cache.get(key)
        if cached is not None:
            return cached
        candidate = os.path.join(self.root, module_dir, "src", "main", "AndroidManifest.xml")
        found = os.path.isfile(candidate)
        self._manifest_cache[key] = found
        return found

    def _module_has_android_gradle(self, module_dir: str) -> bool:
        key = _safe_rel_path(module_dir)
        cached = self._module_cache.get(key)
        if cached is not None:
            return cached
        gradle_candidates = (
            os.path.join(self.root, module_dir, "build.gradle"),
            os.path.join(self.root, module_dir, "build.gradle.kts"),
        )
        found = False
        for candidate in gradle_candidates:
            if not os.path.isfile(candidate):
                continue
            if self._file_declares_android_plugin(candidate):
                found = True
                break
        self._module_cache[key] = found
        return found

    def _gradle_declares_android(self, module_dir: str) -> bool:
        gradle_candidates = (
            os.path.join(self.root, module_dir, "build.gradle"),
            os.path.join(self.root, module_dir, "build.gradle.kts"),
        )
        return any(self._file_declares_android_plugin(path) for path in gradle_candidates)

    def _file_declares_android_plugin(self, abs_path: str) -> bool:
        cached = self._gradle_cache.get(abs_path)
        if cached is not None:
            return cached
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read(16 * 1024)
        except OSError:
            self._gradle_cache[abs_path] = False
            return False
        normalized = text.lower()
        found = any(marker in normalized for marker in _ANDROID_PLUGIN_MARKERS)
        self._gradle_cache[abs_path] = found
        return found


def _select_parser_for_path(path: str, classifier: _AndroidPathClassifier, vb_classifier: VBPathClassifier, vb_owner_mode: str = "heuristic") -> Optional[str]:
    rel = _safe_rel_path(path)
    lower = rel.lower()
    name = os.path.basename(lower)
    ext = os.path.splitext(lower)[1]

    if ext in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
        return "cplus"
    if ext in {".pas", ".dpr", ".inc"}:
        return "delphi"
    if ext == ".py":
        return "python"
    if ext in {".js", ".jsx", ".mjs", ".cjs"}:
        return "js"
    if ext in {".ts", ".tsx", ".mts", ".cts"}:
        return "ts"
    if ext == ".php":
        return "php"
    if ext == ".cs":
        return "csharp"
    if ext in SQL_SCANNER_EXTS:
        return "sql"
    if ext in {".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"}:
        return "plsql"
    if ext in {".vb", ".vbproj", ".vbp", ".vbw", ".frx", ".bas", ".cls", ".frm", ".vbs", ".wsf", ".asp"}:
        return vb_classifier.select_parser_for_path(rel, owner_mode=vb_owner_mode)
    if name.endswith(".gradle") or name.endswith(".gradle.kts"):
        return "android"
    if ext == ".xml":
        return "android" if classifier.is_android_path(rel) else None
    if ext == ".java":
        return "android" if classifier.is_android_path(rel) else "java"
    if ext in {".kt", ".kts"}:
        return "android" if classifier.is_android_path(rel) else "kotlin"
    return None


def _mask_comments(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        chunk = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in chunk)

    masked = _COMMENT_BLOCK_RE.sub(repl, text)
    return _COMMENT_LINE_RE.sub(repl, masked)


def classify_sql_owner(content: str, mode: str = "heuristic") -> SqlOwnerDecision:
    normalized_mode = (mode or "heuristic").strip().lower()
    if normalized_mode in {"sql", "prefer-sql"}:
        return SqlOwnerDecision(owner="sql", plsql_score=0, sql_score=1, reasons=("mode:prefer-sql",))
    if normalized_mode in {"plsql", "prefer-plsql"}:
        return SqlOwnerDecision(owner="plsql", plsql_score=1, sql_score=0, reasons=("mode:prefer-plsql",))

    masked = _mask_comments(content or "")
    plsql_hits = [pattern.pattern for pattern in _PLSQL_STRONG_PATTERNS if pattern.search(masked)]
    sql_hits = [pattern.pattern for pattern in _SQL_STRONG_PATTERNS if pattern.search(masked)]
    plsql_score = len(plsql_hits)
    sql_score = len(sql_hits)
    if plsql_score > sql_score:
        owner = "plsql"
    elif sql_score > plsql_score:
        owner = "sql"
    elif plsql_score > 0:
        owner = "plsql"
    else:
        owner = "sql"
    reasons: List[str] = []
    reasons.extend([f"plsql:{item}" for item in plsql_hits[:3]])
    reasons.extend([f"sql:{item}" for item in sql_hits[:3]])
    if not reasons:
        reasons.append("default:sql")
    return SqlOwnerDecision(
        owner=owner,
        plsql_score=plsql_score,
        sql_score=sql_score,
        reasons=tuple(reasons),
    )


_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "node_modules",
    ".git",
    ".gradle",
    ".idea",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    ".dart_tool",
    ".flutter-plugins",
    "Pods",
})


def _iter_files(root: str) -> Iterable[Tuple[str, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel_path = _safe_rel_path(os.path.relpath(abs_path, root))
            yield abs_path, rel_path


def _read_limited(path: str, limit: int = 2 * 1024 * 1024) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def build_owner_maps(
    *,
    root: str,
    parsers: Sequence[str],
    sql_owner_mode: str = "heuristic",
    vb_owner_mode: str = "heuristic",
) -> BuildOwnerResult:
    selected_parsers = [item for item in parsers if item in SUPPORTED_PARSERS]
    parser_set = set(selected_parsers)
    classifier = _AndroidPathClassifier(root)
    vb_classifier = VBPathClassifier(root)
    owned_by_parser: Dict[str, Set[str]] = {parser: set() for parser in selected_parsers}
    deleted_by_parser: Dict[str, Set[str]] = {parser: set() for parser in selected_parsers}
    sql_decisions: Dict[str, SqlOwnerDecision] = {}
    unassigned: Set[str] = set()

    sql_candidates: Set[str] = set()
    plsql_candidates: Set[str] = set()
    plsql_exclusive_candidates: Set[str] = set()
    for abs_path, rel_path in _iter_files(root):
        base_parser = _select_parser_for_path(rel_path, classifier, vb_classifier, vb_owner_mode)
        if not base_parser:
            continue
        ext = os.path.splitext(rel_path.lower())[1]
        if ext in SQL_SCANNER_EXTS:
            sql_candidates.add(rel_path)
            plsql_candidates.add(rel_path)
        elif ext in PLSQL_SCANNER_EXTS:
            plsql_candidates.add(rel_path)
            plsql_exclusive_candidates.add(rel_path)

        owner = base_parser
        if ext in SQL_SCANNER_EXTS:
            decision = classify_sql_owner(_read_limited(abs_path), sql_owner_mode)
            sql_decisions[rel_path] = decision
            owner = decision.owner

        if owner in parser_set:
            owned_by_parser.setdefault(owner, set()).add(rel_path)
        elif base_parser in parser_set:
            owned_by_parser.setdefault(base_parser, set()).add(rel_path)
        else:
            unassigned.add(rel_path)

    if "sql" in parser_set:
        deleted_by_parser["sql"] = sql_candidates - owned_by_parser.get("sql", set())
    if "plsql" in parser_set:
        # Safety default: plsql should not delete SQL-like files owned by `sql`,
        # otherwise it can wipe data written by the SQL analyzer (same file_path ids).
        deleted_by_parser["plsql"] = plsql_exclusive_candidates - owned_by_parser.get("plsql", set())

    return BuildOwnerResult(
        owned_by_parser=owned_by_parser,
        deleted_by_parser=deleted_by_parser,
        sql_decisions=sql_decisions,
        unassigned=unassigned,
    )
