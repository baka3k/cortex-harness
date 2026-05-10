#!/usr/bin/env python3
"""dev - unified CLI for graph-rag-tiny ingestion (code + documents)."""

import sys
import json
import subprocess
from pathlib import Path

import click

CLI_DIR = Path(__file__).parent.resolve()
REPO_ROOT = CLI_DIR.parent
CODE_TINY = REPO_ROOT / "code-tiny"
DOC_TINY = REPO_ROOT / "doc-tiny"

HARNESS_CONFIG_DIR = ".cortext-harness/config"

LANG_ANALYZERS = {
    "kotlin":  CODE_TINY / "tools/kotlin/kotlin_analyzer.py",
    "java":    CODE_TINY / "tools/java/java_analyzer.py",
    "ts":      CODE_TINY / "tools/ts/ts_analyzer.py",
    "js":      CODE_TINY / "tools/js/js_analyzer.py",
    "php":     CODE_TINY / "tools/php/php_analyzer.py",
    "sql":     CODE_TINY / "tools/sql/sql_analyzer.py",
    "plsql":   CODE_TINY / "tools/plsql/plsql_analyzer.py",
    "cplus":   CODE_TINY / "tools/cplus/cplus_analyzer.py",
    "csharp":  CODE_TINY / "tools/csharp/csharp_analyzer.py",
    "python":  CODE_TINY / "tools/python/python_analyzer.py",
    "android": CODE_TINY / "tools/android/android_analyzer.py",
}

DOC_INGESTOR = DOC_TINY / "graphrag_ingest_langextract.py"

DOC_EXT_FLAGS = {
    ".pdf":  "--pdf",
    ".md":   "--md",
    ".docx": "--docx",
    ".txt":  "--text-file",
    ".pptx": "--pptx",
    ".xlsx": "--xlsx",
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

_SCAN_EXCLUDE = {".cortext-harness", ".git", ".venv", "__pycache__", "node_modules", ".cache"}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _config_dir(project_dir: Path) -> Path:
    return project_dir / HARNESS_CONFIG_DIR


def _config_path(project_dir: Path, env: str) -> Path:
    return _config_dir(project_dir) / f"{env}.json"


def _load_active_config(project_dir: Path) -> tuple:
    """Return (config_dict, config_path) for the active environment."""
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


# ---------------------------------------------------------------------------
# Scaffold helpers
# ---------------------------------------------------------------------------

def _discover_folders(project_dir: Path, root_prefix: str) -> list:
    """Scan project_dir and return relative paths of all subdirs under root_prefix."""
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
    # Prepend the root itself
    return [root_prefix] + result


def _scaffold_project(project_dir: Path) -> tuple:
    """Scaffold standard project structure.

    Returns (doc_folders, code_folders) discovered after scaffold.
    """
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


def _run(cmd: list, dry_run: bool = False) -> int:
    display = " ".join(str(c) for c in cmd)
    click.echo(f"  $ {display}")
    if dry_run:
        click.echo("  [dry-run] skipped\n")
        return 0
    result = subprocess.run([str(c) for c in cmd])
    return result.returncode


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """dev - CortexHarness ingestion CLI.

    \b
    Quick start:
      dev init              # configure project + scaffold folder structure
      dev init --env prod   # create prod config
      dev status            # show active config
      dev sync code         # ingest source code -> Neo4j + Qdrant
      dev sync doc          # ingest documents  -> Neo4j + Qdrant
    """


# ---------------------------------------------------------------------------
# dev init
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--env",
    default="dev",
    show_default=True,
    type=click.Choice(["dev", "prod"]),
    help="Environment to configure (dev or prod).",
)
@click.option(
    "--project-dir",
    default=".",
    show_default=True,
    help="Target project root directory.",
)
def init(env, project_dir):
    """Create/update config and scaffold project folder structure.

    \b
    Config saved to: <project-dir>/.cortext-harness/config/<env>.json
    Format:
      code.env  + code.source  -> source code settings
      doc.env   + doc.source   -> document settings
    """
    project_path = Path(project_dir).resolve()
    config_path = _config_path(project_path, env)

    existing: dict = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            existing = json.load(f)
        click.echo(f"[info] Updating existing config: {config_path}\n")
    else:
        click.echo(f"[info] Creating new {env} config: {config_path}\n")

    def _p(label, keys: list, default=""):
        """Prompt with existing value as default. keys is a nested key path."""
        cur = existing
        for k in keys:
            cur = cur.get(k, {}) if isinstance(cur, dict) else {}
        cur_val = cur if isinstance(cur, str) else default
        return click.prompt(label, default=cur_val or default)

    # ── Project ──────────────────────────────────────────────────────────
    click.echo("─── Project ────────────────────────────────")
    project_code = _p("Project code (short ID)", ["project", "code"], "my_project")
    project_name = _p("Project name",            ["project", "name"], project_code)

    # ── Code env ─────────────────────────────────────────────────────────
    click.echo("\n─── Code — Neo4j + Qdrant + Embedding ──────")
    code_neo4j_uri  = _p("NEO4J_URI",       ["code", "env", "NEO4J_URI"],  "bolt://localhost:7687")
    code_neo4j_db   = _p("NEO4J_DB",        ["code", "env", "NEO4J_DB"],   "neo4j")
    code_neo4j_user = _p("NEO4J_USER",      ["code", "env", "NEO4J_USER"], "neo4j")
    code_neo4j_pass = _p("NEO4J_PASS",      ["code", "env", "NEO4J_PASS"], "")
    code_qdrant_host= _p("QDRANT_HOST",     ["code", "env", "QDRANT_HOST"],"localhost")
    code_qdrant_port= _p("QDRANT_PORT",     ["code", "env", "QDRANT_PORT"],"6333")
    code_embed_model= _p("EMBEDDING_MODEL", ["code", "env", "EMBEDDING_MODEL"], "jinaai/jina-embeddings-v3")
    code_batch_size = _p("BATCH_SIZE",      ["code", "env", "BATCH_SIZE"],  "1")
    code_max_chars  = _p("MAX_EMBED_CHARS", ["code", "env", "MAX_EMBED_CHARS"], "500")
    code_device     = _p("device",          ["code", "env", "device"],      "cpu")

    # ── Code source ───────────────────────────────────────────────────────
    click.echo("\n─── Code — source ──────────────────────────")
    code_git = _p("Git remote URL (blank = none)", ["code", "source", "git"], "")

    # ── Doc env ──────────────────────────────────────────────────────────
    click.echo("\n─── Doc — Neo4j + Qdrant + Embedding ───────")
    doc_neo4j_uri   = _p("NEO4J_URI",       ["doc", "env", "NEO4J_URI"],  code_neo4j_uri)
    doc_neo4j_db    = _p("NEO4J_DB",        ["doc", "env", "NEO4J_DB"],   code_neo4j_db)
    doc_neo4j_user  = _p("NEO4J_USER",      ["doc", "env", "NEO4J_USER"], code_neo4j_user)
    doc_neo4j_pass  = _p("NEO4J_PASS",      ["doc", "env", "NEO4J_PASS"], code_neo4j_pass)
    doc_qdrant_host = _p("QDRANT_HOST",     ["doc", "env", "QDRANT_HOST"],code_qdrant_host)
    doc_qdrant_port = _p("QDRANT_PORT",     ["doc", "env", "QDRANT_PORT"],code_qdrant_port)
    doc_embed_model = _p("EMBEDDING_MODEL", ["doc", "env", "EMBEDDING_MODEL"], "BAAI/bge-m3")
    doc_batch_size  = _p("BATCH_SIZE",      ["doc", "env", "BATCH_SIZE"],  "1")
    doc_max_chars   = _p("MAX_EMBED_CHARS", ["doc", "env", "MAX_EMBED_CHARS"], "500")
    doc_device      = _p("device",          ["doc", "env", "device"],      code_device)

    # ── Doc source ────────────────────────────────────────────────────────
    click.echo("\n─── Doc — source ───────────────────────────")
    doc_git = _p("Git remote URL (blank = none)", ["doc", "source", "git"], code_git)

    # ── Scaffold then auto-discover folders ───────────────────────────────
    doc_folders, code_folders = _scaffold_project(project_path)

    cfg = {
        "active": True,
        "project": {
            "code": project_code,
            "name": project_name,
        },
        "code": {
            "env": {
                "NEO4J_URI":       code_neo4j_uri,
                "NEO4J_DB":        code_neo4j_db,
                "NEO4J_USER":      code_neo4j_user,
                "NEO4J_PASS":      code_neo4j_pass,
                "QDRANT_HOST":     code_qdrant_host,
                "QDRANT_PORT":     code_qdrant_port,
                "EMBEDDING_MODEL": code_embed_model,
                "BATCH_SIZE":      code_batch_size,
                "MAX_EMBED_CHARS": code_max_chars,
                "device":          code_device,
            },
            "source": {
                "git":    code_git,
                "folder": code_folders,
            },
        },
        "doc": {
            "env": {
                "NEO4J_URI":       doc_neo4j_uri,
                "NEO4J_DB":        doc_neo4j_db,
                "NEO4J_USER":      doc_neo4j_user,
                "NEO4J_PASS":      doc_neo4j_pass,
                "QDRANT_HOST":     doc_qdrant_host,
                "QDRANT_PORT":     doc_qdrant_port,
                "EMBEDDING_MODEL": doc_embed_model,
                "BATCH_SIZE":      doc_batch_size,
                "MAX_EMBED_CHARS": doc_max_chars,
                "device":          doc_device,
            },
            "source": {
                "git":    doc_git,
                "folder": doc_folders,
            },
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
    """Show current active config and all available environments."""
    project_path = Path(project_dir).resolve()
    cfg_dir = _config_dir(project_path)

    if not cfg_dir.exists():
        click.echo("[error] No config directory found. Run 'dev init' first.", err=True)
        sys.exit(1)

    envs = sorted(cfg_dir.glob("*.json"))
    if not envs:
        click.echo("[error] No config files found. Run 'dev init' first.", err=True)
        sys.exit(1)

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

    code = cfg.get("code", {})
    code_env = code.get("env", {})
    code_src = code.get("source", {})
    click.echo(f"\n[code]")
    click.echo(f"  Neo4j     : {code_env.get('NEO4J_URI')}  db={code_env.get('NEO4J_DB')}")
    click.echo(f"  Qdrant    : {code_env.get('QDRANT_HOST')}:{code_env.get('QDRANT_PORT')}")
    click.echo(f"  Embedding : {code_env.get('EMBEDDING_MODEL')}  device={code_env.get('device')}")
    click.echo(f"  Git       : {code_src.get('git') or '(none)'}")
    click.echo(f"  Folders   : {len(code_src.get('folder', []))} paths")

    doc = cfg.get("doc", {})
    doc_env = doc.get("env", {})
    doc_src = doc.get("source", {})
    click.echo(f"\n[doc]")
    click.echo(f"  Neo4j     : {doc_env.get('NEO4J_URI')}  db={doc_env.get('NEO4J_DB')}")
    click.echo(f"  Qdrant    : {doc_env.get('QDRANT_HOST')}:{doc_env.get('QDRANT_PORT')}")
    click.echo(f"  Embedding : {doc_env.get('EMBEDDING_MODEL')}  device={doc_env.get('device')}")
    click.echo(f"  Git       : {doc_src.get('git') or '(none)'}")
    click.echo(f"  Folders   : {len(doc_src.get('folder', []))} paths")


# ---------------------------------------------------------------------------
# dev sync
# ---------------------------------------------------------------------------

@cli.group()
def sync():
    """Sync code or documents into Neo4j + Qdrant."""


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


@sync.command("code")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--lang", default=None, help="Sync a single language only (e.g. kotlin).")
@click.option("--collection", default=None, help="Override Qdrant collection name.")
@click.option("--dry-run", is_flag=True)
@click.option("--verbose/--no-verbose", default=True, show_default=True)
def sync_code(project_dir, lang, collection, dry_run, verbose):
    """Ingest source code into Neo4j + Qdrant via language analyzers."""
    project_path = Path(project_dir).resolve()
    cfg, _ = _load_active_config(project_path)

    code_cfg = cfg.get("code", {})
    env      = code_cfg.get("env", {})
    src      = code_cfg.get("source", {})

    folders = [f for f in src.get("folder", []) if f]
    if not folders:
        click.echo("[warn] No code source folders in config. Run 'dev init' first.")
        return

    if lang and lang not in LANG_ANALYZERS:
        click.echo(f"[error] Unknown language '{lang}'. Supported: {', '.join(LANG_ANALYZERS)}")
        sys.exit(1)

    targets = {lang: LANG_ANALYZERS[lang]} if lang else LANG_ANALYZERS
    python = _venv_python(CODE_TINY)
    qdrant_url = _env_to_qdrant_url(env)

    for lname, analyzer in targets.items():
        if not analyzer.exists():
            click.echo(f"[warn] Analyzer not found: {analyzer}")
            continue

        for folder in folders:
            root = str(project_path / folder)
            coll = collection or f"{lname}_functions"
            cmd = [
                python, str(analyzer),
                "--root", root,
                *_env_to_neo4j_args(env),
                "--qdrant-url",        qdrant_url,
                "--qdrant-collection", coll,
                "--embed-model",       env.get("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3"),
                "--device",            env.get("device", "cpu"),
                "--batch-size",        env.get("BATCH_SIZE", "1"),
                "--max-embed-chars",   env.get("MAX_EMBED_CHARS", "500"),
                "--qdrant-timeout", "300",
                "--qdrant-retries", "3",
                "--qdrant-retry-sleep", "2",
            ]
            if verbose:
                cmd.append("--verbose")

            click.echo(f"\n[sync-code] lang={lname}  folder={folder}")
            rc = _run(cmd, dry_run)
            if rc != 0:
                click.echo(f"[error] {lname} analyzer exited {rc}")


@sync.command("doc")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--file", "single_file", default=None, help="Ingest a single file.")
@click.option("--folder", default=None, help="Override doc folder from config.")
@click.option("--source-id", default=None)
@click.option("--entity-provider", default=None, help="Override entity provider (gliner/langextract/spacy).")
@click.option("--batch/--no-batch", default=False, show_default=True)
@click.option("--dry-run", is_flag=True)
def sync_doc(project_dir, single_file, folder, source_id, entity_provider, batch, dry_run):
    """Ingest documents (PDF, DOCX, MD, XLSX, ...) into Neo4j + Qdrant."""
    project_path = Path(project_dir).resolve()
    cfg, _ = _load_active_config(project_path)

    doc_cfg = cfg.get("doc", {})
    env     = doc_cfg.get("env", {})
    src     = doc_cfg.get("source", {})

    if not DOC_INGESTOR.exists():
        click.echo(f"[error] Ingestor not found: {DOC_INGESTOR}", err=True)
        sys.exit(1)

    provider   = entity_provider or "gliner"
    collection = "graphrag_entities"
    python     = _venv_python(DOC_TINY)
    qdrant_url = _env_to_qdrant_url(env)

    cmd = [
        python, str(DOC_INGESTOR),
        *_env_to_neo4j_args(env),
        "--qdrant-url",      qdrant_url,
        "--collection",      collection,
        "--entity-provider", provider,
        "--embedding-model", env.get("EMBEDDING_MODEL", "BAAI/bge-m3"),
    ]

    cmd.append("--batch" if batch else "--no-batch")

    if source_id:
        cmd += ["--source-id", source_id]

    if single_file:
        ext  = Path(single_file).suffix.lower()
        flag = DOC_EXT_FLAGS.get(ext)
        if not flag:
            click.echo(f"[error] Unsupported extension '{ext}'. Supported: {', '.join(DOC_EXT_FLAGS)}")
            sys.exit(1)
        cmd += [flag, single_file]
        click.echo(f"\n[sync-doc] file={single_file}  provider={provider}")
        rc = _run(cmd, dry_run)
        if rc != 0:
            click.echo(f"[error] Ingestor exited {rc}")
        return

    targets = [folder] if folder else [f for f in src.get("folder", []) if f]
    if not targets:
        click.echo("[error] No doc folders in config. Use --folder or run 'dev init' first.")
        sys.exit(1)

    for target in targets:
        full_target = str(project_path / target) if not Path(target).is_absolute() else target
        click.echo(f"\n[sync-doc] folder={target}  provider={provider}")
        rc = _run(cmd + ["--folder", full_target], dry_run)
        if rc != 0:
            click.echo(f"[error] Ingestor exited {rc} for folder '{target}'")


if __name__ == "__main__":
    cli()
