#!/usr/bin/env python3
"""dev - unified CLI for CortexHarness ingestion (code + documents)."""

import sys
import json
import fnmatch
import hashlib
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

DOC_INGESTOR = DOC_TINY / "graphrag_ingest_langextract.py"

DOC_EXT_FLAGS = {
    ".pdf":  "--pdf",
    ".md":   "--md",
    ".docx": "--docx",
    ".txt":  "--text-file",
    ".pptx": "--pptx",
    ".xlsx": "--xlsx",
}

DOC_EXTENSIONS = set(DOC_EXT_FLAGS.keys())

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

_SCAN_EXCLUDE = {".cortext-harness", ".git", ".venv", "__pycache__", "node_modules", ".cache"}


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
    args = [
        "--neo4j-uri",  env.get("NEO4J_URI",  "bolt://localhost:7687"),
        "--neo4j-user", env.get("NEO4J_USER", "neo4j"),
        "--neo4j-pass", env.get("NEO4J_PASS", ""),
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


def _detect_langs(folder_path: Path) -> list:
    """Detect languages from extensions. Android takes priority when AndroidManifest exists."""
    counts: Counter = Counter()
    is_android = any(folder_path.rglob("AndroidManifest.xml"))

    for f in folder_path.rglob("*"):
        if not f.is_file() or _is_sensitive(f):
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
        if f.is_file() and not _is_sensitive(f) and f.stat().st_mtime > since_ts:
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
        if f.is_file() and f.suffix.lower() in DOC_EXTENSIONS and not _is_sensitive(f)
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

    qdrant_url = _env_to_qdrant_url(env)

    base_cmd = [
        python, str(DOC_INGESTOR),
        "--neo4j-uri",       env.get("NEO4J_URI", "bolt://localhost:7687"),
        "--neo4j-user",      env.get("NEO4J_USER", "neo4j"),
        "--neo4j-pass",      env.get("NEO4J_PASS", ""),
        "--qdrant-url",      qdrant_url,
        "--collection",      "graphrag_entities",
        "--entity-provider", entity_provider,
        "--embedding-model", env.get("EMBEDDING_MODEL", "BAAI/bge-m3"),
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
                "last_sync_ts": time.time(),
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
            "last_sync_ts": time.time(),
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
    mode: str,
    changed_files: list,
    deleted_files: list,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Build and invoke one analyzer subprocess. Returns exit code."""
    qdrant_url = _env_to_qdrant_url(env)

    cmd = [
        python, str(analyzer),
        "--root",             str(folder_path),
        *_env_to_neo4j_args(env),
        "--qdrant-url",        qdrant_url,
        "--qdrant-collection", f"{lang}_functions",
        "--embed-model",       env.get("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3"),
        "--device",            env.get("device", "cpu"),
        "--batch-size",        env.get("BATCH_SIZE", "1"),
        "--qdrant-timeout",    "300",
        "--qdrant-retries",    "3",
        "--qdrant-retry-sleep","2",
    ]

    changed_manifest = None
    deleted_manifest = None

    if mode == "incremental" and changed_files:
        # Delegate incremental to the analyzer's built-in mechanism
        changed_manifest = _write_manifest(changed_files, "changed")
        cmd += ["--incremental", "--changed-files-manifest", str(changed_manifest)]

        if deleted_files:
            deleted_manifest = _write_manifest(deleted_files, "deleted")
            cmd += ["--deleted-files-manifest", str(deleted_manifest)]

    if verbose:
        cmd.append("--verbose")

    rc = _run_with_retry(cmd, dry_run=dry_run)

    # Clean up temp manifest files
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

    # Incremental: detect changed / deleted files
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
            "last_sync_ts": time.time(),
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
@click.option("--project-dir", default=".", show_default=True,
              help="Target project root directory.")
def init(env, project_dir):
    """Create/update config and scaffold project folder structure."""
    project_path = Path(project_dir).resolve()
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

    click.echo("\n─── Code — source ──────────────────────────")
    code_git = _p("Git remote URL (blank = none)", ["code", "source", "git"], "")

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

    click.echo("\n─── Doc — source ───────────────────────────")
    doc_git = _p("Git remote URL (blank = none)", ["doc", "source", "git"], code_git)

    doc_folders, code_folders = _scaffold_project(project_path)

    cfg = {
        "active": True,
        "project": {"code": project_code, "name": project_name},
        "code": {
            "env": {
                "NEO4J_URI":       code_neo4j_uri,   "NEO4J_DB":  code_neo4j_db,
                "NEO4J_USER":      code_neo4j_user,  "NEO4J_PASS": code_neo4j_pass,
                "QDRANT_HOST":     code_qdrant_host, "QDRANT_PORT": code_qdrant_port,
                "EMBEDDING_MODEL": code_embed_model, "BATCH_SIZE": code_batch_size,
                "MAX_EMBED_CHARS": code_max_chars,   "device": code_device,
            },
            "source": {"git": code_git, "folder": code_folders},
        },
        "doc": {
            "env": {
                "NEO4J_URI":       doc_neo4j_uri,   "NEO4J_DB":  doc_neo4j_db,
                "NEO4J_USER":      doc_neo4j_user,  "NEO4J_PASS": doc_neo4j_pass,
                "QDRANT_HOST":     doc_qdrant_host, "QDRANT_PORT": doc_qdrant_port,
                "EMBEDDING_MODEL": doc_embed_model, "BATCH_SIZE": doc_batch_size,
                "MAX_EMBED_CHARS": doc_max_chars,   "device": doc_device,
            },
            "source": {"git": doc_git, "folder": doc_folders},
        },
    }

    _deactivate_other_envs(project_path, env)
    _save_config(cfg, config_path)
    click.echo(f"\n[ok] Environment '{env}' is now active.")


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
    click.echo(f"\n─── Active: {active_path.name} ───────────────────────")
    click.echo(f"Project     : {proj.get('name')} ({proj.get('code')})")

    for section in ("code", "doc"):
        sec = cfg.get(section, {})
        env = sec.get("env", {})
        src = sec.get("source", {})
        click.echo(f"\n[{section}]")
        click.echo(f"  Neo4j     : {env.get('NEO4J_URI')}  db={env.get('NEO4J_DB')}")
        click.echo(f"  Qdrant    : {env.get('QDRANT_HOST')}:{env.get('QDRANT_PORT')}")
        click.echo(f"  Embedding : {env.get('EMBEDDING_MODEL')}  device={env.get('device')}")
        click.echo(f"  Git       : {src.get('git') or '(none)'}")
        click.echo(f"  Folders   : {len(src.get('folder', []))} paths")


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
    folders  = [f for f in code_cfg.get("source", {}).get("folder", []) if f]

    if not folders:
        click.echo("[warn] No folders in code.source.folder. Run 'dev init' first.")
        return

    selected = _select_folders_interactive(folders)
    if not selected:
        click.echo("[info] No folders selected.")
        return

    python      = _venv_python(CODE_TINY)
    summaries   = []
    total_start = time.time()

    for folder in selected:
        folder_path = Path(folder) if Path(folder).is_absolute() else project_path / folder
        langs = _detect_langs(folder_path) if folder_path.exists() else []

        result = _sync_folder(
            project_path=project_path,
            folder=folder,
            env=env,
            python=python,
            langs=langs,       # auto-detected
            force_mode="auto",
            dry_run=dry_run,
            verbose=verbose,
            preview=preview,
        )
        summaries.append(result)

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
    folders      = [f for f in code_cfg.get("source", {}).get("folder", []) if f]

    if not folders:
        click.echo("[warn] No folders in code.source.folder. Run 'dev init' first.")
        return

    available = [l for l, p in LANG_ANALYZERS.items() if p.exists()]
    click.echo(f"\n[sync-code all]  folders={len(folders)}  analyzers={len(available)}")
    click.echo(f"  tools: {', '.join(available)}")

    python      = _venv_python(CODE_TINY)
    summaries   = []
    total_start = time.time()

    for folder in folders:
        result = _sync_folder(
            project_path=project_path,
            folder=folder,
            env=env,
            python=python,
            langs=[],           # [] = run every available analyzer
            force_mode="auto",  # incremental if baseline exists, full otherwise
            dry_run=o["dry_run"],
            verbose=o["verbose"],
            preview=False,
        )
        summaries.append(result)

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
    folders = [f for f in doc_cfg.get("source", {}).get("folder", []) if f]

    if not folders:
        click.echo("[warn] No folders in doc.source.folder. Run 'dev init' first.")
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
    folders      = [f for f in doc_cfg.get("source", {}).get("folder", []) if f]

    if not folders:
        click.echo("[warn] No folders in doc.source.folder. Run 'dev init' first.")
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
            force_mode="full",
            entity_provider=o["entity_provider"],
            dry_run=o["dry_run"],
            preview=False,
        )
        summaries.append(result)

    _print_summary(summaries, time.time() - total_start)


if __name__ == "__main__":
    cli()
