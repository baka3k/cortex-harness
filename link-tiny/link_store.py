"""Append-only link store — the only module that writes link data.

Layout per project: ``{instance_root}/links/{project_id}/``

* ``links.jsonl`` — append-only log of link records. Records are never
  rewritten or removed in place; drift appends copies with
  ``status: "stale"`` and deletes are logical.
* ``state.json`` — SHA snapshot (doc + code), counts, coverage telemetry.
  Written atomically (tmp + rename, the ``layout.py`` pattern).

Pure file I/O: no graph access, no MCP imports, no harness imports.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional


LINKS_FILENAME = "links.jsonl"
STATE_FILENAME = "state.json"
LOCK_FILENAME = "links.lock"
SCHEMA_VERSION = 1

STATUS_ACTIVE = "active"
STATUS_STALE = "stale"

# Fields every record must carry (invariant 4: provenance + score are
# mandatory from day one; consumers and the injection mitigation depend on
# them being present).
_REQUIRED_TOP_FIELDS = ("project_id", "relation", "provenance", "score")
_REQUIRED_DOC_FIELDS = ("doc_id",)
_REQUIRED_CODE_FIELDS = ("path", "name")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def posix_path(value: Any) -> str:
    """Normalize a path to POSIX form so link identity is OS-stable (RT-6)."""
    return str(value or "").strip().replace("\\", "/")


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
    # Store-boundary normalization: POSIX paths (RT-6), status default,
    # timestamps, schema version.
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


def append_links(project_dir: Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Append validated records to ``links.jsonl``; return the count written.

    Records are validated (provenance + score mandatory) and normalized
    (POSIX paths, status/timestamp defaults) before hitting the file. The
    append itself runs under the store lock so concurrent writers serialize.
    """
    prepared = [_validate_record(record) for record in records]
    if not prepared:
        return 0
    project_dir = Path(project_dir)
    with _store_lock(project_dir):
        path = project_dir / LINKS_FILENAME
        # Defensive newline: a crash mid-append can leave a tail without a
        # trailing "\n"; appending after it would glue a valid record onto
        # the torn line and lose both.
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
    return len(prepared)


def latest_records(
    records: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Canonical read view: the LAST record per key, in file order.

    The append-only log keeps superseded lines; consumers (server, tests,
    coverage) see one record per (doc ref, code ref) — the newest line
    wins, which is exactly how stale propagation supersedes.
    """
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

    Dedup happens BEFORE the status filter: the newest line decides a
    record's state, so an appended stale copy must supersede the original
    active line it was copied from.
    """
    records = latest_records(load_links(project_dir))
    if status is None:
        return records
    return [record for record in records if record.get("status") == status]


def load_links(
    project_dir: Path,
    *,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read link records, optionally filtered by ``status``.

    A truncated tail (crash mid-append) is skipped with a warning — the
    file stays readable. Undecodable lines anywhere are skipped the same
    way: link data is advisory and must never take a read path down.
    """
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
) -> List[Dict[str, Any]]:
    """Append stale copies of active records matching ``predicate``.

    Existing lines are never touched (append-only invariant). Idempotent:
    a record whose latest state is already stale is not re-appended.
    Returns the stale copies written.
    """
    project_dir = Path(project_dir)
    records = load_links(project_dir)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for record in records:
        latest[record_key(record)] = record
    to_stale = [
        record
        for record in records
        if record.get("status") == STATUS_ACTIVE
        and predicate(record)
        # skip when a newer line already carries the same transition
        and latest.get(record_key(record), {}).get("status") == STATUS_ACTIVE
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
    """Coverage telemetry — the Phase-3 fuzzy-matching gate input.

    ``total_docs`` / ``total_symbols`` are the universe sizes from the doc
    and code graphs; when omitted the percentages degrade to ``None`` and
    only counts are reported.
    """
    records = list(records)
    # Records built by the engine carry no status until the store
    # normalizes them; absence means active.
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
    "LINKS_FILENAME",
    "LOCK_FILENAME",
    "SCHEMA_VERSION",
    "STATE_FILENAME",
    "STATUS_ACTIVE",
    "STATUS_STALE",
    "append_links",
    "coverage_stats",
    "latest_links",
    "latest_records",
    "load_links",
    "load_state",
    "mark_stale",
    "posix_path",
    "record_key",
    "save_state",
]
