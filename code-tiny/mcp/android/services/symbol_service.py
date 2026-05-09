from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional, Sequence, Tuple

from fastapi import HTTPException

from ... import scan_service
from ...scan_service import ParserType
from ...db_flavors import DatabaseFlavor, detect_flavor_path, UnsupportedDatabaseError
from ..utils import lookup_cached_resolution, record_resolution, validate_db_name


@dataclass
class ResolveSymbolResult:
    scan_id: str
    database_name: str
    database_path: str
    parser_type: str
    function_id: Optional[int] = None


EXTENSION_PARSER_MAP = {
    ".c": ParserType.C,
    ".h": ParserType.C,
    ".cpp": ParserType.CPLUS,
    ".cxx": ParserType.CPLUS,
    ".cc": ParserType.CPLUS,
    ".hpp": ParserType.CPLUS,
    ".hh": ParserType.CPLUS,
    ".hxx": ParserType.CPLUS,
    ".java": ParserType.JAVA,
    ".kt": ParserType.KOTLIN,
}


def _infer_parser_type(raw_parser: Optional[str], source_file: Optional[str]) -> ParserType:
    if raw_parser:
        try:
            return ParserType(raw_parser)
        except ValueError as exc:  # pragma: no cover - validated by FastAPI
            raise HTTPException(status_code=400, detail=f"Unsupported parser type: {raw_parser}") from exc
    if source_file:
        suffix = Path(source_file).suffix.lower()
        parser = EXTENSION_PARSER_MAP.get(suffix)
        if parser:
            return parser
    raise HTTPException(
        status_code=400,
        detail="parser_type is required when source_file has no recognised extension.",
    )


def _normalize_source_file(root: Path, raw_file: Optional[str]) -> Optional[Path]:
    if not raw_file:
        return None
    candidate = Path(raw_file).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="source_file must be inside the provided source_folder.",
        )
    if not candidate.exists():
        # Allow non-existing files (e.g., generated) but warn via log
        logger = logging.getLogger("project_call_graph.mcp.symbols")
        logger.warning("Source file does not exist yet: %s", candidate)
    return candidate


def _normalise_db_name(base_name: Optional[str], root: Path, parser: ParserType) -> str:
    if base_name:
        candidate = base_name
        if os.path.isabs(candidate):
            candidate = Path(candidate).name
        try:
            db_name = scan_service.validate_database_name(candidate)
        except ValueError as exc:  # pragma: no cover - validation tested elsewhere
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        db_name = scan_service.get_default_database_name(root)
    suffix = f"_{parser.value}.db"
    lowered = db_name.lower()
    if lowered.endswith(suffix):
        return db_name
    if lowered.endswith(".db"):
        db_name = db_name[:-3]
    return f"{db_name}_{parser.value}.db"


def _candidate_paths(source_file: Path) -> Sequence[str]:
    values = {
        str(source_file),
        str(source_file.resolve()),
        source_file.as_posix(),
    }
    return list(dict.fromkeys(values))


def _lookup_cxx_symbol(db_path: Path, source_file: Path, start_line: Optional[int], end_line: Optional[int]) -> Optional[int]:
    if start_line is None:
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        candidates = _candidate_paths(source_file)
        placeholders = ",".join("?" for _ in candidates)
        target_line = start_line
        upper = end_line or target_line
        # Try exact file match first
        query = f"""
            SELECT f.id, f.start_line
            FROM functions AS f
            JOIN files ON files.id = f.file_id
            WHERE files.path IN ({placeholders})
        """
        rows = conn.execute(query, candidates).fetchall()
        if not rows:
            return None
        best_match: Tuple[int, int] | None = None
        for row in rows:
            row_line = row["start_line"] or 0
            if target_line <= row_line <= upper:
                return int(row["id"])
            distance = abs(row_line - target_line)
            if best_match is None or distance < best_match[1]:
                best_match = (int(row["id"]), distance)
        return best_match[0] if best_match else None
    finally:
        conn.close()


def _maybe_resolve_function_id(db_path: Path, source_file: Optional[Path], start_line: Optional[int], end_line: Optional[int]) -> Optional[int]:
    if not db_path.exists() or not source_file:
        return None
    try:
        flavor = detect_flavor_path(db_path)
    except (UnsupportedDatabaseError, FileNotFoundError):
        return None
    if flavor == DatabaseFlavor.CPLUS:
        return _lookup_cxx_symbol(db_path, source_file, start_line, end_line)
    return None


def _function_id_exists(db_path: Path, function_id: Optional[int]) -> bool:
    if not db_path.exists() or function_id is None:
        return False
    try:
        flavor = detect_flavor_path(db_path)
    except (UnsupportedDatabaseError, FileNotFoundError):
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        if flavor == DatabaseFlavor.CPLUS:
            row = conn.execute(
                "SELECT 1 FROM functions WHERE id = ?",
                (function_id,),
            ).fetchone()
            return row is not None
        if flavor == DatabaseFlavor.JAVA:
            row = conn.execute(
                "SELECT 1 FROM methods WHERE id = ?",
                (function_id,),
            ).fetchone()
            return row is not None
        return False
    finally:
        conn.close()


async def resolve_symbol(
    source_folder: str,
    parser_type: Optional[str],
    database_name: Optional[str],
    *,
    source_file: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    rescan: bool = False,
) -> ResolveSymbolResult:
    parser_enum = _infer_parser_type(parser_type, source_file)
    try:
        root = scan_service.validate_folder_path(source_folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalised_file = _normalize_source_file(root, source_file)
    db_name = _normalise_db_name(database_name, root, parser_enum)
    db_path = scan_service.get_database_path(db_name)

    cached_node_id: Optional[int] = None
    if normalised_file is not None:
        cached = lookup_cached_resolution(str(normalised_file), start_line, end_line)
        if cached:
            cached_path = cached.get("database_path")
            if cached_path:
                try:
                    validated = validate_db_name(cached_path)
                    db_name = validated.name
                    db_path = validated
                except (ValueError, FileNotFoundError):
                    pass
            cached_node_id = cached.get("node_id") if cached.get("node_id") is not None else None

    preexisting_id = _maybe_resolve_function_id(db_path, normalised_file, start_line, end_line)
    if preexisting_id is not None:
        function_id = preexisting_id
    elif _function_id_exists(db_path, cached_node_id):
        function_id = cached_node_id
    else:
        function_id = None

    if not rescan and db_path.exists():
        scan_id = str(uuid.uuid4())
        progress = scan_service.scan_manager.create_scan(scan_id)
        progress.database_name = db_name
        progress.database_path = str(db_path)
        progress.source_folder = str(root)
        progress.complete(
            success=True,
            message=f"Reusing existing database {db_name}",
        )
    else:
        scan_id = str(uuid.uuid4())
        asyncio.create_task(scan_service.start_scan(scan_id, parser_enum, root, db_name, False))

    record_resolution(
        str(root),
        parser_enum.value,
        str(db_path),
        function_id,
        source_file=str(normalised_file) if normalised_file else None,
        start_line=start_line,
        end_line=end_line,
    )
    return ResolveSymbolResult(
        scan_id=scan_id,
        database_name=db_name,
        database_path=str(db_path),
        parser_type=parser_enum.value,
        function_id=function_id,
    )


def get_scan_status(scan_id: str) -> Dict[str, str]:
    scan = scan_service.scan_manager.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
    return scan.to_dict()
