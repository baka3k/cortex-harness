from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests

from tools.common.analyzer_cache import safe_cache_root
from tools.common.incremental_cleanup import cleanup_qdrant_for_files
from tools.common.message_detectors import get_detector, has_specific_detector, supported_parsers
from tools.common.message_detectors.base import BaseMessageDetector, unquote


SUPPORTED_PARSERS: Set[str] = supported_parsers()
MESSAGE_SCHEMA_VERSION = 1
DEFAULT_MESSAGE_VECTOR_SIZE = 1024

_PARSER_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "cplus": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
    "delphi": (".pas", ".dpr", ".inc"),
    "java": (".java",),
    "csharp": (".cs",),
    "kotlin": (".kt", ".kts"),
    "android": (".kt", ".kts", ".java", ".xml", ".gradle", ".gradle.kts"),
    "vbnet": (".vb",),
    "vb6": (".vbp", ".vbw", ".bas", ".cls", ".frm", ".frx"),
    "vba": (".bas", ".cls", ".frm"),
    "vbscript": (".vbs", ".wsf", ".asp"),
    "python": (".py",),
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "ts": (".ts", ".tsx", ".mts", ".cts"),
    "php": (".php",),
    "sql": (".sql", ".ddl", ".dml", ".psql"),
    "plsql": (".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"),
}

_CALL_PATTERN = re.compile(r"(?P<callee>[A-Za-z_][A-Za-z0-9_:.>]*)\s*\((?P<args>[^)]*)\)")


@dataclass(frozen=True)
class MessageRecord:
    id: str
    name: str
    sender: str
    receiver: str
    payload: str
    response: Optional[str]
    explanation: str
    file_path: str
    line: int
    confidence: float
    language: str
    project_id: str


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"


def _normalize_rel_path(root: str, value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized_root = os.path.realpath(os.path.abspath(root))
    raw = raw.replace("\\", "/")
    if os.path.isabs(raw):
        abs_path = os.path.realpath(raw)
        try:
            rel = os.path.relpath(abs_path, normalized_root)
        except ValueError:
            return None
        rel = rel.replace("\\", "/")
        if rel.startswith("../") or rel == "..":
            return None
        return rel
    rel = os.path.normpath(raw).replace("\\", "/")
    if rel == "." or rel.startswith("../") or rel == "..":
        return None
    return rel


def _split_args(text: str) -> List[str]:
    args: List[str] = []
    current: List[str] = []
    depth = 0
    in_quote: Optional[str] = None
    escape = False
    for ch in text:
        if in_quote:
            current.append(ch)
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == in_quote:
                in_quote = None
            continue
        if ch in {'"', "'"}:
            in_quote = ch
            current.append(ch)
            continue
        if ch in "([{":
            depth += 1
            current.append(ch)
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            current.append(ch)
            continue
        if ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                args.append(token)
            current = []
            continue
        current.append(ch)
    token = "".join(current).strip()
    if token:
        args.append(token)
    return args


def _extract_sender_by_line(lines: Sequence[str], detector: BaseMessageDetector) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    active = ""
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            mapping[idx] = active
            continue
        candidate = detector.extract_sender(stripped)
        if candidate:
            active = candidate
        mapping[idx] = active
    return mapping


def _stable_message_id(
    project_id: str,
    file_path: str,
    line: int,
    sender: str,
    receiver: str,
    message_name: str,
    payload: str,
) -> str:
    base = "||".join(
        [
            project_id.strip(),
            file_path.strip(),
            str(line),
            sender.strip(),
            receiver.strip(),
            message_name.strip(),
            payload.strip(),
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:24]
    return f"msg::{digest}"


def _file_matches_parser(path: str, parser: str) -> bool:
    suffix = os.path.splitext(path.lower())[1]
    return suffix in _PARSER_EXTENSIONS.get(parser, ())


def _iter_parser_files(root: str, parser: str) -> List[str]:
    files: List[str] = []
    extensions = _PARSER_EXTENSIONS.get(parser, ())
    if not extensions:
        return files
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            lower = name.lower()
            if any(lower.endswith(ext) for ext in extensions):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def collect_messages_for_parser(
    *,
    root: str,
    parser: str,
    project_id: str,
    language: str,
    target_files: Optional[Iterable[str]] = None,
) -> List[MessageRecord]:
    if parser not in _PARSER_EXTENSIONS:
        raise ValueError(f"Unsupported parser for message scan: {parser}")
    detector = get_detector(parser)
    normalized_root = os.path.abspath(root)
    if target_files is None:
        files = _iter_parser_files(normalized_root, parser)
    else:
        files = []
        for item in target_files:
            rel = _normalize_rel_path(normalized_root, item)
            if not rel or not _file_matches_parser(rel, parser):
                continue
            abs_path = os.path.join(normalized_root, rel)
            if os.path.isfile(abs_path):
                files.append(abs_path)
        files = sorted(set(files))

    keywords = set(detector.keywords)
    collected: Dict[str, MessageRecord] = {}
    for abs_path in files:
        rel_path = os.path.relpath(abs_path, normalized_root).replace("\\", "/")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                content = handle.read()
        except OSError:
            continue
        lines = content.splitlines()
        sender_map = _extract_sender_by_line(lines, detector)
        for line_idx, line_text in enumerate(lines, start=1):
            stripped = line_text.strip()
            if not stripped:
                continue
            for match in _CALL_PATTERN.finditer(stripped):
                callee = match.group("callee").strip()
                args_text = match.group("args") or ""
                short_name = re.split(r"[.:>]+", callee)[-1].lower()
                if short_name not in keywords:
                    continue
                args = _split_args(args_text)
                message_name, receiver, payload, explanation = detector.extract_fields(callee, args)
                sender = sender_map.get(line_idx, "").strip() or os.path.basename(rel_path)
                if not message_name:
                    continue
                confidence = 0.55
                if unquote(args[0]) if args else None:
                    confidence += 0.2
                if sender:
                    confidence += 0.1
                if receiver:
                    confidence += 0.1
                confidence = min(confidence, 0.99)
                msg_id = _stable_message_id(
                    project_id=project_id,
                    file_path=rel_path,
                    line=line_idx,
                    sender=sender,
                    receiver=receiver,
                    message_name=message_name,
                    payload=payload,
                )
                existing = collected.get(msg_id)
                record = MessageRecord(
                    id=msg_id,
                    name=message_name,
                    sender=sender,
                    receiver=receiver,
                    payload=payload,
                    response=None,
                    explanation=explanation,
                    file_path=rel_path,
                    line=line_idx,
                    confidence=round(confidence, 4),
                    language=language,
                    project_id=project_id,
                )
                if existing is None or record.confidence > existing.confidence:
                    collected[msg_id] = record
    return list(collected.values())


async def cleanup_message_nodes_neo4j(
    *,
    driver: Any,
    database: Optional[str],
    project_id: str,
    file_paths: Sequence[str],
    verbose: bool = False,
) -> Dict[str, int]:
    normalized_paths = sorted(
        {
            path.replace("\\", "/").strip()
            for path in file_paths
            if str(path or "").strip()
        }
    )
    if not normalized_paths:
        return {"deleted_messages": 0, "deleted_endpoints": 0}
    if verbose:
        print(f"[message][cleanup][neo4j] files={len(normalized_paths)}")
    delete_query = """
    WITH $project_id AS project_id, $paths AS paths
    MATCH (m:Message {project_id: project_id})
    WHERE m.file_path IN paths
    WITH collect(m) AS nodes
    UNWIND nodes AS m
    DETACH DELETE m
    RETURN count(m) AS deleted_messages
    """
    records, _, _ = await driver.execute_query(
        delete_query,
        {"project_id": project_id, "paths": normalized_paths},
        database=database,
    )
    deleted_messages = int((records or [{}])[0].get("deleted_messages", 0))
    prune_query = """
    MATCH (e:MessageEndpoint {project_id: $project_id})
    WHERE NOT (e)--()
    WITH collect(e) AS endpoints
    UNWIND endpoints AS e
    DETACH DELETE e
    RETURN count(e) AS deleted_endpoints
    """
    records, _, _ = await driver.execute_query(
        prune_query,
        {"project_id": project_id},
        database=database,
    )
    deleted_endpoints = int((records or [{}])[0].get("deleted_endpoints", 0))
    if verbose:
        print(
            "[message][cleanup][neo4j] deleted_messages=%d deleted_endpoints=%d"
            % (deleted_messages, deleted_endpoints)
        )
    return {"deleted_messages": deleted_messages, "deleted_endpoints": deleted_endpoints}


async def cleanup_all_message_nodes_neo4j(
    *,
    driver: Any,
    database: Optional[str],
    project_id: str,
    verbose: bool = False,
) -> Dict[str, int]:
    if verbose:
        print(f"[message][cleanup][neo4j] clear project_id={project_id}")
    delete_query = """
    MATCH (m:Message {project_id: $project_id})
    WITH collect(m) AS nodes
    UNWIND nodes AS m
    DETACH DELETE m
    RETURN count(m) AS deleted_messages
    """
    records, _, _ = await driver.execute_query(
        delete_query,
        {"project_id": project_id},
        database=database,
    )
    deleted_messages = int((records or [{}])[0].get("deleted_messages", 0))
    prune_query = """
    MATCH (e:MessageEndpoint {project_id: $project_id})
    WHERE NOT (e)--()
    WITH collect(e) AS endpoints
    UNWIND endpoints AS e
    DETACH DELETE e
    RETURN count(e) AS deleted_endpoints
    """
    records, _, _ = await driver.execute_query(
        prune_query,
        {"project_id": project_id},
        database=database,
    )
    deleted_endpoints = int((records or [{}])[0].get("deleted_endpoints", 0))
    return {"deleted_messages": deleted_messages, "deleted_endpoints": deleted_endpoints}


def _qdrant_delete_by_project(
    *,
    qdrant_url: str,
    collection: str,
    project_id: str,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    endpoint = qdrant_url.rstrip("/") + f"/collections/{collection}/points/delete?wait=true"
    payload = {"filter": {"must": [{"key": "project_id", "match": {"value": project_id}}]}}
    for attempt in range(retries + 1):
        try:
            response = requests.post(endpoint, json=payload, timeout=timeout)
            if response.status_code == 404:
                return
            response.raise_for_status()
            return
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(retry_sleep)


def _ensure_qdrant_collection(
    *,
    qdrant_url: str,
    collection: str,
    vector_size: int,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    def _extract_existing_size(resp: requests.Response) -> Optional[int]:
        try:
            data = resp.json()
        except ValueError:
            return None
        result = data.get("result", {})
        config = result.get("config", {})
        params = config.get("params", {})
        vectors = params.get("vectors", {})
        if isinstance(vectors, dict):
            size = vectors.get("size")
            if isinstance(size, int):
                return size
        return None

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                qdrant_url.rstrip("/") + f"/collections/{collection}",
                timeout=timeout,
            )
            if response.status_code == 200:
                existing_size = _extract_existing_size(response)
                if existing_size and existing_size != vector_size:
                    raise ValueError(
                        "Qdrant message collection vector size mismatch: "
                        f"{collection} has size {existing_size}, expected {vector_size}. "
                        "Use matching size or recreate collection."
                    )
                return
            payload = {"vectors": {"size": vector_size, "distance": "Cosine"}}
            create_resp = requests.put(
                qdrant_url.rstrip("/") + f"/collections/{collection}",
                json=payload,
                timeout=timeout,
            )
            create_resp.raise_for_status()
            return
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(retry_sleep)


def _hash_vector(text: str, size: int) -> List[float]:
    buckets = [0.0] * size
    for token in re.findall(r"[A-Za-z0-9_:.]+", text.lower()):
        idx = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % size
        buckets[idx] += 1.0
    norm = sum(value * value for value in buckets) ** 0.5
    if norm <= 0:
        return buckets
    return [value / norm for value in buckets]


def _message_point_payload(record: MessageRecord, project_name: str) -> Dict[str, Any]:
    return {
        "message_id": record.id,
        "name": record.name,
        "sender": record.sender,
        "receiver": record.receiver,
        "payload": record.payload,
        "response": record.response,
        "explanation": record.explanation,
        "file_path": record.file_path,
        "line": record.line,
        "confidence": record.confidence,
        "language": record.language,
        "project_id": record.project_id,
        "project_name": project_name,
    }


def upsert_messages_to_qdrant(
    *,
    qdrant_url: str,
    collection: str,
    records: Sequence[MessageRecord],
    project_name: str,
    vector_size: int = DEFAULT_MESSAGE_VECTOR_SIZE,
    batch_size: int = 256,
    embed_texts: Optional[Callable[[List[str], int, bool], List[List[float]]]] = None,
    timeout: float = 300.0,
    retries: int = 3,
    retry_sleep: float = 2.0,
    verbose: bool = False,
) -> Dict[str, int]:
    if not records:
        return {"upserted_points": 0}
    _ensure_qdrant_collection(
        qdrant_url=qdrant_url,
        collection=collection,
        vector_size=vector_size,
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )
    endpoint = qdrant_url.rstrip("/") + f"/collections/{collection}/points?wait=true"
    total = len(records)
    sent = 0
    for offset in range(0, total, max(1, batch_size)):
        batch = records[offset : offset + max(1, batch_size)]
        texts = [
            " | ".join(
                [
                    record.name,
                    record.sender,
                    record.receiver,
                    record.payload,
                    record.explanation,
                ]
            )
            for record in batch
        ]
        vectors: Optional[List[List[float]]] = None
        if embed_texts is not None:
            try:
                vectors = embed_texts(texts, max(1, batch_size), False)
            except TypeError:
                vectors = embed_texts(texts, batch_size=max(1, batch_size), verbose=False)
            except Exception:
                vectors = None
        points: List[Dict[str, Any]] = []
        for idx, record in enumerate(batch):
            vector: List[float]
            if vectors and idx < len(vectors) and isinstance(vectors[idx], list) and vectors[idx]:
                vector = vectors[idx]
            else:
                vector = _hash_vector(texts[idx], vector_size)
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, record.id)),
                    "vector": vector,
                    "payload": _message_point_payload(record, project_name),
                }
            )
        payload = {"points": points}
        for attempt in range(retries + 1):
            try:
                response = requests.put(endpoint, json=payload, timeout=timeout)
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt >= retries:
                    raise
                time.sleep(retry_sleep)
        sent += len(points)
        if verbose:
            print(f"[message][qdrant] upsert {sent}/{total}")
    return {"upserted_points": sent}


async def upsert_messages_to_neo4j(
    *,
    driver: Any,
    database: Optional[str],
    records: Sequence[MessageRecord],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    batch_size: int = 500,
    verbose: bool = False,
) -> Dict[str, int]:
    if not records:
        return {"upserted_messages": 0}
    query = """
    UNWIND $rows AS row
    MERGE (m:Message {id: row.id})
    SET m.name = row.name,
        m.sender = row.sender,
        m.receiver = row.receiver,
        m.payload = row.payload,
        m.response = row.response,
        m.explanation = row.explanation,
        m.file_path = row.file_path,
        m.line = row.line,
        m.confidence = row.confidence,
        m.project_id = row.project_id,
        m.project_name = row.project_name,
        m.language = row.language,
        m.repo = row.repo,
        m.build_system = row.build_system,
        m.updated_at = datetime()
    WITH m, row
    OPTIONAL MATCH (f:File {id: row.file_path})
    WHERE f.project_id = row.project_id
    FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END |
      MERGE (f)-[:CONTAINS]->(m)
    )
    WITH m, row
    MERGE (s:MessageEndpoint {id: row.sender_endpoint_id})
    SET s.name = row.sender,
        s.project_id = row.project_id,
        s.project_name = row.project_name,
        s.updated_at = datetime()
    MERGE (s)-[:SENDS_MESSAGE]->(m)
    WITH m, row
    FOREACH (_ IN CASE WHEN row.receiver_endpoint_id = '' THEN [] ELSE [1] END |
      MERGE (r:MessageEndpoint {id: row.receiver_endpoint_id})
      SET r.name = row.receiver,
          r.project_id = row.project_id,
          r.project_name = row.project_name,
          r.updated_at = datetime()
      MERGE (m)-[:TARGETS_ENDPOINT]->(r)
    )
    RETURN count(m) AS count
    """
    total = len(records)
    written = 0
    for offset in range(0, total, max(1, batch_size)):
        batch = records[offset : offset + max(1, batch_size)]
        rows: List[Dict[str, Any]] = []
        for record in batch:
            sender = record.sender or "unknown"
            receiver = record.receiver or ""
            sender_endpoint_id = f"msg_endpoint::{project_id}::{hashlib.sha1(sender.encode('utf-8')).hexdigest()[:16]}"
            receiver_endpoint_id = (
                f"msg_endpoint::{project_id}::{hashlib.sha1(receiver.encode('utf-8')).hexdigest()[:16]}"
                if receiver
                else ""
            )
            rows.append(
                {
                    "id": record.id,
                    "name": record.name,
                    "sender": sender,
                    "receiver": receiver,
                    "payload": record.payload,
                    "response": record.response,
                    "explanation": record.explanation,
                    "file_path": record.file_path,
                    "line": record.line,
                    "confidence": record.confidence,
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                    "sender_endpoint_id": sender_endpoint_id,
                    "receiver_endpoint_id": receiver_endpoint_id,
                }
            )
        records_result, _, _ = await driver.execute_query(query, {"rows": rows}, database=database)
        written += int((records_result or [{}])[0].get("count", 0))
        if verbose:
            print(f"[message][neo4j] upsert {min(offset + len(batch), total)}/{total}")
    return {"upserted_messages": written}


def write_message_artifact(
    *,
    root: str,
    parser: str,
    project_id: str,
    project_name: str,
    records: Sequence[MessageRecord],
    output_dir: Optional[str],
    cache_dir: Optional[str],
    commit_sha_before: str,
    commit_sha_after: str,
) -> str:
    if output_dir:
        base_dir = os.path.abspath(output_dir)
        os.makedirs(base_dir, exist_ok=True)
    else:
        base_dir = safe_cache_root(cache_dir, "message_scan_artifacts", project_root=root)
    project_dir = os.path.join(base_dir, _safe_segment(project_id))
    os.makedirs(project_dir, exist_ok=True)
    output_path = os.path.join(project_dir, f"{_safe_segment(parser)}_messages.json")
    payload = {
        "schema_version": MESSAGE_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": project_id,
        "project_name": project_name,
        "parser": parser,
        "commit_sha_before": commit_sha_before or "",
        "commit_sha_after": commit_sha_after or "",
        "message_count": len(records),
        "messages": [
            {
                "id": record.id,
                "name": record.name,
                "sender": record.sender,
                "receiver": record.receiver,
                "payload": record.payload,
                "response": record.response,
                "explanation": record.explanation,
                "source": {"file": record.file_path, "line": record.line},
                "confidence": record.confidence,
                "language": record.language,
            }
            for record in records
        ],
    }
    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    os.replace(temp_path, output_path)
    return output_path


async def run_message_scan_pipeline(
    *,
    root: str,
    parser: str,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    incremental: bool,
    changed_files: Optional[Iterable[str]],
    deleted_files: Optional[Iterable[str]],
    driver: Optional[Any],
    neo4j_database: Optional[str],
    qdrant_url: Optional[str],
    qdrant_collection: Optional[str],
    output_dir: Optional[str],
    cache_dir: Optional[str],
    commit_sha_before: str,
    commit_sha_after: str,
    qdrant_vector_size: int = DEFAULT_MESSAGE_VECTOR_SIZE,
    qdrant_batch_size: int = 256,
    embed_texts: Optional[Callable[[List[str], int, bool], List[List[float]]]] = None,
    qdrant_timeout: float = 300.0,
    qdrant_retries: int = 3,
    qdrant_retry_sleep: float = 2.0,
    replace_existing_on_full: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    normalized_changed = sorted(
        {
            _normalize_rel_path(root, path)
            for path in (changed_files or [])
            if _normalize_rel_path(root, path)
        }
    )
    normalized_deleted = sorted(
        {
            _normalize_rel_path(root, path)
            for path in (deleted_files or [])
            if _normalize_rel_path(root, path)
        }
    )
    cleanup_paths = sorted(set(normalized_changed) | set(normalized_deleted))

    if driver:
        if incremental and cleanup_paths:
            await cleanup_message_nodes_neo4j(
                driver=driver,
                database=neo4j_database,
                project_id=project_id,
                file_paths=cleanup_paths,
                verbose=verbose,
            )
        elif not incremental and replace_existing_on_full:
            await cleanup_all_message_nodes_neo4j(
                driver=driver,
                database=neo4j_database,
                project_id=project_id,
                verbose=verbose,
            )

    if qdrant_url and qdrant_collection:
        if incremental and cleanup_paths:
            try:
                cleanup_qdrant_for_files(
                    qdrant_url=qdrant_url,
                    collection=qdrant_collection,
                    project_id=project_id,
                    file_paths=cleanup_paths,
                    timeout=qdrant_timeout,
                    retries=qdrant_retries,
                    retry_sleep=qdrant_retry_sleep,
                    batch_size=qdrant_batch_size,
                    verbose=verbose,
                )
            except requests.RequestException:
                if verbose:
                    print(
                        "[message][cleanup][qdrant] skip: collection unavailable or cleanup failed (%s)"
                        % qdrant_collection
                    )
        elif not incremental and replace_existing_on_full:
            if verbose:
                print(f"[message][cleanup][qdrant] clear project_id={project_id}")
            _qdrant_delete_by_project(
                qdrant_url=qdrant_url,
                collection=qdrant_collection,
                project_id=project_id,
                timeout=qdrant_timeout,
                retries=qdrant_retries,
                retry_sleep=qdrant_retry_sleep,
            )

    target_files = normalized_changed if incremental else None
    records = collect_messages_for_parser(
        root=root,
        parser=parser,
        project_id=project_id,
        language=language,
        target_files=target_files,
    )
    if verbose:
        print(
            "[message][parse] parser=%s detector=%s records=%d incremental=%s changed=%d deleted=%d"
            % (
                parser,
                "specific" if has_specific_detector(parser) else "generic",
                len(records),
                incremental,
                len(normalized_changed),
                len(normalized_deleted),
            )
        )

    neo4j_stats = {"upserted_messages": 0}
    if driver and records:
        neo4j_stats = await upsert_messages_to_neo4j(
            driver=driver,
            database=neo4j_database,
            records=records,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            verbose=verbose,
        )

    qdrant_stats = {"upserted_points": 0}
    if qdrant_url and qdrant_collection and records:
        qdrant_stats = upsert_messages_to_qdrant(
            qdrant_url=qdrant_url,
            collection=qdrant_collection,
            records=records,
            project_name=project_name,
            vector_size=qdrant_vector_size,
            batch_size=qdrant_batch_size,
            embed_texts=embed_texts,
            timeout=qdrant_timeout,
            retries=qdrant_retries,
            retry_sleep=qdrant_retry_sleep,
            verbose=verbose,
        )

    artifact_path = write_message_artifact(
        root=root,
        parser=parser,
        project_id=project_id,
        project_name=project_name,
        records=records,
        output_dir=output_dir,
        cache_dir=cache_dir,
        commit_sha_before=commit_sha_before,
        commit_sha_after=commit_sha_after,
    )
    if verbose:
        print(f"[message][artifact] {artifact_path}")

    return {
        "parser": parser,
        "message_count": len(records),
        "artifact_path": artifact_path,
        "neo4j_upserted": int(neo4j_stats.get("upserted_messages", 0)),
        "qdrant_upserted": int(qdrant_stats.get("upserted_points", 0)),
        "qdrant_collection": qdrant_collection or "",
        "qdrant_vector_size": int(qdrant_vector_size),
    }


def default_message_collection_name(code_collection: str) -> str:
    base = (code_collection or "").strip()
    if not base:
        return "messages"
    if base.endswith("_functions"):
        return base[: -len("_functions")] + "_mess"
    if base.endswith("__functions"):
        return base[: -len("__functions")] + "_mess"
    if base.endswith("_function"):
        return base[: -len("_function")] + "_mess"
    return f"{base}_mess"


def parser_from_language(language: str) -> str:
    text = (language or "").strip().lower()
    if text in {"c++", "cpp"}:
        return "cplus"
    if text in {"pascal"}:
        return "delphi"
    if text in {"android-kotlin", "kotlin-android"}:
        return "android"
    return text
