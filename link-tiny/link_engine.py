"""Link engine — delta → candidates → score → stale propagation → write.

Pure library: takes opened graph handles (anything with ``run(query,
params)``) plus paths in, produces writes through :mod:`link_store`. Knows
nothing about MCP or the CLI.

Isolation contract:

* reads both graphs through the lease-free ``read_only_opener`` — no
  ``StorageLease``, no writes, no Qdrant access;
* exact-name matching only (Phase 1); provenance ``exact-name``, score
  ``1.0`` on every record;
* identity is ``(path, name, start_line, end_line, parser_type,
  source_sha)`` code-side and ``(doc_id, paragraph_id, source_sha)``
  doc-side — never graph-internal ids;
* delta SHAs come from the sync baselines (``.cortext-harness/sync-state/``
  for docs, ``.cache/inventories/`` for code) — the graphs persist no
  per-document SHA.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import normalizers
import link_store
from normalizers import CodeSymbolRef


# Symbol labels carrying the core identity columns (name/file_path/lines).
CODE_SYMBOL_LABELS = (
    "Function",
    "Class",
    "Type",
    "Interface",
    "Enum",
    "Constant",
    "Variable",
    "Property",
)
# Framework-writer labels; identity uses ``source_file`` instead of
# ``file_path`` and carries no line numbers.
CODE_ENDPOINT_LABELS = (
    "ApiEndpoint",
    "HttpEndpoint",
    "Route",
    "ControllerAction",
    "ServletEndpoint",
)

# Mirrors dev.py DOC_EXTENSIONS (importing dev.py would violate the one-way
# dependency rule; keep the two lists in step manually).
DOC_EXTENSIONS = frozenset({".md", ".pdf", ".docx", ".txt", ".pptx", ".xlsx"})


class LinkEngineError(RuntimeError):
    """Raised when a link build cannot proceed."""


# ---------------------------------------------------------------------------
# Graph-side value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineSymbol:
    """Code-side identity exactly as stored in link records."""

    name: str
    path: str
    qualified_name: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    language: str = ""
    target_type: str = "symbol"
    parser_type: str = ""

    @property
    def identity(self) -> tuple:
        return (
            link_store.posix_path(self.path),
            self.name,
            (self.start_line, self.end_line),
            self.target_type,
        )

    def as_symbol_ref(self) -> CodeSymbolRef:
        return CodeSymbolRef(
            name=self.name,
            qualified_name=self.qualified_name,
            path=self.path,
            target_type=self.target_type,
            identity=self.identity,
        )


@dataclass(frozen=True)
class ParagraphRef:
    """One doc-side anchor: (document, paragraph) with its raw text."""

    doc_id: str
    paragraph_id: Any
    text: str = ""

    @property
    def anchor(self) -> tuple:
        return (self.doc_id, self.paragraph_id)


@dataclass
class BuildReport:
    """Outcome of one ``build_links`` run (also the dry-run payload)."""

    project_id: str
    new_links: List[Dict[str, Any]] = field(default_factory=list)
    staled_links: List[Dict[str, Any]] = field(default_factory=list)
    unchanged_links: int = 0
    doc_universe: int = 0
    symbol_universe: int = 0
    dry_run: bool = False
    reconcile: bool = False
    coverage: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graph readers (read-only handles in, plain data out)
# ---------------------------------------------------------------------------


def _run(graph: Any, query: str, **params: Any) -> List[Dict[str, Any]]:
    return list(graph.run(query, params) or [])


def read_doc_paragraphs(doc_graph: Any, project_id_normalized: str) -> List[ParagraphRef]:
    """Read ``Paragraph`` nodes (never ``Chunk`` — that label is vestigial)."""
    rows = _run(
        doc_graph,
        "MATCH (p:Paragraph) "
        "WHERE p.project_id_normalized = $pid "
        "RETURN p.source_id AS source_id, p.paragraph_id AS paragraph_id, "
        "p.text AS text",
        pid=project_id_normalized,
    )
    paragraphs: List[ParagraphRef] = []
    seen: set[tuple] = set()
    for row in rows:
        ref = ParagraphRef(
            doc_id=str(row.get("source_id") or ""),
            paragraph_id=row.get("paragraph_id"),
            text=str(row.get("text") or ""),
        )
        if not ref.doc_id or ref.anchor in seen:
            continue
        seen.add(ref.anchor)
        paragraphs.append(ref)
    return paragraphs


def read_doc_entity_names(
    doc_graph: Any, project_id_normalized: str
) -> Dict[tuple, List[str]]:
    """Map ``(source_id, paragraph_id)`` → entity names mentioning it."""
    rows = _run(
        doc_graph,
        "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
        "WHERE p.project_id_normalized = $pid "
        "RETURN p.source_id AS source_id, p.paragraph_id AS paragraph_id, "
        "e.name AS name",
        pid=project_id_normalized,
    )
    mentions: Dict[tuple, List[str]] = {}
    for row in rows:
        anchor = (str(row.get("source_id") or ""), row.get("paragraph_id"))
        name = str(row.get("name") or "").strip()
        if not anchor[0] or not name:
            continue
        names = mentions.setdefault(anchor, [])
        if name not in names:
            names.append(name)
    return mentions


def _read_labeled_symbols(
    graph: Any,
    label: str,
    project_id_normalized: str,
    parser_type: str,
    target_type: str,
) -> List[EngineSymbol]:
    path_property = "source_file" if target_type == "endpoint" else "file_path"
    rows = _run(
        graph,
        f"MATCH (n:{label}) "
        "WHERE n.project_id_normalized = $pid "
        "RETURN n.name AS name, n.qualified_name AS qualified_name, "
        f"n.{path_property} AS file_path, n.start_line AS start_line, "
        "n.end_line AS end_line, n.language AS language",
        pid=project_id_normalized,
    )
    symbols: List[EngineSymbol] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        path = link_store.posix_path(row.get("file_path"))
        if not name or not path:
            continue
        symbols.append(
            EngineSymbol(
                name=name,
                path=path,
                qualified_name=str(row.get("qualified_name") or ""),
                start_line=row.get("start_line"),
                end_line=row.get("end_line"),
                language=str(row.get("language") or ""),
                target_type=target_type,
                parser_type=parser_type or "",
            )
        )
    return symbols


def read_code_symbols(
    code_graph: Any,
    project_id_normalized: str,
    parser_type: Optional[str] = None,
) -> List[EngineSymbol]:
    """Read symbol + endpoint labels; a missing label degrades to a warning.

    Graphs from older parsers lack some framework labels — one dead label
    must not take the whole build down.
    """
    symbols: List[EngineSymbol] = []
    for label in CODE_SYMBOL_LABELS:
        try:
            symbols.extend(
                _read_labeled_symbols(code_graph, label, project_id_normalized, parser_type or "", "symbol")
            )
        except Exception as exc:  # noqa: BLE001 - per-label degradation
            print(
                f"[link-engine] warning: label {label} unreadable ({exc})",
                file=sys.stderr,
            )
    for label in CODE_ENDPOINT_LABELS:
        try:
            symbols.extend(
                _read_labeled_symbols(code_graph, label, project_id_normalized, parser_type or "", "endpoint")
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[link-engine] warning: endpoint label {label} unreadable ({exc})",
                file=sys.stderr,
            )
    return symbols


# ---------------------------------------------------------------------------
# Delta inputs: sync baselines (the graphs persist no per-document SHA)
# ---------------------------------------------------------------------------


def load_sync_state_hashes(project_dir: Path) -> Dict[str, str]:
    """Merge ``file_hashes`` from every ``.cortext-harness/sync-state/*.json``.

    dev.py writes one state file per scanned folder (``doc:{folder}`` for
    docs, plain folders for code), so the merged map is the project-wide
    baseline; ``split_shas_by_side`` classifies entries by extension.
    """
    merged: Dict[str, str] = {}
    state_dir = Path(project_dir) / ".cortext-harness" / "sync-state"
    if not state_dir.is_dir():
        return merged
    for path in sorted(state_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        hashes = payload.get("file_hashes")
        if isinstance(hashes, Mapping):
            for rel, sha in hashes.items():
                merged[link_store.posix_path(rel)] = str(sha)
    return merged


def split_shas_by_side(
    hashes: Mapping[str, str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Split a merged baseline into ``(doc_shas, code_shas)`` by extension."""
    doc_shas: Dict[str, str] = {}
    code_shas: Dict[str, str] = {}
    for rel, sha in hashes.items():
        suffix = Path(rel).suffix.lower()
        if suffix in DOC_EXTENSIONS:
            doc_shas[rel] = sha
        else:
            code_shas[rel] = sha
    return doc_shas, code_shas


def load_code_inventory_hashes(
    code_folders: Iterable[str],
    project_id: str,
    project_dir: Path,
) -> Dict[str, str]:
    """Read per-file SHAs from the newest code-sync inventory per folder.

    Chain: ``{folder}/.cache/incremental_sync/{safe_project}_{scope}.json``
    → ``inventory_path`` → ``{folder}/.cache/inventories/{snapshot}.json``
    with ``entries: {rel_path: {sha256, …}}``. Paths are re-expressed
    relative to ``project_dir`` when the folder lives inside it, else kept
    folder-relative (identity stays stable either way).
    """
    import re

    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project_id)).strip("._") or "project"
    merged: Dict[str, str] = {}
    project_dir = Path(project_dir).resolve()
    for folder in code_folders:
        cache_dir = Path(folder).expanduser() / ".cache" / "incremental_sync"
        if not cache_dir.is_dir():
            continue
        candidates = sorted(cache_dir.glob(f"{safe_project}_*.json"))
        legacy = cache_dir.glob(f"{safe_project}.json")
        candidates.extend(sorted(legacy))
        newest_state: Optional[Dict[str, Any]] = None
        for state_path in candidates:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            # newest wins: prefer the latest last_run_after token
            if (
                newest_state is None
                or str(payload.get("last_run_after") or "")
                >= str(newest_state.get("last_run_after") or "")
            ):
                newest_state = dict(payload)
        inventory_path = str((newest_state or {}).get("inventory_path") or "").strip()
        if not inventory_path:
            continue
        try:
            inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = inventory.get("entries") if isinstance(inventory, Mapping) else None
        if not isinstance(entries, Mapping):
            continue
        folder_root = Path(folder).expanduser().resolve()
        for rel, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            sha = entry.get("sha256")
            if not sha:
                continue
            try:
                absolute = (folder_root / rel).resolve()
                stored = absolute.relative_to(project_dir).as_posix()
            except ValueError:
                stored = link_store.posix_path(rel)
            merged[stored] = str(sha)
    return merged


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------


def _doc_sha_for(doc_id: str, doc_shas: Mapping[str, str]) -> Optional[str]:
    """Resolve a document SHA; ``doc_id`` is the folder-mode rel path."""
    direct = doc_shas.get(link_store.posix_path(doc_id))
    if direct:
        return direct
    # single-file mode: source_id is the file stem without extension
    for rel, sha in doc_shas.items():
        if Path(rel).stem == doc_id:
            return sha
    return None


def build_record(
    *,
    project_id: str,
    paragraph: ParagraphRef,
    symbol: EngineSymbol,
    doc_sha: Optional[str],
    code_sha: Optional[str],
) -> Dict[str, Any]:
    """One v1 link record (provenance + score mandatory, POSIX paths)."""
    return {
        "schema_version": link_store.SCHEMA_VERSION,
        "project_id": project_id,
        "relation": "implements",
        "doc": {
            "doc_id": paragraph.doc_id,
            "paragraph_id": paragraph.paragraph_id,
            "source_sha": doc_sha,
        },
        "code": {
            "target_type": symbol.target_type,
            "parser_type": symbol.parser_type,
            "path": link_store.posix_path(symbol.path),
            "name": symbol.name,
            "lines": (
                [symbol.start_line, symbol.end_line]
                if symbol.start_line is not None
                else None
            ),
            "source_sha": code_sha,
        },
        "score": normalizers.EXACT_NAME_SCORE,
        "provenance": normalizers.EXACT_NAME_PROVENANCE,
    }


def compute_fresh_pairs(
    paragraphs: Sequence[ParagraphRef],
    entity_mentions: Mapping[tuple, Sequence[str]],
    symbols: Sequence[EngineSymbol],
) -> List[Tuple[ParagraphRef, EngineSymbol]]:
    """All exact-name (paragraph, symbol) pairs, deduped, deterministic.

    Tokens per paragraph = graph entity names + identifier-like tokens
    extracted from the paragraph text. Same name in N files yields N pairs
    (never auto-select — plan risk R5).
    """
    symbol_refs = [symbol.as_symbol_ref() for symbol in symbols]
    symbol_by_ref = dict(zip(symbol_refs, symbols))
    match_index = normalizers.build_match_index(symbol_refs)
    pairs: List[Tuple[ParagraphRef, EngineSymbol]] = []
    seen: set[tuple] = set()
    for paragraph in paragraphs:
        tokens = list(entity_mentions.get(paragraph.anchor, ()))
        tokens.extend(normalizers.extract_identifier_tokens(paragraph.text))
        matched_symbols: List[EngineSymbol] = []
        matched_identities: set[tuple] = set()
        for token in tokens:
            for ref, _score, _prov in normalizers.match_index_lookup(match_index, token):
                symbol = symbol_by_ref[ref]
                if symbol.identity not in matched_identities:
                    matched_identities.add(symbol.identity)
                    matched_symbols.append(symbol)
        for symbol in matched_symbols:
            key = (paragraph.anchor, symbol.identity)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((paragraph, symbol))
    return pairs


# ---------------------------------------------------------------------------
# The 5-step pipeline
# ---------------------------------------------------------------------------


def build_links(
    *,
    project_id: str,
    links_dir: Path,
    code_graph: Any,
    doc_graph: Any,
    doc_shas: Mapping[str, str],
    code_shas: Mapping[str, str],
    parser_type: Optional[str] = None,
    reconcile: bool = False,
    dry_run: bool = False,
) -> BuildReport:
    """Run the full pipeline for one project.

    Steps: (1) delta vs ``state.json``; (2) candidate generation from the
    doc graph against the code symbol index; (3) score/provenance stamp;
    (4) stale propagation (append-only); (5) write records + state.

    ``dry_run`` computes everything and writes nothing.
    """
    links_dir = Path(links_dir)

    # Step 1 — delta inputs from the graphs + baselines.
    paragraphs = read_doc_paragraphs(doc_graph, _norm(project_id))
    entity_mentions = read_doc_entity_names(doc_graph, _norm(project_id))
    symbols = read_code_symbols(code_graph, _norm(project_id), parser_type)
    paragraph_anchors = {paragraph.anchor for paragraph in paragraphs}
    symbol_identities = {symbol.identity for symbol in symbols}

    # Steps 2–3 — fresh candidate pairs with shas joined in.
    fresh_pairs = compute_fresh_pairs(paragraphs, entity_mentions, symbols)
    fresh_records = [
        build_record(
            project_id=project_id,
            paragraph=paragraph,
            symbol=symbol,
            doc_sha=_doc_sha_for(paragraph.doc_id, doc_shas),
            code_sha=code_shas.get(symbol.path),
        )
        for paragraph, symbol in fresh_pairs
    ]

    # Existing state: latest record per key drives the skip / stale rules.
    existing = link_store.load_links(links_dir)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for record in existing:
        latest[link_store.record_key(record)] = record

    def _sha_changed(record: Mapping[str, Any]) -> bool:
        doc_sha = _doc_sha_for(str((record.get("doc") or {}).get("doc_id") or ""), doc_shas)
        previous_doc_sha = (record.get("doc") or {}).get("source_sha")
        if doc_sha and previous_doc_sha and doc_sha != previous_doc_sha:
            return True
        code_path = link_store.posix_path((record.get("code") or {}).get("path"))
        code_sha = code_shas.get(code_path)
        previous_code_sha = (record.get("code") or {}).get("source_sha")
        if code_sha and previous_code_sha and code_sha != previous_code_sha:
            return True
        return False

    # Step 4a — stale propagation for drifted/deleted sides (append-only).
    def _drifted(record: Mapping[str, Any]) -> bool:
        code = record.get("code") or {}
        lines = code.get("lines") or [None, None]
        identity = (
            link_store.posix_path(code.get("path")),
            str(code.get("name") or ""),
            (lines[0], lines[1]),
            str(code.get("target_type") or "symbol"),
        )
        if identity not in symbol_identities:
            return True  # code deleted or renamed — no auto re-match here
        doc = record.get("doc") or {}
        anchor = (str(doc.get("doc_id") or ""), doc.get("paragraph_id"))
        if anchor not in paragraph_anchors:
            return True  # doc or paragraph deleted — no re-match
        return _sha_changed(record)

    staled: List[Dict[str, Any]] = []
    if not dry_run:
        if reconcile:
            # Full rebuild: supersede every existing active record, then
            # re-append the authoritative fresh set below.
            staled = link_store.mark_stale(links_dir, lambda record: True)
        else:
            staled = link_store.mark_stale(links_dir, _drifted)

    # New records: skip pairs whose latest record is active with identical
    # SHAs (delta no-op); anything else re-appends as active. Reconcile
    # bypasses the skip so the fresh set is written authoritatively.
    new_records: List[Dict[str, Any]] = []
    for record in fresh_records:
        previous = latest.get(link_store.record_key(record))
        if (
            not reconcile
            and previous is not None
            and previous.get("status") == link_store.STATUS_ACTIVE
            and previous.get("doc", {}).get("source_sha") == record["doc"]["source_sha"]
            and previous.get("code", {}).get("source_sha") == record["code"]["source_sha"]
        ):
            continue
        new_records.append(record)

    # Step 5 — write (records, then state snapshot with coverage).
    if not dry_run and new_records:
        link_store.append_links(links_dir, new_records)

    # Canonical projection: latest record per key across existing lines,
    # appended stale copies and the fresh set.
    projected = link_store.latest_records(existing + staled + new_records)
    coverage = link_store.coverage_stats(
        projected,
        total_docs=len({p.doc_id for p in paragraphs}),
        total_symbols=len(symbols),
    )
    if not dry_run:
        link_store.save_state(
            links_dir,
            {
                "built_at": _utc_now(),
                "project_id": project_id,
                "doc_shas": dict(doc_shas),
                "code_shas": dict(code_shas),
                "counts": {
                    "active": coverage["active_links"],
                    "stale": coverage["stale_links"],
                    "total": len(projected),
                },
                "coverage": coverage,
            },
        )

    return BuildReport(
        project_id=project_id,
        new_links=new_records,
        staled_links=staled,
        unchanged_links=len(fresh_records) - len(new_records),
        doc_universe=len({p.doc_id for p in paragraphs}),
        symbol_universe=len(symbols),
        dry_run=dry_run,
        reconcile=reconcile,
        coverage=coverage,
    )


def _norm(project_id: str) -> str:
    return str(project_id or "").strip().casefold()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


__all__ = [
    "BuildReport",
    "DOC_EXTENSIONS",
    "EngineSymbol",
    "LinkEngineError",
    "ParagraphRef",
    "build_links",
    "build_record",
    "compute_fresh_pairs",
    "load_code_inventory_hashes",
    "load_sync_state_hashes",
    "read_code_symbols",
    "read_doc_entity_names",
    "read_doc_paragraphs",
    "split_shas_by_side",
]
