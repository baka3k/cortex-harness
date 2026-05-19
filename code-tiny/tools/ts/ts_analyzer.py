from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests
import torch
from transformers import AutoModel, AutoTokenizer
from tree_sitter import Language, Parser

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config
from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    load_state,
    safe_cache_root,
    write_parse_cache,
    write_state,
)
from tools.common.cloc_stats import collect_cloc_stats, normalize_cloc_payload, write_cloc_stats_to_neo4j
from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files, cleanup_qdrant_with_writer
from tools.common.message_scan import default_message_collection_name, run_message_scan_pipeline
from tools.common.react_role_classifier import classify_file as _llm_classify_react_roles
from tools.common.semantic_inference import SemanticInferenceEngine
from tools.common.frontend_relationship_extractor import (
    FrontendRelationshipExtractor,
    FrameworkContext,
)
from tools.common.url_normalizer import (
    normalize_url_pattern,
    merge_base_url,
    normalize_http_method,
)
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter

_semantic_engine = SemanticInferenceEngine()
_frontend_extractor = FrontendRelationshipExtractor()

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None

# ── Sub-package imports (replaces former inline definitions) ──────────────────
from tools.ts.types.ast_types import (
    FunctionDef,
    ApiCallDef,
    RenderEdge,
    NavigateEdge,
    RouteConfigEntry,
    NavigatorDef,
    ParamListDef,
)
from tools.ts.types.graph_types import (
    FileDef,
    NamespaceDef,
    TypeDef,
    RelationEdge,
    CallEdge,
)
from tools.ts.utils.id_utils import (
    _symbol_id,
    _qualified_name,
    _type_id,
    _namespace_id,
    _anonymous_name,
    _stable_point_id,
)
from tools.ts.utils.file_utils import (
    _PARSE_CACHE_VERSION,
    _TS_SOURCE_EXTENSIONS,
    _SCAN_SKIP_DIRS,
    _SCREEN_DIR_SEGMENTS,
    _SERVICE_DIR_SEGMENTS,
    _INDEX_BASENAMES,
    _index_module_name,
    _is_screen_file,
    _is_service_file,
    _scan_ts_files,
    _file_path_to_route,
)
from tools.ts.utils.regex_patterns import (
    _SCREEN_NAME_SUFFIXES,
    _WRAPPER_NAME_SUFFIXES,
    _RE_HOC_FACTORY_NAME,
    _RE_WRAPS_CHILDREN,
    _NAV_CHROME_SUFFIXES,
    _NAVIGATOR_NAME_SUFFIXES,
    _RE_NAVIGATOR_FACTORY_NAME,
    _RE_SCREEN_HOOKS,
    _RE_SCREEN_NAV_CALL,
    _RE_SCREEN_PROP_NAMES,
    _RE_MIDDLEWARE_API,
    _RE_MIDDLEWARE_QUERY,
    _RE_MIDDLEWARE_REDUX,
    _RE_SERVICE_LAYER,
    _RE_FETCH_CALL,
    _RE_FETCH_METHOD,
    _RE_AXIOS_SHORTHAND,
    _RE_AXIOS_CONFIG,
    _RE_HTTP_CLIENT,
    _RE_NAMED_CLIENT,
    _RE_AXIOS_CREATE,
    _RE_ENV_VAR,
    _RE_ASSIGN_USE_NAVIGATION,
    _RE_ASSIGN_USE_NAVIGATION_DESTRUCT,
    _RE_ASSIGN_USE_ROUTER,
    _RE_ASSIGN_USE_NAVIGATE,
    _RE_ASSIGN_USE_HISTORY,
    _RE_NAV_PROP_CALL,
    _RE_NAV_PROP_OBJ,
    _RE_ROUTER_CALL,
    _RE_ROUTER_OBJ,
    _RE_NAV_REF_CALL,
    _RE_JSX_LINK,
    _RE_JSX_NAVIGATE_EL,
    _nav_obj_method_re,
    _nav_fn_call_re,
    _RE_USER_TRIGGER,
    _RE_SYSTEM_TRIGGER,
    _RE_ASYNC_TRIGGER,
    _RE_AUTH_GUARD,
    _RE_PERM_GUARD,
    _RE_SCREEN_ELEM_START,
    _RE_SCREEN_NAME_ATTR,
    _RE_SCREEN_COMP_ATTR,
    _RE_NAVIGATOR_FACTORY,
    _FACTORY_TO_NAV_TYPE,
    _CALL_EXPR_KIND_MAP,
)
from tools.ts.agents.parser_agent import (
    _JSX_NODE_TYPES,
    _get_ts_parser,
    _parse_file,
    _node_text,
    _line_from_byte,
    _node_snippet,
    _find_nodes_by_type,
    _is_benign_jsx_entity_error,
    _tree_error_stats,
    _first_identifier,
    _extract_name_field,
    _extract_leading_comment,
    _extract_file_comment,
    _build_note,
    _normalize_ws,
    _normalize_call_name,
    _extract_scope_stack,
    _count_parameters,
    _extract_return_type,
    _extract_param_types,
    _count_arguments,
    _iter_calls,
    _extract_call_name,
    _collect_imports,
    _collect_exports,
    _jsx_name,
    _collect_jsx_tags,
)
from tools.ts.agents.dependency_agent import (
    _collect_ts_import_graph,
    _expand_impacted_files_by_imports,
)
from tools.ts.agents.symbol_agent import (
    _detect_middleware_kind,
    _detect_react_role,
    _clean_url_expr,
    _extract_file_base_url,
    _extract_api_calls,
    _collect_navigate_calls,
    _classify_nav_context,
    _detect_nav_guard,
    _collect_route_configs,
    _extract_navigator_declarations,
    _extract_param_lists,
    _has_jsx_in_subtree,
    _collect_rendered_components,
)
from tools.ts.agents.traversal_agent import (
    _NAMESPACE_NODE_TYPES,
    _TYPE_NODE_KINDS,
    _FUNCTION_NODE_KINDS,
    _INNER_FUNCTION_TYPES,
    _find_inner_function_arg,
    _extract_root_factory_name,
    _record_function,
    _walk_tree,
)
# ─────────────────────────────────────────────────────────────────────────────

def parse_ts_file(path: str, root: str) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[TypeDef],
    List[NamespaceDef],
    List[RelationEdge],
    List[RenderEdge],
    List[NavigateEdge],
    FileDef,
    Dict[str, Any],
    List["ApiCallDef"],
    List[NavigatorDef],
    List[ParamListDef],
]:
    rel_path = os.path.relpath(path, root)
    tree, source_bytes = _parse_file(path)
    has_error, error_nodes = _tree_error_stats(tree, source_bytes)
    snippet = source_bytes.decode("utf-8", errors="ignore")
    start_line = 1
    end_line = snippet.count("\n") + 1
    file_comment = _extract_file_comment(tree, source_bytes)
    file_summary = file_comment
    file_note = _build_note(snippet, file_comment, file_summary)
    imports = _collect_imports(tree, source_bytes)
    exports = _collect_exports(tree, source_bytes)
    jsx_tags, jsx_components = _collect_jsx_tags(tree, source_bytes)
    file_def = FileDef(
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        code=snippet,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
        imports=imports,
        exports=exports,
        jsx_tags=jsx_tags,
        jsx_components=jsx_components,
    )
    namespaces: List[NamespaceDef] = []
    types: List[TypeDef] = []
    functions: List[FunctionDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    renders: List[RenderEdge] = []
    navigates: List[NavigateEdge] = []
    namespace_registry: Dict[str, NamespaceDef] = {}
    type_registry: Dict[str, TypeDef] = {}
    exported_names: Set[str] = set()
    _walk_tree(
        tree.root_node,
        source_bytes,
        rel_path,
        [],
        [],
        namespaces,
        types,
        functions,
        relations,
        calls,
        renders,
        navigates,
        namespace_registry,
        type_registry,
        False,
        exported_names,
    )
    if exported_names:
        for func in functions:
            if func.exported:
                continue
            if func.scope_name is None and func.name in exported_names:
                func.exported = True
        for type_def in types:
            if type_def.exported:
                continue
            if "::" not in type_def.qualified_name and type_def.name in exported_names:
                type_def.exported = True
    # LLM-assisted react_role upgrade for uncertain PascalCase+JSX candidates.
    # No-op unless REACT_ROLE_LLM_CLASSIFY=1 is set in the environment.
    _llm_classify_react_roles(functions, rel_path)
    # File-level route config extraction — catches navigator declarations that are
    # at module scope (outside any function) and supplements per-function extraction.
    _fn_route_names: Set[str] = {
        nav.target_name for nav in navigates if nav.nav_method == "__route_config__"
    }
    for _rname, _cname in _collect_route_configs(snippet):
        if _rname not in _fn_route_names:
            navigates.append(NavigateEdge(
                source_id=f"file::{rel_path}",
                target_name=_rname,
                nav_method="__route_config__",
                via=_cname,
            ))

    # ── API call extraction (frontend → backend bridge) ───────────────────────
    # Detect outgoing HTTP requests in api_call / query_client functions.
    file_base_url = _extract_file_base_url(snippet)
    api_calls: List[ApiCallDef] = []
    for func in functions:
        if func.middleware_kind in ("api_call", "query_client", "service"):
            extracted = _extract_api_calls(
                func.code,
                func.symbol_id,
                rel_path,
                func.start_line,
                file_base_url=file_base_url,
            )
            api_calls.extend(extracted)

    # ── Navigator factory + ParamList extraction ──────────────────────────────
    file_navigators = _extract_navigator_declarations(snippet, rel_path)
    file_param_lists = _extract_param_lists(tree.root_node, source_bytes, rel_path)

    return (
        functions,
        calls,
        types,
        namespaces,
        relations,
        renders,
        navigates,
        file_def,
        {
            "parser_language": "typescript_tree_sitter",
            "parser_available": True,
            "has_error": has_error,
            "error_nodes": error_nodes,
        },
        api_calls,
        file_navigators,
        file_param_lists,
    )


# Neo4jWriter class has been removed and replaced with GraphDriverFactory + LanguageCodeWriter
# See tools/graph/ for the new abstraction layer
# Migration guide: See kotlin_analyzer.py for reference implementation

# ─────────────────────────────────────────────────────────────────────────────
# Graph DB write helpers — ApiCall nodes + CALLS_API edges
# ─────────────────────────────────────────────────────────────────────────────

_UPSERT_API_CALL_UNWIND = """
UNWIND $rows AS row
MERGE (ac:ApiCall {symbol_id: row.symbol_id})
SET ac.url_pattern    = row.url_pattern,
    ac.http_method    = row.http_method,
    ac.raw_url        = row.raw_url,
    ac.base_url_ref   = row.base_url_ref,
    ac.file_path      = row.file_path,
    ac.start_line     = row.start_line,
    ac.confidence     = row.confidence,
    ac.project_id     = row.project_id,
    ac.project_name   = row.project_name
RETURN count(ac) AS count
"""

_UPSERT_CALLS_API_UNWIND = """
UNWIND $rows AS row
MATCH (f:Function {symbol_id: row.caller_function_id})
MATCH (ac:ApiCall  {symbol_id: row.symbol_id})
MERGE (f)-[:CALLS_API]->(ac)
"""


async def _write_api_calls(
    driver: Any,
    api_calls: List[Dict[str, Any]],
    *,
    database: str,
    batch_size: int = 200,
    verbose: bool = False,
) -> None:
    """Upsert ApiCall nodes and CALLS_API edges to Neo4j."""
    if not api_calls:
        return

    async def _run(query: str, rows: List[Dict[str, Any]], label: str) -> None:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i: i + batch_size]
            try:
                await driver.execute_query(query, {"rows": chunk}, database)
            except Exception as exc:
                if verbose:
                    print(f"[api-call-writer] {label} error: {exc}")

    await _run(_UPSERT_API_CALL_UNWIND, api_calls, "ApiCall")
    await _run(_UPSERT_CALLS_API_UNWIND, api_calls, "CALLS_API")


class QdrantWriter:
    def __init__(
        self,
        url: str,
        collection: str,
        vector_size: int,
        timeout: float = 300.0,
        retries: int = 3,
        retry_sleep: float = 2.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.vector_size = vector_size
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep

    def ensure_collection(self) -> None:
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(
                    f"{self.url}/collections/{self.collection}",
                    timeout=self.timeout,
                )
                if response.status_code == 200:
                    existing_size = self._extract_vector_size(response)
                    if existing_size and existing_size != self.vector_size:
                        raise ValueError(
                            "Qdrant collection vector size mismatch: "
                            f"{self.collection} has size {existing_size}, "
                            f"but embedder produces size {self.vector_size}. "
                            "Use a matching embedder or recreate the collection."
                        )
                    return
                payload = {
                    "vectors": {"size": self.vector_size, "distance": "Cosine"},
                }
                response = requests.put(
                    f"{self.url}/collections/{self.collection}",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return
            except requests.RequestException:
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_sleep)

    @staticmethod
    def _extract_vector_size(response: requests.Response) -> Optional[int]:
        try:
            data = response.json()
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

    def upsert(self, points: List[Dict]) -> None:
        if not points:
            return
        payload = {"points": points}
        for attempt in range(self.retries + 1):
            try:
                response = requests.put(
                    f"{self.url}/collections/{self.collection}/points?wait=true",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return
            except requests.RequestException:
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_sleep)


def _should_trust_remote_code(model_name: str) -> bool:
    jina_path = os.environ.get("JINA_MODEL_PATH")
    if jina_path and os.path.normpath(jina_path) == os.path.normpath(model_name):
        return True
    return "jina" in model_name.lower()


def _resolve_embedding_model_source(model_name: str, *, verbose: bool = False) -> str:
    local_model_path = os.environ.get("CODE_EMBEDDING_MODEL_PATH")
    if not local_model_path:
        return model_name
    resolved_path = os.path.abspath(os.path.expanduser(local_model_path))
    if not os.path.exists(resolved_path):
        raise FileNotFoundError(
            "CODE_EMBEDDING_MODEL_PATH does not exist: %s" % local_model_path
        )
    if verbose:
        print("[embed] using local model path from CODE_EMBEDDING_MODEL_PATH: %s" % resolved_path)
    return resolved_path


def _is_hf_cache_permission_error(exc: BaseException) -> bool:
    current: Optional[BaseException] = exc
    while current is not None:
        if isinstance(current, PermissionError):
            return True
        if isinstance(current, OSError):
            message = str(current).lower()
            if "permissionerror" in message and "huggingface" in message:
                return True
            if "permission denied" in message and "huggingface" in message:
                return True
        current = current.__cause__ or current.__context__
    return False


def _prepare_local_hf_caches(base_dir: str) -> str:
    hub_cache = os.path.join(base_dir, "hub")
    modules_cache = os.path.join(base_dir, "modules")
    os.makedirs(hub_cache, exist_ok=True)
    os.makedirs(modules_cache, exist_ok=True)
    os.environ["HF_HOME"] = base_dir
    os.environ["HUGGINGFACE_HUB_CACHE"] = hub_cache
    os.environ["TRANSFORMERS_CACHE"] = hub_cache
    os.environ["HF_MODULES_CACHE"] = modules_cache
    try:
        import transformers.dynamic_module_utils as dynamic_module_utils

        dynamic_module_utils.HF_MODULES_CACHE = modules_cache
    except Exception:
        pass
    try:
        import transformers.utils.hub as hub_utils

        hub_utils.HUGGINGFACE_HUB_CACHE = hub_cache
        hub_utils.TRANSFORMERS_CACHE = hub_cache
    except Exception:
        pass
    return hub_cache


class CodeEmbedder:
    def __init__(
        self,
        model_name: str,
        device: str,
        max_embed_chars: int,
        chunk_embed: bool,
        *,
        fallback_cache_base_dir: Optional[str] = None,
        project_root: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        model_source = _resolve_embedding_model_source(model_name, verbose=verbose)
        trust_remote_code = _should_trust_remote_code(model_name) or _should_trust_remote_code(model_source)
        extra_tokenizer_kwargs = {"fix_mistral_regex": True} if trust_remote_code else {}

        def _load_pretrained(cache_dir: Optional[str]) -> Tuple[Any, Any]:
            tokenizer_kwargs: Dict[str, Any] = {
                "trust_remote_code": trust_remote_code,
                **extra_tokenizer_kwargs,
            }
            model_kwargs: Dict[str, Any] = {"trust_remote_code": trust_remote_code}
            if cache_dir:
                tokenizer_kwargs["cache_dir"] = cache_dir
                model_kwargs["cache_dir"] = cache_dir
            tokenizer = AutoTokenizer.from_pretrained(model_source, **tokenizer_kwargs)
            model = AutoModel.from_pretrained(model_source, **model_kwargs)
            return tokenizer, model

        if verbose:
            print(f"[embed] Loading tokenizer and model: {model_source}", flush=True)
        try:
            self.tokenizer, self.model = _load_pretrained(cache_dir=None)
        except Exception as exc:
            if not _is_hf_cache_permission_error(exc):
                raise
            fallback_cache_dir = safe_cache_root(
                fallback_cache_base_dir,
                "hugging_cache",
                project_root=project_root,
            )
            fallback_hub_cache = _prepare_local_hf_caches(fallback_cache_dir)
            if verbose:
                print(
                    "[embed] HuggingFace cache permission denied; retrying with local cache: %s"
                    % fallback_cache_dir,
                    flush=True,
                )
            self.tokenizer, self.model = _load_pretrained(cache_dir=fallback_hub_cache)
        if verbose:
            print(f"[embed] Model weights loaded — moving to {device}...", flush=True)
        self.device = torch.device(device)
        self.model.to(self.device)
        self.model.eval()
        if verbose:
            print(f"[embed] Model on {device} — running warmup inference...", flush=True)
        self.max_embed_chars = max_embed_chars if max_embed_chars > 0 else None
        self.chunk_embed = chunk_embed
        self.vector_size = self._infer_vector_size()
        if verbose:
            print(f"[embed] Ready. vector_size={self.vector_size}", flush=True)

    def embed(self, texts: List[str], batch_size: int = 8, verbose: bool = False) -> List[List[float]]:
        if not texts:
            return []
        if self.chunk_embed:
            chunk_texts: List[str] = []
            chunk_map: List[int] = []
            for idx, text in enumerate(texts):
                for chunk in self._split_chunks(text):
                    chunk_texts.append(chunk)
                    chunk_map.append(idx)
            chunk_vectors = self._embed_texts(chunk_texts, batch_size=batch_size, verbose=verbose)
            return self._mean_pool_chunks(chunk_vectors, chunk_map, len(texts))
        truncated = [self._truncate_text(text) for text in texts]
        return self._embed_texts(truncated, batch_size=batch_size, verbose=verbose)

    def _embed_texts(self, texts: List[str], batch_size: int, verbose: bool) -> List[List[float]]:
        vectors: List[List[float]] = []
        total = len(texts)
        with torch.no_grad():
            for idx in range(0, len(texts), batch_size):
                batch = texts[idx : idx + batch_size]
                if verbose:
                    total_batches = max(1, (total + batch_size - 1) // batch_size)
                    print(f"[embed] batch {idx // batch_size + 1} / {total_batches}")
                if hasattr(self.model, "encode"):
                    try:
                        encoded = self.model.encode(batch, device=str(self.device))
                    except TypeError:
                        encoded = self.model.encode(batch)
                    if isinstance(encoded, torch.Tensor):
                        vectors.extend(encoded.detach().cpu().tolist())
                    else:
                        vectors.extend(encoded.tolist() if hasattr(encoded, "tolist") else [list(vec) for vec in encoded])
                    continue
                encoded = self.tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                encoded = {key: value.to(self.device) for key, value in encoded.items()}
                outputs = self.model(**encoded)
                embeddings = self._mean_pool(outputs.last_hidden_state, encoded["attention_mask"])
                vectors.extend(embeddings.cpu().tolist())
        return vectors

    @staticmethod
    def _mean_pool(last_hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        mask = mask.unsqueeze(-1).type_as(last_hidden)
        summed = (last_hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1)
        return summed / counts

    def _truncate_text(self, text: str) -> str:
        if self.max_embed_chars is None:
            return text
        if len(text) <= self.max_embed_chars:
            return text
        return text[: self.max_embed_chars]

    def _split_chunks(self, text: str) -> List[str]:
        if self.max_embed_chars is None:
            return [text]
        if len(text) <= self.max_embed_chars:
            return [text]
        return [text[i : i + self.max_embed_chars] for i in range(0, len(text), self.max_embed_chars)]

    @staticmethod
    def _mean_pool_chunks(
        vectors: List[List[float]],
        chunk_map: List[int],
        total_texts: int,
    ) -> List[List[float]]:
        sums: List[Optional[List[float]]] = [None] * total_texts
        counts = [0] * total_texts
        for vector, idx in zip(vectors, chunk_map):
            if sums[idx] is None:
                sums[idx] = list(vector)
            else:
                current = sums[idx]
                if current is not None:
                    for j, value in enumerate(vector):
                        current[j] += value
            counts[idx] += 1
        results: List[List[float]] = []
        for idx in range(total_texts):
            if sums[idx] is None:
                results.append([])
                continue
            denom = max(counts[idx], 1)
            results.append([value / denom for value in sums[idx]])
        return results

    def _infer_vector_size(self) -> int:
        if hasattr(self.model, "get_sentence_embedding_dimension"):
            try:
                size = self.model.get_sentence_embedding_dimension()
                if isinstance(size, int) and size > 0:
                    return size
            except Exception:
                pass
        config = getattr(self.model, "config", None)
        if config:
            for attr in ("sentence_embedding_dimension", "projection_dim", "hidden_size", "dim"):
                size = getattr(config, attr, None)
                if isinstance(size, int) and size > 0:
                    return size
        sample = self.embed(["_"], batch_size=1, verbose=False)
        if sample and sample[0]:
            return len(sample[0])
        raise ValueError("Unable to infer embedding vector size.")


def _stable_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))


def _func_qdrant_payload(
    func_item: Dict[str, Any],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
) -> Dict[str, Any]:
    """Build Qdrant payload dict for a function, including semantic fields."""
    return {
        "symbol_id":      func_item["symbol_id"],
        "qualified_name": func_item["qualified_name"],
        "name":           func_item["name"],
        "kind":           func_item["kind"],
        "scope_name":     func_item["scope_name"],
        "file_path":      func_item["file_path"],
        "start_line":     func_item["start_line"],
        "end_line":       func_item["end_line"],
        "arity":          func_item["arity"],
        "code":           func_item["code"],
        "comment":        func_item["comment"],
        "summary":        func_item["summary"],
        "note":           func_item["note"],
        "exported":       func_item.get("exported", False),
        # Semantic fields
        "intent":         func_item.get("intent", ""),
        "inferred_doc":   func_item.get("inferred_doc", False),
        "doc_confidence": func_item.get("doc_confidence", 0.0),
        "signals":        func_item.get("signals") or {},
        "side_effect":    func_item.get("side_effect", False),
        "return_type":    func_item.get("return_type", ""),
        # Project meta
        "project_id":   project_id,
        "project_name": project_name,
        "language":     language,
        "repo":         repo,
        "build_system": build_system,
    }


def _scan_ts_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SCAN_SKIP_DIRS]
        for name in filenames:
            if name.endswith(_TS_SOURCE_EXTENSIONS):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def _extract_module_specifiers_from_text(text: str) -> List[str]:
    specifiers: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            continue
        import_match = re.match(
            r"^(?:import|export)\s+(?:.+?\s+from\s+)?[\"'](?P<spec>[^\"']+)[\"']",
            line,
        )
        if import_match:
            specifiers.append(import_match.group("spec"))
        for req_match in re.finditer(r"(?:require|import)\(\s*[\"'](?P<spec>[^\"']+)[\"']\s*\)", line):
            specifiers.append(req_match.group("spec"))
    return specifiers


def _resolve_ts_module_specifier(
    source_rel_path: str,
    specifier: str,
    file_set: set[str],
) -> Optional[str]:
    if not specifier or not specifier.startswith("."):
        return None
    base_dir = os.path.dirname(source_rel_path)
    candidate = os.path.normpath(os.path.join(base_dir, specifier)).replace("\\", "/")
    if candidate in file_set:
        return candidate
    root_candidate, ext = os.path.splitext(candidate)
    probes: List[str] = []
    if ext:
        probes.append(candidate)
    else:
        probes.extend(f"{candidate}{suffix}" for suffix in _TS_SOURCE_EXTENSIONS)
    probes.append(f"{candidate}.d.ts")
    probes.extend(f"{candidate}/index{suffix}" for suffix in _TS_SOURCE_EXTENSIONS)
    probes.append(f"{candidate}/index.d.ts")
    for path in probes:
        normalized = os.path.normpath(path).replace("\\", "/")
        if normalized in file_set:
            return normalized
    if ext in {".ts", ".tsx", ".mts", ".cts"}:
        return None
    if not ext:
        for fallback_ext in (".ts", ".tsx", ".d.ts"):
            normalized = f"{root_candidate}{fallback_ext}".replace("\\", "/")
            if normalized in file_set:
                return normalized
    return None


def _collect_ts_import_graph(
    all_ts_files: List[str],
    root: str,
) -> Dict[str, List[str]]:
    rel_paths = [os.path.relpath(path, root).replace("\\", "/") for path in all_ts_files]
    file_set = set(rel_paths)
    deps_by_file: Dict[str, List[str]] = {}
    for abs_path, rel_path in zip(all_ts_files, rel_paths):
        resolved: set[str] = set()
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            deps_by_file[rel_path] = []
            continue
        for specifier in _extract_module_specifiers_from_text(text):
            dep = _resolve_ts_module_specifier(rel_path, specifier, file_set)
            if dep:
                resolved.add(dep)
        resolved.discard(rel_path)
        deps_by_file[rel_path] = sorted(resolved)
    return deps_by_file


def _expand_impacted_files_by_imports(
    changed_existing: set[str],
    deps_by_file: Dict[str, List[str]],
) -> set[str]:
    reverse_map: Dict[str, set[str]] = {}
    for source, deps in deps_by_file.items():
        for dep in deps:
            reverse_map.setdefault(dep, set()).add(source)

    impacted: set[str] = set()
    queue: List[str] = list(changed_existing)
    seen: set[str] = set(changed_existing)
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse_map.get(current, set())):
            if dependent in seen:
                continue
            seen.add(dependent)
            impacted.add(dependent)
            queue.append(dependent)
    return impacted


def _load_or_parse_payload(
    file_path: str,
    root: str,
    parse_cache_root: str,
    parse_cache: bool,
) -> Dict[str, Any]:
    def ensure_text_fields(item: Dict[str, Any]) -> None:
        if "comment" not in item:
            item["comment"] = ""
        if "summary" not in item:
            item["summary"] = item.get("comment") or ""
        if "note" not in item:
            item["note"] = _build_note(
                item.get("code") or "",
                item.get("comment") or "",
                item.get("summary") or "",
            )

    def ensure_file_fields(item: Dict[str, Any]) -> None:
        ensure_text_fields(item)
        if "imports" not in item or item["imports"] is None:
            item["imports"] = []
        if "exports" not in item or item["exports"] is None:
            item["exports"] = []
        if "jsx_tags" not in item or item["jsx_tags"] is None:
            item["jsx_tags"] = []
        if "jsx_components" not in item or item["jsx_components"] is None:
            item["jsx_components"] = []

    def ensure_exported_field(item: Dict[str, Any]) -> None:
        if "exported" not in item:
            item["exported"] = False

    def ensure_react_fields(item: Dict[str, Any]) -> None:
        if "react_role" not in item:
            item["react_role"] = ""
        if "middleware_kind" not in item:
            item["middleware_kind"] = ""

    def normalize_cached_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        file_def = payload.get("file_def")
        if isinstance(file_def, dict):
            ensure_file_fields(file_def)
        namespaces = payload.get("namespaces")
        if isinstance(namespaces, list):
            for item in namespaces:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        types = payload.get("types")
        if isinstance(types, list):
            for item in types:
                if isinstance(item, dict):
                    ensure_text_fields(item)
                    ensure_exported_field(item)
        functions = payload.get("functions")
        if isinstance(functions, list):
            for item in functions:
                if isinstance(item, dict):
                    ensure_text_fields(item)
                    ensure_exported_field(item)
                    ensure_react_fields(item)
        if "renders" not in payload or payload["renders"] is None:
            payload["renders"] = []
        if "navigates" not in payload or payload["navigates"] is None:
            payload["navigates"] = []
        if "api_calls" not in payload or payload["api_calls"] is None:
            payload["api_calls"] = []
        # Backward compat: older cache entries lack navigator extraction fields
        if "navigators" not in payload or payload["navigators"] is None:
            payload["navigators"] = []
        if "param_lists" not in payload or payload["param_lists"] is None:
            payload["param_lists"] = []
        for _nav_item in payload["navigates"]:
            if isinstance(_nav_item, dict):
                _nav_item.setdefault("via", "direct")
                _nav_item.setdefault("trigger_type", "user")
                _nav_item.setdefault("guard", None)
                _nav_item.setdefault("call_depth", 0)
                _nav_item.setdefault("source_trace", [])
                _nav_item.setdefault("confidence", 1.0)
        parse_meta = payload.get("parse_meta")
        if not isinstance(parse_meta, dict):
            payload["parse_meta"] = {
                "parser_language": "typescript_tree_sitter",
                "parser_available": True,
                "has_error": False,
                "error_nodes": 0,
            }
        return payload

    rel_path = os.path.relpath(file_path, root)
    cached_payload = None
    signature = None
    if parse_cache:
        file_sig = file_signature(file_path)
        if file_sig is not None:
            signature = f"{file_sig}|schema:{_PARSE_CACHE_VERSION}"
        cached_payload = load_parse_cache(parse_cache_root, rel_path, signature)
    if cached_payload:
        return normalize_cached_payload(cached_payload)
    (
        file_functions,
        file_calls,
        file_types,
        file_namespaces,
        file_relations,
        file_renders,
        file_navigates,
        file_def,
        parse_meta,
        file_api_calls,
        file_navigators,
        file_param_lists,
    ) = parse_ts_file(file_path, root)
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "types": [asdict(item) for item in file_types],
        "namespaces": [asdict(item) for item in file_namespaces],
        "relations": [asdict(item) for item in file_relations],
        "renders": [asdict(item) for item in file_renders],
        "navigates": [asdict(item) for item in file_navigates],
        "api_calls": [asdict(item) for item in file_api_calls],
        "navigators": [asdict(item) for item in file_navigators],
        "param_lists": [asdict(item) for item in file_param_lists],
        "file_def": asdict(file_def),
        "parse_meta": parse_meta,
    }
    if parse_cache and signature is not None:
        write_parse_cache(parse_cache_root, rel_path, signature, payload)
    return payload


async def build_call_graph(
    root: str,
    code_writer: Optional['LanguageCodeWriter'],
    qdrant_writer: Optional[QdrantWriter],
    embedder: Optional[CodeEmbedder],
    batch_size: int,
    qdrant_batch_size: int,
    cache_dir: Optional[str],
    keep_cache: bool,
    parse_cache: bool,
    neo4j_batch_size: int,
    neo4j_state_path: Optional[str],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    verbose: bool,
    incremental: bool = False,
    changed_files: Optional[Iterable[str]] = None,
    deleted_files: Optional[Iterable[str]] = None,
    commit_sha: str = "",
    commit_sha_before: str = "",
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "ts_analyzer", project_root=root)
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    all_scanned_files = _scan_ts_files(root)
    all_rel_paths = [os.path.relpath(path, root).replace("\\", "/") for path in all_scanned_files]
    rel_to_abs = {os.path.relpath(path, root).replace("\\", "/"): path for path in all_scanned_files}
    changed_set = {item.replace("\\", "/") for item in (changed_files or []) if item}
    deleted_set = {item.replace("\\", "/") for item in (deleted_files or []) if item}
    selected_rel_paths: set[str]
    impacted_by_imports_count = 0
    if incremental:
        changed_existing = {path for path in changed_set if path in rel_to_abs}
        deps_by_file = _collect_ts_import_graph(all_scanned_files, root)
        impacted = _expand_impacted_files_by_imports(changed_existing, deps_by_file)
        selected_rel_paths = changed_existing | impacted
        impacted_by_imports_count = len(impacted)
        selected_files = [rel_to_abs[path] for path in all_rel_paths if path in selected_rel_paths]
    else:
        selected_rel_paths = set(all_rel_paths)
        selected_files = all_scanned_files
    if verbose:
        if incremental:
            print(
                "[scan] incremental before=%s after=%s changed=%d deleted=%d selected=%d/%d impacted_by_imports=%d"
                % (
                    commit_sha_before or "unknown",
                    commit_sha or "unknown",
                    len(changed_set),
                    len(deleted_set),
                    len(selected_files),
                    len(all_scanned_files),
                    impacted_by_imports_count,
                )
            )
        print(f"[scan] Found {len(selected_files)} TypeScript files under {root}")
    total_files = len(selected_files)

    cleanup_targets = sorted(changed_set | deleted_set)
    if incremental and cleanup_targets:
        if code_writer:
            await cleanup_neo4j_for_files(
                driver=code_writer.driver,
                database=code_writer.database,
                project_id=project_id,
                file_paths=cleanup_targets,
                verbose=verbose,
            )
        if qdrant_writer:
            cleanup_qdrant_with_writer(
                writer=qdrant_writer,
                project_id=project_id,
                file_paths=cleanup_targets,
                verbose=verbose,
            )

    def iter_selected_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(selected_files, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    selected_payloads: List[Dict[str, Any]] = []
    selected_payload_by_rel: Dict[str, Dict[str, Any]] = {}
    parse_error_file_count = 0
    parse_error_node_total = 0
    parse_error_examples: List[str] = []
    for payload in iter_selected_payloads(log_parse=True):
        selected_payloads.append(payload)
        file_def = payload.get("file_def") or {}
        rel_path = file_def.get("file_path") or ""
        if rel_path:
            selected_payload_by_rel[rel_path] = payload
        parse_meta = payload.get("parse_meta") or {}
        has_error = bool(parse_meta.get("has_error"))
        error_nodes = int(parse_meta.get("error_nodes") or 0)
        if has_error or error_nodes > 0:
            parse_error_file_count += 1
            parse_error_node_total += error_nodes
            if rel_path and len(parse_error_examples) < 10:
                parse_error_examples.append(rel_path)

    if verbose:
        if parse_error_file_count:
            print(
                "[parse] tree-sitter reported errors in %d/%d files (%d ERROR nodes)"
                % (parse_error_file_count, total_files, parse_error_node_total)
            )
            for path in parse_error_examples:
                print(f"  [parse][sample-error] {path}")
        else:
            print("[parse] tree-sitter parse status: no error nodes detected")

    # ── Semantic enrichment ──────────────────────────────────────────────────
    # Collect all functions + calls across selected payloads for batch enrichment.
    # Only processes functions that lack a developer-written comment.
    if verbose:
        print("[semantic] Running semantic inference on selected functions...")
    _all_selected_functions: List[Dict[str, Any]] = []
    _all_selected_calls: List[Dict[str, Any]] = []
    for _payload in selected_payloads:
        _all_selected_functions.extend(_payload.get("functions") or [])
        _all_selected_calls.extend(_payload.get("calls") or [])
    _semantic_engine.enrich_corpus(_all_selected_functions, _all_selected_calls)
    if verbose:
        _enriched = sum(1 for f in _all_selected_functions if f.get("inferred_doc"))
        _avg_conf = (
            sum(f.get("doc_confidence", 0.0) for f in _all_selected_functions) / max(len(_all_selected_functions), 1)
        )
        print(
            "[semantic] Enriched %d/%d functions; avg confidence=%.2f"
            % (_enriched, len(_all_selected_functions), _avg_conf)
        )

    # ── Frontend relationship extraction ─────────────────────────────────────
    # Infers structured graph relationships (RENDER, CALLS, NAVIGATE,
    # STATE_UPDATE, SIDE_EFFECT) from framework semantics, not raw strings.
    _fe_known_screens = frozenset(
        f["name"] for f in _all_selected_functions
        if f.get("react_role") == "screen" and f.get("name")
    )
    _fe_ctx = FrameworkContext(
        framework=language or "react",
        known_screens=_fe_known_screens,
    )
    for _c in _all_selected_calls:
        _caller = _c.get("caller_id") or ""
        _callee = _c.get("callee_id") or ""
        if _caller and _callee:
            _fe_ctx.call_graph.setdefault(_caller, []).append(_callee)
    _frontend_extractor.extract_batch(
        _all_selected_functions,
        _all_selected_calls,
        framework=_fe_ctx.framework,
    )
    if verbose:
        print("[frontend-rel] Relationship extraction complete")
    # ─────────────────────────────────────────────────────────────────────────

    index_payloads: List[Dict[str, Any]]
    if incremental and selected_rel_paths:
        index_payloads = []
        for index, rel_path in enumerate(all_rel_paths, start=1):
            cached = selected_payload_by_rel.get(rel_path)
            if cached is not None:
                index_payloads.append(cached)
                continue
            abs_path = rel_to_abs[rel_path]
            if verbose and (index == 1 or index % 200 == 0 or index == len(all_rel_paths)):
                print(f"[index] {index}/{len(all_rel_paths)}: {rel_path}")
            index_payloads.append(_load_or_parse_payload(abs_path, root, parse_cache_root, parse_cache))
    elif incremental:
        index_payloads = []
    else:
        index_payloads = list(selected_payloads)

    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_name_arity: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    # Index for resolving rendered component names → symbol_id
    render_target_index: Dict[str, List[str]] = {}
    # Index for resolving navigation targets → symbol_id
    nav_screen_index: Dict[str, List[str]] = {}   # screen name → [symbol_ids]
    nav_route_index: Dict[str, List[str]] = {}    # normalized route → [symbol_ids]
    # Navigation Intelligence V2.0 indexes
    func_role_map: Dict[str, str] = {}            # symbol_id → react_role
    route_config_map: Dict[str, str] = {}         # navigator route_name → component_name

    # ── Pass 1: Collect ALL navigator route registrations across the whole codebase.
    # This must happen BEFORE the function index build so that components registered
    # as navigator screens (component={HomeScreen}) can be upgraded to react_role
    # "screen" in Pass 2, regardless of their parse-time classification.
    # Navigator registration is the strongest possible "is a screen" signal —
    # more reliable than file paths, name suffixes, or hook usage.
    for _payload in index_payloads:
        for _rc in _payload.get("navigates") or []:
            if isinstance(_rc, dict) and _rc.get("nav_method") == "__route_config__":
                _rname = _rc.get("target_name", "")
                _cname = _rc.get("via", "")
                if _rname and _cname:
                    route_config_map.setdefault(_rname, _cname)

    # Component names confirmed as screens by navigator registration
    _navigator_registered_screens: Set[str] = set(route_config_map.values())

    # ── Pass 2: Build function indexes, upgrading navigator-registered components.
    expected_points = 0
    for payload in index_payloads:
        file_def = payload.get("file_def") or {}
        file_path = file_def.get("file_path")
        for func in payload["functions"]:
            if file_path in selected_rel_paths:
                expected_points += 1
            entry = {
                "symbol_id": func["symbol_id"],
                "scope_name": func["scope_name"],
                "arity": func["arity"],
            }
            function_index_by_name.setdefault(func["name"], []).append(entry)
            if func["arity"] is not None:
                function_index_by_name_arity.setdefault((func["name"], func["arity"]), []).append(entry)

            react_role = func.get("react_role", "")
            # Navigator registration overrides parse-time classification:
            # if a component appears as component={X} inside any Stack/Tab/Drawer.Screen,
            # it IS a screen — promote "component" and "" to "screen".
            # Guard: never promote known nav-chrome names (HeaderRight, TabBarIcon, etc.)
            if (
                func["name"] in _navigator_registered_screens
                and react_role in {"component", ""}
                and not func["name"].endswith(_NAV_CHROME_SUFFIXES)
            ):
                react_role = "screen"

            if react_role in {"component", "screen"}:
                render_target_index.setdefault(func["name"], []).append(func["symbol_id"])
            # nav_screen_index must contain ONLY confirmed screens.  Including
            # "component" here would let navigation.navigate("Modal") resolve to
            # a generic component, creating false NAVIGATE edges.
            if react_role == "screen":
                nav_screen_index.setdefault(func["name"], []).append(func["symbol_id"])
            if react_role == "screen":
                route = _file_path_to_route(func.get("file_path", ""))
                if route:
                    nav_route_index.setdefault(route, []).append(func["symbol_id"])
            # V2.0: store upgraded role for attribution in NAVIGATE resolution
            fid = func.get("symbol_id")
            if fid:
                func_role_map[fid] = react_role

    if code_writer:
        if verbose:
            print("[graph] Writing nodes and relations (streaming)...")

        def resolve_callee_id(call: Dict[str, Any]) -> Optional[str]:
            candidates = None
            if call.get("callee_arity") is not None:
                candidates = function_index_by_name_arity.get((call["callee_name"], call["callee_arity"]))
            if not candidates:
                candidates = function_index_by_name.get(call["callee_name"])
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]["symbol_id"]
            caller_scope = call.get("caller_scope")
            if caller_scope:
                scoped = [cand for cand in candidates if cand.get("scope_name") == caller_scope]
                if len(scoped) == 1:
                    return scoped[0]["symbol_id"]
            return None

        all_projects = [
            {
                "id": project_id,
                "name": project_name,
                "language": language,
                "repo": repo,
                "root": root,
                "build_system": build_system,
            }
        ]
        all_namespaces: List[Dict[str, Any]] = []
        all_files: List[Dict[str, Any]] = []
        all_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []
        all_raw_navigates: List[Dict[str, Any]] = []   # V2.0: collected then resolved post-loop
        all_navigators: List[Dict[str, Any]] = []
        all_param_lists: List[Dict[str, Any]] = []

        for payload in selected_payloads:
            file_def = payload["file_def"]
            file_id = file_def["file_path"]
            all_files.append(
                {
                    "id": file_id,
                    "path": file_id,
                    "start_line": file_def["start_line"],
                    "end_line": file_def["end_line"],
                    "code": file_def["code"],
                    "comment": file_def["comment"],
                    "summary": file_def["summary"],
                    "note": file_def["note"],
                    "imports": file_def.get("imports") or [],
                    "exports": file_def.get("exports") or [],
                    "jsx_tags": file_def.get("jsx_tags") or [],
                    "jsx_components": file_def.get("jsx_components") or [],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                }
            )
            all_relations.append(
                {"source_id": project_id, "target_id": file_id, "rel_type": "CONTAINS", "properties": {}}
            )
            for ns in payload["namespaces"]:
                all_namespaces.append(
                    {
                        "id": ns["symbol_id"],
                        "name": ns["name"],
                        "qualified_name": ns["qualified_name"],
                        "file_path": ns["file_path"],
                        "start_line": ns["start_line"],
                        "end_line": ns["end_line"],
                        "code": ns["code"],
                        "comment": ns["comment"],
                        "summary": ns["summary"],
                        "note": ns["note"],
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": ns["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
            for type_def in payload["types"]:
                all_types.append(
                    {
                        "id": type_def["symbol_id"],
                        "name": type_def["name"],
                        "qualified_name": type_def["qualified_name"],
                        "kind": type_def["kind"],
                        "file_path": type_def["file_path"],
                        "start_line": type_def["start_line"],
                        "end_line": type_def["end_line"],
                        "code": type_def["code"],
                        "comment": type_def["comment"],
                        "summary": type_def["summary"],
                        "note": type_def["note"],
                        "exported": type_def.get("exported", False),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": type_def["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
            for func in payload["functions"]:
                all_functions.append(
                    {
                        "id": func["symbol_id"],
                        "name": func["name"],
                        "qualified_name": func["qualified_name"],
                        "kind": func["kind"],
                        "scope_name": func["scope_name"],
                        "class_name": None,
                        "package_name": None,
                        "file_path": func["file_path"],
                        "start_line": func["start_line"],
                        "end_line": func["end_line"],
                        "arity": func["arity"],
                        "code": func["code"],
                        "comment": func["comment"],
                        "summary": func["summary"],
                        "note": func["note"],
                        "exported": func.get("exported", False),
                        # Semantic fields
                        "intent":         func.get("intent", ""),
                        "doc_confidence": func.get("doc_confidence", 0.0),
                        "inferred_doc":   func.get("inferred_doc", False),
                        "side_effect":    func.get("side_effect", False),
                        "return_type":    func.get("return_type", ""),
                        # React classification — use func_role_map which may have been
                        # upgraded by navigator registration (component → screen)
                        "react_role":      func_role_map.get(func["symbol_id"], func.get("react_role", "")),
                        "middleware_kind": func.get("middleware_kind", ""),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": func["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
            for rel in payload["relations"]:
                all_relations.append(
                    {
                        "source_id": rel["source_id"],
                        "target_id": rel["target_id"],
                        "rel_type": rel["rel_type"],
                        "properties": rel["properties"],
                    }
                )
            for call in payload["calls"]:
                callee_id = call.get("callee_id") or resolve_callee_id(call)
                if callee_id:
                    all_calls.append({"caller_id": call["caller_id"], "callee_id": callee_id})
            # ── Resolve RENDERS edges ─────────────────────────────────────────
            for render in payload.get("renders") or []:
                renderer_id = render["renderer_id"]
                rendered_name = render["rendered_name"]
                candidates = render_target_index.get(rendered_name) or []
                renderer_is_screen = func_role_map.get(renderer_id) == "screen"
                if len(candidates) == 1:
                    # Screen renders Screen → skip; NAVIGATE system models this edge
                    if renderer_is_screen and func_role_map.get(candidates[0]) == "screen":
                        pass
                    else:
                        all_relations.append({
                            "source_id": renderer_id,
                            "target_id": candidates[0],
                            "rel_type": "RENDERS",
                            "properties": {},
                        })
                elif candidates:
                    # Multiple candidates - emit an edge to each (best-effort)
                    for cid in candidates:
                        # Screen renders Screen → skip
                        if renderer_is_screen and func_role_map.get(cid) == "screen":
                            continue
                        all_relations.append({
                            "source_id": renderer_id,
                            "target_id": cid,
                            "rel_type": "RENDERS",
                            "properties": {},
                        })
            # ────────────────────────────────────────────────────────────────
            # ── Collect NAVIGATE intents (resolved after loop with full call graph) ──
            all_raw_navigates.extend(
                nav for nav in (payload.get("navigates") or [])
                if isinstance(nav, dict) and nav.get("nav_method") != "__route_config__"
            )
            # ── Collect Navigator + ParamList declarations ────────────────────
            all_navigators.extend(
                n for n in (payload.get("navigators") or []) if isinstance(n, dict)
            )
            all_param_lists.extend(
                p for p in (payload.get("param_lists") or []) if isinstance(p, dict)
            )
            # ────────────────────────────────────────────────────────────────

        # ── Incremental-mode: pull navigates/navigators/param_lists from UNCHANGED
        # files too.  NAVIGATE edges are a GLOBAL product — their attribution
        # depends on the full render/call topology.  If only the changed-files
        # subset contributes nav intents, deeply-nested navigates whose source
        # file wasn't edited this run never get re-attributed, leaving stale
        # (pre-fix) edges in Neo4j and missing newly-discoverable ones.
        # In full-build mode index_payloads == selected_payloads so this loop
        # is a no-op.
        _sel_nav_paths: Set[str] = {
            (p.get("file_def") or {}).get("file_path", "")
            for p in selected_payloads
        }
        for _aug_p in index_payloads:
            _aug_fp = (_aug_p.get("file_def") or {}).get("file_path", "")
            if _aug_fp in _sel_nav_paths:
                continue
            all_raw_navigates.extend(
                nav for nav in (_aug_p.get("navigates") or [])
                if isinstance(nav, dict) and nav.get("nav_method") != "__route_config__"
            )
            all_navigators.extend(
                n for n in (_aug_p.get("navigators") or []) if isinstance(n, dict)
            )
            all_param_lists.extend(
                p for p in (_aug_p.get("param_lists") or []) if isinstance(p, dict)
            )

        # ── Navigation Intelligence V2.0: post-loop resolution and attribution ──
        # Build reverse call graph (callee → [callers]) from resolved CALL edges.
        reverse_call_graph: Dict[str, List[str]] = {}
        for _c in all_calls:
            _cee = _c.get("callee_id")
            _cer = _c.get("caller_id")
            if _cee and _cer:
                reverse_call_graph.setdefault(_cee, []).append(_cer)

        # Build reverse renders graph (rendered_component → [renderers]) directly
        # from raw `renders` payloads of ALL index_payloads.  Two independent
        # reasons we do NOT build this from all_relations:
        #
        # 1. The write-layer filters Screen→Screen RENDERS out of all_relations
        #    (those edges are intentionally modelled by NAVIGATE instead).  But
        #    heuristic React role classification can mis-promote a reusable
        #    component to react_role="screen"; if its true parent is a genuine
        #    screen the RENDERS edge gets dropped and BFS loses the link, so
        #    navigate calls at that subtree get attributed to the wrong owner.
        #
        # 2. In incremental mode all_relations is built only from
        #    selected_payloads (changed files), so unchanged parent files
        #    contribute no render edges.  BFS must see the full JSX topology
        #    regardless of which files happened to change this run.
        #
        # Iterating index_payloads with no role filter satisfies both.
        reverse_renders_graph: Dict[str, List[str]] = {}
        for _bfs_ip in index_payloads:
            for _bfs_r in _bfs_ip.get("renders") or []:
                _bfs_r_src = _bfs_r.get("renderer_id", "")
                _bfs_r_name = _bfs_r.get("rendered_name", "")
                if not _bfs_r_src or not _bfs_r_name:
                    continue
                for _bfs_r_tgt in render_target_index.get(_bfs_r_name) or []:
                    _rr_list = reverse_renders_graph.setdefault(_bfs_r_tgt, [])
                    if _bfs_r_src not in _rr_list:
                        _rr_list.append(_bfs_r_src)

        # Augment reverse_call_graph with CALLS from unchanged files (incremental).
        # In full-build mode this is a no-op because all_calls already covers
        # every payload.  In incremental mode all_calls only covers the changed
        # subset; replay the rest so BFS sees hook/service call chains.
        _bfs_sel_paths: Set[str] = {
            (p.get("file_def") or {}).get("file_path", "")
            for p in selected_payloads
        }
        for _bfs_ip in index_payloads:
            _bfs_fp = (_bfs_ip.get("file_def") or {}).get("file_path", "")
            if _bfs_fp in _bfs_sel_paths:
                continue
            for _bfs_c in _bfs_ip.get("calls") or []:
                _bfs_cee = resolve_callee_id(_bfs_c)
                _bfs_cer = _bfs_c.get("caller_id") or ""
                if _bfs_cee and _bfs_cer:
                    _rc_list = reverse_call_graph.setdefault(_bfs_cee, [])
                    if _bfs_cer not in _rc_list:
                        _rc_list.append(_bfs_cer)
        # ─────────────────────────────────────────────────────────────────────────

        def _find_screen_owners(
            sid: str, max_depth: int = 6
        ) -> List[Tuple[str, int]]:
            """BFS up RENDERS then CALLS to find every screen on the ancestry chain.

            Ownership model:  a screen S "owns" a navigate call at node N iff
            there is a path N → … → S in the (RENDERS ∪ CALLS) reverse graph,
            where each edge means "is contained in" / "is invoked by".  Every
            such S is a legitimate workflow: while the user is on S they can
            trigger the UI at N which triggers the navigate.

            The BFS does NOT stop at the first screen ancestor — nested
            navigators (Tab / Stack / Drawer) legitimately place one screen
            inside another, so both the inner and outer screens own the
            navigate.  `call_depth` is the BFS distance from N to each owner;
            consumers use it to distinguish direct owners (small depth) from
            outer-layer workflows (large depth).

            The function also does NOT short-circuit when `sid` itself has
            react_role=="screen".  Heuristic classification (e.g. useNavigation
            hook + screen-dir path) can misclassify a reusable component as a
            screen; such a node still has a true outer screen owner reachable
            via BFS.  A genuinely top-level screen has no screen ancestors, so
            BFS returns [] and the caller falls back to direct attribution.
            """
            visited: Set[str] = {sid}
            queue: List[Tuple[str, int]] = [(sid, 0)]
            found: List[Tuple[str, int]] = []
            while queue:
                curr, depth = queue.pop(0)
                if depth >= max_depth:
                    continue
                parents: List[str] = list(reverse_renders_graph.get(curr, []))
                for _p in reverse_call_graph.get(curr, []):
                    if _p not in parents:
                        parents.append(_p)
                for parent in parents:
                    if parent in visited:
                        continue
                    visited.add(parent)
                    if func_role_map.get(parent) == "screen":
                        found.append((parent, depth + 1))
                    queue.append((parent, depth + 1))
            return found

        _ROLE_TO_VIA: Dict[str, str] = {
            "component": "component",
            "hook":      "hook",
            "":          "wrapped",
        }
        # Dedup set: avoid emitting duplicate (source_screen, target_screen) pairs
        _emitted_nav: Set[Tuple[str, str]] = set()

        for nav in all_raw_navigates:
            source_id = nav.get("source_id", "")
            target_name = nav.get("target_name", "")
            method = nav.get("nav_method", "navigate")
            trigger_type = nav.get("trigger_type", "user") or "user"
            guard = nav.get("guard") or ""

            # ── Phase 3: Target resolution (4-tier) ──────────────────────────
            target_id: Optional[str] = None
            target_confidence: float = 0.0

            # Tier 1: exact screen/component name match
            t1 = nav_screen_index.get(target_name) or []
            if t1:
                target_id = t1[0]
                target_confidence = 1.0 if len(t1) == 1 else 0.7

            # Tier 2: route config map (Stack.Screen name → component_name → screen)
            if not target_id:
                comp_name = route_config_map.get(target_name)
                if comp_name:
                    t2 = nav_screen_index.get(comp_name) or []
                    if t2:
                        target_id = t2[0]
                        target_confidence = 0.9 if len(t2) == 1 else 0.65

            # Tier 3: Expo Router / Next.js route path match
            if not target_id and "/" in target_name:
                normalized = target_name.rstrip("/")
                t3 = (
                    nav_route_index.get(normalized)
                    or nav_route_index.get(normalized.lstrip("/"))
                    or []
                )
                if t3:
                    target_id = t3[0]
                    target_confidence = 0.85 if len(t3) == 1 else 0.6

            # Tier 4: last-resort screen-only name match (avoids matching generic
            # components — e.g. "Modal" — which would create false NAVIGATE edges).
            # We intentionally do NOT fall back to render_target_index here.
            if not target_id:
                t4 = nav_screen_index.get(target_name) or []
                if t4:
                    target_id = t4[0]
                    target_confidence = 0.5 if len(t4) == 1 else 0.3

            if not target_id:
                continue  # unresolvable → skip

            # ── Phase 4: Source attribution → emit one edge per screen owner ──
            # Always BFS first regardless of source_role: heuristic classification
            # can mis-promote a reusable component (useNavigation + screen-dir)
            # to react_role="screen".  If BFS returns any screen ancestors we use
            # those; only when there are none AND the source itself is a screen
            # do we fall back to direct self-attribution.
            source_role = func_role_map.get(source_id, "")
            screen_owners = _find_screen_owners(source_id)
            if not screen_owners:
                if source_role == "screen":
                    screen_owners = [(source_id, 0)]
                else:
                    continue  # unattributable component/hook — skip

            # No owner-count penalty: having multiple owners is the EXPECTED
            # shape of nested navigators (Tab inside Stack inside Drawer, etc.),
            # not an ambiguity signal.  Confidence drops with call_depth only,
            # which correctly captures "how direct is this workflow".  No cap
            # on owner list: every screen in the ancestry chain is a valid
            # workflow and consumers can filter by confidence if desired.
            for screen_id, call_depth in screen_owners:
                pair = (screen_id, target_id)
                if pair in _emitted_nav:
                    continue
                _emitted_nav.add(pair)
                edge_via = (
                    "direct" if call_depth == 0
                    else _ROLE_TO_VIA.get(source_role, "wrapped")
                )
                call_path_score = max(0.5, 1.0 - 0.15 * call_depth)
                confidence = round(target_confidence * call_path_score, 3)
                all_relations.append({
                    "source_id": screen_id,
                    "target_id": target_id,
                    "rel_type": "NAVIGATE",
                    "properties": {
                        "method": method,
                        "target": target_name,
                        "via": edge_via,
                        "trigger_type": trigger_type,
                        "guard": guard,
                        "call_depth": call_depth,
                        "confidence": confidence,
                    },
                })
        # ── End Navigation Intelligence V2.0 resolution ──────────────────────

        # ── Build Navigator write-rows (post-loop, nav_screen_index is complete) ──
        _param_list_by_name: Dict[str, Dict[str, Any]] = {}
        for _pl in all_param_lists:
            _param_list_by_name[_pl["name"]] = _pl

        nav_write_rows: List[Dict[str, Any]] = []
        for _nav in all_navigators:
            nav_write_rows.append({
                "id": _nav["symbol_id"],
                "var_name": _nav["var_name"],
                "factory": _nav["factory"],
                "nav_type": _nav["nav_type"],
                "param_list_ref": _nav.get("param_list_ref", ""),
                "file_path": _nav["file_path"],
                "start_line": _nav.get("start_line", 0),
                "project_id": project_id,
                "project_name": project_name,
            })

        has_routes_rows: List[Dict[str, Any]] = []
        for _nav in all_navigators:
            _pl = _param_list_by_name.get(_nav.get("param_list_ref", ""), {})
            _pl_routes: Dict[str, str] = _pl.get("routes", {}) if _pl else {}
            for _route_name, _comp_name in _nav.get("routes") or []:
                _screen_ids = nav_screen_index.get(_comp_name) or []
                for _screen_id in _screen_ids:
                    has_routes_rows.append({
                        "navigator_id": _nav["symbol_id"],
                        "screen_id": _screen_id,
                        "route_name": _route_name,
                        "param_schema": _pl_routes.get(_route_name, ""),
                    })

        param_list_write_rows: List[Dict[str, Any]] = []
        for _pl in all_param_lists:
            for _route_name, _type_str in (_pl.get("routes") or {}).items():
                param_list_write_rows.append({
                    "symbol_id": _pl["symbol_id"],
                    "name": _pl["name"],
                    "file_path": _pl["file_path"],
                    "route_name": _route_name,
                    "type_str": _type_str,
                    "project_id": project_id,
                })
        # ────────────────────────────────────────────────────────────────────

        await code_writer.write_all(            projects=all_projects,
            namespaces=all_namespaces or None,
            files=all_files or None,
            types=all_types or None,
            functions=all_functions or None,
            navigators=nav_write_rows or None,
            has_routes=has_routes_rows or None,
            param_lists=param_list_write_rows or None,
            relations=all_relations or None,
            calls=all_calls or None,
            use_full_writers=True,
            files_variant="with_jsx",
        )
        if verbose:
            print("[graph] Write complete")

        # ── Write ApiCall nodes + CALLS_API edges (FE→BE bridge layer) ───────
        all_api_calls: List[Dict[str, Any]] = []
        for _payload in selected_payloads:
            for ac in _payload.get("api_calls") or []:
                all_api_calls.append({**ac, "project_id": project_id, "project_name": project_name})
        if all_api_calls:
            await _write_api_calls(
                code_writer.driver,
                all_api_calls,
                database=code_writer.database,
                batch_size=neo4j_batch_size,
                verbose=verbose,
            )
            if verbose:
                print(f"[graph] ApiCall nodes written: {len(all_api_calls)}")

    if qdrant_writer and embedder:
        if verbose:
            print("[qdrant] Ensuring collection...")
        qdrant_writer.ensure_collection()
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", qdrant_writer.collection)
        points_path = os.path.join(qdrant_cache_root, f"{safe_name}_points.jsonl")
        state_path = os.path.join(qdrant_cache_root, f"{safe_name}_state.json")

        def read_qdrant_state() -> Dict[str, int]:
            if not os.path.exists(state_path):
                return {}
            with open(state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)

        def write_qdrant_state(state: Dict[str, int]) -> None:
            temp_path = f"{state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(temp_path, state_path)

        state = read_qdrant_state()
        cached_points = state.get("total_points")
        if not os.path.exists(points_path) or cached_points != expected_points:
            if verbose:
                print(f"[cache] Building Qdrant cache at {points_path}")
            with open(points_path, "w", encoding="utf-8") as handle:
                batch_funcs: List[Dict[str, Any]] = []
                batch_index = 0
                total_batches = max(1, (expected_points + batch_size - 1) // batch_size)
                for payload in selected_payloads:
                    for func in payload["functions"]:
                        batch_funcs.append(func)
                        if len(batch_funcs) < batch_size:
                            continue
                        batch_index += 1
                        if verbose:
                            print(f"[embed] batch {batch_index} / {total_batches}")
                        texts = [item["note"] or item["code"] for item in batch_funcs]
                        vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                        for func_item, vector in zip(batch_funcs, vectors):
                            point = {
                                "id": _stable_point_id(func_item["symbol_id"]),
                                "vector": vector,
                                "payload": _func_qdrant_payload(
                                    func_item, project_id, project_name, language, repo, build_system
                                ),
                            }
                            handle.write(json.dumps(point, ensure_ascii=True) + "\n")
                        batch_funcs.clear()
                if batch_funcs:
                    batch_index += 1
                    if verbose:
                        print(f"[embed] batch {batch_index} / {total_batches}")
                    texts = [item["note"] or item["code"] for item in batch_funcs]
                    vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                    for func_item, vector in zip(batch_funcs, vectors):
                        point = {
                            "id": _stable_point_id(func_item["symbol_id"]),
                            "vector": vector,
                            "payload": _func_qdrant_payload(
                                func_item, project_id, project_name, language, repo, build_system
                            ),
                        }
                        handle.write(json.dumps(point, ensure_ascii=True) + "\n")
            state = {"total_points": expected_points, "upserted": 0}
            write_qdrant_state(state)
        else:
            if verbose:
                print(f"[cache] Using existing Qdrant cache at {points_path}")

        total_points = state.get("total_points", expected_points)
        upserted = state.get("upserted", 0)
        if verbose:
            print(f"[qdrant] Resuming at {upserted}/{total_points}")
        remaining = max(total_points - upserted, 0)
        total_batches = max(1, (remaining + qdrant_batch_size - 1) // qdrant_batch_size)
        batch_index = 0
        line_index = 0
        batch: List[Dict] = []
        with open(points_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line_index < upserted:
                    line_index += 1
                    continue
                batch.append(json.loads(line))
                line_index += 1
                if len(batch) >= qdrant_batch_size:
                    batch_index += 1
                    if verbose:
                        print(f"[qdrant] Upsert batch {batch_index}/{total_batches}")
                    qdrant_writer.upsert(batch)
                    write_qdrant_state({"total_points": total_points, "upserted": line_index})
                    batch = []
            if batch:
                batch_index += 1
                if verbose:
                    print(f"[qdrant] Upsert batch {batch_index}/{total_batches}")
                qdrant_writer.upsert(batch)
                write_qdrant_state({"total_points": total_points, "upserted": line_index})
        if verbose:
            print("[qdrant] Upsert complete")
        if not keep_cache:
            try:
                os.remove(points_path)
                os.remove(state_path)
            except OSError:
                pass
    _sr_fn = sum(len(p.get("functions") or []) for p in selected_payloads)
    _sr_cls = sum(len(p.get("classes") or []) for p in selected_payloads)
    print(f"[SCAN_RESULT] parser={language} files={len(selected_payloads)} functions={_sr_fn} classes={_sr_cls}", flush=True)
    if verbose:
        elapsed = time.time() - start_time
        print(f"[done] Total time: {elapsed:.2f}s")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TypeScript call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing TypeScript sources")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION", "typescript_functions"),
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", 4000)))
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", 4)))  # for embedding - 4 function 1 turn embedding
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128)  # for qdrant upsert - 128 vectors 1 time upsert
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.set_defaults(enable_message_scan=True)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true", help="Enable message scan and sync (default)")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false", help="Disable message scan and sync")
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--keep-cache", action="store_true")
    parser.add_argument("--disable-parse-cache", action="store_true")
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore local caches for this run (parse cache, Neo4j/Qdrant resume state).",
    )
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true", help="Enable incremental ingestion mode")
    parser.add_argument(
        "--changed-files-manifest",
        help="JSON/TXT manifest of changed+impacted file paths (relative to --root)",
    )
    parser.add_argument(
        "--deleted-files-manifest",
        help="JSON/TXT manifest of deleted file paths (relative to --root)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _resolve_embed_device(requested: str) -> str:
    if requested and requested.lower() not in {"auto", ""}:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend and mps_backend.is_available():
        return "mps"
    return "cpu"


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    code_writer = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        driver = await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
        )
        code_writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )

    qdrant_writer = None
    embedder = None
    if args.qdrant_url:
        args.device = _resolve_embed_device(args.device)
        if args.verbose:
            print(f"[embed] device: {args.device}")
        embedder = CodeEmbedder(
            args.embed_model,
            args.device,
            args.max_embed_chars,
            args.chunk_embed,
            fallback_cache_base_dir=args.cache_dir,
            project_root=args.root,
            verbose=args.verbose,
        )
        qdrant_writer = QdrantWriter(
            args.qdrant_url,
            args.qdrant_collection,
            vector_size=embedder.vector_size,
            timeout=args.qdrant_timeout,
            retries=args.qdrant_retries,
            retry_sleep=args.qdrant_retry_sleep,
        )

    parse_cache = not args.disable_parse_cache
    effective_cache_dir = args.cache_dir
    if args.ignore_cache:
        run_cache_root = safe_cache_root(effective_cache_dir, "ts_analyzer", project_root=args.root)
        effective_cache_dir = os.path.join(
            run_cache_root,
            "ignore_runs",
            f"run_{int(time.time() * 1000)}",
        )
        os.makedirs(effective_cache_dir, exist_ok=True)
        parse_cache = False
        args.disable_neo4j_resume = True
        args.keep_cache = False
        if args.verbose:
            print(
                "[cache] ignore-cache enabled; using isolated cache dir: %s"
                % effective_cache_dir
            )
    changed_manifest_files: List[str] = []
    deleted_manifest_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_manifest_files = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_manifest_files = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        if args.verbose:
            print(
                "[diff] incremental manifests changed=%d deleted=%d"
                % (len(changed_manifest_files), len(deleted_manifest_files))
            )
    neo4j_state_path = None
    if not args.disable_neo4j_resume and not args.incremental:
        cache_root = safe_cache_root(effective_cache_dir, "ts_analyzer", project_root=args.root)
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    elif args.incremental and args.verbose:
        print("[state] incremental mode disables neo4j resume state")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "typescript"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    commit_sha = args.commit_sha_after or ""
    commit_sha_before = args.commit_sha_before or ""
    message_qdrant_collection = (
        args.message_qdrant_collection
        or default_message_collection_name(args.qdrant_collection)
    )
    if code_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            await write_cloc_stats_to_neo4j(
                driver=code_writer.driver,
                database=code_writer.database,
                project_id=project_id,
                project_name=project_name,
                root=args.root,
                repo=repo,
                language=language,
                stats=cloc_stats,
            )
            if args.verbose:
                print("[cloc] Stats stored in Neo4j")
        elif args.verbose:
            print("[cloc] Skipped (cloc not available or failed)")

    try:
        if args.dry_run:
            files = _scan_ts_files(args.root)
            if args.incremental and changed_manifest_files:
                manifest_set = set(changed_manifest_files)
                files = [
                    file_path
                    for file_path in files
                    if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
                ]
                print(
                    "Dry run (incremental): %d TypeScript files selected (manifest=%d)"
                    % (len(files), len(changed_manifest_files))
                )
            else:
                print(f"Dry run: {len(files)} TypeScript files found")
            return 0
        await build_call_graph(
            args.root,
            code_writer=code_writer,
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=effective_cache_dir,
            keep_cache=args.keep_cache,
            parse_cache=parse_cache,
            neo4j_batch_size=args.neo4j_batch_size,
            neo4j_state_path=neo4j_state_path,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            verbose=args.verbose,
            incremental=args.incremental,
            changed_files=changed_manifest_files,
            deleted_files=deleted_manifest_files,
            commit_sha=commit_sha,
            commit_sha_before=commit_sha_before,
        )
        if args.enable_message_scan:
            message_summary = await run_message_scan_pipeline(
                root=args.root,
                parser="ts",
                project_id=project_id,
                project_name=project_name,
                language=language,
                repo=repo,
                build_system=build_system,
                incremental=args.incremental,
                changed_files=changed_manifest_files,
                deleted_files=deleted_manifest_files,
                driver=driver,
                neo4j_database=args.neo4j_db,
                qdrant_url=args.qdrant_url,
                qdrant_collection=message_qdrant_collection if args.qdrant_url else None,
                qdrant_vector_size=embedder.vector_size if embedder else 1024,
                embed_texts=embedder.embed if embedder else None,
                output_dir=args.message_output_dir,
                cache_dir=effective_cache_dir,
                commit_sha_before=commit_sha_before,
                commit_sha_after=commit_sha,
                qdrant_batch_size=args.qdrant_batch_size,
                qdrant_timeout=args.qdrant_timeout,
                qdrant_retries=args.qdrant_retries,
                qdrant_retry_sleep=args.qdrant_retry_sleep,
                verbose=args.verbose,
            )
            print(
                "[message] parser=%s count=%s neo4j=%s qdrant=%s collection=%s artifact=%s"
                % (
                    message_summary.get("parser"),
                    message_summary.get("message_count"),
                    message_summary.get("neo4j_upserted"),
                    message_summary.get("qdrant_upserted"),
                    message_summary.get("qdrant_collection"),
                    message_summary.get("artifact_path"),
                )
            )
    finally:
        if driver:
            close_result = driver.close()
            if hasattr(close_result, "__await__"):
                await close_result
    return 0


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(asyncio.run(main()))
