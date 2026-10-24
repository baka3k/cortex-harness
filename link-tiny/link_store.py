"""Indexed SQLite WAL + append-only link store — the only module that writes link data.

Layout per project: ``{instance_root}/links/{project_id}/``

* ``links.db`` — normalized SQLite WAL database with B-Tree indexes on
  documents, paragraphs (with pre-redacted snippets), symbols, and links
  for sub-millisecond MCP queries and fast delta/coverage computation.
* ``links.jsonl`` — append-only log of link records (compacted automatically
  on ``--reconcile`` when oversized).
* ``state.json`` — SHA snapshot (doc + code), counts, coverage telemetry.
  Written atomically (tmp + rename, the ``layout.py`` pattern).

Pure file/SQLite I/O: no graph access, no MCP imports, no harness imports.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple


LINKS_FILENAME = "links.jsonl"
LINKS_DB_FILENAME = "links.db"
STATE_FILENAME = "state.json"
LOCK_FILENAME = "links.lock"
SCHEMA_VERSION = 1

STATUS_ACTIVE = "active"
STATUS_STALE = "stale"

# Threshold above which --reconcile truncates legacy oversized JSONL logs
# (e.g. multi-GB v1 logs) before writing the reconciled set.
MAX_RAW_LOG_BYTES = 10 * 1024 * 1024  # 10 MB

# Fields every record must carry (invariant 4: provenance + score are
# mandatory from day one; consumers and the injection mitigation depend on
# them being present).
_REQUIRED_TOP_FIELDS = ("project_id", "relation", "provenance", "score")
_REQUIRED_DOC_FIELDS = ("doc_id",)
_REQUIRED_CODE_FIELDS = ("path", "name")

_IDENTIFIER_SPLIT_RE = re.compile(r"[A-Za-z0-9]+")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def posix_path(value: Any) -> str:
    """Normalize a path to POSIX form so link identity is OS-stable (RT-6)."""
    return str(value or "").strip().replace("\\", "/")


def _split_camel_words(chunk: str) -> List[str]:
    words: List[str] = []
    buffer = ""
    for index, char in enumerate(chunk):
        if (
            buffer
            and char.isupper()
            and (
                buffer[-1].islower()
                or buffer[-1].isdigit()
                or (
                    buffer[-1].isupper()
                    and index + 1 < len(chunk)
                    and chunk[index + 1].islower()
                )
            )
        ):
            words.append(buffer)
            buffer = char
        else:
            buffer += char
    if buffer:
        words.append(buffer)
    return words


def normalize_match_key_str(value: Any) -> str:
    """Space-joined lowercase word key for fast SQLite equality/index lookups."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts: List[str] = []
    for chunk in _IDENTIFIER_SPLIT_RE.findall(text):
        parts.extend(_split_camel_words(chunk))
    return " ".join(word.lower() for word in parts if word)


def _doc_stem(doc_id: str) -> str:
    """Extract normalized lowercase document stem from raw or __-flattened doc_id."""
    posix = posix_path(doc_id)
    if "__" in posix:
        last_part = posix.rsplit("__", 1)[-1]
    else:
        last_part = Path(posix).name
    stem = Path(last_part).stem or last_part
    return stem.strip().casefold()


def record_key(record: Mapping[str, Any]) -> tuple:
    """Stable identity of a link: (doc ref, code ref) minus lifecycle fields."""
    doc = record.get("doc") or {}
    code = record.get("code") or {}
    return (
        str(doc.get("doc_id") or ""),
        doc.get("paragraph_id"),
        posix_path(code.get("path")),
        str(code.get("name") or ""),
        tuple(code.get("lines") or ()),
        str(code.get("target_type") or "symbol"),
    )


def _validate_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("link record must be a mapping")
    for field in _REQUIRED_TOP_FIELDS:
        if record.get(field) in (None, ""):
            raise ValueError(f"link record missing required field '{field}'")
    doc = record.get("doc")
    code = record.get("code")
    if not isinstance(doc, Mapping):
        raise ValueError("link record field 'doc' must be a mapping")
    if not isinstance(code, Mapping):
        raise ValueError("link record field 'code' must be a mapping")
    for field in _REQUIRED_DOC_FIELDS:
        if doc.get(field) in (None, ""):
            raise ValueError(f"link record doc missing required field '{field}'")
    for field in _REQUIRED_CODE_FIELDS:
        if code.get(field) in (None, ""):
            raise ValueError(f"link record code missing required field '{field}'")
    try:
        float(record["score"])
    except (TypeError, ValueError) as exc:
        raise ValueError("link record field 'score' must be numeric") from exc
    normalized = dict(record)
    normalized["schema_version"] = int(
        record.get("schema_version") or SCHEMA_VERSION
    )
    normalized["code"] = {**code, "path": posix_path(code.get("path"))}
    normalized.setdefault("status", STATUS_ACTIVE)
    if not record.get("created_at"):
        normalized["created_at"] = _utc_now()
    return normalized


def _serialize(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


@contextlib.contextmanager
def _store_lock(project_dir: Path) -> Iterator[None]:
    """Serialize writers within the instance (OS-level lock, portalocker)."""
    import portalocker

    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    lock_path = project_dir / LOCK_FILENAME
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        portalocker.lock(handle, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(handle)
    finally:
        handle.close()


# ---------------------------------------------------------------------------
# SQLite WAL Store (links.db)
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;

CREATE TABLE IF NOT EXISTS docs (
    doc_uid INTEGER PRIMARY KEY,
    doc_id TEXT NOT NULL UNIQUE,
    norm_doc_id TEXT NOT NULL,
    flat_doc_id TEXT NOT NULL,
    stem TEXT NOT NULL,
    source_sha TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_norm ON docs(norm_doc_id);
CREATE INDEX IF NOT EXISTS idx_docs_flat ON docs(flat_doc_id);
CREATE INDEX IF NOT EXISTS idx_docs_stem ON docs(stem);

CREATE TABLE IF NOT EXISTS paragraphs (
    para_uid INTEGER PRIMARY KEY,
    para_key TEXT NOT NULL UNIQUE,
    doc_uid INTEGER NOT NULL,
    paragraph_id_json TEXT NOT NULL,
    snippet TEXT
);
CREATE INDEX IF NOT EXISTS idx_para_doc ON paragraphs(doc_uid);

CREATE TABLE IF NOT EXISTS symbols (
    sym_uid INTEGER PRIMARY KEY,
    sym_key TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    norm_path TEXT NOT NULL,
    basename TEXT NOT NULL,
    file_stem TEXT NOT NULL,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL,
    match_key TEXT NOT NULL,
    qualified_name TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT 'symbol',
    parser_type TEXT NOT NULL DEFAULT '',
    start_line INTEGER,
    end_line INTEGER,
    source_sha TEXT
);
CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(norm_name);
CREATE INDEX IF NOT EXISTS idx_sym_key ON symbols(match_key);
CREATE INDEX IF NOT EXISTS idx_sym_path ON symbols(norm_path);
CREATE INDEX IF NOT EXISTS idx_sym_base ON symbols(basename);
CREATE INDEX IF NOT EXISTS idx_sym_stem ON symbols(file_stem);

CREATE TABLE IF NOT EXISTS links (
    para_uid INTEGER NOT NULL,
    sym_uid INTEGER NOT NULL,
    doc_uid INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'implements',
    provenance TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    staled_at TEXT,
    doc_sha TEXT,
    code_sha TEXT,
    PRIMARY KEY (para_uid, sym_uid)
);
CREATE INDEX IF NOT EXISTS idx_links_doc_score ON links(doc_uid, status, score DESC);
CREATE INDEX IF NOT EXISTS idx_links_sym_score ON links(sym_uid, status, score DESC);
CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);
"""


def _db_path(project_dir: Path) -> Path:
    return Path(project_dir) / LINKS_DB_FILENAME


def _open_db(project_dir: Path, *, read_only: bool = False) -> sqlite3.Connection:
    path = _db_path(project_dir)
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def _sym_key_from_code(code: Mapping[str, Any]) -> Tuple[str, str, str, str, str, str, str, Optional[int], Optional[int], str, str]:
    path = posix_path(code.get("path"))
    norm_path = path.casefold()
    basename = Path(path).name.casefold() if path else ""
    file_stem = Path(path).stem.casefold() if path else ""
    name = str(code.get("name") or "")
    norm_name = name.casefold()
    match_key = normalize_match_key_str(name)
    lines = code.get("lines")
    start_line = int(lines[0]) if isinstance(lines, (list, tuple)) and len(lines) >= 1 and lines[0] is not None else None
    end_line = int(lines[1]) if isinstance(lines, (list, tuple)) and len(lines) >= 2 and lines[1] is not None else None
    target_type = str(code.get("target_type") or "symbol")
    parser_type = str(code.get("parser_type") or "")
    s_tok = "" if start_line is None else str(start_line)
    e_tok = "" if end_line is None else str(end_line)
    sym_key = f"{path}\0{name}\0{s_tok}\0{e_tok}\0{target_type}"
    return (
        sym_key,
        path,
        norm_path,
        basename,
        file_stem,
        name,
        norm_name,
        match_key,
        start_line,
        end_line,
        target_type,
        parser_type,
    )


def _para_key_from_doc(doc: Mapping[str, Any]) -> Tuple[str, str, str]:
    doc_id = str(doc.get("doc_id") or "")
    para_id_json = json.dumps(doc.get("paragraph_id"), ensure_ascii=False)
    return f"{doc_id}\0{para_id_json}", doc_id, para_id_json


def _upsert_records_to_sqlite(
    project_dir: Path,
    records: Sequence[Mapping[str, Any]],
    *,
    snippets: Optional[Mapping[Tuple[str, Any], str]] = None,
    replace_all: bool = False,
) -> None:
    if not records and not replace_all:
        return
    conn = _open_db(project_dir, read_only=False)
    try:
        with conn:
            if replace_all:
                conn.execute("DELETE FROM links")
                conn.execute("DELETE FROM paragraphs")
                conn.execute("DELETE FROM docs")
                conn.execute("DELETE FROM symbols")

            if not records:
                return

            doc_rows: Dict[str, Tuple[str, str, str, str, Optional[str]]] = {}
            para_rows: Dict[str, Tuple[str, str, str, Optional[str]]] = {}
            sym_rows: Dict[str, tuple] = {}

            for record in records:
                doc = record.get("doc") or {}
                code = record.get("code") or {}
                para_key, doc_id, para_id_json = _para_key_from_doc(doc)
                doc_sha = doc.get("source_sha")
                norm_doc = posix_path(doc_id).casefold()
                flat_doc = norm_doc.replace("/", "__").replace("\\", "__")
                stem = _doc_stem(doc_id)
                if doc_id not in doc_rows or doc_sha is not None:
                    doc_rows[doc_id] = (doc_id, norm_doc, flat_doc, stem, doc_sha)

                snippet = doc.get("snippet")
                if snippet is None and snippets is not None:
                    snippet = snippets.get((doc_id, doc.get("paragraph_id")))
                if para_key not in para_rows or snippet is not None:
                    para_rows[para_key] = (para_key, doc_id, para_id_json, snippet)

                (
                    sym_key,
                    path,
                    norm_path,
                    basename,
                    file_stem,
                    name,
                    norm_name,
                    match_key,
                    start_line,
                    end_line,
                    target_type,
                    parser_type,
                ) = _sym_key_from_code(code)
                code_sha = code.get("source_sha")
                qualified_name = str(code.get("qualified_name") or "")
                if sym_key not in sym_rows or code_sha is not None:
                    sym_rows[sym_key] = (
                        sym_key,
                        path,
                        norm_path,
                        basename,
                        file_stem,
                        name,
                        norm_name,
                        match_key,
                        qualified_name,
                        target_type,
                        parser_type,
                        start_line,
                        end_line,
                        code_sha,
                    )

            conn.executemany(
                """
                INSERT INTO docs (doc_id, norm_doc_id, flat_doc_id, stem, source_sha)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    source_sha = COALESCE(excluded.source_sha, docs.source_sha)
                """,
                list(doc_rows.values()),
            )

            doc_uid_map: Dict[str, int] = {
                row["doc_id"]: int(row["doc_uid"])
                for row in conn.execute("SELECT doc_uid, doc_id FROM docs")
            }

            conn.executemany(
                """
                INSERT INTO paragraphs (para_key, doc_uid, paragraph_id_json, snippet)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(para_key) DO UPDATE SET
                    snippet = COALESCE(excluded.snippet, paragraphs.snippet)
                """,
                [
                    (p_key, doc_uid_map[d_id], p_json, snip)
                    for p_key, d_id, p_json, snip in para_rows.values()
                    if d_id in doc_uid_map
                ],
            )

            conn.executemany(
                """
                INSERT INTO symbols (
                    sym_key, path, norm_path, basename, file_stem,
                    name, norm_name, match_key, qualified_name,
                    target_type, parser_type, start_line, end_line, source_sha
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sym_key) DO UPDATE SET
                    source_sha = COALESCE(excluded.source_sha, symbols.source_sha),
                    parser_type = CASE WHEN excluded.parser_type != '' THEN excluded.parser_type ELSE symbols.parser_type END
                """,
                list(sym_rows.values()),
            )

            para_uid_map: Dict[str, int] = {
                row["para_key"]: int(row["para_uid"])
                for row in conn.execute("SELECT para_uid, para_key FROM paragraphs")
            }
            sym_uid_map: Dict[str, int] = {
                row["sym_key"]: int(row["sym_uid"])
                for row in conn.execute("SELECT sym_uid, sym_key FROM symbols")
            }

            link_params: List[tuple] = []
            for record in records:
                doc = record.get("doc") or {}
                code = record.get("code") or {}
                para_key, doc_id, _ = _para_key_from_doc(doc)
                sym_tuple = _sym_key_from_code(code)
                sym_key = sym_tuple[0]
                para_uid = para_uid_map.get(para_key)
                sym_uid = sym_uid_map.get(sym_key)
                doc_uid = doc_uid_map.get(doc_id)
                if para_uid is None or sym_uid is None or doc_uid is None:
                    continue
                link_params.append(
                    (
                        para_uid,
                        sym_uid,
                        doc_uid,
                        str(record.get("project_id") or ""),
                        str(record.get("relation") or "implements"),
                        str(record.get("provenance") or "exact-name"),
                        float(record.get("score") or 0.0),
                        str(record.get("status") or STATUS_ACTIVE),
                        str(record.get("created_at") or _utc_now()),
                        record.get("staled_at"),
                        doc.get("source_sha"),
                        code.get("source_sha"),
                    )
                )

            conn.executemany(
                """
                INSERT INTO links (
                    para_uid, sym_uid, doc_uid, project_id, relation,
                    provenance, score, status, created_at, staled_at,
                    doc_sha, code_sha
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(para_uid, sym_uid) DO UPDATE SET
                    project_id = excluded.project_id,
                    relation = excluded.relation,
                    provenance = excluded.provenance,
                    score = excluded.score,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    staled_at = excluded.staled_at,
                    doc_sha = excluded.doc_sha,
                    code_sha = excluded.code_sha
                """,
                link_params,
            )
    finally:
        conn.close()


def save_paragraph_snippets(
    project_dir: Path,
    snippets: Mapping[Tuple[str, Any], str],
) -> None:
    """Persist pre-redacted paragraph snippets into ``links.db``."""
    if not snippets:
        return
    db_file = _db_path(project_dir)
    if not db_file.is_file():
        return
    conn = _open_db(project_dir, read_only=False)
    try:
        with conn:
            params = [
                (
                    text,
                    f"{doc_id}\0{json.dumps(para_id, ensure_ascii=False)}",
                )
                for (doc_id, para_id), text in snippets.items()
                if text
            ]
            if params:
                conn.executemany(
                    "UPDATE paragraphs SET snippet = ? WHERE para_key = ?",
                    params,
                )
    finally:
        conn.close()


def _row_to_record(row: Mapping[str, Any]) -> Dict[str, Any]:
    start_line = row["start_line"]
    end_line = row["end_line"]
    try:
        para_id = json.loads(row["paragraph_id_json"])
    except (TypeError, json.JSONDecodeError):
        para_id = row["paragraph_id_json"]
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_id": row["project_id"],
        "relation": row["relation"],
        "doc": {
            "doc_id": row["doc_id"],
            "paragraph_id": para_id,
            "source_sha": row["doc_sha"],
        },
        "code": {
            "target_type": row["target_type"],
            "parser_type": row["parser_type"],
            "path": row["path"],
            "name": row["name"],
            "lines": [start_line, end_line] if start_line is not None else None,
            "source_sha": row["code_sha"],
        },
        "score": float(row["score"]),
        "provenance": row["provenance"],
        "status": row["status"],
        "created_at": row["created_at"],
    }
    if row["staled_at"]:
        record["staled_at"] = row["staled_at"]
    return record


def _ensure_sqlite_synced(project_dir: Path) -> bool:
    """Ensure ``links.db`` exists if a small ``links.jsonl`` is present."""
    project_dir = Path(project_dir)
    db_file = _db_path(project_dir)
    jsonl_file = project_dir / LINKS_FILENAME
    if db_file.is_file():
        return True
    if not jsonl_file.is_file():
        return False
    try:
        size = jsonl_file.stat().st_size
    except OSError:
        return False
    if size > MAX_RAW_LOG_BYTES:
        return False
    records = latest_records(load_links(project_dir))
    if records:
        with _store_lock(project_dir):
            _upsert_records_to_sqlite(project_dir, records, replace_all=True)
        return True
    return False


# ---------------------------------------------------------------------------
# Fast SQLite Query API for link_mcp and link_engine
# ---------------------------------------------------------------------------

_SELECT_LINK_COLUMNS = """
    l.project_id, l.relation, l.provenance, l.score, l.status,
    l.created_at, l.staled_at, l.doc_sha, l.code_sha,
    d.doc_id, p.paragraph_id_json, p.snippet,
    s.sym_uid, s.path, s.name, s.target_type, s.parser_type,
    s.start_line, s.end_line
"""


def query_code_for_doc(
    project_dir: Path,
    *,
    doc_id: Optional[str] = None,
    query: Optional[str] = None,
    top_k: int = 20,
    min_score: float = 0.0,
) -> Optional[Tuple[List[Tuple[Dict[str, Any], Optional[str]]], int]]:
    """Query active links for a document using SQLite indexes + waterfall resolution.

    Returns ``([(record, pre_stored_snippet), ...], total_matched)`` or
    ``None`` when no SQLite DB exists.
    """
    if not _ensure_sqlite_synced(project_dir):
        return None
    conn = _open_db(project_dir, read_only=True)
    try:
        doc_uids: Optional[List[int]] = None
        if doc_id:
            clean_doc = posix_path(doc_id)
            norm_doc = clean_doc.casefold()
            flat_doc = norm_doc.replace("/", "__").replace("\\", "__")
            stem = _doc_stem(clean_doc)

            # Waterfall 1: exact doc_id / norm_doc_id / flat_doc_id
            rows = conn.execute(
                "SELECT doc_uid FROM docs WHERE doc_id = ? OR norm_doc_id = ? OR flat_doc_id = ?",
                (clean_doc, norm_doc, flat_doc),
            ).fetchall()
            # Waterfall 2: stem match (with or without extension)
            if not rows and stem:
                rows = conn.execute(
                    "SELECT doc_uid FROM docs WHERE stem = ? OR norm_doc_id = ?",
                    (stem, f"{flat_doc}"),
                ).fetchall()
            # Waterfall 3: suffix or substring match on norm_doc_id / flat_doc_id
            if not rows:
                like_token = f"%{norm_doc}%"
                like_flat = f"%{flat_doc}%"
                rows = conn.execute(
                    "SELECT doc_uid FROM docs WHERE norm_doc_id LIKE ? OR flat_doc_id LIKE ?",
                    (like_token, like_flat),
                ).fetchall()
            if not rows:
                return [], 0
            doc_uids = [int(r["doc_uid"]) for r in rows]

        where_clauses = ["l.status = 'active'", "l.score >= ?"]
        params: List[Any] = [float(min_score)]

        if doc_uids is not None:
            placeholders = ",".join("?" for _ in doc_uids)
            where_clauses.append(f"l.doc_uid IN ({placeholders})")
            params.extend(doc_uids)

        needle_words = normalize_match_key_str(query) if query else ""
        if needle_words:
            like_pattern = f"%{needle_words}%"
            raw_like = f"%{str(query).strip().casefold()}%"
            where_clauses.append(
                "(s.match_key LIKE ? OR s.norm_name LIKE ? OR s.norm_path LIKE ? OR d.norm_doc_id LIKE ?)"
            )
            params.extend([like_pattern, raw_like, raw_like, raw_like])

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT {_SELECT_LINK_COLUMNS}
            FROM links l
            JOIN docs d ON d.doc_uid = l.doc_uid
            JOIN paragraphs p ON p.para_uid = l.para_uid
            JOIN symbols s ON s.sym_uid = l.sym_uid
            WHERE {where_sql}
            ORDER BY l.score DESC, s.path ASC, s.name ASC
        """
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return [], 0

        # Prioritize distinct (doc_id, sym_uid) in top_k while preserving total count
        primary: List[Tuple[Dict[str, Any], Optional[str]]] = []
        secondary: List[Tuple[Dict[str, Any], Optional[str]]] = []
        seen_doc_sym: set[Tuple[str, int]] = set()
        for row in rows:
            key = (row["doc_id"], int(row["sym_uid"]))
            item = (_row_to_record(row), row["snippet"])
            if key in seen_doc_sym:
                secondary.append(item)
            else:
                seen_doc_sym.add(key)
                primary.append(item)

        ordered = primary + secondary
        total = len(ordered)
        return ordered[:top_k], total
    finally:
        conn.close()


def query_doc_for_code(
    project_dir: Path,
    *,
    path: Optional[str] = None,
    name: Optional[str] = None,
    top_k: int = 20,
    min_score: float = 0.0,
) -> Optional[Tuple[List[Tuple[Dict[str, Any], Optional[str]]], int]]:
    """Reverse lookup: documents linked to a code symbol/file via SQLite indexes."""
    if not _ensure_sqlite_synced(project_dir):
        return None
    clean_path = posix_path(path) if path else ""
    clean_name = str(name or "").strip()
    if not clean_path and not clean_name:
        return [], 0

    conn = _open_db(project_dir, read_only=True)
    try:
        sym_uids: Optional[List[int]] = None

        # Step 1: resolve candidate symbols by path and/or name waterfall
        path_uids: Optional[set[int]] = None
        if clean_path:
            norm_p = clean_path.casefold()
            base_p = Path(clean_path).name.casefold()
            stem_p = Path(clean_path).stem.casefold()
            # Waterfall 1: exact norm_path
            rows = conn.execute(
                "SELECT sym_uid FROM symbols WHERE norm_path = ?", (norm_p,)
            ).fetchall()
            # Waterfall 2: suffix match (e.g. repo-prefixed or partial path) or basename
            if not rows:
                rows = conn.execute(
                    "SELECT sym_uid FROM symbols WHERE basename = ? OR norm_path LIKE ? OR ? LIKE '%/' || norm_path",
                    (base_p, f"%/{norm_p}", norm_p),
                ).fetchall()
            # Waterfall 3: file_stem match (e.g. OrderSummaryForm matches .vb and .Designer.vb)
            if not rows and stem_p:
                rows = conn.execute(
                    "SELECT sym_uid FROM symbols WHERE file_stem = ? OR file_stem LIKE ?",
                    (stem_p, f"{stem_p}.%"),
                ).fetchall()
            if not rows:
                return [], 0
            path_uids = {int(r["sym_uid"]) for r in rows}

        name_uids: Optional[set[int]] = None
        if clean_name:
            norm_n = clean_name.casefold()
            key_n = normalize_match_key_str(clean_name)
            # Waterfall 1: exact norm_name or match_key
            rows = conn.execute(
                "SELECT sym_uid FROM symbols WHERE norm_name = ? OR match_key = ?",
                (norm_n, key_n),
            ).fetchall()
            # Waterfall 2: trailing segment of qualified name (a.b.Func -> Func)
            if not rows and "." in clean_name:
                short_n = clean_name.rsplit(".", 1)[-1].strip()
                short_key = normalize_match_key_str(short_n)
                rows = conn.execute(
                    "SELECT sym_uid FROM symbols WHERE norm_name = ? OR match_key = ?",
                    (short_n.casefold(), short_key),
                ).fetchall()
            if not rows:
                return [], 0
            name_uids = {int(r["sym_uid"]) for r in rows}

        if path_uids is not None and name_uids is not None:
            combined = path_uids & name_uids
        elif path_uids is not None:
            combined = path_uids
        else:
            combined = name_uids or set()

        if not combined:
            return [], 0
        sym_uids = sorted(combined)

        # Chunk IN clause if needed
        all_rows = []
        for offset in range(0, len(sym_uids), 500):
            chunk = sym_uids[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT {_SELECT_LINK_COLUMNS}
                FROM links l
                JOIN docs d ON d.doc_uid = l.doc_uid
                JOIN paragraphs p ON p.para_uid = l.para_uid
                JOIN symbols s ON s.sym_uid = l.sym_uid
                WHERE l.status = 'active'
                  AND l.score >= ?
                  AND l.sym_uid IN ({placeholders})
                ORDER BY l.score DESC, d.doc_id ASC
            """
            all_rows.extend(conn.execute(sql, [float(min_score), *chunk]).fetchall())

        all_rows.sort(key=lambda r: (-float(r["score"]), str(r["doc_id"])))
        primary: List[Tuple[Dict[str, Any], Optional[str]]] = []
        secondary: List[Tuple[Dict[str, Any], Optional[str]]] = []
        seen_doc_sym: set[Tuple[str, int]] = set()
        for row in all_rows:
            key = (row["doc_id"], int(row["sym_uid"]))
            item = (_row_to_record(row), row["snippet"])
            if key in seen_doc_sym:
                secondary.append(item)
            else:
                seen_doc_sym.add(key)
                primary.append(item)
        ordered = primary + secondary
        total = len(ordered)
        return ordered[:top_k], total
    finally:
        conn.close()


def query_status_counts(project_dir: Path) -> Optional[Dict[str, int]]:
    """Fast O(1)/indexed status count query from ``links.db``."""
    if not _ensure_sqlite_synced(project_dir):
        return None
    conn = _open_db(project_dir, read_only=True)
    try:
        counts = {"active": 0, "stale": 0, "total": 0}
        for row in conn.execute("SELECT status, COUNT(*) AS c FROM links GROUP BY status"):
            status = row["status"]
            c = int(row["c"])
            if status == STATUS_ACTIVE:
                counts["active"] = c
            elif status == STATUS_STALE:
                counts["stale"] = c
            counts["total"] += c
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public Store API (append-only JSONL + synced SQLite WAL)
# ---------------------------------------------------------------------------


def append_links(
    project_dir: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    snippets: Optional[Mapping[Tuple[str, Any], str]] = None,
) -> int:
    """Append validated records to ``links.jsonl`` and sync to ``links.db``."""
    prepared = [_validate_record(record) for record in records]
    if not prepared:
        return 0
    project_dir = Path(project_dir)
    with _store_lock(project_dir):
        path = project_dir / LINKS_FILENAME
        needs_newline = False
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                needs_newline = probe.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as handle:
            if needs_newline:
                handle.write("\n")
            for record in prepared:
                handle.write(_serialize(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _upsert_records_to_sqlite(project_dir, prepared, snippets=snippets, replace_all=False)
    return len(prepared)


def compact_store_on_reconcile(
    project_dir: Path,
    active_records: Sequence[Mapping[str, Any]],
    *,
    snippets: Optional[Mapping[Tuple[str, Any], str]] = None,
) -> int:
    """Compact an oversized legacy ``links.jsonl`` and rebuild ``links.db``."""
    project_dir = Path(project_dir)
    prepared = [_validate_record(record) for record in active_records]
    with _store_lock(project_dir):
        path = project_dir / LINKS_FILENAME
        tmp_path = path.with_suffix(".jsonl.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            for record in prepared:
                handle.write(_serialize(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
        _upsert_records_to_sqlite(project_dir, prepared, snippets=snippets, replace_all=True)
    return len(prepared)


def latest_records(
    records: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Canonical read view: the LAST record per key, in file order."""
    latest: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for record in records:
        key = record_key(record)
        if key not in latest:
            order.append(key)
        latest[key] = dict(record)
    return [latest[key] for key in order]


def latest_links(
    project_dir: Path,
    *,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load links through the latest-per-key view, optionally by status.

    Uses ``links.db`` when present and in sync with ``links.jsonl`` so large
    projects never re-parse multi-GB JSONL files; falls back to ``load_links``
    when ``links.jsonl`` was modified directly (e.g. crash-tail unit tests).
    """
    project_dir = Path(project_dir)
    db_file = _db_path(project_dir)
    jsonl_file = project_dir / LINKS_FILENAME
    if db_file.is_file() and (
        not jsonl_file.is_file()
        or db_file.stat().st_mtime_ns >= jsonl_file.stat().st_mtime_ns
    ):
        conn = _open_db(project_dir, read_only=True)
        try:
            where = "WHERE l.status = ?" if status is not None else ""
            params = (status,) if status is not None else ()
            rows = conn.execute(
                f"""
                SELECT {_SELECT_LINK_COLUMNS}
                FROM links l
                JOIN docs d ON d.doc_uid = l.doc_uid
                JOIN paragraphs p ON p.para_uid = l.para_uid
                JOIN symbols s ON s.sym_uid = l.sym_uid
                {where}
                ORDER BY l.rowid ASC
                """,
                params,
            ).fetchall()
            return [_row_to_record(row) for row in rows]
        finally:
            conn.close()

    records = latest_records(load_links(project_dir))
    if status is None:
        return records
    return [record for record in records if record.get("status") == status]


def load_links(
    project_dir: Path,
    *,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read raw link records from ``links.jsonl``, optionally filtered by ``status``."""
    path = Path(project_dir) / LINKS_FILENAME
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                print(
                    f"[link-store] warning: skipping undecodable line "
                    f"{line_number} in {path}",
                    file=sys.stderr,
                )
                continue
            if isinstance(record, Mapping):
                if status is None or record.get("status") == status:
                    records.append(dict(record))
    return records


def mark_stale(
    project_dir: Path,
    predicate: Callable[[Mapping[str, Any]], bool],
    *,
    _loaded_records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Append stale copies of active records matching ``predicate``."""
    project_dir = Path(project_dir)
    records = _loaded_records if _loaded_records is not None else load_links(project_dir)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for record in records:
        latest[record_key(record)] = record
    to_stale = [
        record
        for record in latest.values()
        if record.get("status") == STATUS_ACTIVE and predicate(record)
    ]
    if not to_stale:
        return []
    stale_at = _utc_now()
    copies = [
        {**record, "status": STATUS_STALE, "staled_at": stale_at}
        for record in to_stale
    ]
    append_links(project_dir, copies)
    return copies


def load_state(project_dir: Path) -> Optional[Dict[str, Any]]:
    """Return the persisted ``state.json`` payload, or ``None`` if absent."""
    path = Path(project_dir) / STATE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_state(project_dir: Path, state: Mapping[str, Any]) -> None:
    """Atomically persist ``state.json`` (tmp + rename, ``layout.py`` style)."""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **state}
    path = project_dir / STATE_FILENAME
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def coverage_stats(
    records: Iterable[Mapping[str, Any]],
    *,
    total_docs: Optional[int] = None,
    total_symbols: Optional[int] = None,
) -> Dict[str, Any]:
    """Coverage telemetry — the Phase-3 fuzzy-matching gate input."""
    records = list(records)
    active = [
        r for r in records
        if r.get("status", STATUS_ACTIVE) == STATUS_ACTIVE
    ]
    docs_with_active: set[str] = set()
    symbols_referenced: set[tuple] = set()
    provenance_distribution: Dict[str, int] = {}
    for record in active:
        doc = record.get("doc") or {}
        code = record.get("code") or {}
        doc_id = str(doc.get("doc_id") or "")
        if doc_id:
            docs_with_active.add(doc_id)
        symbols_referenced.add(
            (posix_path(code.get("path")), str(code.get("name") or ""))
        )
        provenance_value = str(record.get("provenance") or "unknown")
        provenance_distribution[provenance_value] = (
            provenance_distribution.get(provenance_value, 0) + 1
        )
    return {
        "active_links": len(active),
        "stale_links": len(records) - len(active),
        "docs_with_active_links": len(docs_with_active),
        "doc_coverage": (
            round(len(docs_with_active) / total_docs, 4)
            if total_docs
            else None
        ),
        "symbols_referenced": len(symbols_referenced),
        "symbol_coverage": (
            round(len(symbols_referenced) / total_symbols, 4)
            if total_symbols
            else None
        ),
        "provenance_distribution": provenance_distribution,
    }


__all__ = [
    "LINKS_DB_FILENAME",
    "LINKS_FILENAME",
    "LOCK_FILENAME",
    "MAX_RAW_LOG_BYTES",
    "SCHEMA_VERSION",
    "STATE_FILENAME",
    "STATUS_ACTIVE",
    "STATUS_STALE",
    "append_links",
    "compact_store_on_reconcile",
    "coverage_stats",
    "latest_links",
    "latest_records",
    "load_links",
    "load_state",
    "mark_stale",
    "normalize_match_key_str",
    "posix_path",
    "query_code_for_doc",
    "query_doc_for_code",
    "query_status_counts",
    "record_key",
    "save_paragraph_snippets",
    "save_state",
]
