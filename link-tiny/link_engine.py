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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import normalizers
import link_store
from normalizers import CodeSymbolRef

ProgressFn = Callable[[str], None]
_NOOP_PROGRESS: ProgressFn = lambda msg: None


# Symbol labels carrying the core identity columns (name/file_path/lines).
# ``Field`` covers designer controls — the artifacts screen/business-rule
# documents actually name (plan 261019-1700 Phase 03).
CODE_SYMBOL_LABELS = (
    "Function",
    "Class",
    "Type",
    "Interface",
    "Enum",
    "Constant",
    "Variable",
    "Property",
    "Field",
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

# Keyset page size for label reads — comfortably below any observed
# resultset_size (FalkorDB default 10000) so pages are never cut.
SYMBOL_READ_BATCH = 5000


class LinkEngineError(RuntimeError):
    """Raised when a link build cannot proceed."""


# ---------------------------------------------------------------------------
# Graph-side value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineSymbol:
    """Code-side identity exactly as stored in link records.

    ``repo`` is a join input only (the scan folder the parser stamped on
    the node); it is deliberately NOT part of ``identity`` — the record
    schema and ``record_key`` stay byte-identical.
    """

    name: str
    path: str
    qualified_name: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    language: str = ""
    target_type: str = "symbol"
    parser_type: str = ""
    repo: str = ""

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


def _empty_sha_join() -> Dict[str, int]:
    return {
        "doc_matched": 0,
        "doc_missing": 0,
        "code_matched": 0,
        "code_missing": 0,
        "code_ambiguous": 0,
    }


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
    sha_join: Dict[str, int] = field(default_factory=_empty_sha_join)
    symbol_universe_by_label: Dict[str, Dict[str, int]] = field(default_factory=dict)
    symbol_universe_expected: int = 0


# ---------------------------------------------------------------------------
# Graph readers (read-only handles in, plain data out)
# ---------------------------------------------------------------------------


def _run(graph: Any, query: str, **params: Any) -> List[Dict[str, Any]]:
    return list(graph.run(query, params) or [])


# Window paging floor: never emit a zero/negative-width id window even on
# degenerate count/bounds ratios.
_READ_WINDOW_MIN_IDS = 256


def _row_id_tuple(row: Dict[str, Any]) -> List[Any]:
    return [
        value for key, value in row.items()
        if key.startswith("_") and key.endswith("_id")
    ]


def _paged_rows(
    graph: Any,
    *,
    match_cypher: str,
    group_cypher: str,
    count_cypher: str,
    range_cypher: str,
    id_exprs: List[str],
    pid: str,
    batch: int,
    what: str,
) -> List[Dict[str, Any]]:
    """All rows of one match, paged over the anchor's internal-id space.

    The server's ``resultset_size`` caps a single query's result set
    (FalkorDB default 10000, silent) — paging keeps every page under it.
    Pages are scoped to a window of ``id_exprs[0]`` (``$wlo``/``$whi``,
    width targets ~one batch of rows); when a page ends inside the
    boundary anchor's group, ``group_cypher`` resumes that group past
    ``$after``. Both predicates are AND-only so FalkorDB pushes them to
    the anchor scan — the previous pager's shapes forced full-graph work
    per page (growing ``SKIP`` re-ran the whole traversal + sort;
    an ``OR``-shaped cursor referencing ``id(e)`` defeated filter
    pushdown: ~6x slower measured) and blew the server's query timeout.
    Bounds are parameterized so each shape compiles one server plan per
    read. Invariant: rows read == the server's count; a shortfall is a
    loud warning, never a silent cut (a silent doc-side cut would be
    destructive — missing paragraphs stale their own links).

    Rows without ``_*_id`` columns degrade to a single-page read.
    """
    expected_rows = _run(graph, count_cypher, pid=pid)
    expected = expected_rows[0].get("c") if expected_rows else None
    expected = int(expected) if expected is not None else None
    bounds = _run(graph, range_cypher, pid=pid)
    lo_bound = bounds[0].get("lo") if bounds else None
    hi_bound = bounds[0].get("hi") if bounds else None
    lo = int(lo_bound) if lo_bound is not None else 0
    hi = int(hi_bound) + 1 if hi_bound is not None else 1 << 62
    width = max(
        _READ_WINDOW_MIN_IDS,
        (int(batch) * (hi - lo)) // max(expected or 1, 1),
    )
    order = ", ".join(id_exprs)
    rows: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def absorb(page: List[Dict[str, Any]]) -> None:
        for row in page:
            node_id = tuple(_row_id_tuple(row))
            if node_id:
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
            rows.append(row)

    window_lo = lo
    while window_lo < hi:
        if expected and len(rows) >= expected:
            break
        window_hi = min(window_lo + width, hi)
        page_lo, after = window_lo, None
        while True:
            if after is None:
                page = _run(
                    graph,
                    f"{match_cypher} ORDER BY {order} LIMIT {int(batch)}",
                    pid=pid, wlo=page_lo, whi=window_hi,
                )
            else:
                page = _run(
                    graph,
                    f"{group_cypher} ORDER BY {order} LIMIT {int(batch)}",
                    pid=pid, wlo=page_lo, after=after,
                )
            if not page:
                if after is None:
                    break  # window drained
                page_lo, after = page_lo + 1, None  # group drained
                if page_lo >= window_hi:
                    break
                continue
            absorb(page)
            if expected and len(rows) >= expected:
                break
            ids = _row_id_tuple(page[-1])
            if not ids:
                break  # rows carry no id columns to page on
            anchor = ids[0]
            if anchor > page_lo:
                page_lo, after = anchor, None  # jump to the boundary group
                continue
            after = ids[-1]  # page ended inside this group; resume past it
        window_lo = window_hi
    if expected is not None and len(rows) < expected:
        print(
            f"[link-engine] warning: {what} truncated — read {len(rows)} "
            f"of {expected} rows (server resultset cap?); links for the "
            "missing rows would stale-propagate",
            file=sys.stderr,
        )
    return rows


def read_doc_paragraphs(doc_graph: Any, project_id_normalized: str) -> List[ParagraphRef]:
    """Read ``Paragraph`` nodes (never ``Chunk`` — that label is vestigial)."""
    rows = _paged_rows(
        doc_graph,
        match_cypher=(
            "MATCH (p:Paragraph) "
            "WHERE p.project_id_normalized = $pid "
            "AND id(p) >= $wlo AND id(p) < $whi "
            "RETURN id(p) AS _node_id, p.source_id AS source_id, "
            "p.paragraph_id AS paragraph_id, p.text AS text"
        ),
        group_cypher=(
            "MATCH (p:Paragraph) "
            "WHERE p.project_id_normalized = $pid "
            "AND id(p) = $wlo AND id(p) > $after "
            "RETURN id(p) AS _node_id, p.source_id AS source_id, "
            "p.paragraph_id AS paragraph_id, p.text AS text"
        ),
        count_cypher=(
            "MATCH (p:Paragraph) "
            "WHERE p.project_id_normalized = $pid "
            "RETURN count(p) AS c"
        ),
        range_cypher=(
            "MATCH (p:Paragraph) "
            "WHERE p.project_id_normalized = $pid "
            "RETURN min(id(p)) AS lo, max(id(p)) AS hi"
        ),
        id_exprs=["id(p)"],
        pid=project_id_normalized,
        batch=SYMBOL_READ_BATCH,
        what="Paragraph",
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
    rows = _paged_rows(
        doc_graph,
        match_cypher=(
            "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
            "WHERE p.project_id_normalized = $pid "
            "AND id(p) >= $wlo AND id(p) < $whi "
            "RETURN id(p) AS _node_id, id(e) AS _entity_id, "
            "p.source_id AS source_id, p.paragraph_id AS paragraph_id, "
            "e.name AS name"
        ),
        group_cypher=(
            "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
            "WHERE p.project_id_normalized = $pid "
            "AND id(p) = $wlo AND id(e) > $after "
            "RETURN id(p) AS _node_id, id(e) AS _entity_id, "
            "p.source_id AS source_id, p.paragraph_id AS paragraph_id, "
            "e.name AS name"
        ),
        count_cypher=(
            "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
            "WHERE p.project_id_normalized = $pid "
            "RETURN count(*) AS c"
        ),
        range_cypher=(
            "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
            "WHERE p.project_id_normalized = $pid "
            "RETURN min(id(p)) AS lo, max(id(p)) AS hi"
        ),
        id_exprs=["id(p)", "id(e)"],
        pid=project_id_normalized,
        batch=SYMBOL_READ_BATCH,
        what="HAS_ENTITY edges",
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


def _label_server_count(
    graph: Any, label: str, project_id_normalized: str
) -> Optional[int]:
    """Server-side node count for one label (aggregation, not result rows)."""
    rows = _run(
        graph,
        f"MATCH (n:{label}) "
        "WHERE n.project_id_normalized = $pid "
        "RETURN count(n) AS c",
        pid=project_id_normalized,
    )
    count = rows[0].get("c") if rows else None
    return int(count) if count is not None else None


def _read_labeled_symbols_paged(
    graph: Any,
    label: str,
    project_id_normalized: str,
    parser_type: str,
    target_type: str,
    batch: int,
) -> Tuple[List[EngineSymbol], Dict[str, int]]:
    """Read one label in internal-id window batches.

    Invariant (plan 261019-1700 Phase 03): rows read == the server's count
    for the label, regardless of the server's ``resultset_size`` — the
    server caps each query's RESULT SET (observed: FalkorDB default
    10000, silently truncating an unpaginated read), and paging keeps
    every page under it. A shortfall vs the count is a loud warning,
    never a silent cut. Paging shares :func:`_paged_rows` so label reads
    cost pages proportional to rows, not the quadratic-growing SKIP the
    pager replaced.
    """
    path_property = "source_file" if target_type == "endpoint" else "file_path"
    expected = _label_server_count(graph, label, project_id_normalized)
    rows = _paged_rows(
        graph,
        match_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "AND id(n) >= $wlo AND id(n) < $whi "
            "RETURN id(n) AS _node_id, n.name AS name, "
            "n.qualified_name AS qualified_name, "
            f"n.{path_property} AS file_path, n.start_line AS start_line, "
            "n.end_line AS end_line, n.language AS language, n.repo AS repo"
        ),
        group_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "AND id(n) = $wlo AND id(n) > $after "
            "RETURN id(n) AS _node_id, n.name AS name, "
            "n.qualified_name AS qualified_name, "
            f"n.{path_property} AS file_path, n.start_line AS start_line, "
            "n.end_line AS end_line, n.language AS language, n.repo AS repo"
        ),
        count_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "RETURN count(n) AS c"
        ),
        range_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "RETURN min(id(n)) AS lo, max(id(n)) AS hi"
        ),
        id_exprs=["id(n)"],
        pid=project_id_normalized,
        batch=batch,
        what=f"label {label}",
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
                repo=str(row.get("repo") or ""),
            )
        )
    stats = {"rows_read": len(rows), "expected": expected or 0, "kept": len(symbols)}
    if expected is not None and len(rows) < expected:
        print(
            f"[link-engine] warning: label {label} truncated — read "
            f"{len(rows)} of {expected} rows (server resultset cap?); "
            "these symbols are MISSING from the match index",
            file=sys.stderr,
        )
    return symbols, stats


def _read_labeled_symbols(
    graph: Any,
    label: str,
    project_id_normalized: str,
    parser_type: str,
    target_type: str,
) -> List[EngineSymbol]:
    symbols, _stats = _read_labeled_symbols_paged(
        graph, label, project_id_normalized, parser_type, target_type, SYMBOL_READ_BATCH
    )
    return symbols


def read_code_symbols_report(
    code_graph: Any,
    project_id_normalized: str,
    parser_type: Optional[str] = None,
    batch: int = SYMBOL_READ_BATCH,
    progress: ProgressFn = _NOOP_PROGRESS,
) -> Tuple[List[EngineSymbol], Dict[str, Dict[str, int]]]:
    """Read every symbol + endpoint label with per-label read accounting.

    Returns ``(symbols, {label: {rows_read, expected, kept}})`` — the
    per-label-accounted ``symbol_universe`` that makes a future
    resultset-cap regression visible in the sync report.
    """
    symbols: List[EngineSymbol] = []
    by_label: Dict[str, Dict[str, int]] = {}
    all_labels = [
        (label, "symbol") for label in CODE_SYMBOL_LABELS
    ] + [
        (label, "endpoint") for label in CODE_ENDPOINT_LABELS
    ]
    total_labels = len(all_labels)
    for idx, (label, target_type) in enumerate(all_labels, 1):
        t0 = time.monotonic()
        try:
            label_symbols, stats = _read_labeled_symbols_paged(
                code_graph, label, project_id_normalized, parser_type or "", target_type, batch
            )
        except Exception as exc:  # noqa: BLE001 - per-label degradation
            print(
                f"[link-engine] warning: label {label} unreadable ({exc})",
                file=sys.stderr,
            )
            by_label[label] = {"rows_read": 0, "expected": 0, "kept": 0, "error": 1}
            progress(f"  [{idx}/{total_labels}] {label}: ERROR ({exc})")
            continue
        elapsed = time.monotonic() - t0
        symbols.extend(label_symbols)
        by_label[label] = stats
        kept = stats.get("kept", len(label_symbols))
        progress(f"  [{idx}/{total_labels}] {label}: {kept} symbols ({elapsed:.1f}s)")
    return symbols, by_label


def read_code_symbols(
    code_graph: Any,
    project_id_normalized: str,
    parser_type: Optional[str] = None,
) -> List[EngineSymbol]:
    """Read symbol + endpoint labels; a missing label degrades to a warning.

    Graphs from older parsers lack some framework labels — one dead label
    must not take the whole build down. Reads are paginated, so the
    server's ``resultset_size`` never silently drops rows.
    """
    symbols, _by_label = read_code_symbols_report(code_graph, project_id_normalized, parser_type)
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


def _read_folder_inventory_entries(
    code_folders: Iterable[str],
    project_id: str,
) -> List[Tuple[str, Dict[str, str]]]:
    """Newest per-folder inventory as ``(resolved_folder, {rel → sha})``.

    Shared chain reader for both flat and repo-aware baseline loaders:
    ``{folder}/.cache/incremental_sync/{safe_project}_{scope}.json`` →
    ``inventory_path`` → ``{folder}/.cache/inventories/{snapshot}.json``
    with ``entries: {rel_path: {sha256, …}}``.
    """
    import re

    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project_id)).strip("._") or "project"
    result: List[Tuple[str, Dict[str, str]]] = []
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
        mapping: Dict[str, str] = {}
        for rel, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            sha = entry.get("sha256")
            if not sha:
                continue
            mapping[link_store.posix_path(rel)] = str(sha)
        if mapping:
            result.append((str(folder_root), mapping))
    return result


# ---------------------------------------------------------------------------
# SHA join (LINK_CONTRACT.md §1.5): baseline keys ↔ graph identities
# ---------------------------------------------------------------------------

JOIN_MATCHED = "matched"
JOIN_MISSING = "missing"
JOIN_AMBIGUOUS = "ambiguous"


def flatten_doc_rel(rel: str) -> str:
    """dev.py's ``source_id`` rule: POSIX rel with both separators → ``__``.

    Mirrors ``doc-tiny/graphrag_ingest_langextract._safe_source_id`` (and
    dev.py ``_prune_yake_rule_files``): ``a/b.md`` → ``a__b.md``. Never
    applied in reverse — file names may legitimately contain ``__``.
    """
    posix = link_store.posix_path(rel)
    return posix.replace("/", "__").replace("\\", "__")


def normalize_repo_key(value: Any) -> str:
    """Canonical lookup key for a graph ``repo`` / configured folder.

    Resolved absolute POSIX path, casefolded for lookup only (macOS/Windows
    case-insensitivity). Empty input maps to ``""`` — never a folder key.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return Path(text).expanduser().resolve().as_posix().casefold()
    except OSError:
        return Path(text).as_posix().casefold()


@dataclass(frozen=True)
class DocShaIndex:
    """Baseline lookup structure for one build (built once, not per record).

    ``direct`` holds POSIX rel keys, ``flattened`` their ``__``-spelling
    (dev.py ``source_id``), ``stems`` groups keys per file stem. A
    flattened id or stem shared by keys with *different* SHAs maps to
    ``None`` — genuine upstream ambiguity degrades to null, never a guess
    (the graph itself cannot tell the two files apart).
    """

    direct: Dict[str, str]
    flattened: Dict[str, Optional[str]]
    stems: Dict[str, List[str]]


def build_doc_sha_index(doc_shas: Mapping[str, str]) -> DocShaIndex:
    direct: Dict[str, str] = {}
    flattened: Dict[str, Optional[str]] = {}
    stems: Dict[str, List[str]] = {}
    for rel, sha in doc_shas.items():
        posix = link_store.posix_path(rel)
        direct[posix] = str(sha)
        flat_id = flatten_doc_rel(posix)
        existing = flattened.get(flat_id)
        if flat_id in flattened and existing != sha:
            flattened[flat_id] = None  # differing SHAs → ambiguous, stays so
        elif existing is None:
            flattened[flat_id] = str(sha)
        stems.setdefault(Path(posix).stem, []).append(posix)
    return DocShaIndex(direct=direct, flattened=flattened, stems=stems)


def _doc_sha_for(doc_id: str, index: DocShaIndex) -> Optional[str]:
    """Doc SHA per contract: direct rel → flattened → agreeing stem set."""
    doc_id = link_store.posix_path(doc_id)
    if not doc_id:
        return None
    sha = index.direct.get(doc_id)
    if sha:
        return sha
    sha = index.flattened.get(doc_id)
    if sha:
        return sha
    keys = index.stems.get(doc_id)
    if keys:
        stem_shas = {index.direct[key] for key in keys}
        if len(stem_shas) == 1:
            return next(iter(stem_shas))  # same content under one stem: exact
    return None


class CodeShaIndex:
    """Repo-aware resolver: ``(graph repo, graph path)`` → inventory SHA.

    Resolution order (never a naive suffix match — the live deployment carries 1
    exact cross-folder collision and 152 suffix shadows):

    1. ``(repo, path)`` exact against the repo-aware folder maps;
    2. a ``repo`` pinning a KNOWN folder is authoritative: a path its map
       lacks is ``missing`` — another folder's same-spelled file is a
       different file, never a borrowed SHA;
    3. unique cross-folder fallback — only when ``repo`` is null/foreign
       (endpoint labels; parsers stamping a project root): exactly one
       known folder whose folder-relative map contains ``path``;
    4. flat-map direct hit, then a unique component-boundary suffix match
       over flat project-relative keys (legacy callers);
    5. otherwise ``missing``; more than one candidate → ``ambiguous``.
    """

    def __init__(
        self,
        folders: Optional[Mapping[str, Mapping[str, str]]] = None,
        flat: Optional[Mapping[str, str]] = None,
    ):
        self.folders: Dict[str, Dict[str, str]] = {
            normalize_repo_key(folder): dict(mapping)
            for folder, mapping in (folders or {}).items()
            if normalize_repo_key(folder)
        }
        self.flat: Dict[str, str] = {
            link_store.posix_path(rel): str(sha)
            for rel, sha in (flat or {}).items()
        }
        # basename → flat keys sharing it (bounds the suffix fallback)
        self._flat_by_basename: Dict[str, List[str]] = {}
        for key in self.flat:
            self._flat_by_basename.setdefault(Path(key).name, []).append(key)

    def resolve(self, repo: Any, path: str) -> Tuple[Optional[str], str]:
        path = link_store.posix_path(path)
        if not path:
            return None, JOIN_MISSING
        repo_key = normalize_repo_key(repo)
        folder = self.folders.get(repo_key) if repo_key else None
        if folder is not None:
            # the pin is authoritative — no borrowing across folders
            if path in folder:
                return folder[path], JOIN_MATCHED
            return None, JOIN_MISSING
        if self.folders:
            hits = [
                mapping[path]
                for mapping in self.folders.values()
                if path in mapping
            ]
            if len(hits) == 1:
                return hits[0], JOIN_MATCHED
            if len(hits) > 1:
                return None, JOIN_AMBIGUOUS
        sha = self.flat.get(path)
        if sha:
            return sha, JOIN_MATCHED
        suffix_hits = [
            self.flat[key]
            for key in self._flat_by_basename.get(Path(path).name, ())
            if key.endswith("/" + path)
        ]
        if len(suffix_hits) == 1:
            return suffix_hits[0], JOIN_MATCHED
        if len(suffix_hits) > 1:
            return None, JOIN_AMBIGUOUS
        return None, JOIN_MISSING


def load_code_inventory_hashes(
    code_folders: Iterable[str],
    project_id: str,
    project_dir: Path,
) -> Dict[str, str]:
    """Read per-file SHAs from the newest code-sync inventory per folder.

    Flat project-wide view: paths are re-expressed relative to
    ``project_dir`` when the folder lives inside it, else kept
    folder-relative (identity stays stable either way). The repo-aware
    view lives in :func:`load_code_folder_inventories`.
    """
    merged: Dict[str, str] = {}
    project_dir = Path(project_dir).resolve()
    for folder_root, mapping in _read_folder_inventory_entries(code_folders, project_id):
        folder_path = Path(folder_root)
        for rel, sha in mapping.items():
            try:
                absolute = (folder_path / rel).resolve()
                stored = absolute.relative_to(project_dir).as_posix()
            except ValueError:
                stored = rel
            merged[stored] = sha
    return merged


def load_code_folder_inventories(
    code_folders: Iterable[str],
    project_id: str,
) -> Dict[str, Dict[str, str]]:
    """Repo-aware baseline: ``{resolved scan folder → {folder_rel → sha}}``.

    Folder keys are the resolved absolute POSIX paths the code graph's
    ``repo`` property stamps (``normalize_repo_key`` matches them,
    casefolded on lookup only). Values stay **folder-relative** — the
    same spelling the graph's ``file_path`` uses — so the
    ``(repo, path)`` join is an exact dict hit.
    """
    return {
        folder_root: dict(mapping)
        for folder_root, mapping in _read_folder_inventory_entries(
            code_folders, project_id
        )
    }


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------


def _code_identity_of(record: Mapping[str, Any]) -> tuple:
    """Record-side code identity (mirrors ``EngineSymbol.identity``)."""
    code = record.get("code") or {}
    lines = code.get("lines") or [None, None]
    return (
        link_store.posix_path(code.get("path")),
        str(code.get("name") or ""),
        (lines[0], lines[1]),
        str(code.get("target_type") or "symbol"),
    )


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
    code_folder_shas: Optional[Mapping[str, Mapping[str, str]]] = None,
    progress: ProgressFn = _NOOP_PROGRESS,
) -> BuildReport:
    """Run the full pipeline for one project.

    Complexity per step (n = data size, bounded by graph/file contents):

    * Step 1 — graph reads: O(P + E + S) where P=paragraphs, E=entity
      edges, S=symbols.  Doc reads run in parallel (2 threads).  Code
      reads iterate 14 labels sequentially (shared connection).
    * Step 2 — matching: O(P·T·M) with M bounded (exact-name, typically
      1–5 matches/token) → effectively O(total_tokens) = O(n).
    * Step 3 — SHA join: O(S) dict lookups, one per unique identity.
    * Step 4 — stale propagation: O(L) single pass over cached records
      (no re-read of links.jsonl).
    * Step 5 — write + coverage: O(F + L) where F=fresh pairs.

    ``dry_run`` computes everything and writes nothing.
    """
    t_pipeline = time.monotonic()
    links_dir = Path(links_dir)
    doc_index = build_doc_sha_index(doc_shas)
    code_index = CodeShaIndex(code_folder_shas or {}, code_shas)
    pid = _norm(project_id)

    # ── Step 1/6: Read graph data ────────────────────────────────────────
    # Doc reads are independent → parallel (2 threads, same read-only graph).
    # Code reads: 14 labels × (count + range + paged) → sequential per label.
    progress("[step 1/6] Reading graph data...")
    t1 = time.monotonic()

    paragraphs: List[ParagraphRef] = []
    entity_mentions: Dict[tuple, List[str]] = {}

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_paras = pool.submit(read_doc_paragraphs, doc_graph, pid)
            fut_entities = pool.submit(read_doc_entity_names, doc_graph, pid)
            paragraphs = fut_paras.result()
            entity_mentions = fut_entities.result()
    except Exception:
        paragraphs = read_doc_paragraphs(doc_graph, pid)
        entity_mentions = read_doc_entity_names(doc_graph, pid)

    progress(
        f"[step 1/6]   doc paragraphs: {len(paragraphs)}  "
        f"entity mentions: {len(entity_mentions)}  "
        f"({time.monotonic() - t1:.1f}s)"
    )

    t1b = time.monotonic()
    symbols, symbol_stats = read_code_symbols_report(
        code_graph, pid, parser_type, progress=progress,
    )
    progress(
        f"[step 1/6]   code symbols: {len(symbols)}  "
        f"({time.monotonic() - t1b:.1f}s)"
    )
    progress(f"[step 1/6] Reading graph data done ({time.monotonic() - t1:.1f}s)")

    paragraph_anchors = {paragraph.anchor for paragraph in paragraphs}
    symbol_identities = {symbol.identity for symbol in symbols}
    symbol_universe_expected = sum(
        int(stats.get("expected") or 0) for stats in symbol_stats.values()
    )

    if not reconcile and not dry_run and not symbols:
        prior_state = link_store.load_state(links_dir) or {}
        prior_active = ((prior_state.get("counts") or {}).get("active")) or 0
        if prior_active > 0:
            raise LinkEngineError(
                f"code-symbol read collapsed to 0 (paragraphs="
                f"{len(paragraphs)}) against {prior_active} active links "
                "— refusing to stale-mark; check the store and re-run, "
                "or use --reconcile to force"
            )

    # ── Step 2/6: SHA join (O(S), one dict lookup per unique identity) ───
    progress("[step 2/6] Building SHA join index...")
    t2 = time.monotonic()
    code_join_by_identity: Dict[tuple, Tuple[Optional[str], str]] = {}
    for symbol in symbols:
        if symbol.identity not in code_join_by_identity:
            code_join_by_identity[symbol.identity] = code_index.resolve(
                symbol.repo, symbol.path
            )
    progress(
        f"[step 2/6] SHA join: {len(code_join_by_identity)} unique identities "
        f"({time.monotonic() - t2:.1f}s)"
    )

    # ── Step 3/6: Match paragraphs × symbols (O(P·T·M) ≈ O(n)) ─────────
    progress("[step 3/6] Matching paragraphs × symbols (exact-name)...")
    t3 = time.monotonic()
    fresh_pairs = compute_fresh_pairs(paragraphs, entity_mentions, symbols)
    progress(
        f"[step 3/6] Matching: {len(fresh_pairs)} pairs "
        f"({time.monotonic() - t3:.1f}s)"
    )

    sha_join = _empty_sha_join()
    fresh_records: List[Dict[str, Any]] = []
    for paragraph, symbol in fresh_pairs:
        doc_sha = _doc_sha_for(paragraph.doc_id, doc_index)
        sha_join["doc_matched" if doc_sha else "doc_missing"] += 1
        code_sha, outcome = code_join_by_identity[symbol.identity]
        if outcome == JOIN_AMBIGUOUS:
            sha_join["code_ambiguous"] += 1
        elif outcome == JOIN_MATCHED:
            sha_join["code_matched"] += 1
        else:
            sha_join["code_missing"] += 1
        fresh_records.append(
            build_record(
                project_id=project_id,
                paragraph=paragraph,
                symbol=symbol,
                doc_sha=doc_sha,
                code_sha=code_sha,
            )
        )

    # ── Step 4/6: Load existing links + stale propagation (O(L)) ─────────
    progress("[step 4/6] Loading existing links...")
    t4 = time.monotonic()
    existing = link_store.load_links(links_dir)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for record in existing:
        latest[link_store.record_key(record)] = record
    progress(
        f"[step 4/6] Existing links: {len(existing)} records "
        f"({time.monotonic() - t4:.1f}s)"
    )

    def _sha_changed(record: Mapping[str, Any]) -> bool:
        doc_sha = _doc_sha_for(
            str((record.get("doc") or {}).get("doc_id") or ""), doc_index
        )
        previous_doc_sha = (record.get("doc") or {}).get("source_sha")
        if doc_sha and previous_doc_sha and doc_sha != previous_doc_sha:
            return True
        code_sha, _outcome = code_join_by_identity.get(
            _code_identity_of(record), (None, JOIN_MISSING)
        )
        previous_code_sha = (record.get("code") or {}).get("source_sha")
        if code_sha and previous_code_sha and code_sha != previous_code_sha:
            return True
        return False

    def _drifted(record: Mapping[str, Any]) -> bool:
        if _code_identity_of(record) not in symbol_identities:
            return True
        doc = record.get("doc") or {}
        anchor = (str(doc.get("doc_id") or ""), doc.get("paragraph_id"))
        if anchor not in paragraph_anchors:
            return True
        return _sha_changed(record)

    progress("[step 4/6] Stale propagation...")
    t4b = time.monotonic()
    staled: List[Dict[str, Any]] = []
    if not dry_run:
        if reconcile:
            staled = link_store.mark_stale(
                links_dir, lambda record: True, _loaded_records=existing,
            )
        else:
            staled = link_store.mark_stale(
                links_dir, _drifted, _loaded_records=existing,
            )
    progress(
        f"[step 4/6] Stale: {len(staled)} staled  "
        f"({time.monotonic() - t4b:.1f}s)"
    )

    # ── Step 5/6: Delta filter + write new records ───────────────────────
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

    progress(f"[step 5/6] Writing {len(new_records)} new links...")
    t5 = time.monotonic()
    if not dry_run and new_records:
        link_store.append_links(links_dir, new_records)
    progress(f"[step 5/6] Write done ({time.monotonic() - t5:.1f}s)")

    # ── Step 6/6: Coverage + state ───────────────────────────────────────
    progress("[step 6/6] Computing coverage + saving state...")
    t6 = time.monotonic()

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
                "sha_join": dict(sha_join),
                "symbol_universe_by_label": symbol_stats,
                "symbol_universe_expected": symbol_universe_expected,
            },
        )
    total_elapsed = time.monotonic() - t_pipeline
    progress(
        f"[step 6/6] Coverage: {coverage['active_links']} active, "
        f"{coverage['stale_links']} stale  "
        f"({time.monotonic() - t6:.1f}s)"
    )
    progress(f"─── done in {total_elapsed:.1f}s ───")

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
        sha_join=sha_join,
        symbol_universe_by_label=symbol_stats,
        symbol_universe_expected=symbol_universe_expected,
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
    "CODE_ENDPOINT_LABELS",
    "CODE_SYMBOL_LABELS",
    "DOC_EXTENSIONS",
    "DocShaIndex",
    "EngineSymbol",
    "JOIN_AMBIGUOUS",
    "JOIN_MATCHED",
    "JOIN_MISSING",
    "LinkEngineError",
    "ParagraphRef",
    "build_doc_sha_index",
    "build_links",
    "build_record",
    "compute_fresh_pairs",
    "flatten_doc_rel",
    "load_code_folder_inventories",
    "load_code_inventory_hashes",
    "load_sync_state_hashes",
    "normalize_repo_key",
    "SYMBOL_READ_BATCH",
    "read_code_symbols",
    "read_code_symbols_report",
    "read_doc_entity_names",
    "read_doc_paragraphs",
    "split_shas_by_side",
]
