#!/usr/bin/env python3
"""dev - unified CLI for CortexHarness ingestion (code + documents)."""

import os
import sys
import json
import fnmatch
import hashlib
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import click

CLI_DIR = Path(__file__).parent.resolve()
REPO_ROOT = CLI_DIR.parent
CODE_TINY = REPO_ROOT / "code-tiny"
DOC_TINY = REPO_ROOT / "doc-tiny"

HARNESS_CONFIG_DIR = ".cortext-harness/config"
SYNC_STATE_DIR = ".cortext-harness/sync-state"

LANG_ANALYZERS = {
    "kotlin":         CODE_TINY / "tools/kotlin/kotlin_analyzer.py",
    "java":           CODE_TINY / "tools/java/java_analyzer.py",
    "ts":             CODE_TINY / "tools/ts/ts_analyzer.py",
    "js":             CODE_TINY / "tools/js/js_analyzer.py",
    "php":            CODE_TINY / "tools/php/php_analyzer.py",
    "sql":            CODE_TINY / "tools/sql/sql_analyzer.py",
    "plsql":          CODE_TINY / "tools/plsql/plsql_analyzer.py",
    "cplus":          CODE_TINY / "tools/cplus/cplus_analyzer.py",
    "csharp":         CODE_TINY / "tools/csharp/csharp_analyzer.py",
    "python":         CODE_TINY / "tools/python/python_analyzer.py",
    "android_java":   CODE_TINY / "tools/android/android_java_analyzer.py",
    "android_kotlin": CODE_TINY / "tools/android/android_kotlin_analyzer.py",
    "android_mixed":  CODE_TINY / "tools/android/android_mixed_analyzer.py",
}

LANG_EXTENSIONS = {
    "kotlin":  {".kt", ".kts"},
    "java":    {".java"},
    "ts":      {".ts", ".tsx"},
    "js":      {".js", ".jsx"},
    "php":     {".php"},
    "sql":     {".sql"},
    "plsql":   {".pls", ".pkb", ".pks"},
    "cplus":   {".c", ".cpp", ".cxx", ".cc", ".h", ".hpp", ".hxx"},
    "csharp":  {".cs"},
    "python":  {".py"},
}

SENSITIVE_PATTERNS = [
    ".env", "*.env", ".env.*",
    "*.key", "*.pem", "*.p12", "*.pfx", "*.crt", "*.cer",
    "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    "*secret*", "*password*", "*credential*", "*token*",
    "*.keystore", "*.jks",
]

DOC_INGESTOR          = DOC_TINY  / "graphrag_ingest_langextract.py"
BUILD_OWNER_MANIFESTS = CODE_TINY / "tools/sync/build_owner_manifests.py"

HARNESS_SCRIPTS   = REPO_ROOT / "harness" / "scripts"
HARNESS_TEMPLATES = REPO_ROOT / "harness" / "templates"

_LANG_TO_OWNER_PARSER = {
    "android_java":   "android",
    "android_kotlin": "android",
    "android_mixed":  "android",
}

DOC_EXT_FLAGS = {
    ".pdf":  "--pdf",
    ".md":   "--md",
    ".docx": "--docx",
    ".txt":  "--text-file",
    ".pptx": "--pptx",
    ".xlsx": "--xlsx",
}

DOC_EXTENSIONS = set(DOC_EXT_FLAGS.keys())

MCP_LOG_DIR = REPO_ROOT / ".cache"

MCP_SERVICES = {
    "code-tiny": {
        "dir":     CODE_TINY,
        "cmd":     ["mcp/unified_mcp.py",
                    "--transport", "streamable-http",
                    "--host", "127.0.0.1", "--port", "8788", "--path", "/mcp"],
        "port":    8788,
        "pattern": "unified_mcp.py",
        "url":     "http://127.0.0.1:8788/mcp",
    },
    "doc-tiny": {
        "dir":     DOC_TINY,
        "cmd":     ["mcp_graph_rag.py",
                    "--host", "127.0.0.1", "--port", "8789",
                    "--transport", "streamable-http", "--path", "/mcp"],
        "port":    8789,
        "pattern": "mcp_graph_rag.py",
        "url":     "http://127.0.0.1:8789/mcp",
    },
}


def _agent_configs() -> dict:
    """Return {agent_name: {path, key}} for the current platform."""
    home = Path.home()
    if sys.platform == "darwin":
        app_support = home / "Library" / "Application Support"
    elif sys.platform == "win32":
        app_support = Path(os.environ.get("APPDATA", str(home)))
    else:
        app_support = home / ".config"

    return {
        "claude": {
            "path": app_support / "Claude" / "claude_desktop_config.json",
            "key":  "mcpServers",
        },
        "claude-code": {
            "path": home / ".claude" / "settings.json",
            "key":  "mcpServers",
        },
        "vscode": {
            "path": app_support / "Code" / "User" / "mcp.json",
            "key":  "servers",
        },
        "cursor": {
            "path": home / ".cursor" / "mcp.json",
            "key":  "mcpServers",
        },
    }


_SCAFFOLD_DIRS = [
    "docs/design-docs",
    "docs/exec-plans/active",
    "docs/exec-plans/completed",
    "docs/generated",
    "docs/product-specs",
    "docs/references",
    "src/core/migration",
    "src/core/services",
    "src/infra/persistence",
    "src/infra/providers",
    "src/interface/api",
    "src/interface/cli",
    "src/shared",
]

_SCAFFOLD_FILES = {
    "docs/design-docs/index.md":            "# Design Docs\n",
    "docs/exec-plans/tech-debt-tracker.md": "# Tech Debt Tracker\n",
    "docs/generated/db-schema.md":          "# DB Schema\n",
    "docs/product-specs/index.md":          "# Product Specs\n",
    "docs/DESIGN.md":                       "# Design\n",
    "docs/FRONTEND.md":                     "# Frontend Guidelines\n",
    "docs/PLANS.md":                        "# Project Roadmap\n",
    "docs/PRODUCT_SENSE.md":               "# Product Logic & Philosophy\n",
    "docs/QUALITY_SCORE.md":               "# Engineering Standards\n",
    "docs/RELIABILITY.md":                  "# Stability & Error Handling\n",
    "docs/SECURITY.md":                     "# Security Protocols\n",
    "AGENTS.md":                            "# Agents\n",
    "ARCHITECTURE.md":                      "# Architecture\n",
    ".cursorrules":                         "# AI Instruction Set\n",
    "README.md":                            "# Project\n",
}

_SCAN_EXCLUDE = {
    # version control
    ".git",
    # python envs & build artefacts
    ".venv", "venv", "env", ".env",
    "__pycache__", "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    # js/ts
    "node_modules",
    # java/kotlin/android build
    "build", "out", "target", ".gradle",
    # compiled output (generic)
    "dist", "bin", "obj",
    # ios
    "Pods", "DerivedData",
    # go / php
    "vendor",
    # ide & tool caches
    ".idea", ".vscode", ".cache", ".cortext-harness",
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _config_dir(project_dir: Path) -> Path:
    return project_dir / HARNESS_CONFIG_DIR


def _config_path(project_dir: Path, env: str) -> Path:
    return _config_dir(project_dir) / f"{env}.json"


def _load_active_config(project_dir: Path) -> tuple:
    cfg_dir = _config_dir(project_dir)
    if not cfg_dir.exists():
        click.echo(f"[error] No config found at '{cfg_dir}'. Run 'dev init' first.", err=True)
        sys.exit(1)

    configs = sorted(cfg_dir.glob("*.json"))
    if not configs:
        click.echo(f"[error] No config files in '{cfg_dir}'. Run 'dev init' first.", err=True)
        sys.exit(1)

    for p in configs:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("active"):
            return cfg, p

    p = configs[0]
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    click.echo(f"[warn] No active config found; using '{p.name}'", err=True)
    return cfg, p


def _save_config(cfg: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    click.echo(f"[ok] Config saved -> {path}")


def _source_projects(source: dict) -> list:
    """Return list of {git, folder[]} entries. Handles both old and new format.

    New format: source.projects = [{git, folder}, ...]
    Old format: source.git + source.folder  (migrated transparently)
    """
    if "projects" in source:
        return list(source["projects"])
    return [{"git": source.get("git", ""), "folder": source.get("folder", [])}]


def _source_folders(source: dict) -> list:
    """Flatten all project folders into a deduplicated, ordered list."""
    result, seen = [], set()
    for p in _source_projects(source):
        for f in p.get("folder", []):
            if f and f not in seen:
                seen.add(f)
                result.append(f)
    return result


def _deactivate_other_envs(project_dir: Path, current_env: str) -> None:
    cfg_dir = _config_dir(project_dir)
    if not cfg_dir.exists():
        return
    for p in cfg_dir.glob("*.json"):
        if p.stem == current_env:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                other = json.load(f)
            if other.get("active"):
                other["active"] = False
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(other, f, indent=2, ensure_ascii=False)
                click.echo(f"[info] Deactivated config: {p.name}")
        except Exception:
            pass


def _env_to_neo4j_args(env: dict) -> list:
    """For doc-tiny ingestor (uses --neo4j-pass, no --neo4j-db support)."""
    return [
        "--neo4j-uri",  env.get("NEO4J_URI",  "bolt://localhost:7687"),
        "--neo4j-user", env.get("NEO4J_USER", "neo4j"),
        "--neo4j-pass", env.get("NEO4J_PASS", ""),
    ]


def _neo4j_args_code(env: dict) -> list:
    """For code-tiny analyzers (uses --neo4j-password)."""
    args = [
        "--neo4j-uri",      env.get("NEO4J_URI",  "bolt://localhost:7687"),
        "--neo4j-user",     env.get("NEO4J_USER", "neo4j"),
        "--neo4j-password", env.get("NEO4J_PASS", ""),
    ]
    if env.get("NEO4J_DB"):
        args += ["--neo4j-db", env["NEO4J_DB"]]
    return args


def _env_to_qdrant_url(env: dict) -> str:
    host = env.get("QDRANT_HOST", "localhost")
    port = env.get("QDRANT_PORT", "6333")
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Scaffold helpers
# ---------------------------------------------------------------------------

def _discover_folders(project_dir: Path, root_prefix: str) -> list:
    result = []
    base = project_dir / root_prefix
    if not base.exists():
        return result
    for item in sorted(base.rglob("*")):
        if not item.is_dir():
            continue
        rel = item.relative_to(project_dir)
        if not set(rel.parts).intersection(_SCAN_EXCLUDE):
            result.append(str(rel))
    return [root_prefix] + result


def _scaffold_project(project_dir: Path) -> tuple:
    click.echo("\n─── Scaffolding project structure ─────────")
    created = []

    for d in _SCAFFOLD_DIRS:
        full = project_dir / d
        if not full.exists():
            full.mkdir(parents=True, exist_ok=True)
            created.append(f"  [dir]  {d}/")

    for rel, content in _SCAFFOLD_FILES.items():
        full = project_dir / rel
        if not full.exists():
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            created.append(f"  [file] {rel}")

    if created:
        for line in created:
            click.echo(line)
    else:
        click.echo("  (all paths already exist, nothing created)")

    doc_folders  = _discover_folders(project_dir, "docs")
    code_folders = _discover_folders(project_dir, "src")
    click.echo(f"  [scan] doc folders: {len(doc_folders)}  code folders: {len(code_folders)}")
    return doc_folders, code_folders


# ---------------------------------------------------------------------------
# Sync state helpers
# ---------------------------------------------------------------------------

def _state_path(project_dir: Path, folder: str) -> Path:
    key = hashlib.md5(folder.encode()).hexdigest()[:12]
    return project_dir / SYNC_STATE_DIR / f"{key}.json"


def _load_state(project_dir: Path, folder: str) -> dict:
    p = _state_path(project_dir, folder)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(project_dir: Path, folder: str, state: dict) -> None:
    p = _state_path(project_dir, folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Language detection & sensitive file filtering
# ---------------------------------------------------------------------------

def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SENSITIVE_PATTERNS)


def _is_excluded_path(path: Path, root: Path) -> bool:
    """True if path lives inside a _SCAN_EXCLUDE directory (e.g. .venv, node_modules)."""
    try:
        return bool(set(path.relative_to(root).parts[:-1]).intersection(_SCAN_EXCLUDE))
    except ValueError:
        return False


def _detect_langs(folder_path: Path) -> list:
    """Detect languages from extensions. Android takes priority when AndroidManifest exists."""
    counts: Counter = Counter()
    is_android = any(folder_path.rglob("AndroidManifest.xml"))

    for f in folder_path.rglob("*"):
        if not f.is_file() or _is_sensitive(f) or _is_excluded_path(f, folder_path):
            continue
        ext = f.suffix.lower()
        for lang, exts in LANG_EXTENSIONS.items():
            if ext in exts:
                counts[lang] += 1

    if is_android and (counts.get("java", 0) > 0 or counts.get("kotlin", 0) > 0):
        has_java   = counts.get("java", 0) > 0
        has_kotlin = counts.get("kotlin", 0) > 0
        if has_java and has_kotlin:
            return ["android_mixed"]
        elif has_kotlin:
            return ["android_kotlin"]
        else:
            return ["android_java"]

    return [lang for lang, _ in counts.most_common() if counts[lang] > 0 and lang in LANG_ANALYZERS]


# ---------------------------------------------------------------------------
# Git / mtime change detection
# ---------------------------------------------------------------------------

def _git_head(folder_path: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(folder_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_status_since(folder_path: Path, since_commit: str) -> tuple:
    """Return (changed_files, deleted_files) as paths relative to folder_path.

    Uses --relative so paths align with --root passed to analyzers.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(folder_path), "diff",
             "--name-status", "--relative", since_commit, "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return [], []
        changed, deleted = [], []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            fname  = parts[-1].strip()
            if not fname or _is_sensitive(Path(fname)):
                continue
            if status.startswith("D"):
                deleted.append(fname)
            else:
                changed.append(fname)
        return changed, deleted
    except Exception:
        return [], []


def _mtime_changed_files(folder_path: Path, since_ts: float) -> tuple:
    """Return (changed_files, []) using mtime comparison. Paths relative to folder_path."""
    changed = []
    for f in folder_path.rglob("*"):
        if (f.is_file() and not _is_sensitive(f)
                and not _is_excluded_path(f, folder_path)
                and f.stat().st_mtime > since_ts):
            try:
                changed.append(str(f.relative_to(folder_path)))
            except ValueError:
                pass
    return changed, []


def _write_manifest(files: list, suffix: str) -> Path:
    """Write file list to a temp JSON manifest. Returns temp file path."""
    fd, path = tempfile.mkstemp(suffix=f"_{suffix}.json")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(files, f)
    return Path(path)


# ---------------------------------------------------------------------------
# Doc change detection helpers
# ---------------------------------------------------------------------------

def _doc_file_hash(path: Path) -> str:
    """SHA-256 hash of a file (for hash-based incremental detection)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_doc_files(folder_path: Path) -> list:
    """Return all supported document files under folder_path (sorted)."""
    return sorted(
        f for f in folder_path.rglob("*")
        if (f.is_file() and f.suffix.lower() in DOC_EXTENSIONS
                and not _is_sensitive(f) and not _is_excluded_path(f, folder_path))
    )


def _detect_changed_docs(folder_path: Path, state: dict) -> tuple:
    """Return (changed_files, deleted_rel_paths) for incremental doc sync.

    Priority: git diff > file-hash comparison > mtime.
    changed_files  : list of absolute Path objects to ingest.
    deleted_rel_paths: list of relative str paths no longer on disk.
    """
    since_commit = state.get("git_commit", "")
    since_ts     = state.get("last_sync_ts", 0.0)
    stored_hashes: dict = state.get("file_hashes", {})

    # ── git diff ──────────────────────────────────────────────────────────
    if since_commit:
        current = _git_head(folder_path)
        if current and current == since_commit:
            return [], []                        # nothing changed
        changed_rel, deleted_rel = _git_status_since(folder_path, since_commit)
        changed = [folder_path / r for r in changed_rel
                   if (folder_path / r).suffix.lower() in DOC_EXTENSIONS
                   and not _is_sensitive(folder_path / r)]
        deleted = [r for r in deleted_rel if Path(r).suffix.lower() in DOC_EXTENSIONS]
        return changed, deleted

    # ── hash comparison ───────────────────────────────────────────────────
    if stored_hashes:
        current_files = {
            str(f.relative_to(folder_path)): f
            for f in _find_doc_files(folder_path)
        }
        changed = [
            abs_path for rel, abs_path in current_files.items()
            if _doc_file_hash(abs_path) != stored_hashes.get(rel, "")
        ]
        deleted = [rel for rel in stored_hashes if rel not in current_files]
        return changed, deleted

    # ── mtime fallback ────────────────────────────────────────────────────
    if since_ts:
        changed = [
            f for f in _find_doc_files(folder_path)
            if f.stat().st_mtime > since_ts
        ]
        return changed, []

    return [], []


def _build_file_hashes(folder_path: Path) -> dict:
    """Build {relative_path: sha256} mapping for all doc files in folder."""
    return {
        str(f.relative_to(folder_path)): _doc_file_hash(f)
        for f in _find_doc_files(folder_path)
    }


def _sync_doc_folder(
    *,
    project_path: Path,
    folder: str,
    env: dict,
    python: str,
    project: dict,
    force_mode: str,   # "full" | "incremental" | "auto"
    entity_provider: str,
    dry_run: bool,
    preview: bool,
) -> dict:
    """Sync one doc folder. Returns result summary dict."""
    folder_path = Path(folder) if Path(folder).is_absolute() else project_path / folder

    if not folder_path.exists():
        click.echo(f"\n[warn] Folder not found: {folder_path} — skipping")
        return {"folder": folder, "status": "skipped", "reason": "not found"}

    state = _load_state(project_path, f"doc:{folder}")
    mode  = force_mode if force_mode != "auto" else ("incremental" if state else "full")

    qdrant_url   = _env_to_qdrant_url(env)
    project_name = project.get("name", "project")

    base_cmd = [
        python, str(DOC_INGESTOR),
        *_env_to_neo4j_args(env),
        "--qdrant-url",          qdrant_url,
        "--collection",          project_name,
        "--entity-provider",     entity_provider,
        "--embedding-model",     env.get("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "--embedding-device",    env.get("device", "cpu"),
        "--max-paragraph-chars", env.get("MAX_PARAGRAPH_CHARS", "500"),
        "--gliner-model-name",   env.get("GLINER_MODEL_NAME", "urchade/gliner_large-v2.1"),
        "--gliner-labels",       env.get("GLINER_LABELS", "PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD"),
        "--gliner-threshold",    env.get("GLINER_THRESHOLD", "0.35"),
        "--gliner-batch-size",   env.get("GLINER_BATCH_SIZE", "1"),
        "--neo4j-batch-size",    env.get("NEO4J_BATCH_SIZE", "1"),
        "--no-batch",
    ]
    click.echo(f"\n{'─' * 52}")
    click.echo(f" folder : {folder}")
    click.echo(f" mode   : {mode}")
    click.echo(f" provider: {entity_provider}")

    start_ts = time.time()

    # ── Full sync ─────────────────────────────────────────────────────────
    if mode == "full":
        all_files = _find_doc_files(folder_path)
        click.echo(f" files  : {len(all_files)} document(s)")
        if not all_files:
            click.echo("  [warn] No supported document files found — skipping")
            return {"folder": folder, "status": "skipped", "reason": "no doc files"}

        rc = _run_with_retry(base_cmd + ["--folder", str(folder_path)], dry_run=dry_run)
        elapsed = time.time() - start_ts

        if rc == 0 and not dry_run:
            _save_state(project_path, f"doc:{folder}", {
                "folder":       folder,
                "last_sync":    datetime.now(timezone.utc).isoformat(),
                "last_sync_ts": start_ts,
                "mode":         "full",
                "git_commit":   _git_head(folder_path),
                "file_hashes":  _build_file_hashes(folder_path),
                "file_count":   len(all_files),
            })

        return {"folder": folder, "status": "ok" if rc == 0 else "error",
                "mode": mode, "elapsed": elapsed}

    # ── Incremental sync ──────────────────────────────────────────────────
    changed_files, deleted_rel = _detect_changed_docs(folder_path, state)

    if not changed_files and not deleted_rel:
        click.echo("  [ok] No changes detected — skipping")
        return {"folder": folder, "status": "skipped", "reason": "no changes"}

    click.echo(f"  changed: {len(changed_files)}  deleted: {len(deleted_rel)}")

    if preview and changed_files:
        click.echo(f"\n  Preview — {len(changed_files)} file(s) queued:")
        for f in sorted(changed_files)[:20]:
            try:
                rel = f.relative_to(folder_path)
            except ValueError:
                rel = f
            click.echo(f"    + {rel}")
        if len(changed_files) > 20:
            click.echo(f"    … and {len(changed_files) - 20} more")
        if deleted_rel:
            click.echo(f"  Deleted ({len(deleted_rel)}):")
            for r in deleted_rel[:10]:
                click.echo(f"    - {r}")
        if not click.confirm("\n  Proceed?", default=True):
            return {"folder": folder, "status": "cancelled"}

    errors = 0
    for file_path in changed_files:
        ext  = file_path.suffix.lower()
        flag = DOC_EXT_FLAGS.get(ext)
        if not flag:
            continue
        click.echo(f"  [+] {file_path.name}")
        rc = _run_with_retry(base_cmd + [flag, str(file_path)], dry_run=dry_run)
        if rc != 0:
            click.echo(f"    [error] exited {rc}")
            errors += 1

    elapsed = time.time() - start_ts
    success = errors == 0

    if success and not dry_run:
        # Update stored hashes for changed files only
        new_hashes = dict(state.get("file_hashes", {}))
        for f in changed_files:
            try:
                rel = str(f.relative_to(folder_path))
                new_hashes[rel] = _doc_file_hash(f)
            except (ValueError, OSError):
                pass
        for rel in deleted_rel:
            new_hashes.pop(rel, None)

        _save_state(project_path, f"doc:{folder}", {
            "folder":       folder,
            "last_sync":    datetime.now(timezone.utc).isoformat(),
            "last_sync_ts": start_ts,
            "mode":         "incremental",
            "git_commit":   _git_head(folder_path),
            "file_hashes":  new_hashes,
            "file_count":   len(changed_files),
        })

    return {
        "folder":  folder,
        "status":  "ok" if success else "error",
        "mode":    mode,
        "elapsed": elapsed,
    }


# ---------------------------------------------------------------------------
# Owner-manifest helpers (code-tiny pre-processing step)
# ---------------------------------------------------------------------------

def _owner_manifest_dir(project_path: Path, project_id: str) -> Path:
    return project_path / ".cache" / "owner_manifests" / project_id


def _run_owner_manifests(
    *,
    folder_path: Path,
    project_id: str,
    langs: list,
    owner_dir: Path,
    python: str,
    verbose: bool,
    dry_run: bool,
) -> Path:
    """Run build_owner_manifests.py to partition files by parser. Returns owner_dir."""
    if not BUILD_OWNER_MANIFESTS.exists():
        click.echo(f"  [warn] build_owner_manifests not found — skipping pre-processing step")
        return owner_dir

    canonical = list(dict.fromkeys(
        _LANG_TO_OWNER_PARSER.get(l, l) for l in langs
    )) if langs else ["auto"]
    parsers_str = ",".join(canonical)
    owner_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        python, str(BUILD_OWNER_MANIFESTS),
        "--root",       str(folder_path),
        "--project-id", project_id,
        "--parsers",    parsers_str,
        "--output-dir", str(owner_dir),
    ]
    if verbose:
        cmd.append("--verbose")

    click.echo(f"  [owner-manifests] parsers={parsers_str}  out={owner_dir}")
    _run_with_retry(cmd, max_retries=1, dry_run=dry_run)
    return owner_dir


def _owner_manifest_counts(owner_dir: Path, langs: list) -> dict:
    """Read per-lang changed/deleted counts from owner manifest files."""
    counts = {}
    for lang in langs:
        canonical = _LANG_TO_OWNER_PARSER.get(lang, lang)
        changed_path = owner_dir / f"{canonical}_changed_owner.json"
        deleted_path = owner_dir / f"{canonical}_deleted_owner.json"
        changed, deleted = 0, 0
        if changed_path.exists():
            try:
                changed = len(json.loads(changed_path.read_text()))
            except Exception:
                pass
        if deleted_path.exists():
            try:
                deleted = len(json.loads(deleted_path.read_text()))
            except Exception:
                pass
        counts[lang] = {"changed": changed, "deleted": deleted}
    return counts


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def _venv_python(base_dir: Path) -> str:
    for candidate in [
        base_dir / ".venv" / "Scripts" / "python.exe",
        base_dir / ".venv" / "bin" / "python",
    ]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run_with_retry(cmd: list, max_retries: int = 3, dry_run: bool = False) -> int:
    display = " ".join(str(c) for c in cmd)
    click.echo(f"  $ {display}")
    if dry_run:
        click.echo("  [dry-run] skipped")
        return 0
    for attempt in range(1, max_retries + 1):
        rc = subprocess.run([str(c) for c in cmd]).returncode
        if rc == 0:
            return 0
        if attempt < max_retries:
            wait = 2 ** attempt
            click.echo(f"  [retry {attempt}/{max_retries - 1}] exit={rc}, retrying in {wait}s…")
            time.sleep(wait)
    return rc


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def _select_folders_interactive(folders: list) -> list:
    click.echo("\nConfigured source folders:")
    for i, f in enumerate(folders, 1):
        click.echo(f"  [{i:2d}] {f}")
    click.echo("  [ 0] All folders")

    raw = click.prompt("\nSelect (comma-separated numbers, 0 = all)", default="0").strip()
    if raw == "0":
        return list(folders)

    selected = []
    for part in raw.split(","):
        part = part.strip()
        try:
            idx = int(part) - 1
            if 0 <= idx < len(folders):
                selected.append(folders[idx])
            else:
                click.echo(f"  [warn] Index {part} out of range, skipping")
        except ValueError:
            click.echo(f"  [warn] Invalid input '{part}', skipping")
    return selected


def _run_analyzer(
    *,
    lang: str,
    analyzer: Path,
    folder_path: Path,
    env: dict,
    python: str,
    project: dict,
    mode: str,
    changed_files: list,
    deleted_files: list,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Build and invoke one analyzer subprocess. Returns exit code."""
    qdrant_url   = _env_to_qdrant_url(env)
    project_name = project.get("name", "project")
    project_id   = project.get("code", project_name)
    repo         = folder_path.name

    cmd = [
        python, str(analyzer),
        "--root",             str(folder_path),
        *_neo4j_args_code(env),
        "--qdrant-url",        qdrant_url,
        "--qdrant-collection", project_name,
        "--embed-model",       env.get("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3"),
        "--device",            env.get("device", "cpu"),
        "--batch-size",        env.get("BATCH_SIZE", "1"),
        "--max-embed-chars",   env.get("MAX_EMBED_CHARS", "800"),
        "--language",          lang,
        "--project-id",        project_id,
        "--project-name",      project_name,
        "--repo",              repo,
        "--disable-message-scan",
    ]

    changed_manifest = None
    deleted_manifest = None

    if mode == "incremental" and (changed_files or deleted_files):
        cmd += ["--incremental"]
        if changed_files:
            changed_manifest = _write_manifest(changed_files, "changed")
            cmd += ["--changed-files-manifest", str(changed_manifest)]
        if deleted_files:
            deleted_manifest = _write_manifest(deleted_files, "deleted")
            cmd += ["--deleted-files-manifest", str(deleted_manifest)]

    if verbose:
        cmd.append("--verbose")

    rc = _run_with_retry(cmd, dry_run=dry_run)

    for m in (changed_manifest, deleted_manifest):
        if m and m.exists():
            try:
                m.unlink()
            except Exception:
                pass

    return rc


def _sync_folder(
    *,
    project_path: Path,
    folder: str,
    env: dict,
    python: str,
    project: dict,
    langs: list,      # [] = run ALL available analyzers
    force_mode: str,  # "full" | "incremental" | "auto"
    dry_run: bool,
    verbose: bool,
    preview: bool,
) -> dict:
    """Sync one folder. langs=[] means run every analyzer that exists on disk."""
    folder_path = Path(folder) if Path(folder).is_absolute() else project_path / folder

    if not folder_path.exists():
        click.echo(f"\n[warn] Folder not found: {folder_path} — skipping")
        return {"folder": folder, "status": "skipped", "reason": "not found"}

    state = _load_state(project_path, folder)
    mode  = force_mode if force_mode != "auto" else ("incremental" if state else "full")

    # Resolve which analyzers to run
    if langs:
        targets = {l: LANG_ANALYZERS[l] for l in langs if l in LANG_ANALYZERS}
    else:
        targets = {l: p for l, p in LANG_ANALYZERS.items() if p.exists()}

    click.echo(f"\n{'─' * 52}")
    click.echo(f" folder : {folder}")
    click.echo(f" mode   : {mode}")
    click.echo(f" tools  : {', '.join(targets)}")

    # ── Incremental: detect changed/deleted via git diff ─────────────────
    changed_files, deleted_files = [], []
    if mode == "incremental":
        since_commit = state.get("git_commit", "")
        since_ts     = state.get("last_sync_ts", 0.0)

        if since_commit:
            current = _git_head(folder_path)
            if current and current == since_commit:
                click.echo("  [ok] No new commits since last sync — skipping")
                return {"folder": folder, "status": "skipped", "reason": "no changes"}
            changed_files, deleted_files = _git_status_since(folder_path, since_commit)
            click.echo(f"  git diff: {len(changed_files)} changed, {len(deleted_files)} deleted")
        elif since_ts:
            changed_files, _ = _mtime_changed_files(folder_path, since_ts)
            click.echo(f"  mtime:    {len(changed_files)} changed")
        else:
            click.echo("  [info] No baseline — switching to full")
            mode = "full"

        if mode == "incremental" and not changed_files and not deleted_files:
            click.echo("  [ok] No changes detected — skipping")
            return {"folder": folder, "status": "skipped", "reason": "no changes"}

        if preview and (changed_files or deleted_files):
            if changed_files:
                click.echo(f"\n  Changed ({len(changed_files)}):")
                for f in sorted(changed_files)[:20]:
                    click.echo(f"    + {f}")
                if len(changed_files) > 20:
                    click.echo(f"    … and {len(changed_files) - 20} more")
            if deleted_files:
                click.echo(f"\n  Deleted ({len(deleted_files)}):")
                for f in sorted(deleted_files)[:10]:
                    click.echo(f"    - {f}")
            if not click.confirm("\n  Proceed?", default=True):
                return {"folder": folder, "status": "cancelled"}

    # ── Run each analyzer ─────────────────────────────────────────────────
    start_ts     = time.time()
    lang_results = []

    for lang, analyzer in targets.items():
        if not analyzer.exists():
            click.echo(f"\n  [warn] Analyzer not found: {analyzer.name} — skipping {lang}")
            continue

        click.echo(f"\n  [{lang}] running…")
        rc = _run_analyzer(
            lang=lang,
            analyzer=analyzer,
            folder_path=folder_path,
            env=env,
            python=python,
            project=project,
            mode=mode,
            changed_files=changed_files,
            deleted_files=deleted_files,
            dry_run=dry_run,
            verbose=verbose,
        )
        lang_results.append({"lang": lang, "exit_code": rc})
        if rc != 0:
            click.echo(f"  [error] {lang} exited {rc}")

    elapsed = time.time() - start_ts
    success = bool(lang_results) and all(r["exit_code"] == 0 for r in lang_results)

    if success and not dry_run:
        _save_state(project_path, folder, {
            "folder":       folder,
            "last_sync":    datetime.now(timezone.utc).isoformat(),
            "last_sync_ts": start_ts,
            "mode":         mode,
            "git_commit":   _git_head(folder_path),
            "langs":        list(targets.keys()),
        })

    return {
        "folder":  folder,
        "status":  "ok" if success else "error",
        "mode":    mode,
        "elapsed": elapsed,
        "results": lang_results,
    }


def _print_summary(summaries: list, total_elapsed: float) -> None:
    click.echo(f"\n{'═' * 52}")
    click.echo(f"  Sync Summary  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    click.echo(f"{'─' * 52}")
    for s in summaries:
        status  = s.get("status", "?")
        folder  = s.get("folder", "?")
        mode    = s.get("mode", "")
        elapsed = s.get("elapsed", 0)
        reason  = s.get("reason", "")
        icon    = {"ok": "✓", "skipped": "↷", "cancelled": "–", "error": "✗"}.get(status, "?")
        parts   = [f"  {icon} {folder}"]
        if mode:
            parts.append(f"[{mode}]")
        if elapsed:
            parts.append(f"{elapsed:.1f}s")
        if reason:
            parts.append(f"({reason})")
        click.echo("  ".join(parts))
    ok      = sum(1 for s in summaries if s.get("status") == "ok")
    skipped = sum(1 for s in summaries if s.get("status") in ("skipped", "cancelled"))
    errors  = sum(1 for s in summaries if s.get("status") == "error")
    click.echo(f"{'─' * 52}")
    click.echo(f"  {ok} ok  {skipped} skipped  {errors} errors  total {total_elapsed:.1f}s")
    click.echo(f"{'═' * 52}")


# ---------------------------------------------------------------------------
# MCP process helpers
# ---------------------------------------------------------------------------

def _mcp_pids(pattern: str) -> list:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        return [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    except Exception:
        return []


def _mcp_uptime(pid: int) -> str:
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "etime="],
                           capture_output=True, text=True)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


def _mcp_stop_pattern(pattern: str) -> int:
    pids = _mcp_pids(pattern)
    for pid in pids:
        try:
            subprocess.run(["kill", "-TERM", str(pid)], check=False)
        except Exception:
            pass
    if pids:
        time.sleep(1)
    return len(pids)


def _load_dotenv(path: Path) -> dict:
    """Parse a .env file into a dict. Skips comments and blank lines."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _mcp_start_one(name: str, svc: dict) -> dict:
    svc_dir: Path = svc["dir"]
    python        = _venv_python(svc_dir)
    entry_script  = svc_dir / svc["cmd"][0]

    if not entry_script.exists():
        return {"name": name, "status": "error",
                "reason": f"entry not found: {entry_script}"}

    cmd = [python, str(entry_script)] + svc["cmd"][1:]

    # Inherit env and layer .env file on top
    env = {**os.environ, **_load_dotenv(svc_dir / ".env")}

    MCP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = MCP_LOG_DIR / f"dev-mcp-{name}.log"
    pid_file = MCP_LOG_DIR / f"dev-mcp-{name}.pid"

    with open(log_file, "a") as lf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(svc_dir),
            stdout=lf, stderr=lf,
            env=env,
            start_new_session=True,
        )
    pid_file.write_text(str(proc.pid))
    return {
        "name": name, "status": "started", "pid": proc.pid,
        "url": svc["url"], "log": str(log_file),
    }


def _integrate_workspace(project_path: Path, entries: dict) -> None:
    mcp_file = project_path / ".mcp.json"
    existing: dict = {}

    if mcp_file.exists():
        try:
            with open(mcp_file, encoding="utf-8") as f:
                existing = json.load(f)
        except json.JSONDecodeError:
            pass
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = mcp_file.with_suffix(f".bak.{ts}.json")
        shutil.copy2(str(mcp_file), str(bak))
        click.echo(f"  [backup] {bak.name}")

    section = existing.setdefault("mcpServers", {})
    added, updated = [], []
    for svc_name, entry in entries.items():
        if svc_name in section:
            if section[svc_name] != entry:
                section[svc_name] = entry
                updated.append(svc_name)
        else:
            section[svc_name] = entry
            added.append(svc_name)

    with open(mcp_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    click.echo(f"\n[workspace] {mcp_file}")
    if added:
        click.echo(f"  [added]   {', '.join(added)}")
    if updated:
        click.echo(f"  [updated] {', '.join(updated)}")
    if not added and not updated:
        click.echo("  [ok] already up to date")
    click.echo("  [note] Restart Claude Code / your editor to apply changes.")


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """dev - CortexHarness ingestion CLI.

    \b
    Quick start:
      dev init              # configure project + scaffold folder structure
      dev status            # show active config
      dev sync code         # interactive: pick folders, auto incremental/full
      dev sync code all     # ALL analyzers on all folders (incremental if baseline)
      dev sync doc          # ingest documents -> Neo4j + Qdrant
    """


# ---------------------------------------------------------------------------
# dev init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--env", default="dev", show_default=True,
              type=click.Choice(["dev", "prod"]),
              help="Environment to configure.")
@click.option("--project-dir", default=None,
              help="Target project root directory.")
@click.argument("path", default=None, required=False, metavar="[PATH]")
def init(env, project_dir, path):
    """Create/update config and scaffold project folder structure.

    PATH can be passed positionally, e.g. 'dev init .' to use the current directory.
    """
    project_path = Path(path or project_dir or ".").resolve()
    config_path  = _config_path(project_path, env)

    existing: dict = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            existing = json.load(f)
        click.echo(f"[info] Updating existing config: {config_path}\n")
    else:
        click.echo(f"[info] Creating new {env} config: {config_path}\n")

    def _p(label, keys: list, default=""):
        cur = existing
        for k in keys:
            cur = cur.get(k, {}) if isinstance(cur, dict) else {}
        cur_val = cur if isinstance(cur, str) else default
        return click.prompt(label, default=cur_val or default)

    click.echo("─── Project ────────────────────────────────")
    project_code = _p("Project code (short ID)", ["project", "code"], "my_project")
    project_name = _p("Project name",            ["project", "name"], project_code)

    click.echo("\n─── Code — Neo4j + Qdrant + Embedding ──────")
    code_neo4j_uri   = _p("NEO4J_URI",       ["code", "env", "NEO4J_URI"],       "bolt://localhost:7687")
    code_neo4j_db    = _p("NEO4J_DB",        ["code", "env", "NEO4J_DB"],        "neo4j")
    code_neo4j_user  = _p("NEO4J_USER",      ["code", "env", "NEO4J_USER"],      "neo4j")
    code_neo4j_pass  = _p("NEO4J_PASS",      ["code", "env", "NEO4J_PASS"],      "")
    code_qdrant_host = _p("QDRANT_HOST",     ["code", "env", "QDRANT_HOST"],     "localhost")
    code_qdrant_port = _p("QDRANT_PORT",     ["code", "env", "QDRANT_PORT"],     "6333")
    code_embed_model = _p("EMBEDDING_MODEL", ["code", "env", "EMBEDDING_MODEL"], "jinaai/jina-embeddings-v3")
    code_batch_size  = _p("BATCH_SIZE",      ["code", "env", "BATCH_SIZE"],      "1")
    code_max_chars   = _p("MAX_EMBED_CHARS", ["code", "env", "MAX_EMBED_CHARS"], "500")
    code_device      = _p("device",          ["code", "env", "device"],          "cpu")

    click.echo("\n─── Doc — Neo4j + Qdrant + Embedding ───────")
    doc_neo4j_uri   = _p("NEO4J_URI",       ["doc", "env", "NEO4J_URI"],       code_neo4j_uri)
    doc_neo4j_db    = _p("NEO4J_DB",        ["doc", "env", "NEO4J_DB"],        code_neo4j_db)
    doc_neo4j_user  = _p("NEO4J_USER",      ["doc", "env", "NEO4J_USER"],      code_neo4j_user)
    doc_neo4j_pass  = _p("NEO4J_PASS",      ["doc", "env", "NEO4J_PASS"],      code_neo4j_pass)
    doc_qdrant_host = _p("QDRANT_HOST",     ["doc", "env", "QDRANT_HOST"],     code_qdrant_host)
    doc_qdrant_port = _p("QDRANT_PORT",     ["doc", "env", "QDRANT_PORT"],     code_qdrant_port)
    doc_embed_model = _p("EMBEDDING_MODEL", ["doc", "env", "EMBEDDING_MODEL"], "BAAI/bge-m3")
    doc_batch_size  = _p("BATCH_SIZE",      ["doc", "env", "BATCH_SIZE"],      "1")
    doc_max_chars   = _p("MAX_EMBED_CHARS", ["doc", "env", "MAX_EMBED_CHARS"], "500")
    doc_device      = _p("device",          ["doc", "env", "device"],          code_device)

    # ── Code source — first project ──────────────────────────────────────────
    existing_code_projects = _source_projects(existing.get("code", {}).get("source", {}))
    first_code = existing_code_projects[0] if existing_code_projects else {}

    click.echo("\n─── Code — first project ───────────────────")
    if existing_code_projects and any(p.get("folder") for p in existing_code_projects):
        click.echo(f"  Existing projects: {len(existing_code_projects)}")
        for i, p in enumerate(existing_code_projects, 1):
            click.echo(f"    [{i}] git={p.get('git') or '(local)'}  folders={p.get('folder', [])}")
        click.echo("  (Run 'dev sync code add' to add more; editing here updates project #1 only)")

    code_git     = click.prompt("  Git URL (blank = local)", default=first_code.get("git", "") or "")
    code_folders_raw = click.prompt(
        "  Source folders (comma-separated, blank = auto-scaffold)",
        default=", ".join(f for f in first_code.get("folder", []) if f) or "",
    )

    # ── Doc source — first project ────────────────────────────────────────────
    existing_doc_projects = _source_projects(existing.get("doc", {}).get("source", {}))
    first_doc = existing_doc_projects[0] if existing_doc_projects else {}

    click.echo("\n─── Doc — first project ────────────────────")
    if existing_doc_projects and any(p.get("folder") for p in existing_doc_projects):
        click.echo(f"  Existing projects: {len(existing_doc_projects)}")
        for i, p in enumerate(existing_doc_projects, 1):
            click.echo(f"    [{i}] git={p.get('git') or '(local)'}  folders={p.get('folder', [])}")
        click.echo("  (Run 'dev sync doc add' to add more; editing here updates project #1 only)")

    doc_git      = click.prompt("  Git URL (blank = local)", default=first_doc.get("git", "") or "")
    doc_folders_raw = click.prompt(
        "  Doc folders (comma-separated, blank = auto-scaffold)",
        default=", ".join(f for f in first_doc.get("folder", []) if f) or "",
    )

    # ── Scaffold / resolve folders ────────────────────────────────────────────
    if code_folders_raw.strip() or doc_folders_raw.strip():
        code_folders = [f.strip() for f in code_folders_raw.split(",") if f.strip()]
        doc_folders  = [f.strip() for f in doc_folders_raw.split(",")  if f.strip()]
    else:
        click.echo("")
        doc_folders, code_folders = _scaffold_project(project_path)

    # ── Merge: keep existing extra projects, replace/set project #1 ──────────
    new_first_code = {"git": code_git, "folder": code_folders}
    if len(existing_code_projects) > 1:
        code_projects = [new_first_code] + existing_code_projects[1:]
    else:
        code_projects = [new_first_code]

    new_first_doc = {"git": doc_git, "folder": doc_folders}
    if len(existing_doc_projects) > 1:
        doc_projects = [new_first_doc] + existing_doc_projects[1:]
    else:
        doc_projects = [new_first_doc]

    cfg = {
        "active": True,
        "project": {"code": project_code, "name": project_name},
        "code": {
            "env": {
                "NEO4J_URI":       code_neo4j_uri,   "NEO4J_DB":   code_neo4j_db,
                "NEO4J_USER":      code_neo4j_user,  "NEO4J_PASS": code_neo4j_pass,
                "QDRANT_HOST":     code_qdrant_host, "QDRANT_PORT": code_qdrant_port,
                "EMBEDDING_MODEL": code_embed_model, "BATCH_SIZE": code_batch_size,
                "MAX_EMBED_CHARS": code_max_chars,   "device": code_device,
            },
            "source": {"projects": code_projects},
        },
        "doc": {
            "env": {
                "NEO4J_URI":       doc_neo4j_uri,   "NEO4J_DB":   doc_neo4j_db,
                "NEO4J_USER":      doc_neo4j_user,  "NEO4J_PASS": doc_neo4j_pass,
                "QDRANT_HOST":     doc_qdrant_host, "QDRANT_PORT": doc_qdrant_port,
                "EMBEDDING_MODEL": doc_embed_model, "BATCH_SIZE": doc_batch_size,
                "MAX_EMBED_CHARS": doc_max_chars,   "device": doc_device,
            },
            "source": {"projects": doc_projects},
        },
    }

    _deactivate_other_envs(project_path, env)
    _save_config(cfg, config_path)
    click.echo(f"\n[ok] Environment '{env}' is now active.")
    click.echo(f"     Code projects : {len(code_projects)}  "
               f"(total {len(_source_folders({'projects': code_projects}))} folders)")
    click.echo(f"     Doc  projects : {len(doc_projects)}  "
               f"(total {len(_source_folders({'projects': doc_projects}))} folders)")
    click.echo("     Tip: 'dev sync code add' / 'dev sync doc add' to add more projects.")


# ---------------------------------------------------------------------------
# dev status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--project-dir", default=".", show_default=True)
def status(project_dir):
    """Show active config and all available environments."""
    project_path = Path(project_dir).resolve()
    cfg_dir = _config_dir(project_path)

    if not cfg_dir.exists():
        click.echo("[error] No config directory found. Run 'dev init' first.", err=True)
        sys.exit(1)

    envs = sorted(cfg_dir.glob("*.json"))
    click.echo(f"\nProject dir : {project_path}")
    click.echo(f"Config dir  : {cfg_dir}\n")
    click.echo("Environments:")
    for p in envs:
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        marker = " [ACTIVE]" if c.get("active") else ""
        click.echo(f"  {p.name}{marker}")

    cfg, active_path = _load_active_config(project_path)
    proj = cfg.get("project", {})
    # Use ASCII-compatible characters for Windows compatibility
    click.echo(f"\n--- Active: {active_path.name} -----------------------------")
    click.echo(f"Project     : {proj.get('name')} ({proj.get('code')})")

    for section in ("code", "doc"):
        sec = cfg.get(section, {})
        env = sec.get("env", {})
        src = sec.get("source", {})
        projects = _source_projects(src)
        click.echo(f"\n[{section}]")
        click.echo(f"  Neo4j     : {env.get('NEO4J_URI')}  db={env.get('NEO4J_DB')}")
        click.echo(f"  Qdrant    : {env.get('QDRANT_HOST')}:{env.get('QDRANT_PORT')}")
        click.echo(f"  Embedding : {env.get('EMBEDDING_MODEL')}  device={env.get('device')}")
        click.echo(f"  Projects  : {len(projects)}")
        for i, p in enumerate(projects, 1):
            git_label = p.get("git") or "(local)"
            folders   = [f for f in p.get("folder", []) if f]
            click.echo(f"    [{i}] {git_label}  ({len(folders)} folder(s))")
            for f in folders:
                click.echo(f"         • {f}")


# ---------------------------------------------------------------------------
# dev sync
# ---------------------------------------------------------------------------

@cli.group()
def sync():
    """Sync code or documents into Neo4j + Qdrant."""


# ── sync code ────────────────────────────────────────────────────────────────

@sync.group("code", invoke_without_command=True)
@click.option("--project-dir", default=".", show_default=True)
@click.option("--preview", is_flag=True, help="Preview changed files before syncing.")
@click.option("--verbose/--no-verbose", default=True, show_default=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sync_code(ctx, project_dir, preview, verbose, dry_run):
    """Interactive: pick folders, auto-detect language, incremental if baseline exists.

    \b
    First run   -> full sync (no baseline)
    Next runs   -> incremental via analyzer --changed-files-manifest (built-in)
    Sub-command:
      all       Run ALL analyzers on all folders (incremental if baseline exists).
    """
    ctx.ensure_object(dict)
    ctx.obj.update(project_dir=project_dir, preview=preview, verbose=verbose, dry_run=dry_run)

    if ctx.invoked_subcommand is not None:
        return

    project_path = Path(project_dir).resolve()
    cfg, _   = _load_active_config(project_path)
    code_cfg = cfg.get("code", {})
    env      = code_cfg.get("env", {})
    project  = cfg.get("project", {})
    folders  = _source_folders(code_cfg.get("source", {}))

    if not folders:
        click.echo("[warn] No source folders configured. Run 'dev init' or 'dev sync code add'.")
        return

    selected = _select_folders_interactive(folders)
    if not selected:
        click.echo("[info] No folders selected.")
        return

    incremental_sync = CODE_TINY / "tools" / "sync" / "incremental_sync.py"
    python           = _venv_python(CODE_TINY)
    summaries        = []
    total_start      = time.time()

    for folder in selected:
        folder_path = Path(folder) if Path(folder).is_absolute() else project_path / folder
        if not folder_path.exists():
            click.echo(f"\n[warn] Folder not found: {folder} — skipping")
            continue

        cmd = [
            python, str(incremental_sync),
            "--root", str(folder_path),
            "--project-id", project.get("code", project.get("name", "project")),
            "--project-name", project.get("name", "project"),
            "--python-bin", python,
            *_neo4j_args_code(env),
            "--qdrant-url", _env_to_qdrant_url(env),
        ]
        if dry_run:
            cmd.append("--verbose")
            click.echo(f"\n[dry-run] {' '.join(cmd)}")
            summaries.append({"folder": folder, "status": "dry_run"})
            continue
        if verbose:
            cmd.append("--verbose")

        start = time.time()
        rc = _run_with_retry(cmd, dry_run=False)
        elapsed = time.time() - start
        summaries.append({
            "folder": folder,
            "status": "ok" if rc == 0 else "error",
            "elapsed": elapsed,
            "exit_code": rc,
        })

    _print_summary(summaries, time.time() - total_start)


@sync_code.command("all")
@click.pass_context
def sync_code_all(ctx):
    """Run ALL available analyzers on every configured folder.

    \b
    - Every tool in LANG_ANALYZERS that exists on disk is invoked.
    - Each analyzer filters its own file types internally.
    - Incremental if a sync baseline exists, full sync on first run.
    - Changed/deleted files are passed via --changed-files-manifest
      to the analyzer's built-in incremental engine.
    """
    o            = ctx.obj
    project_path = Path(o["project_dir"]).resolve()
    cfg, _       = _load_active_config(project_path)
    code_cfg     = cfg.get("code", {})
    env          = code_cfg.get("env", {})
    project      = cfg.get("project", {})
    folders      = _source_folders(code_cfg.get("source", {}))

    if not folders:
        click.echo("[warn] No source folders configured. Run 'dev init' or 'dev sync code add'.")
        return

    available = [l for l, p in LANG_ANALYZERS.items() if p.exists()]
    click.echo(f"\n[sync-code all]  folders={len(folders)}  analyzers={len(available)}")
    click.echo(f"  tools: {', '.join(available)}")

    incremental_sync = CODE_TINY / "tools" / "sync" / "incremental_sync.py"
    python      = _venv_python(CODE_TINY)
    summaries   = []
    total_start = time.time()

    for folder in folders:
        folder_path = Path(folder) if Path(folder).is_absolute() else project_path / folder
        if not folder_path.exists():
            click.echo(f"[warn] Folder not found: {folder} — skipping")
            continue

        cmd = [
            python, str(incremental_sync),
            "--root", str(folder_path),
            "--project-id", project.get("code", project.get("name", "project")),
            "--project-name", project.get("name", "project"),
            "--python-bin", python,
            *_neo4j_args_code(env),
            "--qdrant-url", _env_to_qdrant_url(env),
        ]
        if o["dry_run"]:
            cmd.append("--verbose")
            click.echo(f"[dry-run] {' '.join(cmd)}")
            summaries.append({"folder": folder, "status": "dry_run"})
            continue
        if o["verbose"]:
            cmd.append("--verbose")

        start = time.time()
        rc = _run_with_retry(cmd, dry_run=False)
        elapsed = time.time() - start
        summaries.append({
            "folder": folder,
            "status": "ok" if rc == 0 else "error",
            "elapsed": elapsed,
            "exit_code": rc,
        })

    _print_summary(summaries, time.time() - total_start)


# ── sync doc ─────────────────────────────────────────────────────────────────

@sync.group("doc", invoke_without_command=True)
@click.option("--project-dir", default=".", show_default=True)
@click.option("--preview", is_flag=True, help="Preview changed files before syncing.")
@click.option("--entity-provider", default="gliner", show_default=True,
              help="Entity extraction provider: gliner / langextract / spacy")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sync_doc(ctx, project_dir, preview, entity_provider, dry_run):
    """Interactive: pick doc folders, incremental if baseline exists.

    \b
    First run   -> full sync (no baseline)
    Next runs   -> incremental (git diff > hash comparison > mtime)
    Sub-command:
      all       Full sync for all configured doc folders.
    """
    ctx.ensure_object(dict)
    ctx.obj.update(project_dir=project_dir, preview=preview,
                   entity_provider=entity_provider, dry_run=dry_run)

    if ctx.invoked_subcommand is not None:
        return

    project_path = Path(project_dir).resolve()
    cfg, _  = _load_active_config(project_path)
    doc_cfg = cfg.get("doc", {})
    env     = doc_cfg.get("env", {})
    project = cfg.get("project", {})
    folders = _source_folders(doc_cfg.get("source", {}))

    if not folders:
        click.echo("[warn] No doc folders configured. Run 'dev init' or 'dev sync doc add'.")
        return

    if not DOC_INGESTOR.exists():
        click.echo(f"[error] Ingestor not found: {DOC_INGESTOR}", err=True)
        sys.exit(1)

    selected = _select_folders_interactive(folders)
    if not selected:
        click.echo("[info] No folders selected.")
        return

    python      = _venv_python(DOC_TINY)
    summaries   = []
    total_start = time.time()

    for folder in selected:
        result = _sync_doc_folder(
            project_path=project_path,
            folder=folder,
            env=env,
            python=python,
            project=project,
            force_mode="auto",
            entity_provider=entity_provider,
            dry_run=dry_run,
            preview=preview,
        )
        summaries.append(result)

    _print_summary(summaries, time.time() - total_start)


@sync_doc.command("all")
@click.pass_context
def sync_doc_all(ctx):
    """Full sync for all configured doc folders.

    \b
    Pushes every doc file through the doc-tiny pipeline.
    Uses incremental if a sync baseline exists, full sync on first run.
    """
    o            = ctx.obj
    project_path = Path(o["project_dir"]).resolve()
    cfg, _       = _load_active_config(project_path)
    doc_cfg      = cfg.get("doc", {})
    env          = doc_cfg.get("env", {})
    project      = cfg.get("project", {})
    folders      = _source_folders(doc_cfg.get("source", {}))

    if not folders:
        click.echo("[warn] No doc folders configured. Run 'dev init' or 'dev sync doc add'.")
        return

    if not DOC_INGESTOR.exists():
        click.echo(f"[error] Ingestor not found: {DOC_INGESTOR}", err=True)
        sys.exit(1)

    click.echo(f"\n[sync-doc all]  folders={len(folders)}")

    python      = _venv_python(DOC_TINY)
    summaries   = []
    total_start = time.time()

    for folder in folders:
        result = _sync_doc_folder(
            project_path=project_path,
            folder=folder,
            env=env,
            python=python,
            project=project,
            force_mode="full",
            entity_provider=o["entity_provider"],
            dry_run=o["dry_run"],
            preview=False,
        )
        summaries.append(result)

    _print_summary(summaries, time.time() - total_start)


# ── sync code add ─────────────────────────────────────────────────────────────

@sync_code.command("add")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--git", "git_url", default=None, help="Git remote URL (blank = local).")
@click.argument("folders", nargs=-1, required=False, metavar="[FOLDER...]")
def sync_code_add(project_dir, git_url, folders):
    """Add a new source project to code.source.projects.

    \b
    Folders can be passed as positional arguments or entered interactively.
    Examples:
      dev sync code add /path/to/src
      dev sync code add /path/a /path/b --git https://github.com/org/repo.git
    """
    project_path = Path(project_dir).resolve()
    cfg, cfg_path = _load_active_config(project_path)

    existing_projects = _source_projects(cfg.get("code", {}).get("source", {}))
    click.echo(f"\n─── Add code project  (current: {len(existing_projects)}) ───")
    for i, p in enumerate(existing_projects, 1):
        git_label = p.get("git") or "(local)"
        click.echo(f"  [{i}] {git_label}  folders={p.get('folder', [])}")

    click.echo("")
    if git_url is None:
        git_url = click.prompt("  Git URL (blank = local)", default="")
    if not folders:
        folders_raw = click.prompt("  Source folders (comma-separated)")
        folders     = [f.strip() for f in folders_raw.split(",") if f.strip()]
    else:
        folders = list(folders)

    if not folders:
        click.echo("[error] At least one folder is required.", err=True)
        sys.exit(1)

    new_project = {"git": git_url, "folder": folders}
    existing_projects.append(new_project)
    cfg.setdefault("code", {}).setdefault("source", {})["projects"] = existing_projects
    # remove old flat keys if present (migration)
    cfg["code"]["source"].pop("git", None)
    cfg["code"]["source"].pop("folder", None)

    _save_config(cfg, cfg_path)
    click.echo(f"\n[ok] Added project #{len(existing_projects)}: {git_url or '(local)'}  {folders}")
    click.echo(f"     Total code projects: {len(existing_projects)}  "
               f"({len(_source_folders(cfg['code']['source']))} folders)")


# ── sync doc add ──────────────────────────────────────────────────────────────

@sync_doc.command("add")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--git", "git_url", default=None, help="Git remote URL (blank = local).")
@click.argument("folders", nargs=-1, required=False, metavar="[FOLDER...]")
def sync_doc_add(project_dir, git_url, folders):
    """Add a new doc project to doc.source.projects.

    \b
    Folders can be passed as positional arguments or entered interactively.
    Examples:
      dev sync doc add /path/to/docs
      dev sync doc add /path/a /path/b --git https://github.com/org/repo.git
    """
    project_path = Path(project_dir).resolve()
    cfg, cfg_path = _load_active_config(project_path)

    existing_projects = _source_projects(cfg.get("doc", {}).get("source", {}))
    click.echo(f"\n─── Add doc project  (current: {len(existing_projects)}) ───")
    for i, p in enumerate(existing_projects, 1):
        git_label = p.get("git") or "(local)"
        click.echo(f"  [{i}] {git_label}  folders={p.get('folder', [])}")

    click.echo("")
    if git_url is None:
        git_url = click.prompt("  Git URL (blank = local)", default="")
    if not folders:
        folders_raw = click.prompt("  Doc folders (comma-separated)")
        folders     = [f.strip() for f in folders_raw.split(",") if f.strip()]
    else:
        folders = list(folders)

    if not folders:
        click.echo("[error] At least one folder is required.", err=True)
        sys.exit(1)

    new_project = {"git": git_url, "folder": folders}
    existing_projects.append(new_project)
    cfg.setdefault("doc", {}).setdefault("source", {})["projects"] = existing_projects
    # remove old flat keys if present (migration)
    cfg["doc"]["source"].pop("git", None)
    cfg["doc"]["source"].pop("folder", None)

    _save_config(cfg, cfg_path)
    click.echo(f"\n[ok] Added project #{len(existing_projects)}: {git_url or '(local)'}  {folders}")
    click.echo(f"     Total doc projects: {len(existing_projects)}  "
               f"({len(_source_folders(cfg['doc']['source']))} folders)")


# ---------------------------------------------------------------------------
# dev mcp
# ---------------------------------------------------------------------------

@cli.group()
def mcp():
    """Start and integrate MCP servers (code-tiny + doc-tiny)."""


@mcp.command("start")
@click.option("--force-restart", is_flag=True,
              help="Kill existing instances before starting.")
def mcp_start(force_restart):
    """Start MCP servers for code-tiny (port 8788) and doc-tiny (port 8789).

    \b
    Both servers run in the background (non-blocking).
    If already running, displays PID and uptime — use --force-restart to reload.
    Logs: .cache/dev-mcp-<name>.log
    """
    for name, svc in MCP_SERVICES.items():
        pattern = svc["pattern"]
        pids    = _mcp_pids(pattern)

        click.echo(f"\n── {name} (port {svc['port']}) {'─' * 30}")

        if pids and not force_restart:
            uptime = _mcp_uptime(pids[0])
            click.echo(f"  [running]  pid={pids[0]}  uptime={uptime}")
            click.echo(f"  [url]      {svc['url']}")
            continue

        if pids and force_restart:
            stopped = _mcp_stop_pattern(pattern)
            click.echo(f"  [stopped]  killed {stopped} process(es)")

        click.echo(f"  [starting] {svc['cmd'][0]}")
        result = _mcp_start_one(name, svc)

        if result["status"] == "started":
            click.echo(f"  [ok]  url={svc['url']}")
            click.echo(f"  [pid] {result['pid']}")
            click.echo(f"  [log] {result['log']}")
        else:
            click.echo(f"  [error] {result.get('reason', '?')}", err=True)


@mcp.command("add")
@click.option("--scope", default="workspace",
              type=click.Choice(["global", "workspace"]), show_default=True,
              help="'workspace' writes .mcp.json; 'global' updates agent config files.")
@click.option("--agent", default="all", show_default=True,
              help="Target: claude / claude-code / vscode / cursor / all")
@click.option("--project-dir", default=".", show_default=True)
def mcp_add(scope, agent, project_dir):
    """Register MCP endpoints into agent configurations.

    \b
    Scope workspace  → writes .mcp.json in the project root (Claude Code picks this up).
    Scope global     → patches system-wide agent config files (Claude Desktop, VS Code, Cursor…).
    Config files are backed up before modification.
    """
    project_path = Path(project_dir).resolve()
    configs      = _agent_configs()

    entries = {
        name: {"type": "http", "url": svc["url"]}
        for name, svc in MCP_SERVICES.items()
    }

    # ── workspace scope ───────────────────────────────────────────────────
    if scope == "workspace":
        _integrate_workspace(project_path, entries)
        return

    # ── global scope ──────────────────────────────────────────────────────
    click.echo("\n[warn] Modifying system-wide agent configuration files.\n")

    if agent == "all":
        targets = list(configs.keys())
    elif agent in configs:
        targets = [agent]
    else:
        click.echo(
            f"[error] Unknown agent '{agent}'. "
            f"Choose from: {', '.join(configs)} or 'all'", err=True
        )
        sys.exit(1)

    for agent_name in targets:
        cfg_info = configs[agent_name]
        cfg_path = cfg_info["path"]
        key      = cfg_info["key"]

        click.echo(f"── {agent_name} {'─' * 40}")

        if not cfg_path.parent.exists():
            click.echo(f"  [skip] directory not found: {cfg_path.parent}")
            continue

        existing: dict = {}
        if cfg_path.exists():
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except json.JSONDecodeError:
                click.echo(f"  [warn] could not parse {cfg_path.name} — patching section only")
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = cfg_path.with_suffix(f".bak.{ts}.json")
            shutil.copy2(str(cfg_path), str(bak))
            click.echo(f"  [backup]  {bak.name}")
        else:
            click.echo(f"  [create]  {cfg_path}")

        section = existing.setdefault(key, {})
        added, updated = [], []
        for svc_name, entry in entries.items():
            if svc_name in section:
                if section[svc_name] != entry:
                    section[svc_name] = entry
                    updated.append(svc_name)
            else:
                section[svc_name] = entry
                added.append(svc_name)

        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        if added:
            click.echo(f"  [added]   {', '.join(added)}")
        if updated:
            click.echo(f"  [updated] {', '.join(updated)}")
        if not added and not updated:
            click.echo("  [ok] already up to date")
        click.echo(f"  [saved]   {cfg_path}\n")


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def _harness_dir(project_dir: Path) -> Path:
    return project_dir / ".harness"


def _harness_state(project_dir: Path) -> Path:
    return project_dir / ".harness" / "state"


def _harness_feature_list(project_dir: Path) -> Path:
    return _harness_state(project_dir) / "feature_list.json"


def _harness_load_features(project_dir: Path) -> dict:
    p = _harness_feature_list(project_dir)
    if not p.exists():
        click.echo(f"[error] No feature_list.json at '{p}'. Run 'dev harness init' first.", err=True)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _harness_save_features(project_dir: Path, payload: dict) -> None:
    p = _harness_feature_list(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _harness_next_task_id(features: list) -> str:
    nums = []
    for t in features:
        tid = str(t.get("id", ""))
        if tid.startswith("task-"):
            try:
                nums.append(int(tid[5:]))
            except ValueError:
                pass
    n = (max(nums) + 1) if nums else 1
    return f"task-{n:03d}"


def _harness_probe_mcp(url: str) -> str:
    """Quick HTTP probe of an MCP endpoint. Returns status string."""
    if not url:
        return "not configured"
    import urllib.request, urllib.error
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "probe", "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "dev-harness-probe", "version": "0.1"}},
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return f"ok (HTTP {resp.status})"
    except Exception as exc:
        return f"unreachable ({exc})"


def _harness_read_config_yaml(project_dir: Path) -> dict:
    """Read .harness/config.yaml using the simple YAML parser from orchestrator."""
    cfg_path = _harness_dir(project_dir) / "config.yaml"
    if not cfg_path.exists():
        return {}

    root: dict = {}
    stack: list = [(-1, root)]

    with open(cfg_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.lstrip().startswith("-"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            content = line.strip()
            if ":" not in content:
                continue
            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1] if stack else root
            if value == "":
                node: dict = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = value
    return root


# ---------------------------------------------------------------------------
# dev harness
# ---------------------------------------------------------------------------

@cli.group()
def harness():
    """Manage AI agent task sessions (harness orchestration layer).

    \b
    Bootstrap:
      dev harness init          Set up .harness/ in a project
    Task management:
      dev harness task list     List all tasks
      dev harness task add      Add a new task
      dev harness task show     Show full task details
    Execution:
      dev harness run           Run orchestrator session
      dev harness context       Select context for a task
      dev harness verify        Run verify gate
      dev harness status        Show backlog summary + MCP health
    """


@harness.command("init")
@click.option("--project-dir", default=".", show_default=True,
              help="Target project root to bootstrap .harness/ in.")
def harness_init(project_dir):
    """Bootstrap .harness/ structure in a target project.

    \b
    Copies scripts and templates from the cortex-harness repo,
    creates state directories, and writes config.yaml.
    Existing files are NOT overwritten.
    """
    project_path = Path(project_dir).resolve()
    h_dir = _harness_dir(project_path)

    if not HARNESS_SCRIPTS.exists():
        click.echo(f"[error] harness/scripts not found at '{HARNESS_SCRIPTS}'", err=True)
        sys.exit(1)

    click.echo(f"\n─── Bootstrapping .harness/ in: {project_path} ───")

    # Create directories
    for sub in ["scripts", "templates", "state", "state/session_log"]:
        d = h_dir / sub
        d.mkdir(parents=True, exist_ok=True)

    # Copy scripts
    for script in ["init.sh", "verify.sh", "context_selector.py", "orchestrator.py"]:
        src = HARNESS_SCRIPTS / script
        dst = h_dir / "scripts" / script
        if dst.exists():
            click.echo(f"  [skip]   scripts/{script} (already exists)")
        else:
            shutil.copy2(str(src), str(dst))
            if script.endswith(".sh"):
                dst.chmod(dst.stat().st_mode | 0o111)
            click.echo(f"  [copied] scripts/{script}")

    # Copy templates
    for tmpl in ["feature_template.json", "session_template.json", "AGENT.md"]:
        src = HARNESS_TEMPLATES / tmpl
        dst = h_dir / "templates" / tmpl
        if dst.exists():
            click.echo(f"  [skip]   templates/{tmpl} (already exists)")
        else:
            shutil.copy2(str(src), str(dst))
            click.echo(f"  [copied] templates/{tmpl}")

    # Initial feature_list.json — with example task showing all required fields
    fl = _harness_feature_list(project_path)
    if fl.exists():
        click.echo(f"  [skip]   state/feature_list.json (already exists)")
    else:
        tmpl_fl = HARNESS_TEMPLATES / "state" / "feature_list.json"
        if tmpl_fl.exists():
            shutil.copy2(str(tmpl_fl), str(fl))
        else:
            with open(fl, "w", encoding="utf-8") as f:
                json.dump({"features": []}, f, indent=2, ensure_ascii=False)
                f.write("\n")
        click.echo(f"  [created] state/feature_list.json")

    # progress.md — session continuity log
    progress_md = h_dir / "state" / "progress.md"
    if progress_md.exists():
        click.echo(f"  [skip]   state/progress.md (already exists)")
    else:
        tmpl_prog = HARNESS_TEMPLATES / "progress.md"
        if tmpl_prog.exists():
            shutil.copy2(str(tmpl_prog), str(progress_md))
            click.echo(f"  [copied] state/progress.md")

    # session-handoff.md — handoff template
    handoff_tmpl = h_dir / "templates" / "session-handoff.md"
    if handoff_tmpl.exists():
        click.echo(f"  [skip]   templates/session-handoff.md (already exists)")
    else:
        tmpl_ho = HARNESS_TEMPLATES / "session-handoff.md"
        if tmpl_ho.exists():
            shutil.copy2(str(tmpl_ho), str(handoff_tmpl))
            click.echo(f"  [copied] templates/session-handoff.md")

    # config.yaml
    cfg_path = h_dir / "config.yaml"
    if cfg_path.exists():
        click.echo(f"  [skip]   config.yaml (already exists)")
    else:
        click.echo("\n─── MCP configuration ──────────────────────────")
        graph_url = click.prompt("  graph_mcp_url (code-tiny)",
                                 default="http://127.0.0.1:8788/mcp")
        mind_url  = click.prompt("  mind_mcp_url  (doc-tiny)",
                                 default="http://127.0.0.1:8789/mcp")
        click.echo("\n─── Verify commands (blank = skip) ─────────────")
        test_cmd  = click.prompt("  critical test_cmd", default="")
        lint_cmd  = click.prompt("  critical lint_cmd", default="")
        type_cmd  = click.prompt("  critical type_cmd", default="")

        tmpl = HARNESS_TEMPLATES / "config.yaml"
        cfg_text = tmpl.read_text(encoding="utf-8")
        cfg_text = cfg_text.replace(
            'graph_mcp_url: "http://127.0.0.1:8788/mcp"',
            f'graph_mcp_url: "{graph_url}"',
        ).replace(
            'mind_mcp_url: "http://127.0.0.1:8789/mcp"',
            f'mind_mcp_url: "{mind_url}"',
        ).replace(
            '    test_cmd: ""', f'    test_cmd: "{test_cmd}"',
        ).replace(
            '    lint_cmd: ""', f'    lint_cmd: "{lint_cmd}"',
        ).replace(
            '    type_cmd: ""', f'    type_cmd: "{type_cmd}"',
        )
        cfg_path.write_text(cfg_text, encoding="utf-8")
        click.echo(f"  [created] config.yaml")

    # .claude/settings.json — project-level Claude Code hooks
    claude_dir      = project_path / ".claude"
    claude_settings = claude_dir / "settings.json"
    tmpl_claude_settings = HARNESS_TEMPLATES / ".claude" / "settings.json"
    if claude_settings.exists():
        click.echo(f"  [skip]   .claude/settings.json (already exists)")
    elif tmpl_claude_settings.exists():
        claude_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(tmpl_claude_settings), str(claude_settings))
        click.echo(f"  [created] .claude/settings.json (Claude Code hooks)")
    else:
        click.echo(f"  [skip]   .claude/settings.json (template not found)")

    # harness_manifest.json — bootstrap record
    manifest_path = h_dir / "harness_manifest.json"
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_path),
        "cortex_harness_version": "cli",
        "subsystems": ["instructions", "state", "verification", "scope", "lifecycle"],
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    click.echo(f"  [created] harness_manifest.json")

    click.echo(f"\n[ok] .harness/ is ready at: {h_dir}")
    click.echo("     Next steps:")
    click.echo("       dev harness task add        # add your first task")
    click.echo("       dev mcp start               # start MCP servers")
    click.echo("       dev harness run             # run orchestrator")
    click.echo("       /cortex-harness-session     # invoke skill at session start")


@harness.command("status")
@click.option("--project-dir", default=".", show_default=True)
def harness_status(project_dir):
    """Show task backlog summary and MCP endpoint health."""
    project_path = Path(project_dir).resolve()

    payload  = _harness_load_features(project_path)
    features = payload.get("features", [])

    counts: dict = {}
    for t in features:
        s = t.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1

    click.echo(f"\n── Harness status: {project_path} ─────────────────")
    click.echo(f"  Tasks total : {len(features)}")
    for status in ("todo", "in_progress", "done", "blocked"):
        n = counts.get(status, 0)
        click.echo(f"  {status:<12}: {n}")

    if features:
        in_prog = [t for t in features if t.get("status") == "in_progress"]
        if in_prog:
            t = in_prog[0]
            click.echo(f"\n  In-progress : [{t['id']}] {t.get('title', '')}")

    cfg = _harness_read_config_yaml(project_path)
    mcp_cfg = cfg.get("mcp", {})
    graph_url = mcp_cfg.get("graph_mcp_url", "http://127.0.0.1:8788/mcp")
    mind_url  = mcp_cfg.get("mind_mcp_url",  "http://127.0.0.1:8789/mcp")

    click.echo(f"\n── MCP endpoints ───────────────────────────────────")
    click.echo(f"  graph_mcp  {graph_url}")
    click.echo(f"             → {_harness_probe_mcp(graph_url)}")
    click.echo(f"  mind_mcp   {mind_url}")
    click.echo(f"             → {_harness_probe_mcp(mind_url)}")


# ── harness task sub-group ────────────────────────────────────────────────────

@harness.group("task")
def harness_task():
    """Manage tasks in the harness backlog."""


@harness_task.command("list")
@click.option("--project-dir", default=".", show_default=True)
def harness_task_list(project_dir):
    """List all tasks in the backlog."""
    project_path = Path(project_dir).resolve()
    payload  = _harness_load_features(project_path)
    features = payload.get("features", [])

    if not features:
        click.echo("[info] No tasks yet. Use 'dev harness task add' to create one.")
        return

    STATUS_ICON = {"todo": "○", "in_progress": "◎", "done": "✓", "blocked": "✗"}
    click.echo(f"\n{'ID':<12} {'ST':<2} {'P':<3} {'TYPE':<10} TITLE")
    click.echo("─" * 70)
    for t in features:
        sid  = STATUS_ICON.get(t.get("status", ""), "?")
        click.echo(
            f"{t.get('id', '?'):<12} {sid:<2} {str(t.get('priority', '')):<3}"
            f" {t.get('type', ''):<10} {t.get('title', '')}"
        )


@harness_task.command("add")
@click.option("--project-dir", default=".", show_default=True)
def harness_task_add(project_dir):
    """Add a new task to the backlog interactively."""
    project_path = Path(project_dir).resolve()
    payload  = _harness_load_features(project_path)
    features = payload.get("features", [])

    task_id = _harness_next_task_id(features)

    click.echo(f"\n─── Add task {task_id} ─────────────────────────────────")
    title       = click.prompt("  Title")
    task_type   = click.prompt("  Type", default="feature",
                               type=click.Choice(["feature", "bugfix", "refactor"]))
    priority    = click.prompt("  Priority", default=1, type=int)
    entry_node  = click.prompt("  Graph entry node (namespace.Symbol)", default="")
    modules_raw = click.prompt("  Related modules (comma-separated)", default="")
    files_raw   = click.prompt("  Related files   (comma-separated)", default="")
    notes       = click.prompt("  Notes", default="")

    related_modules = [m.strip() for m in modules_raw.split(",") if m.strip()]
    related_files   = [f.strip() for f in files_raw.split(",") if f.strip()]

    task = {
        "id":               task_id,
        "title":            title,
        "type":             task_type,
        "status":           "todo",
        "priority":         priority,
        "graph_entry_node": entry_node or None,
        "related_modules":  related_modules,
        "related_files":    related_files,
        "notes":            notes,
        "session_id":       None,
    }
    features.append(task)
    payload["features"] = features
    _harness_save_features(project_path, payload)
    click.echo(f"\n[ok] Task '{task_id}' added → {_harness_feature_list(project_path)}")


@harness_task.command("show")
@click.argument("task_id")
@click.option("--project-dir", default=".", show_default=True)
def harness_task_show(task_id, project_dir):
    """Show full details for a task."""
    project_path = Path(project_dir).resolve()
    payload  = _harness_load_features(project_path)
    features = payload.get("features", [])

    for t in features:
        if t.get("id") == task_id:
            click.echo(json.dumps(t, indent=2, ensure_ascii=False))
            return

    click.echo(f"[error] Task '{task_id}' not found.", err=True)
    sys.exit(1)


@harness.command("run")
@click.option("--task-id", default=None, help="Run a specific task ID (default: next todo).")
@click.option("--max-rounds", default=None, type=int,
              help="Override max_rounds from config.")
@click.option("--agent-command", default="",
              help="Shell command that invokes the agent (if used as sub-process).")
@click.option("--project-dir", default=".", show_default=True)
def harness_run(task_id, max_rounds, agent_command, project_dir):
    """Run an orchestrator session for the next todo task (or --task-id).

    \b
    Delegates entirely to .harness/scripts/orchestrator.py.
    Session log is written to .harness/state/session_log/<session-id>.json.
    """
    project_path = Path(project_dir).resolve()
    orchestrator = project_path / ".harness" / "scripts" / "orchestrator.py"

    if not orchestrator.exists():
        click.echo(f"[error] orchestrator.py not found. Run 'dev harness init' first.", err=True)
        sys.exit(1)

    cmd = [sys.executable, str(orchestrator),
           "--root",     str(project_path),
           "--config",   ".harness/config.yaml",
           "--state",    ".harness/state/feature_list.json",
           "--progress", ".harness/state/progress.md",
           "--session-log-dir", ".harness/state/session_log"]

    if task_id:
        cmd += ["--task-id", task_id]
    if max_rounds is not None:
        cmd += ["--max-rounds", str(max_rounds)]
    if agent_command:
        cmd += ["--agent-command", agent_command]

    click.echo(f"[harness run] {' '.join(cmd[:6])} …")
    rc = subprocess.run(cmd, cwd=str(project_path)).returncode
    sys.exit(rc)


@harness.command("context")
@click.argument("task_id")
@click.option("--output", default="-", show_default=True,
              help="Output file path, or '-' for stdout.")
@click.option("--project-dir", default=".", show_default=True)
def harness_context(task_id, output, project_dir):
    """Run context_selector.py for a task and print the result.

    \b
    Queries graph_mcp + mind_mcp using settings from .harness/config.yaml.
    """
    project_path = Path(project_dir).resolve()
    selector = project_path / ".harness" / "scripts" / "context_selector.py"

    if not selector.exists():
        click.echo(f"[error] context_selector.py not found. Run 'dev harness init' first.", err=True)
        sys.exit(1)

    cfg = _harness_read_config_yaml(project_path)
    mcp = cfg.get("mcp", {})

    cmd = [
        sys.executable, str(selector),
        "--state",       ".harness/state/feature_list.json",
        "--task-id",     task_id,
        "--output",      output,
        "--graph-mcp-url", mcp.get("graph_mcp_url", "http://127.0.0.1:8788/mcp"),
        "--mind-mcp-url",  mcp.get("mind_mcp_url",  "http://127.0.0.1:8789/mcp"),
    ]
    graph_tool = mcp.get("graph_mcp_tool", "")
    mind_tool  = mcp.get("mind_mcp_tool", "")
    if graph_tool:
        cmd += ["--graph-mcp-tool", graph_tool]
    if mind_tool:
        cmd += ["--mind-mcp-tool", mind_tool]

    click.echo(f"[harness context] task={task_id}")
    rc = subprocess.run(cmd, cwd=str(project_path)).returncode
    sys.exit(rc)


@harness.command("verify")
@click.option("--project-dir", default=".", show_default=True)
def harness_verify(project_dir):
    """Run the verify gate (.harness/scripts/verify.sh).

    \b
    Reads CRITICAL_TEST_CMD / CRITICAL_LINT_CMD / CRITICAL_TYPE_CMD
    from .harness/config.yaml and exports them before invoking verify.sh.
    """
    project_path = Path(project_dir).resolve()
    verify_sh = project_path / ".harness" / "scripts" / "verify.sh"

    if not verify_sh.exists():
        click.echo(f"[error] verify.sh not found. Run 'dev harness init' first.", err=True)
        sys.exit(1)

    cfg = _harness_read_config_yaml(project_path)
    verify_cfg = cfg.get("verify", {}).get("critical", {})

    env = {**os.environ}
    if verify_cfg.get("test_cmd"):
        env["CRITICAL_TEST_CMD"] = str(verify_cfg["test_cmd"])
    if verify_cfg.get("lint_cmd"):
        env["CRITICAL_LINT_CMD"] = str(verify_cfg["lint_cmd"])
    if verify_cfg.get("type_cmd"):
        env["CRITICAL_TYPE_CMD"] = str(verify_cfg["type_cmd"])

    click.echo(f"[harness verify] running verify.sh")
    rc = subprocess.run(["bash", str(verify_sh)], cwd=str(project_path), env=env).returncode
    sys.exit(rc)


# ── installer ───────────────────────────────────────────────────────────────────

@cli.group()
def installer():
    """Build and manage context menu installers for Windows, macOS, and Ubuntu."""

@installer.command("build")
@click.option("--platform", "platforms", multiple=True,
              type=click.Choice(["windows", "macos", "ubuntu", "all"]),
              default=["all"], show_default=True)
@click.option("--output-dir", type=click.Path(), default="dist", show_default=True)
def installer_build(platforms, output_dir):
    """Build platform-specific installers for context menu integration.

    \b
    Builds installers for the specified platforms:
      windows    -> Inno Setup .exe installer
      macos      -> pkgbuild .pkg installer
      ubuntu     -> Debian .deb package
      all        -> All three platforms
    """
    import subprocess
    
    # Convert tuple to list and handle "all" option
    target_platforms = list(platforms)
    if "all" in target_platforms:
        target_platforms = ["windows", "macos", "ubuntu"]
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    click.echo(f"Building installers for: {', '.join(target_platforms)}")
    click.echo(f"Output directory: {output_path}")
    
    for platform in target_platforms:
        click.echo(f"\\n--- Building {platform} installer {'-' * 30}")
        
        if platform == "windows":
            _build_windows_installer(output_path)
        elif platform == "macos":
            _build_macos_installer(output_path)
        elif platform == "ubuntu":
            _build_ubuntu_installer(output_path)

def _build_windows_installer(output_dir: Path):
    """Build Windows installer using Inno Setup."""
    iss_script = Path("installers/windows/inno_setup/cortex_harness.iss")
    
    if not iss_script.exists():
        click.echo(f"  [skip] Inno Setup script not found: {iss_script}")
        click.echo("  [info] Use 'dev installer install --local' instead")
        return False
    
    # Check if Inno Setup compiler is available
    iscc_cmd = _find_iscc()
    if not iscc_cmd:
        click.echo("  [skip] Inno Setup compiler (ISCC.exe) not found in PATH")
        click.echo("  [info] For development use: dev installer install --local")
        click.echo("  [info] For production: Download from https://jrsoftware.org/isdl.php")
        return False
    
    # Build installer
    cmd = [iscc_cmd, str(iss_script)]
    click.echo(f"  [building] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("  [success] Windows installer created successfully")
            return True
        else:
            click.echo(f"  [error] Build failed: {result.stderr}")
            return False
    except Exception as e:
        click.echo(f"  [error] Build failed: {e}")
        return False

def _find_iscc() -> str:
    """Find Inno Setup compiler executable."""
    import shutil
    
    # Try to find ISCC in PATH
    iscc = shutil.which("ISCC")
    if iscc:
        return iscc
    
    # Try common installation paths
    common_paths = [
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files (x86)/Inno Setup 5/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 5/ISCC.exe"),
    ]
    
    for path in common_paths:
        if path.exists():
            return str(path)
    
    return None

def _build_macos_installer(output_dir: Path):
    """Build macOS installer using pkgbuild."""
    build_script = Path("installers/macos/build_pkg.sh")
    
    if not build_script.exists():
        click.echo(f"  [error] macOS build script not found: {build_script}")
        return False
    
    # Check if running on macOS
    if sys.platform != "darwin":
        click.echo("  [skip] macOS installers can only be built on macOS")
        return False
    
    # Run build script
    cmd = ["bash", str(build_script), "--output", str(output_dir)]
    click.echo(f"  [building] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("  [success] macOS installer created successfully")
            return True
        else:
            click.echo(f"  [error] Build failed: {result.stderr}")
            return False
    except Exception as e:
        click.echo(f"  [error] Build failed: {e}")
        return False

def _build_ubuntu_installer(output_dir: Path):
    """Build Ubuntu installer using Debian packaging tools."""
    build_script = Path("installers/ubuntu/build_deb.sh")
    
    if not build_script.exists():
        click.echo(f"  [error] Ubuntu build script not found: {build_script}")
        return False
    
    # Check if running on Linux
    if not sys.platform.startswith("linux"):
        click.echo("  [skip] Ubuntu packages can only be built on Linux")
        return False
    
    # Run build script
    cmd = ["bash", str(build_script), "--output", str(output_dir)]
    click.echo(f"  [building] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("  [success] Ubuntu package created successfully")
            return True
        else:
            click.echo(f"  [error] Build failed: {result.stderr}")
            return False
    except Exception as e:
        click.echo(f"  [error] Build failed: {e}")
        return False

@installer.command("install")
@click.option("--local", is_flag=True, help="Install context menu for current user only (development mode)")
@click.option("--project-dir", type=click.Path(exists=True), default=".", show_default=True)
def installer_install(local, project_dir):
    """Install context menu integration for the current platform.

    \b
    Development mode (--local):
      Windows   -> Registry entries for current user
      macOS     -> Services in ~/Library
      Ubuntu    -> Nautilus scripts in home directory

    System-wide mode (default, requires admin/sudo):
      Windows   -> Registry entries + Program Files installation
      macOS     -> Services in /Library
      Ubuntu    -> Nautilus scripts in /usr/share
    """
    project_path = Path(project_dir).resolve()
    
    # Detect platform
    if sys.platform == "win32":
        _install_windows_context_menu(local, project_path)
    elif sys.platform == "darwin":
        _install_macos_context_menu(local, project_path)
    elif sys.platform.startswith("linux"):
        _install_ubuntu_context_menu(local, project_path)
    else:
        click.echo(f"[error] Unsupported platform: {sys.platform}")
        sys.exit(1)

def _install_windows_context_menu(local: bool, project_path: Path):
    """Install Windows context menu integration."""
    # Import Windows registry manager
    import sys
    sys.path.insert(0, str(project_path / "installers"))
    
    from windows.registry_manager import WindowsRegistryManager
    from common.config_manager import ContextMenuConfig
    
    config_manager = ContextMenuConfig(project_path)
    config = config_manager.get_config()
    
    # Create registry manager with user-specific option
    registry_manager = WindowsRegistryManager(config["menu_name"], user_specific=local)
    
    if not local and not registry_manager.is_admin():
        click.echo("[error] System-wide installation requires administrator privileges")
        click.echo("        Run as administrator, or use --local for user-only installation")
        sys.exit(1)
    
    # Create context menu
    commands = config_manager.get_menu_commands()
    install_path = Path(config["platforms"]["windows"]["install_path"])
    
    if local:
        install_path = Path.home() / "CortexHarness"
        click.echo("Installation mode: User-specific (HKCU)")
    else:
        click.echo("Installation mode: System-wide (HKCR)")
    
    click.echo(f"Installing context menu: {config['menu_name']}")
    click.echo(f"Install path: {install_path}")
    
    # Create installation directory
    install_path.mkdir(parents=True, exist_ok=True)
    
    # Copy wrapper script
    scripts_dir = install_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    
    wrapper_src = project_path / "installers" / "windows" / "scripts" / "wrapper.bat"
    wrapper_dst = scripts_dir / "wrapper.bat"
    
    import shutil
    if wrapper_src.exists():
        shutil.copy2(wrapper_src, wrapper_dst)
        click.echo(f"  [copied] {wrapper_dst}")
    else:
        click.echo(f"  [warning] Wrapper script not found: {wrapper_src}")
    
    # Create registry entries
    if registry_manager.create_context_menu(commands, install_path):
        click.echo("  [success] Context menu installed successfully")
        
        if local:
            click.echo("\n  [info] Installed in HKEY_CURRENT_USER (for current user only)")
            click.echo("  [info] Right-click any folder to see CortexHarness menu")
        else:
            click.echo("\n  [info] Installed in HKEY_CLASSES_ROOT (system-wide)")
    else:
        click.echo("  [error] Failed to install context menu")
        sys.exit(1)

def _install_macos_context_menu(local: bool, project_path: Path):
    """Install macOS context menu integration."""
    click.echo("macOS context menu installation")
    click.echo("  [info] Requires Automator workflows in ~/Library/Services/")
    
    # Implementation would copy .workflow bundles
    workflows_src = project_path / "installers" / "macos" / "workflows"
    services_dst = Path.home() / "Library" / "Services"
    
    if not workflows_src.exists():
        click.echo(f"  [error] Workflows directory not found: {workflows_src}")
        return
    
    import shutil
    services_dst.mkdir(parents=True, exist_ok=True)
    
    workflows = list(workflows_src.glob("*.workflow"))
    if not workflows:
        click.echo("  [warning] No Automator workflows found")
        return
    
    for workflow in workflows:
        dst = services_dst / workflow.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(workflow, dst)
        click.echo(f"  [copied] {workflow.name}")
    
    # Refresh services
    click.echo("  [refresh] Reloading system services...")
    try:
        subprocess.run([
            "/System/Library/CoreServices/pbs",
            "-flush"
        ], check=True, capture_output=True)
        click.echo("  [success] Context menu installed successfully")
    except Exception as e:
        click.echo(f"  [warning] Could not refresh services: {e}")
        click.echo("  [info] You may need to log out and log back in")

def _install_ubuntu_context_menu(local: bool, project_path: Path):
    """Install Ubuntu context menu integration."""
    click.echo("Ubuntu context menu installation")
    click.echo("  [info] Installing Nautilus scripts")
    
    scripts_src = project_path / "installers" / "ubuntu" / "scripts"
    
    if local:
        scripts_dst = Path.home() / ".local" / "share" / "nautilus" / "scripts" / "CortexHarness"
    else:
        scripts_dst = Path("/usr/share/nautilus-scripts/CortexHarness")
    
    if not scripts_src.exists():
        click.echo(f"  [error] Scripts directory not found: {scripts_src}")
        return
    
    import shutil
    scripts_dst.mkdir(parents=True, exist_ok=True)
    
    scripts = list(scripts_src.glob("*.sh"))
    if not scripts:
        click.echo("  [warning] No shell scripts found")
        return
    
    for script in scripts:
        dst = scripts_dst / script.name
        shutil.copy2(script, dst)
        
        # Make executable
        import os
        os.chmod(dst, 0o755)
        click.echo(f"  [copied] {script.name}")
    
    click.echo("  [success] Context menu installed successfully")
    click.echo("  [info] Restart Nautilus: nautilus -q")

@installer.command("uninstall")
@click.option("--local", is_flag=True, help="Uninstall local user installation only")
@click.option("--project-dir", type=click.Path(exists=True), default=".", show_default=True)
def installer_uninstall(local, project_dir):
    """Remove context menu integration for the current platform."""
    project_path = Path(project_dir).resolve()
    
    # Detect platform
    if sys.platform == "win32":
        _uninstall_windows_context_menu(local, project_path)
    elif sys.platform == "darwin":
        _uninstall_macos_context_menu(local, project_path)
    elif sys.platform.startswith("linux"):
        _uninstall_ubuntu_context_menu(local, project_path)
    else:
        click.echo(f"[error] Unsupported platform: {sys.platform}")

def _uninstall_windows_context_menu(local: bool, project_path: Path):
    """Remove Windows context menu integration."""
    import sys
    sys.path.insert(0, str(project_path / "installers"))
    
    from windows.registry_manager import WindowsRegistryManager
    from common.config_manager import ContextMenuConfig
    
    config_manager = ContextMenuConfig(project_path)
    config = config_manager.get_config()
    
    # Create registry manager with user-specific option
    registry_manager = WindowsRegistryManager(config["menu_name"], user_specific=local)
    
    if not local and not registry_manager.is_admin():
        click.echo("[error] System-wide uninstallation requires administrator privileges")
        sys.exit(1)
    
    click.echo(f"Removing context menu: {config['menu_name']}")
    
    if registry_manager.remove_context_menu():
        click.echo("  [success] Context menu removed successfully")
    else:
        click.echo("  [error] Failed to remove context menu")

def _uninstall_macos_context_menu(local: bool, project_path: Path):
    """Remove macOS context menu integration."""
    click.echo("Removing macOS context menu integration")
    
    services_dir = Path.home() / "Library" / "Services"
    
    # Remove CortexHarness workflows
    workflows = list(services_dir.glob("*CortexHarness*.workflow"))
    
    if not workflows:
        click.echo("  [info] No CortexHarness workflows found")
        return
    
    import shutil
    for workflow in workflows:
        shutil.rmtree(workflow)
        click.echo(f"  [removed] {workflow.name}")
    
    click.echo("  [success] Context menu removed successfully")

def _uninstall_ubuntu_context_menu(local: bool, project_path: Path):
    """Remove Ubuntu context menu integration."""
    click.echo("Removing Ubuntu context menu integration")
    
    if local:
        scripts_dir = Path.home() / ".local" / "share" / "nautilus" / "scripts" / "CortexHarness"
    else:
        scripts_dir = Path("/usr/share/nautilus-scripts/CortexHarness")
    
    if not scripts_dir.exists():
        click.echo("  [info] No CortexHarness scripts found")
        return
    
    import shutil
    shutil.rmtree(scripts_dir)
    click.echo("  [success] Context menu removed successfully")


if __name__ == "__main__":
    cli()
