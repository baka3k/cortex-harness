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
    "kotlin":     CODE_TINY / "tools/kotlin/kotlin_analyzer.py",
    "java":       CODE_TINY / "tools/java/java_analyzer.py",
    "ts":         CODE_TINY / "tools/ts/ts_analyzer.py",
    "js":         CODE_TINY / "tools/js/js_analyzer.py",
    "php":        CODE_TINY / "tools/php/php_analyzer.py",
    "sql":        CODE_TINY / "tools/sql/sql_analyzer.py",
    "plsql":      CODE_TINY / "tools/plsql/plsql_analyzer.py",
    "cplus":      CODE_TINY / "tools/cplus/cplus_analyzer.py",
    "csharp":     CODE_TINY / "tools/csharp/csharp_analyzer.py",
    "python":     CODE_TINY / "tools/python/python_analyzer.py",
    "android":    CODE_TINY / "tools/android/android_analyzer.py",
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

# Folders and placeholder files to scaffold in the target project
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
    "docs/design-docs/index.md":         "# Design Docs\n",
    "docs/exec-plans/tech-debt-tracker.md": "# Tech Debt Tracker\n",
    "docs/generated/db-schema.md":       "# DB Schema\n",
    "docs/product-specs/index.md":       "# Product Specs\n",
    "docs/DESIGN.md":                    "# Design\n",
    "docs/FRONTEND.md":                  "# Frontend Guidelines\n",
    "docs/PLANS.md":                     "# Project Roadmap\n",
    "docs/PRODUCT_SENSE.md":             "# Product Logic & Philosophy\n",
    "docs/QUALITY_SCORE.md":             "# Engineering Standards\n",
    "docs/RELIABILITY.md":               "# Stability & Error Handling\n",
    "docs/SECURITY.md":                  "# Security Protocols\n",
    "AGENTS.md":                         "# Agents\n",
    "ARCHITECTURE.md":                   "# Architecture\n",
    ".cursorrules":                      "# AI Instruction Set\n",
    "README.md":                         "# Project\n",
}


def _venv_python(base_dir: Path) -> str:
    for candidate in [
        base_dir / ".venv" / "Scripts" / "python.exe",
        base_dir / ".venv" / "bin" / "python",
    ]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _config_dir(project_dir: Path) -> Path:
    return project_dir / HARNESS_CONFIG_DIR


def _config_path(project_dir: Path, env: str) -> Path:
    return _config_dir(project_dir) / f"{env}.json"


def _load_active_config(project_dir: Path) -> tuple[dict, Path]:
    """Return (config_dict, config_path) for the active environment."""
    cfg_dir = _config_dir(project_dir)
    if not cfg_dir.exists():
        click.echo(f"[error] No config found at '{cfg_dir}'. Run 'dev init' first.", err=True)
        sys.exit(1)

    configs = list(cfg_dir.glob("*.json"))
    if not configs:
        click.echo(f"[error] No config files in '{cfg_dir}'. Run 'dev init' first.", err=True)
        sys.exit(1)

    for p in configs:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("active"):
            return cfg, p

    # Fall back to the first config if none marked active
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


def _scaffold_project(project_dir: Path, source_folders: list[str]) -> None:
    click.echo("\n─── Scaffolding project structure ─────────")
    created = []

    for d in _SCAFFOLD_DIRS:
        root = d.split("/")[0]
        if root not in source_folders:
            continue
        full = project_dir / d
        if not full.exists():
            full.mkdir(parents=True, exist_ok=True)
            created.append(f"  [dir]  {d}/")

    for rel, content in _SCAFFOLD_FILES.items():
        root = rel.split("/")[0]
        # Root-level files (AGENTS.md etc.) always scaffold
        if "/" in rel and root not in source_folders:
            continue
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
    Config is saved to: <project-dir>/.cortext-harness/config/<env>.json
    The new config is marked active; other env configs are deactivated.
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

    def _prompt(label, key_path: list, default=""):
        cur = existing
        for k in key_path:
            cur = cur.get(k, {}) if isinstance(cur, dict) else {}
        cur_val = cur if isinstance(cur, str) else default
        return click.prompt(label, default=cur_val or default)

    # ── Project ──────────────────────────────────────────────────────────
    click.echo("─── Project ────────────────────────────────")
    project_id = _prompt("Project ID", ["project_id"], "my_project")
    git_remote = _prompt("Git remote URL (blank = none)", ["source", "git"], "")

    # ── Source folders ────────────────────────────────────────────────────
    click.echo("\n─── Source folders ─────────────────────────")
    click.echo("Which top-level folders to include in this project?")
    existing_folders = existing.get("source", {}).get("folder", ["docs", "src"])
    folders_default = ",".join(existing_folders)
    folders_raw = click.prompt("Folders (comma-separated)", default=folders_default)
    source_folders = [f.strip() for f in folders_raw.split(",") if f.strip()]

    # ── Neo4j ────────────────────────────────────────────────────────────
    click.echo("\n─── Neo4j ──────────────────────────────────")
    neo4j_uri  = _prompt("URI",      ["neo4j", "uri"],      "bolt://localhost:7687")
    neo4j_user = _prompt("User",     ["neo4j", "user"],     "neo4j")
    neo4j_pass = _prompt("Password", ["neo4j", "password"], "")
    neo4j_db   = _prompt("Database", ["neo4j", "db"],       "neo4j")

    # ── Qdrant ───────────────────────────────────────────────────────────
    click.echo("\n─── Qdrant ─────────────────────────────────")
    qdrant_url = _prompt("URL", ["qdrant", "url"], "http://localhost:6333")

    # ── Embedding ────────────────────────────────────────────────────────
    click.echo("\n─── Embedding model ────────────────────────")
    embed_model  = _prompt("Model name/ID", ["embed", "model"],  "jinaai/jina-embeddings-v3")
    embed_path   = _prompt("Local path (blank = download)", ["embed", "path"], "")
    embed_device = _prompt("Device (cpu/cuda/mps)", ["embed", "device"], "cpu")

    # ── Code sources ─────────────────────────────────────────────────────
    click.echo("\n─── Code sources ───────────────────────────")
    click.echo("Supported languages: " + ", ".join(LANG_ANALYZERS.keys()))
    existing_sources = existing.get("code", {}).get("sources", {})
    langs_default = ",".join(existing_sources.keys()) if existing_sources else ""
    langs_raw = click.prompt(
        "Languages to sync (comma-separated, blank = skip code)",
        default=langs_default,
    )
    sources: dict = {}
    for lang in [l.strip().lower() for l in langs_raw.split(",") if l.strip()]:
        if lang not in LANG_ANALYZERS:
            click.echo(f"  [warn] Unknown language '{lang}', skipping")
            continue
        ex = existing_sources.get(lang, {})
        root = click.prompt(f"  [{lang}] Source root", default=ex.get("root", ""))
        coll = click.prompt(f"  [{lang}] Qdrant collection", default=ex.get("collection", f"{lang}_functions"))
        if root:
            sources[lang] = {"root": root, "collection": coll}

    # ── Document sources ─────────────────────────────────────────────────
    click.echo("\n─── Document sources ───────────────────────")
    doc_ex = existing.get("doc", {})
    doc_folder     = _prompt("Folder to ingest (blank = skip doc)", ["doc", "folder"], "")
    doc_collection = _prompt("Qdrant collection", ["doc", "collection"], "graphrag_entities")
    doc_provider   = _prompt("Entity provider (gliner/langextract/spacy)", ["doc", "entity_provider"], "gliner")

    # ── GLiNER ───────────────────────────────────────────────────────────
    click.echo("\n─── GLiNER (used when entity_provider=gliner) ──")
    gliner_ex = existing.get("gliner", {})
    gliner_name = click.prompt("  Model name", default=gliner_ex.get("model_name", "urchade/gliner_large-v2.1"))
    gliner_path = click.prompt("  Local path (blank = download)", default=gliner_ex.get("model_path", ""))

    cfg = {
        "active": True,
        "environment": env,
        "project_id": project_id,
        "source": {
            "git": git_remote,
            "folder": source_folders,
        },
        "neo4j":  {"uri": neo4j_uri, "user": neo4j_user, "password": neo4j_pass, "db": neo4j_db},
        "qdrant": {"url": qdrant_url},
        "embed":  {"model": embed_model, "path": embed_path, "device": embed_device},
        "code":   {"sources": sources},
        "doc":    {"folder": doc_folder, "collection": doc_collection, "entity_provider": doc_provider},
        "gliner": {"model_name": gliner_name, "model_path": gliner_path},
    }

    _deactivate_other_envs(project_path, env)
    _save_config(cfg, config_path)
    _scaffold_project(project_path, source_folders)

    click.echo(f"\n[ok] Environment '{env}' is now active.")


# ---------------------------------------------------------------------------
# dev status
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--project-dir",
    default=".",
    show_default=True,
    help="Target project root directory.",
)
def status(project_dir):
    """Show current active config and all available environments."""
    project_path = Path(project_dir).resolve()
    cfg_dir = _config_dir(project_path)

    if not cfg_dir.exists():
        click.echo("[error] No config directory found. Run 'dev init' first.", err=True)
        sys.exit(1)

    envs = list(cfg_dir.glob("*.json"))
    if not envs:
        click.echo("[error] No config files found. Run 'dev init' first.", err=True)
        sys.exit(1)

    # Show all envs
    click.echo(f"\nProject dir : {project_path}")
    click.echo(f"Config dir  : {cfg_dir}\n")
    click.echo("Environments:")
    for p in sorted(envs):
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        marker = " [ACTIVE]" if c.get("active") else ""
        click.echo(f"  {p.name}{marker}")

    cfg, active_path = _load_active_config(project_path)
    click.echo(f"\n─── Active: {active_path.name} ───────────────────────")
    click.echo(f"Project     : {cfg.get('project_id', '?')}")
    click.echo(f"Environment : {cfg.get('environment', '?')}")

    src = cfg.get("source", {})
    click.echo(f"Git remote  : {src.get('git') or '(none)'}")
    click.echo(f"Folders     : {', '.join(src.get('folder', []))}")

    neo4j = cfg.get("neo4j", {})
    click.echo(f"\nNeo4j       : {neo4j.get('uri')}  db={neo4j.get('db')}  user={neo4j.get('user')}")

    qdrant = cfg.get("qdrant", {})
    click.echo(f"Qdrant      : {qdrant.get('url')}")

    embed = cfg.get("embed", {})
    click.echo(f"Embed       : {embed.get('path') or embed.get('model')}  device={embed.get('device')}")

    sources = cfg.get("code", {}).get("sources", {})
    if sources:
        click.echo("\nCode sources:")
        for lang, s in sources.items():
            click.echo(f"  {lang:10s}  root={s.get('root')}  collection={s.get('collection')}")
    else:
        click.echo("\nCode sources: (none)")

    doc = cfg.get("doc", {})
    if doc.get("folder"):
        click.echo(f"\nDoc folder  : {doc.get('folder')}")
        click.echo(f"Doc collect : {doc.get('collection')}")
        click.echo(f"Doc provider: {doc.get('entity_provider')}")
    else:
        click.echo("\nDoc folder  : (none)")


# ---------------------------------------------------------------------------
# dev sync
# ---------------------------------------------------------------------------

@cli.group()
def sync():
    """Sync code or documents into Neo4j + Qdrant."""


@sync.command("code")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--lang", default=None, help="Sync a single language only (e.g. kotlin).")
@click.option("--dry-run", is_flag=True, help="Print commands without executing.")
@click.option("--verbose/--no-verbose", default=True, show_default=True)
@click.option("--batch-size", default=None, type=int, help="Embedding batch size.")
def sync_code(project_dir, lang, dry_run, verbose, batch_size):
    """Ingest source code into Neo4j + Qdrant via language analyzers."""
    project_path = Path(project_dir).resolve()
    cfg, _ = _load_active_config(project_path)

    neo4j  = cfg["neo4j"]
    qdrant = cfg["qdrant"]
    embed  = cfg["embed"]
    sources = cfg.get("code", {}).get("sources", {})

    if not sources:
        click.echo("[warn] No code sources in config. Run 'dev init' first.")
        return

    if lang:
        if lang not in sources:
            click.echo(f"[error] '{lang}' not in config. Available: {', '.join(sources)}")
            sys.exit(1)
        targets = {lang: sources[lang]}
    else:
        targets = sources

    python = _venv_python(CODE_TINY)

    for lname, src in targets.items():
        analyzer = LANG_ANALYZERS.get(lname)
        if analyzer is None:
            click.echo(f"[warn] No analyzer mapping for '{lname}'")
            continue
        if not analyzer.exists():
            click.echo(f"[warn] Analyzer not found: {analyzer}")
            continue

        root = src.get("root", "")
        if not root:
            click.echo(f"[warn] No root path for '{lname}', skipping")
            continue

        embed_model = embed.get("path") or embed.get("model", "jinaai/jina-embeddings-v3")

        cmd = [
            python, str(analyzer),
            "--root", root,
            "--neo4j-uri", neo4j["uri"],
            "--neo4j-user", neo4j["user"],
            "--neo4j-pass", neo4j["password"],
            "--qdrant-url", qdrant["url"],
            "--qdrant-collection", src.get("collection", f"{lname}_functions"),
            "--embed-model", embed_model,
            "--device", embed.get("device", "cpu"),
            "--qdrant-timeout", "300",
            "--qdrant-retries", "3",
            "--qdrant-retry-sleep", "2",
        ]
        if neo4j.get("db"):
            cmd += ["--neo4j-db", neo4j["db"]]
        if batch_size is not None:
            cmd += ["--batch-size", str(batch_size)]
        if verbose:
            cmd.append("--verbose")

        click.echo(f"\n[sync-code] lang={lname}  root={root}")
        rc = _run(cmd, dry_run)
        if rc != 0:
            click.echo(f"[error] {lname} analyzer exited {rc}")


@sync.command("doc")
@click.option("--project-dir", default=".", show_default=True)
@click.option("--folder", default=None, help="Override doc folder from config.")
@click.option("--file", "single_file", default=None, help="Ingest a single file (pdf/md/docx/txt/pptx/xlsx).")
@click.option("--source-id", default=None, help="Override source_id in Neo4j / Qdrant.")
@click.option("--entity-provider", default=None, help="Override entity provider (gliner/langextract/spacy).")
@click.option("--batch/--no-batch", default=False, show_default=True, help="Enable GLiNER + Neo4j batching.")
@click.option("--dry-run", is_flag=True)
def sync_doc(project_dir, folder, single_file, source_id, entity_provider, batch, dry_run):
    """Ingest documents (PDF, DOCX, MD, XLSX, ...) into Neo4j + Qdrant."""
    project_path = Path(project_dir).resolve()
    cfg, _ = _load_active_config(project_path)

    neo4j  = cfg["neo4j"]
    qdrant = cfg["qdrant"]
    embed  = cfg["embed"]
    doc_cfg = cfg.get("doc", {})
    gliner  = cfg.get("gliner", {})

    if not DOC_INGESTOR.exists():
        click.echo(f"[error] Ingestor not found: {DOC_INGESTOR}", err=True)
        sys.exit(1)

    provider   = entity_provider or doc_cfg.get("entity_provider", "gliner")
    collection = doc_cfg.get("collection", "graphrag_entities")
    python     = _venv_python(DOC_TINY)

    cmd = [
        python, str(DOC_INGESTOR),
        "--neo4j-uri",  neo4j["uri"],
        "--neo4j-user", neo4j["user"],
        "--neo4j-pass", neo4j["password"],
        "--qdrant-url",  qdrant["url"],
        "--collection",  collection,
        "--entity-provider", provider,
    ]

    embed_val = embed.get("path") or embed.get("model")
    if embed_val:
        cmd += ["--embedding-model", embed_val]

    if provider == "gliner":
        if gliner.get("model_path"):
            cmd += ["--gliner-model-path", gliner["model_path"]]
        elif gliner.get("model_name"):
            cmd += ["--gliner-model-name", gliner["model_name"]]

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
        click.echo(f"\n[sync-doc] file={single_file}  provider={provider}  collection={collection}")
    else:
        target = folder or doc_cfg.get("folder", "")
        if not target:
            click.echo("[error] No folder specified. Use --folder or set 'doc.folder' in config.")
            sys.exit(1)
        cmd += ["--folder", target]
        click.echo(f"\n[sync-doc] folder={target}  provider={provider}  collection={collection}")

    rc = _run(cmd, dry_run)
    if rc != 0:
        click.echo(f"[error] Ingestor exited {rc}")


if __name__ == "__main__":
    cli()
