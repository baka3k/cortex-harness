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

CONFIG_FILE = "dev.json"

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


def _venv_python(base_dir: Path) -> str:
    """Return venv python if found, else fall back to current interpreter."""
    for candidate in [
        base_dir / ".venv" / "Scripts" / "python.exe",
        base_dir / ".venv" / "bin" / "python",
    ]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        click.echo(f"[error] '{path}' not found. Run 'dev init' first.", err=True)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    click.echo(f"[ok] Config saved -> {path}")


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
    """dev - graph-rag-tiny ingestion CLI.

    \b
    Quick start:
      dev init          # configure project (creates dev.json)
      dev sync code     # ingest source code -> Neo4j + Qdrant
      dev sync doc      # ingest documents  -> Neo4j + Qdrant
      dev status        # show current config
    """


# ---------------------------------------------------------------------------
# dev init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", default=CONFIG_FILE, show_default=True, help="Config file to create/update.")
def init(config):
    """Set up project environment and save config to dev.json."""
    existing: dict = {}
    if Path(config).exists():
        with open(config, encoding="utf-8") as f:
            existing = json.load(f)
        click.echo(f"[info] Updating existing config: {config}\n")
    else:
        click.echo(f"[info] Creating new config: {config}\n")

    def _prompt(label, key_path: list, default=""):
        cur = existing
        for k in key_path:
            cur = cur.get(k, {}) if isinstance(cur, dict) else {}
        cur_val = cur if isinstance(cur, str) else default
        return click.prompt(label, default=cur_val or default)

    # ── Project ──────────────────────────────────────────────────────────
    click.echo("─── Project ───────────────────────────────")
    project_id = _prompt("Project ID", ["project_id"], "my_project")

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
        "project_id": project_id,
        "neo4j":   {"uri": neo4j_uri, "user": neo4j_user, "password": neo4j_pass, "db": neo4j_db},
        "qdrant":  {"url": qdrant_url},
        "embed":   {"model": embed_model, "path": embed_path, "device": embed_device},
        "code":    {"sources": sources},
        "doc":     {"folder": doc_folder, "collection": doc_collection, "entity_provider": doc_provider},
        "gliner":  {"model_name": gliner_name, "model_path": gliner_path},
    }
    _save_config(cfg, config)


# ---------------------------------------------------------------------------
# dev status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", default=CONFIG_FILE, show_default=True)
def status(config):
    """Show current project config."""
    cfg = _load_config(config)

    click.echo(f"\nProject : {cfg.get('project_id', '?')}")
    click.echo(f"Config  : {Path(config).resolve()}\n")

    neo4j = cfg.get("neo4j", {})
    click.echo(f"Neo4j   : {neo4j.get('uri')}  db={neo4j.get('db')}  user={neo4j.get('user')}")

    qdrant = cfg.get("qdrant", {})
    click.echo(f"Qdrant  : {qdrant.get('url')}")

    embed = cfg.get("embed", {})
    click.echo(f"Embed   : {embed.get('path') or embed.get('model')}  device={embed.get('device')}")

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
@click.option("--config", default=CONFIG_FILE, show_default=True)
@click.option("--lang", default=None, help="Sync a single language only (e.g. kotlin).")
@click.option("--dry-run", is_flag=True, help="Print commands without executing.")
@click.option("--verbose/--no-verbose", default=True, show_default=True)
@click.option("--batch-size", default=None, type=int, help="Embedding batch size.")
def sync_code(config, lang, dry_run, verbose, batch_size):
    """Ingest source code into Neo4j + Qdrant via language analyzers."""
    cfg = _load_config(config)
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
            "--neo4j-password", neo4j["password"],
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
@click.option("--config", default=CONFIG_FILE, show_default=True)
@click.option("--folder", default=None, help="Override doc folder from config.")
@click.option("--file", "single_file", default=None, help="Ingest a single file (pdf/md/docx/txt/pptx/xlsx).")
@click.option("--source-id", default=None, help="Override source_id in Neo4j / Qdrant.")
@click.option("--entity-provider", default=None, help="Override entity provider (gliner/langextract/spacy).")
@click.option("--batch/--no-batch", default=False, show_default=True, help="Enable GLiNER + Neo4j batching.")
@click.option("--dry-run", is_flag=True)
def sync_doc(config, folder, single_file, source_id, entity_provider, batch, dry_run):
    """Ingest documents (PDF, DOCX, MD, XLSX, ...) into Neo4j + Qdrant."""
    cfg = _load_config(config)
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
            click.echo("[error] No folder specified. Use --folder or set 'doc.folder' in dev.json.")
            sys.exit(1)
        cmd += ["--folder", target]
        click.echo(f"\n[sync-doc] folder={target}  provider={provider}  collection={collection}")

    rc = _run(cmd, dry_run)
    if rc != 0:
        click.echo(f"[error] Ingestor exited {rc}")


if __name__ == "__main__":
    cli()
