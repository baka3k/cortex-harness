"""Link engine — delta → candidates → IDF/affinity score → stale propagation → write.

Pure library: takes opened graph handles (anything with ``run(query,
params)``) plus paths in, produces writes through :mod:`link_store`. Knows
nothing about MCP or the CLI.

Isolation contract:

* reads both graphs through the lease-free ``read_only_opener`` — no
  ``StorageLease``, no writes, no Qdrant access;
* exact-name + qualified-segment matching with IDF/co-occurrence gating;
  provenance ``exact-name``, score ``1.0`` for low-DF matches and
  IDF-weighted ``(0, 1.0]`` for higher-DF matches;
* identity is ``(path, name, start_line, end_line, parser_type,
  source_sha)`` code-side and ``(doc_id, paragraph_id, source_sha)``
  doc-side — never graph-internal ids;
* delta SHAs come from the sync baselines (``.cortext-harness/sync-state/``
  plus doc source folders for docs, ``.cache/inventories/`` for code) — the
  graphs persist no per-document SHA.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter
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
# Framework-writer labels; identity uses ``source_file`` or ``file_path``
# and carries no line numbers.
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

# v2 scoring & combinatorial explosion guards
MAX_UNSCOPED_SYMBOL_DF = 8
MAX_CANDIDATES_PER_TOKEN = 10
MAX_PARAGRAPHS_PER_DOC_SYMBOL = 3
SNIPPET_STORE_MAX_CHARS = 240
REDACTED_GLYPH = "▮"

_GENERIC_FILE_STEMS = frozenset({
    "common", "program", "assemblyinfo", "resources", "settings",
    "index", "main", "utils", "helper", "helpers", "base", "default",
    "config", "constants", "types", "models", "startup",
})
_COMPOUND_CODE_SUFFIXES = (
    ".designer.vb",
    ".designer.cs",
    ".razor.cs",
    ".g.cs",
    ".g.vb",
    ".vb",
    ".cs",
    ".razor",
    ".py",
    ".java",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
)


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
# Sensitive-term snippet redaction (pure file reader, no mcp_server import)
# ---------------------------------------------------------------------------


def _load_repo_sensitive_terms() -> List[Tuple[str, bool]]:
    env_override = os.environ.get("CORTEX_SENSITIVE_TERMS")
    candidate = (
        Path(env_override).expanduser()
        if env_override
        else Path(__file__).resolve().parents[1] / ".sensitive-terms"
    )
    if not candidate.is_file():
        return []
    terms: List[Tuple[str, bool]] = []
    try:
        raw_lines = candidate.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("*") and line.endswith("*") and len(line) > 2:
            line = line[1:-1].strip()
        if line.startswith("case:"):
            term = line[len("case:"):].strip()
            if term:
                terms.append((term, True))
        else:
            terms.append((line, False))
    return terms


def _redact_snippet_for_store(
    text: str,
    terms: Sequence[Tuple[str, bool]],
    max_chars: int = SNIPPET_STORE_MAX_CHARS,
) -> str:
    output = str(text or "")
    if not output:
        return ""
    for term, case_sensitive in terms:
        if not term:
            continue
        flags = 0 if case_sensitive else re.IGNORECASE
        replacement = REDACTED_GLYPH * min(len(term), 8)
        output = re.sub(re.escape(term), replacement, output, flags=flags)
    return output[:max_chars]


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
    """All rows of one match, paged over the anchor's internal-id space."""
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
    doc_graph: Any,
    project_id_normalized: str,
    match_index: Optional[Mapping[Tuple[str, ...], Sequence[CodeSymbolRef]]] = None,
) -> Dict[tuple, List[str]]:
    """Map ``(source_id, paragraph_id)`` → entity names mentioning it.

    When ``match_index`` is supplied and the graph exposes ``Entity`` nodes,
    uses a 2-stage filtered query (read distinct ``Entity`` nodes first, keep
    only identifier-shaped entities that match a known code symbol, then fetch
    ``HAS_ENTITY`` edges for those entity ids). This avoids scanning 2M+
    prose entity edges when only ~1% match code symbols.
    """
    if match_index is not None:
        try:
            cnt_rows = _run(
                doc_graph,
                "MATCH (e:Entity) WHERE e.project_id_normalized = $pid RETURN count(e) AS c",
                pid=project_id_normalized,
            )
            entity_count = int(cnt_rows[0]["c"]) if cnt_rows and cnt_rows[0].get("c") is not None else 0
        except Exception:
            entity_count = 0

        if entity_count > 0:
            entity_rows = _paged_rows(
                doc_graph,
                match_cypher=(
                    "MATCH (e:Entity) "
                    "WHERE e.project_id_normalized = $pid "
                    "AND id(e) >= $wlo AND id(e) < $whi "
                    "RETURN id(e) AS _node_id, e.name AS name"
                ),
                group_cypher=(
                    "MATCH (e:Entity) "
                    "WHERE e.project_id_normalized = $pid "
                    "AND id(e) = $wlo AND id(e) > $after "
                    "RETURN id(e) AS _node_id, e.name AS name"
                ),
                count_cypher=(
                    "MATCH (e:Entity) "
                    "WHERE e.project_id_normalized = $pid "
                    "RETURN count(e) AS c"
                ),
                range_cypher=(
                    "MATCH (e:Entity) "
                    "WHERE e.project_id_normalized = $pid "
                    "RETURN min(id(e)) AS lo, max(id(e)) AS hi"
                ),
                id_exprs=["id(e)"],
                pid=project_id_normalized,
                batch=SYMBOL_READ_BATCH,
                what="Entity",
            )
            matched_eids: List[int] = []
            for row in entity_rows:
                eid = row.get("_node_id")
                name = str(row.get("name") or "").strip()
                if (
                    eid is not None
                    and normalizers.is_linkable_entity_token(name)
                    and normalizers.match_index_lookup(match_index, name)
                ):
                    matched_eids.append(int(eid))

            mentions: Dict[tuple, List[str]] = {}
            for offset in range(0, len(matched_eids), 500):
                chunk = matched_eids[offset : offset + 500]
                edge_rows = _run(
                    doc_graph,
                    "MATCH (p:Paragraph)-[:HAS_ENTITY]->(e:Entity) "
                    "WHERE p.project_id_normalized = $pid AND id(e) IN $eids "
                    "RETURN p.source_id AS source_id, p.paragraph_id AS paragraph_id, "
                    "e.name AS name",
                    pid=project_id_normalized,
                    eids=chunk,
                )
                for row in edge_rows:
                    anchor = (str(row.get("source_id") or ""), row.get("paragraph_id"))
                    name = str(row.get("name") or "").strip()
                    if not anchor[0] or not name:
                        continue
                    names = mentions.setdefault(anchor, [])
                    if name not in names:
                        names.append(name)
            return mentions

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
    mentions = {}
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

    Returns both ``n.file_path`` and ``n.source_file`` for endpoint labels so
    parsers that stamp either property (e.g. ASP.NET Core ``HttpEndpoint`` /
    ``Route`` using ``file_path`` vs ``ApiEndpoint`` using ``source_file``)
    preserve 100% of their endpoints.
    """
    expected = _label_server_count(graph, label, project_id_normalized)
    rows = _paged_rows(
        graph,
        match_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "AND id(n) >= $wlo AND id(n) < $whi "
            "RETURN id(n) AS _node_id, n.name AS name, "
            "n.qualified_name AS qualified_name, "
            "n.file_path AS file_path, n.source_file AS source_file, "
            "n.start_line AS start_line, "
            "n.end_line AS end_line, n.language AS language, n.repo AS repo"
        ),
        group_cypher=(
            f"MATCH (n:{label}) "
            "WHERE n.project_id_normalized = $pid "
            "AND id(n) = $wlo AND id(n) > $after "
            "RETURN id(n) AS _node_id, n.name AS name, "
            "n.qualified_name AS qualified_name, "
            "n.file_path AS file_path, n.source_file AS source_file, "
            "n.start_line AS start_line, "
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
        raw_path = (
            row.get("source_file") or row.get("file_path")
            if target_type == "endpoint"
            else row.get("file_path") or row.get("source_file")
        )
        path = link_store.posix_path(raw_path)
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
    """Read every symbol + endpoint label with per-label read accounting."""
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
    """Read symbol + endpoint labels; a missing label degrades to a warning."""
    symbols, _by_label = read_code_symbols_report(code_graph, project_id_normalized, parser_type)
    return symbols


# ---------------------------------------------------------------------------
# Delta inputs: sync baselines + doc folder SHA supplement
# ---------------------------------------------------------------------------


def load_sync_state_hashes(project_dir: Path) -> Dict[str, str]:
    """Merge ``file_hashes`` from every ``.cortext-harness/sync-state/*.json``."""
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


def load_doc_folder_hashes(
    doc_folders: Iterable[str],
    existing: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Supplement sync-state doc SHAs by scanning configured doc source folders.

    When a doc folder was ingested across multiple partial runs or before
    sync-state tracking captured every file, scanning the configured doc
    folders ensures 100% of on-disk doc files have a valid SHA-256 baseline.
    """
    merged: Dict[str, str] = {
        link_store.posix_path(rel): str(sha)
        for rel, sha in (existing or {}).items()
    }
    for folder in doc_folders:
        root = Path(folder).expanduser()
        if not root.is_dir():
            continue
        root_resolved = root.resolve()
        for file_path in sorted(root_resolved.rglob("*")):
            if not file_path.is_file():
                continue
            name = file_path.name
            if name.startswith("~$") or name.startswith("."):
                continue
            if file_path.suffix.lower() not in DOC_EXTENSIONS:
                continue
            try:
                rel = file_path.relative_to(root_resolved).as_posix()
            except ValueError:
                continue
            if rel in merged:
                continue
            try:
                merged[rel] = hashlib.sha256(file_path.read_bytes()).hexdigest()
            except OSError:
                continue
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
    """Per-folder inventory as ``(resolved_folder, {rel → sha})``.

    Merges all valid incremental_sync scope states for the folder in
    chronological ``last_run_after`` order (oldest first, newest last) so
    multi-language folders keep every language's file hashes while newest
    snapshots win on overlapping files.
    """
    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project_id)).strip("._") or "project"
    result: List[Tuple[str, Dict[str, str]]] = []
    for folder in code_folders:
        cache_dir = Path(folder).expanduser() / ".cache" / "incremental_sync"
        if not cache_dir.is_dir():
            continue
        candidates = sorted(cache_dir.glob(f"{safe_project}_*.json"))
        legacy = cache_dir.glob(f"{safe_project}.json")
        candidates.extend(sorted(legacy))
        states: List[Tuple[str, int, Mapping[str, Any]]] = []
        for idx, state_path in enumerate(candidates):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            states.append((str(payload.get("last_run_after") or ""), idx, payload))
        if not states:
            continue
        states.sort(key=lambda item: (item[0], item[1]))
        folder_root = Path(folder).expanduser().resolve()
        mapping: Dict[str, str] = {}
        for _run_at, _idx, state_payload in states:
            inventory_path = str(state_payload.get("inventory_path") or "").strip()
            if not inventory_path:
                continue
            try:
                inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries = inventory.get("entries") if isinstance(inventory, Mapping) else None
            if not isinstance(entries, Mapping):
                continue
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
    """dev.py's ``source_id`` rule: POSIX rel with both separators → ``__``."""
    posix = link_store.posix_path(rel)
    return posix.replace("/", "__").replace("\\", "__")


def normalize_repo_key(value: Any) -> str:
    """Canonical lookup key for a graph ``repo`` / configured folder."""
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
    (dev.py ``source_id``), ``flattened_no_ext`` their extensionless
    ``__``-spelling, and ``stems`` groups keys per file stem. Any key shared
    by files with *different* SHAs maps to ``None`` — genuine upstream
    ambiguity degrades to null, never a guess.
    """

    direct: Dict[str, str]
    flattened: Dict[str, Optional[str]]
    stems: Dict[str, List[str]]
    flattened_no_ext: Dict[str, Optional[str]] = field(default_factory=dict)


def _record_unambiguous_sha(
    table: Dict[str, Optional[str]], key: str, sha: str
) -> None:
    existing = table.get(key)
    if key in table and existing != sha:
        table[key] = None  # differing SHAs → ambiguous, stays so
    elif existing is None and key not in table:
        table[key] = str(sha)


def build_doc_sha_index(doc_shas: Mapping[str, str]) -> DocShaIndex:
    direct: Dict[str, str] = {}
    flattened: Dict[str, Optional[str]] = {}
    flattened_no_ext: Dict[str, Optional[str]] = {}
    stems: Dict[str, List[str]] = {}
    for rel, sha in doc_shas.items():
        base_posix = link_store.posix_path(rel)
        sha_str = str(sha)
        variants = list(
            dict.fromkeys(
                (
                    base_posix,
                    unicodedata.normalize("NFC", base_posix),
                    unicodedata.normalize("NFD", base_posix),
                )
            )
        )
        for posix in variants:
            direct[posix] = sha_str
            flat_id = flatten_doc_rel(posix)
            _record_unambiguous_sha(flattened, flat_id, sha_str)
            p_obj = Path(posix)
            if p_obj.suffix:
                no_ext = flatten_doc_rel(p_obj.with_suffix("").as_posix())
                _record_unambiguous_sha(flattened_no_ext, no_ext, sha_str)
            stems.setdefault(p_obj.stem, []).append(posix)
    return DocShaIndex(
        direct=direct,
        flattened=flattened,
        stems=stems,
        flattened_no_ext=flattened_no_ext,
    )


def _doc_sha_for(doc_id: str, index: DocShaIndex) -> Optional[str]:
    """Doc SHA per contract: direct rel → flattened → flattened_no_ext → stem."""
    doc_id = link_store.posix_path(doc_id)
    if not doc_id:
        return None
    variants = list(
        dict.fromkeys(
            (
                doc_id,
                unicodedata.normalize("NFC", doc_id),
                unicodedata.normalize("NFD", doc_id),
            )
        )
    )
    for variant in variants:
        sha = index.direct.get(variant)
        if sha:
            return sha
    for variant in variants:
        sha = index.flattened.get(variant)
        if sha:
            return sha
    for variant in variants:
        if variant in index.flattened and index.flattened[variant] is None:
            return None
        sha = index.flattened_no_ext.get(variant)
        if sha:
            return sha
    for variant in variants:
        keys = index.stems.get(variant)
        if keys:
            stem_shas = {index.direct[key] for key in keys if key in index.direct}
            if len(stem_shas) == 1:
                return next(iter(stem_shas))
    return None


class CodeShaIndex:
    """Repo-aware resolver: ``(graph repo, graph path)`` → inventory SHA."""

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
    """Read per-file SHAs from the code-sync inventories per folder."""
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
    """Repo-aware baseline: ``{resolved scan folder → {folder_rel → sha}}``."""
    return {
        folder_root: dict(mapping)
        for folder_root, mapping in _read_folder_inventory_entries(
            code_folders, project_id
        )
    }


# ---------------------------------------------------------------------------
# Record assembly & IDF/affinity-scored candidate generation
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
    score: float = normalizers.EXACT_NAME_SCORE,
    provenance: str = normalizers.EXACT_NAME_PROVENANCE,
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
        "score": float(score),
        "provenance": str(provenance),
    }


def _clean_file_stem(path_str: str) -> str:
    """Lowercased base module stem stripped of compound language suffixes."""
    name = Path(link_store.posix_path(path_str)).name.casefold()
    for suffix in _COMPOUND_CODE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return Path(name).stem.casefold()


def compute_scored_pairs(
    paragraphs: Sequence[ParagraphRef],
    entity_mentions: Mapping[tuple, Sequence[str]],
    symbols: Sequence[EngineSymbol],
    *,
    match_index: Optional[Dict[Tuple[str, ...], List[CodeSymbolRef]]] = None,
) -> List[Tuple[ParagraphRef, EngineSymbol, float, str]]:
    """Compute ``(paragraph, symbol, score, provenance)`` pairs with IDF gating.

    * Symbols appearing in ``<= 2`` files keep ``score = 1.0`` (exact-name).
    * Symbols appearing in ``3 .. MAX_UNSCOPED_SYMBOL_DF`` files receive an
      IDF-weighted score ``(0, 1.0]`` with a ``+0.20`` file-affinity bonus.
    * High-DF symbols (``file_df > MAX_UNSCOPED_SYMBOL_DF``) are linked ONLY
      when the paragraph/document establishes file-context affinity (the
      symbol's file stem appears in ``doc_id`` or co-occurs with a low-DF
      symbol ``file_df <= 3`` from the same file/module in that paragraph).
    * Per-token fan-out is capped at ``MAX_CANDIDATES_PER_TOKEN`` and
      redundant paragraph links for the same ``(doc_id, symbol.identity)``
      are capped at ``MAX_PARAGRAPHS_PER_DOC_SYMBOL``.
    """
    if not paragraphs or not symbols:
        return []
    symbol_refs = [symbol.as_symbol_ref() for symbol in symbols]
    symbol_by_ref = dict(zip(symbol_refs, symbols))
    if match_index is None:
        match_index = normalizers.build_match_index(symbol_refs)

    # Document frequency: number of distinct code files defining each match key
    key_file_df: Dict[Tuple[str, ...], int] = {
        key: len({ref.path for ref in refs})
        for key, refs in match_index.items()
    }
    sym_stem: Dict[tuple, str] = {
        symbol.identity: _clean_file_stem(symbol.path) for symbol in symbols
    }
    meaningful_stems = {
        stem for stem in sym_stem.values()
        if len(stem) >= 4 and stem not in _GENERIC_FILE_STEMS
    }
    unique_doc_ids = {p.doc_id for p in paragraphs}
    doc_stems: Dict[str, set[str]] = {}
    for doc_id in unique_doc_ids:
        doc_lower = doc_id.casefold()
        doc_stems[doc_id] = {
            stem for stem in meaningful_stems if stem in doc_lower
        }

    text_keys_cache: Dict[str, List[Tuple[str, ...]]] = {}
    doc_sym_counts: Counter = Counter()
    results: List[Tuple[ParagraphRef, EngineSymbol, float, str]] = []

    for paragraph in paragraphs:
        cached_keys = text_keys_cache.get(paragraph.text)
        if cached_keys is None:
            raw_tokens = normalizers.extract_identifier_tokens(paragraph.text)
            keys_list: List[Tuple[str, ...]] = []
            seen_k: set[Tuple[str, ...]] = set()
            for tok in raw_tokens:
                if not normalizers.is_linkable_token(tok):
                    continue
                k = normalizers.token_match_key(tok)
                if k and k in match_index and k not in seen_k:
                    seen_k.add(k)
                    keys_list.append(k)
            text_keys_cache[paragraph.text] = keys_list
            cached_keys = keys_list

        ent_tokens = entity_mentions.get(paragraph.anchor, ())
        if ent_tokens:
            para_keys = list(cached_keys)
            seen_para_k = set(para_keys)
            for ent in ent_tokens:
                if not normalizers.is_linkable_entity_token(ent):
                    continue
                k = normalizers.token_match_key(ent)
                if k and k in match_index and k not in seen_para_k:
                    seen_para_k.add(k)
                    para_keys.append(k)
        else:
            para_keys = cached_keys

        if not para_keys:
            continue

        # Establish active file-context stems from doc_id + low-DF symbols in paragraph
        active_stems = set(doc_stems.get(paragraph.doc_id) or ())
        for key in para_keys:
            if 0 < key_file_df.get(key, 0) <= 3:
                for ref in match_index.get(key, ()):
                    st = sym_stem[symbol_by_ref[ref].identity]
                    if len(st) >= 4 and st not in _GENERIC_FILE_STEMS:
                        active_stems.add(st)

        best_by_identity: Dict[tuple, Tuple[float, EngineSymbol]] = {}
        for key in para_keys:
            df = key_file_df.get(key, 0)
            if df == 0:
                continue
            token_candidates: List[Tuple[float, EngineSymbol]] = []
            for ref in match_index.get(key, ()):
                symbol = symbol_by_ref[ref]
                st = sym_stem[symbol.identity]
                has_affinity = bool(st and st in active_stems)
                if df > MAX_UNSCOPED_SYMBOL_DF and not has_affinity:
                    continue
                if df <= 2:
                    score = normalizers.EXACT_NAME_SCORE
                else:
                    base = 0.95 if symbol.target_type == "endpoint" else 0.85
                    idf = 1.0 / (1.0 + 0.30 * math.log(df))
                    bonus = 0.20 if has_affinity else 0.0
                    score = round(min(1.0, base * idf + bonus), 4)
                token_candidates.append((score, symbol))

            if len(token_candidates) > MAX_CANDIDATES_PER_TOKEN:
                token_candidates.sort(
                    key=lambda item: (-item[0], item[1].path, item[1].name)
                )
                token_candidates = token_candidates[:MAX_CANDIDATES_PER_TOKEN]

            for score, symbol in token_candidates:
                prev = best_by_identity.get(symbol.identity)
                if prev is None or score > prev[0]:
                    best_by_identity[symbol.identity] = (score, symbol)

        for score, symbol in best_by_identity.values():
            ds_key = (paragraph.doc_id, symbol.identity)
            if doc_sym_counts[ds_key] >= MAX_PARAGRAPHS_PER_DOC_SYMBOL:
                continue
            doc_sym_counts[ds_key] += 1
            results.append(
                (paragraph, symbol, score, normalizers.EXACT_NAME_PROVENANCE)
            )

    return results


def compute_fresh_pairs(
    paragraphs: Sequence[ParagraphRef],
    entity_mentions: Mapping[tuple, Sequence[str]],
    symbols: Sequence[EngineSymbol],
) -> List[Tuple[ParagraphRef, EngineSymbol]]:
    """All matched ``(paragraph, symbol)`` pairs, deduped, deterministic."""
    return [
        (paragraph, symbol)
        for paragraph, symbol, _score, _prov in compute_scored_pairs(
            paragraphs, entity_mentions, symbols
        )
    ]


# ---------------------------------------------------------------------------
# The pipeline
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
    """Run the full pipeline for one project."""
    t_pipeline = time.monotonic()
    links_dir = Path(links_dir)
    doc_index = build_doc_sha_index(doc_shas)
    code_index = CodeShaIndex(code_folder_shas or {}, code_shas)
    pid = _norm(project_id)

    # ── Step 1/6: Read graph data ────────────────────────────────────────
    progress("[step 1/6] Reading graph data...")
    t1 = time.monotonic()

    paragraphs = read_doc_paragraphs(doc_graph, pid)
    progress(
        f"[step 1/6]   doc paragraphs: {len(paragraphs)}  "
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

    t1c = time.monotonic()
    symbol_refs = [symbol.as_symbol_ref() for symbol in symbols]
    match_index = normalizers.build_match_index(symbol_refs)
    entity_mentions = read_doc_entity_names(
        doc_graph, pid, match_index=match_index
    )
    progress(
        f"[step 1/6]   entity mentions: {len(entity_mentions)}  "
        f"({time.monotonic() - t1c:.1f}s)"
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

    # ── Step 3/6: Match paragraphs × symbols (IDF + affinity gated) ──────
    progress("[step 3/6] Matching paragraphs × symbols...")
    t3 = time.monotonic()
    scored_pairs = compute_scored_pairs(
        paragraphs, entity_mentions, symbols, match_index=match_index
    )
    progress(
        f"[step 3/6] Matching: {len(scored_pairs)} pairs "
        f"({time.monotonic() - t3:.1f}s)"
    )

    sha_join = _empty_sha_join()
    fresh_records: List[Dict[str, Any]] = []
    linked_paragraphs: Dict[Tuple[str, Any], str] = {}
    for paragraph, symbol, score, provenance in scored_pairs:
        doc_sha = _doc_sha_for(paragraph.doc_id, doc_index)
        sha_join["doc_matched" if doc_sha else "doc_missing"] += 1
        code_sha, outcome = code_join_by_identity[symbol.identity]
        if outcome == JOIN_AMBIGUOUS:
            sha_join["code_ambiguous"] += 1
        elif outcome == JOIN_MATCHED:
            sha_join["code_matched"] += 1
        else:
            sha_join["code_missing"] += 1
        if paragraph.text and paragraph.anchor not in linked_paragraphs:
            linked_paragraphs[paragraph.anchor] = paragraph.text
        fresh_records.append(
            build_record(
                project_id=project_id,
                paragraph=paragraph,
                symbol=symbol,
                doc_sha=doc_sha,
                code_sha=code_sha,
                score=score,
                provenance=provenance,
            )
        )

    sensitive_terms = _load_repo_sensitive_terms()
    snippets: Dict[Tuple[str, Any], str] = {
        anchor: _redact_snippet_for_store(raw_text, sensitive_terms)
        for anchor, raw_text in linked_paragraphs.items()
    }

    # ── Step 4/6: Load existing links + stale propagation (O(L)) ─────────
    progress("[step 4/6] Loading existing links...")
    t4 = time.monotonic()
    raw_log_path = links_dir / link_store.LINKS_FILENAME
    oversized_reconcile = (
        reconcile
        and raw_log_path.is_file()
        and raw_log_path.stat().st_size > link_store.MAX_RAW_LOG_BYTES
    )
    if oversized_reconcile:
        existing: List[Dict[str, Any]] = []
        latest: Dict[tuple, Dict[str, Any]] = {}
        progress(
            f"[step 4/6] Reconcile compact mode: bypassing oversized "
            f"links.jsonl ({raw_log_path.stat().st_size // (1024 * 1024)} MB)"
        )
    else:
        existing = link_store.load_links(links_dir)
        latest = {link_store.record_key(record): record for record in existing}
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
    if not dry_run and not oversized_reconcile:
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
    if not dry_run:
        if oversized_reconcile:
            link_store.compact_store_on_reconcile(
                links_dir, new_records, snippets=snippets
            )
        elif new_records:
            link_store.append_links(
                links_dir, new_records, snippets=snippets
            )
        elif snippets:
            link_store.save_paragraph_snippets(links_dir, snippets)
    progress(f"[step 5/6] Write done ({time.monotonic() - t5:.1f}s)")

    # ── Step 6/6: Coverage + state ───────────────────────────────────────
    progress("[step 6/6] Computing coverage + saving state...")
    t6 = time.monotonic()

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
    "MAX_CANDIDATES_PER_TOKEN",
    "MAX_PARAGRAPHS_PER_DOC_SYMBOL",
    "MAX_UNSCOPED_SYMBOL_DF",
    "ParagraphRef",
    "build_doc_sha_index",
    "build_links",
    "build_record",
    "compute_fresh_pairs",
    "compute_scored_pairs",
    "flatten_doc_rel",
    "load_code_folder_inventories",
    "load_code_inventory_hashes",
    "load_doc_folder_hashes",
    "load_sync_state_hashes",
    "normalize_repo_key",
    "SYMBOL_READ_BATCH",
    "read_code_symbols",
    "read_code_symbols_report",
    "read_doc_entity_names",
    "read_doc_paragraphs",
    "split_shas_by_side",
]
