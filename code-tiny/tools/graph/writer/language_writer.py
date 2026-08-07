"""
Language Code Writer

Unified writer for all language analyzers with state management and batching.
Replaces the duplicated Neo4jWriter classes across all analyzer files.
"""

import asyncio
import os
import time
from typing import Any, Callable, Dict, List, Optional

from tools.graph.core.base import GraphDriver
from tools.graph.operations.package_ops import PackageNodeOperations
from tools.graph.operations.class_ops import ClassNodeOperations
from tools.graph.operations.namespace_ops import NamespaceNodeOperations
from tools.graph.operations.type_ops import TypeNodeOperations
from tools.graph.operations.function_ops import FunctionNodeOperations
from tools.graph.writer.query_contract import (
    compile_relationship_upsert,
    group_typed_relations,
)
from tools.graph.journal.identity import canonical_json
from tools.graph.journal.runtime import GraphWriteJournalRuntime, JournalTicket
from tools.graph.journal.reconcile import (
    compile_reconciliation_readback,
    readback_count,
)
from tools.graph.journal.guard import journaled_mutation
from tools.graph.journal.operation import GraphWriteOperation, operation_for_custom_query
from tools.graph.journal.models import BatchStatus

_OPTIONAL_EXTERNAL_RELATION_TYPES = frozenset(
    {"EXTENDS", "IMPLEMENTS", "INHERITS_FROM", "MIXES_IN"}
)

class LanguageCodeWriter:
    """
    Unified code writer for all programming languages
    
    Provides stateful batch writing with resume capability.
    Replaces language-specific Neo4jWriter implementations.
    """
    
    def __init__(
        self,
        driver: GraphDriver,
        database: Optional[str] = None,
        batch_size: int = 1000,
        verbose: bool = False,
    ):
        """
        Initialize language code writer
        
        Args:
            driver: Graph driver instance
            database: Optional database name
            batch_size: Batch size for bulk operations
            verbose: Enable verbose logging
        """
        self.driver = driver
        self.database = database
        self.batch_size = batch_size
        self.verbose = verbose
        self._schema_ready = False
        self._progress_heartbeat_seconds = 10.0
        self._reconciliation_timeout_seconds = float(
            os.getenv("GRAPH_WRITE_RECONCILE_TIMEOUT_SECONDS", "30")
        )
        self._journal_config = getattr(driver, "journal_config", None)
        self._journal_runtime = (
            GraphWriteJournalRuntime(self._journal_config)
            if self._journal_config is not None and self._journal_config.required
            else None
        )
        self._deferred_journal_writes: list[
            tuple[JournalTicket, Callable[[List[Dict[str, Any]]], Any]]
        ] = []
        
        # Initialize operations
        self.package_ops = PackageNodeOperations()
        self.class_ops = ClassNodeOperations()
        self.namespace_ops = NamespaceNodeOperations()
        self.type_ops = TypeNodeOperations()
        self.function_ops = FunctionNodeOperations()
    
    def _provider_name(self) -> str:
        provider = getattr(self.driver, "provider", "graph")
        return str(getattr(provider, "value", provider))

    def _emit_progress(self, event: str, label: str, **fields: Any) -> None:
        """Emit one visible, flushed progress event for verbose CLI runs."""

        if not self.verbose:
            return
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        suffix = f" {details}" if details else ""
        print(
            f"[{self._provider_name()}] {label} {event}{suffix}",
            flush=True,
        )

    async def ensure_schema(self) -> Any:
        """Enforce the production-driver schema invariant once per writer."""

        if self._schema_ready:
            return None
        ensure = getattr(self.driver, "ensure_schema", None)
        if callable(ensure):
            with journaled_mutation():
                result = await ensure(database=self.database)
            self._schema_ready = True
            return result
        # Recording drivers in isolated unit tests intentionally expose only
        # execute_query. Real GraphDriver implementations always provide the
        # inherited ensure_schema method.
        self._schema_ready = True
        return None
    
    async def write_batches(
        self,
        label: str,
        rows: List[Dict[str, Any]],
        write_fn: Callable,
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
        operation: GraphWriteOperation | None = None,
    ) -> int:
        """
        Write data in batches with state tracking
        
        Args:
            label: Label for logging
            rows: Data rows to write
            write_fn: Async function to write a batch
            state: State dict for resume capability
            state_writer: Function to persist state
            
        Returns:
            Number of items written
        """
        # Durable job identity supersedes legacy memory/file offsets. Required
        # mode must reconstruct every batch so DONE jobs are verified in the
        # journal instead of silently trusting an older analyzer state file.
        start_index = (
            0
            if self._journal_runtime is not None
            else state.get(label, 0) if state else 0
        )
        total = len(rows)
        
        if start_index >= total:
            self._emit_progress(
                "batch_skipped",
                label,
                completed=total,
                total=total,
                reason="already_completed",
            )
            return 0
        
        written = 0
        await self.ensure_schema()
        for offset in range(start_index, total, self.batch_size):
            batch = rows[offset : offset + self.batch_size]

            if self._journal_config is not None and self._journal_config.shadow:
                # Shadow mode exercises the exact serialization boundary but
                # deliberately leaves mutation execution and state unchanged.
                for row in batch:
                    canonical_json(row)

            ticket: JournalTicket | None = None
            if self._journal_runtime is not None:
                ticket = self._journal_runtime.prepare(
                    label=label,
                    rows=batch,
                    sequence=offset,
                    operation=operation,
                )
                if ticket.reconcile:
                    readback = compile_reconciliation_readback(
                        ticket.operation,
                        ticket.rows,
                        job_id=ticket.batch.job_id,
                    )
                    applied: bool | None = None
                    if readback is not None:
                        query, parameters = readback
                        records, _, _ = await self.driver.execute_query(
                            query, parameters, self.database
                        )
                        applied = readback_count(records) == 1
                    ticket = self._journal_runtime.resolve_reconciliation(
                        ticket, applied=applied
                    )
                if not ticket.execute:
                    next_index = offset + len(batch)
                    written += ticket.batch.expected_count
                    if state is not None:
                        state[label] = next_index
                        if state_writer:
                            state_writer(state)
                    self._emit_progress(
                        "batch_skipped",
                        label,
                        offset=offset,
                        size=len(batch),
                        completed=next_index,
                        total=total,
                        reason="journal_done",
                    )
                    continue

            started = time.monotonic()
            self._emit_progress(
                "batch_started",
                label,
                offset=offset,
                size=len(batch),
                completed=offset,
                total=total,
            )
            with journaled_mutation(
                ticket.batch.job_id if ticket is not None else None,
                ticket.operation.operation_key if ticket is not None else "schema",
            ):
                write_task = asyncio.create_task(write_fn(batch))
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {write_task}, timeout=self._progress_heartbeat_seconds
                    )
                    if done:
                        count = write_task.result()
                        break
                    if ticket is not None:
                        self._journal_runtime.renew(ticket)
                    self._emit_progress(
                        "query_running",
                        label,
                        offset=offset,
                        size=len(batch),
                        completed=offset,
                        total=total,
                        elapsed=f"{time.monotonic() - started:.1f}s",
                    )
                if ticket is not None:
                    self._journal_runtime.acknowledge(
                        ticket,
                        int(count),
                        int((time.monotonic() - started) * 1000),
                    )
                written += count

                # Persist only after the database call has returned successfully.
                next_index = offset + len(batch)
                if state is not None:
                    state[label] = next_index
                    if state_writer:
                        state_writer(state)
            except BaseException as exc:
                reconciled = False
                if not write_task.done():
                    self._emit_progress(
                        "batch_reconciling",
                        label,
                        offset=offset,
                        size=len(batch),
                        reason=type(exc).__name__,
                    )
                    reconcile_deadline = (
                        time.monotonic() + self._reconciliation_timeout_seconds
                    )
                    while not write_task.done():
                        remaining = reconcile_deadline - time.monotonic()
                        if remaining <= 0:
                            self._emit_progress(
                                "batch_reconcile_ambiguous",
                                label,
                                offset=offset,
                                size=len(batch),
                                elapsed=f"{time.monotonic() - started:.3f}s",
                            )
                            break
                        done, _ = await asyncio.wait(
                            {write_task},
                            timeout=min(self._progress_heartbeat_seconds, remaining),
                        )
                        if not done:
                            if ticket is not None:
                                self._journal_runtime.renew(ticket)
                            self._emit_progress(
                                "batch_reconcile_running",
                                label,
                                offset=offset,
                                size=len(batch),
                                elapsed=f"{time.monotonic() - started:.1f}s",
                            )
                    if write_task.done():
                        try:
                            reconciled_count = int(write_task.result())
                            if ticket is not None:
                                self._journal_runtime.acknowledge(
                                    ticket,
                                    reconciled_count,
                                    int((time.monotonic() - started) * 1000),
                                )
                            reconciled = True
                            self._emit_progress(
                                "batch_reconciled",
                                label,
                                offset=offset,
                                size=len(batch),
                                outcome="completed",
                            )
                        except BaseException as reconcile_exc:
                            self._emit_progress(
                                "batch_reconciled",
                                label,
                                offset=offset,
                                size=len(batch),
                                outcome=type(reconcile_exc).__name__,
                            )
                if ticket is not None and not reconciled:
                    self._journal_runtime.mark_ambiguous(ticket)
                self._emit_progress(
                    "batch_failed",
                    label,
                    offset=offset,
                    size=len(batch),
                    completed=offset,
                    total=total,
                    elapsed=f"{time.monotonic() - started:.3f}s",
                    error=type(exc).__name__,
                )
                raise

            self._emit_progress(
                "batch_finished",
                label,
                offset=offset,
                size=len(batch),
                completed=next_index,
                total=total,
                matched=count,
                elapsed=f"{time.monotonic() - started:.3f}s",
            )
        
        return written

    def close_journal(self) -> None:
        """Release local journal handles; production is closed by the parent gate."""

        if self._journal_runtime is not None:
            self._journal_runtime.close()
            self._journal_runtime = None
    
    async def write_packages(
        self,
        packages: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write package nodes in batches"""
        if not packages:
            return 0
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            return await self.package_ops.batch_create_packages(
                self.driver,
                batch,
                self.database
            )
        
        return await self.write_batches(
            "packages",
            packages,
            write_batch,
            state,
            state_writer
        )
    
    async def write_namespaces(
        self,
        namespaces: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write namespace nodes in batches"""
        if not namespaces:
            return 0
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            return await self.namespace_ops.batch_create_namespaces(
                self.driver,
                batch,
                self.database
            )
        
        return await self.write_batches(
            "namespaces",
            namespaces,
            write_batch,
            state,
            state_writer
        )
    
    async def write_files(
        self,
        files: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write file nodes in batches"""
        if not files:
            return 0
        for row in files:
            row.setdefault("node_type", "code")
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            """
            records, _, _ = await self.driver.execute_query(
                query,
                {"rows": batch},
                self.database
            )
            return records[0]["count"] if records else 0
        
        return await self.write_batches(
            "files",
            files,
            write_batch,
            state,
            state_writer
        )

    async def write_repo_file_edges(
        self,
        files: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Create (Repository)-[:HAS_FILE]->(File) edges for every file that
        carries a ``repo`` field matching a Repository node's ``name``.

        This must run AFTER write_files so the File nodes already exist.
        The Repository nodes are created by setup_graph_project.py before
        the analyzer runs, so they are guaranteed to be present.
        """
        rows = [f for f in (files or []) if f.get("repo")]
        if not rows:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MATCH (r:Repository {name: row.repo})
            MATCH (f:File {id: row.id})
            MERGE (r)-[:HAS_FILE]->(f)
            RETURN count(f) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "repo_file_edges",
            rows,
            write_batch,
            state,
            state_writer,
        )

    async def write_classes(
        self,
        classes: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write class nodes in batches"""
        if not classes:
            return 0
        for row in classes:
            row.setdefault("node_type", "code")
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            return await self.class_ops.batch_create_classes(
                self.driver,
                batch,
                self.database
            )
        
        return await self.write_batches(
            "classes",
            classes,
            write_batch,
            state,
            state_writer
        )
    
    async def write_types(
        self,
        types: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write type nodes in batches"""
        if not types:
            return 0
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            return await self.type_ops.batch_create_types(
                self.driver,
                batch,
                self.database
            )
        
        return await self.write_batches(
            "types",
            types,
            write_batch,
            state,
            state_writer
        )
    
    async def write_functions(
        self,
        functions: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write function nodes in batches"""
        if not functions:
            return 0
        for row in functions:
            row.setdefault("node_type", "code")
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            return await self.function_ops.batch_create_functions(
                self.driver,
                batch,
                self.database
            )
        
        return await self.write_batches(
            "functions",
            functions,
            write_batch,
            state,
            state_writer
        )
    
    async def write_relations(
        self,
        relations: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write generic relationships through the safe typed contract."""

        return await self.write_relations_typed(relations, state, state_writer)
    
    async def write_calls(
        self,
        calls: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write function call relationships in batches"""
        if not calls:
            return 0

        # Collapse duplicate observations before journaling. Replays then set
        # the same absolute count instead of incrementing an existing edge.
        aggregated: Dict[tuple[Any, ...], Dict[str, Any]] = {}
        for call in calls:
            key = (
                call.get("caller_id"),
                call.get("callee_id"),
                call.get("project_id"),
                call.get("project_id_normalized"),
            )
            existing = aggregated.get(key)
            if existing is None:
                existing = dict(call)
                existing["count"] = int(call.get("count") or 1)
                aggregated[key] = existing
            else:
                existing["count"] += int(call.get("count") or 1)
                existing["call_type"] = min(
                    str(existing.get("call_type") or ""),
                    str(call.get("call_type") or ""),
                )
        replay_safe_calls = list(aggregated.values())

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            # ``enrich_project_scope`` (via the upstream pipeline) adds
            # ``project_id_normalized`` to each row. Per Phase 03 of the
            # unified ingest/query contract plan, CALLS edges now also carry
            # ``project_id`` and ``project_id_normalized`` so the standard
            # ``n.project_id_normalized = $project_id_normalized`` predicate
            # returns CALLS edges alongside the nodes they connect.
            query = """
            UNWIND $rows AS row
            MATCH (caller:Function {id: row.caller_id})
            MATCH (callee:Function {id: row.callee_id})
            MERGE (caller)-[r:CALLS]->(callee)
            SET r.count                = row.count,
                r.call_type            = row.call_type,
                r.project_id           = row.project_id,
                r.project_id_normalized = row.project_id_normalized,
                r.updated_at           = datetime()
            RETURN count(r) AS count
            """

            records, _, _ = await self.driver.execute_query(
                query,
                {"rows": batch},
                self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "calls",
            replay_safe_calls,
            write_batch,
            state,
            state_writer
        )
    
    async def write_function_types(
        self,
        function_types: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write C++ function-type nodes (typedef/using for function signatures) in batches"""
        if not function_types:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (ft:FunctionType {id: row.id})
            SET ft.type_signature        = row.type_signature,
                ft.file_path             = row.file_path,
                ft.start_line            = row.start_line,
                ft.end_line              = row.end_line,
                ft.code                  = row.code,
                ft.project_id            = row.project_id,
                ft.project_id_normalized = row.project_id_normalized,
                ft.project_name          = row.project_name,
                ft.language              = row.language,
                ft.repo                  = row.repo,
                ft.build_system          = row.build_system
            RETURN count(ft) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "function_types", function_types, write_batch, state, state_writer
        )

    async def write_fields(
        self,
        fields: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write C++ field/member-variable nodes in batches"""
        if not fields:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:Field {id: row.id})
            SET f.name                  = row.name,
                f.qualified_name        = row.qualified_name,
                f.scope_name            = row.scope_name,
                f.type_signature        = row.type_signature,
                f.file_path             = row.file_path,
                f.start_line            = row.start_line,
                f.end_line              = row.end_line,
                f.code                  = row.code,
                f.project_id            = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name          = row.project_name,
                f.language              = row.language,
                f.repo                  = row.repo,
                f.build_system          = row.build_system
            RETURN count(f) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "fields", fields, write_batch, state, state_writer
        )

    async def write_aliases(
        self,
        aliases: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write C++ typedef/using alias nodes in batches"""
        if not aliases:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (a:Alias {id: row.id})
            SET a.name                  = row.name,
                a.qualified_name        = row.qualified_name,
                a.kind                  = row.kind,
                a.target_name           = row.target_name,
                a.file_path             = row.file_path,
                a.start_line            = row.start_line,
                a.end_line              = row.end_line,
                a.code                  = row.code,
                a.project_id            = row.project_id,
                a.project_id_normalized = row.project_id_normalized,
                a.project_name          = row.project_name,
                a.language              = row.language,
                a.repo                  = row.repo,
                a.build_system          = row.build_system
            RETURN count(a) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "aliases", aliases, write_batch, state, state_writer
        )

    async def write_templates(
        self,
        templates: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write C++ template nodes in batches"""
        if not templates:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (t:Template {id: row.id})
            SET t.name                  = row.name,
                t.file_path             = row.file_path,
                t.start_line            = row.start_line,
                t.end_line              = row.end_line,
                t.code                  = row.code,
                t.project_id            = row.project_id,
                t.project_id_normalized = row.project_id_normalized,
                t.project_name          = row.project_name,
                t.language              = row.language,
                t.repo                  = row.repo,
                t.build_system          = row.build_system
            RETURN count(t) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "templates", templates, write_batch, state, state_writer
        )

    async def write_projects(
        self,
        projects: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write project nodes in batches"""
        if not projects:
            return 0
        for row in projects:
            row.setdefault("node_type", "code")

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (p:Project {project_id: row.id})
            SET p.name = row.name,
                p.node_type = 'code',
                p.language = row.language,
                p.repo = row.repo,
                p.root = row.root,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("projects", projects, write_batch, state, state_writer)

    async def write_packages_full(
        self,
        packages: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write package nodes with full project metadata in batches"""
        if not packages:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (p:Package {id: row.id})
            SET p.name = row.name,
                p.start_line = row.start_line,
                p.end_line = row.end_line,
                p.code = row.code,
                p.comment = row.comment,
                p.summary = row.summary,
                p.note = row.note,
                p.project_id = row.project_id,
                p.project_id_normalized = row.project_id_normalized,
                p.project_name = row.project_name,
                p.language = row.language,
                p.repo = row.repo,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("packages", packages, write_batch, state, state_writer)

    async def write_namespaces_full(
        self,
        namespaces: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write namespace nodes with full project metadata in batches"""
        if not namespaces:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (n:Namespace {id: row.id})
            SET n.name = row.name,
                n.qualified_name = row.qualified_name,
                n.file_path = row.file_path,
                n.start_line = row.start_line,
                n.end_line = row.end_line,
                n.code = row.code,
                n.comment = row.comment,
                n.summary = row.summary,
                n.note = row.note,
                n.project_id = row.project_id,
                n.project_id_normalized = row.project_id_normalized,
                n.project_name = row.project_name,
                n.language = row.language,
                n.repo = row.repo,
                n.build_system = row.build_system,
                n.updated_at = datetime()
            RETURN count(n) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("namespaces", namespaces, write_batch, state, state_writer)

    async def write_files_with_imports(
        self,
        files: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write file nodes with imports/exports/jsx fields (for JS/TS/PHP/Python)"""
        if not files:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.imports = row.imports,
                f.exports = row.exports,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("files", files, write_batch, state, state_writer)

    async def write_files_jsx(
        self,
        files: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write file nodes with imports/exports/jsx fields (for JS/TS/PHP with JSX)"""
        if not files:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.imports = row.imports,
                f.exports = row.exports,
                f.jsx_tags = row.jsx_tags,
                f.jsx_components = row.jsx_components,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("files", files, write_batch, state, state_writer)

    async def write_files_with_package(
        self,
        files: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write file nodes with package_name field (for Java/Kotlin/Android)"""
        if not files:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:File {id: row.id})
            SET f.path = row.path,
                f.node_type = 'code',
                f.package_name = row.package_name,
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("files", files, write_batch, state, state_writer)

    async def write_classes_full(
        self,
        classes: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write class nodes with full project metadata in batches"""
        if not classes:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (c:Class {id: row.id})
            SET c.name = row.name,
                c.node_type = 'code',
                c.qualified_name = row.qualified_name,
                c.kind = row.kind,
                c.package_name = row.package_name,
                c.file_path = row.file_path,
                c.start_line = row.start_line,
                c.end_line = row.end_line,
                c.code = row.code,
                c.comment = row.comment,
                c.summary = row.summary,
                c.note = row.note,
                c.visibility = coalesce(row.visibility, 'unknown'),
                c.is_public_api = coalesce(row.is_public_api, false),
                c.visibility_source = coalesce(row.visibility_source, ''),
                c.export_evidence = coalesce(row.export_evidence, ''),
                c.signature = coalesce(row.signature, ''),
                c.project_id = row.project_id,
                c.project_id_normalized = row.project_id_normalized,
                c.project_name = row.project_name,
                c.language = row.language,
                c.repo = row.repo,
                c.build_system = row.build_system,
                c.updated_at = datetime()
            RETURN count(c) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("classes", classes, write_batch, state, state_writer)

    async def write_types_full(
        self,
        types: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write type nodes with full project metadata in batches"""
        if not types:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (t:Type {id: row.id})
            SET t.name = row.name,
                t.qualified_name = row.qualified_name,
                t.kind = row.kind,
                t.file_path = row.file_path,
                t.start_line = row.start_line,
                t.end_line = row.end_line,
                t.code = row.code,
                t.comment = row.comment,
                t.summary = row.summary,
                t.note = row.note,
                t.exported = coalesce(row.exported, false),
                t.project_id = row.project_id,
                t.project_id_normalized = row.project_id_normalized,
                t.project_name = row.project_name,
                t.language = row.language,
                t.repo = row.repo,
                t.build_system = row.build_system,
                t.updated_at = datetime()
            RETURN count(t) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("types", types, write_batch, state, state_writer)

    async def write_functions_full(
        self,
        functions: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write function nodes with full project metadata in batches.
        Supports both JVM-style (class_name, package_name) and JS/TS-style (scope_name, exported).
        """
        if not functions:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (f:Function {id: row.id})
            SET f.name = row.name,
                f.node_type = 'code',
                f.qualified_name = row.qualified_name,
                f.kind = row.kind,
                f.class_name = row.class_name,
                f.package_name = row.package_name,
                f.scope_name = row.scope_name,
                f.file_path = row.file_path,
                f.start_byte = row.start_byte,
                f.end_byte = row.end_byte,
                f.start_line = row.start_line,
                f.end_line = row.end_line,
                f.arity = row.arity,
                f.code = row.code,
                f.comment = row.comment,
                f.summary = row.summary,
                f.note = row.note,
                f.exported = coalesce(row.exported, false),
                f.visibility = coalesce(row.visibility, 'unknown'),
                f.is_public_api = coalesce(row.is_public_api, false),
                f.visibility_source = coalesce(row.visibility_source, ''),
                f.export_evidence = coalesce(row.export_evidence, ''),
                f.signature = coalesce(row.signature, ''),
                f.external = coalesce(row.external, false),
                f.builtin = coalesce(row.builtin, false),
                f.react_role = coalesce(row.react_role, ''),
                f.middleware_kind = coalesce(row.middleware_kind, ''),
                f.project_id = row.project_id,
                f.project_id_normalized = row.project_id_normalized,
                f.project_name = row.project_name,
                f.language = row.language,
                f.repo = row.repo,
                f.build_system = row.build_system,
                f.updated_at = datetime()
            RETURN count(f) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("functions", functions, write_batch, state, state_writer)

    async def write_properties_full(
        self,
        properties: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write property nodes with full project metadata in batches."""
        if not properties:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (p:Property {id: row.id})
            SET p.name = row.name,
                p.qualified_name = row.qualified_name,
                p.kind = row.kind,
                p.scope_name = row.scope_name,
                p.class_name = row.class_name,
                p.package_name = row.package_name,
                p.file_path = row.file_path,
                p.start_line = row.start_line,
                p.end_line = row.end_line,
                p.parameters = row.parameters,
                p.return_type = row.return_type,
                p.code = row.code,
                p.comment = row.comment,
                p.summary = row.summary,
                p.note = row.note,
                p.exported = row.exported,
                p.project_id = row.project_id,
                p.project_id_normalized = row.project_id_normalized,
                p.project_name = row.project_name,
                p.language = row.language,
                p.repo = row.repo,
                p.build_system = row.build_system,
                p.updated_at = datetime()
            RETURN count(p) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("properties", properties, write_batch, state, state_writer)

    async def write_events_full(
        self,
        events: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write event nodes with full project metadata in batches."""
        if not events:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (e:Event {id: row.id})
            SET e.name = row.name,
                e.qualified_name = row.qualified_name,
                e.kind = row.kind,
                e.scope_name = row.scope_name,
                e.class_name = row.class_name,
                e.package_name = row.package_name,
                e.file_path = row.file_path,
                e.start_line = row.start_line,
                e.end_line = row.end_line,
                e.parameters = row.parameters,
                e.code = row.code,
                e.comment = row.comment,
                e.summary = row.summary,
                e.note = row.note,
                e.exported = row.exported,
                e.project_id = row.project_id,
                e.project_id_normalized = row.project_id_normalized,
                e.project_name = row.project_name,
                e.language = row.language,
                e.repo = row.repo,
                e.build_system = row.build_system,
                e.updated_at = datetime()
            RETURN count(e) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("events", events, write_batch, state, state_writer)

    async def write_interfaces_full(
        self,
        interfaces: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write interface nodes with full project metadata in batches."""
        if not interfaces:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (i:Interface {id: row.id})
            SET i.name = row.name,
                i.qualified_name = row.qualified_name,
                i.kind = row.kind,
                i.file_path = row.file_path,
                i.start_line = row.start_line,
                i.end_line = row.end_line,
                i.base_interfaces = row.base_interfaces,
                i.code = row.code,
                i.comment = row.comment,
                i.summary = row.summary,
                i.note = row.note,
                i.project_id = row.project_id,
                i.project_id_normalized = row.project_id_normalized,
                i.project_name = row.project_name,
                i.language = row.language,
                i.repo = row.repo,
                i.build_system = row.build_system,
                i.updated_at = datetime()
            RETURN count(i) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("interfaces", interfaces, write_batch, state, state_writer)

    async def write_enums_full(
        self,
        enums: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write enum nodes with full project metadata in batches."""
        if not enums:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (e:Enum {id: row.id})
            SET e.name = row.name,
                e.qualified_name = row.qualified_name,
                e.kind = row.kind,
                e.scope_name = row.scope_name,
                e.class_name = row.class_name,
                e.package_name = row.package_name,
                e.file_path = row.file_path,
                e.start_line = row.start_line,
                e.end_line = row.end_line,
                e.members = row.members,
                e.code = row.code,
                e.comment = row.comment,
                e.summary = row.summary,
                e.note = row.note,
                e.project_id = row.project_id,
                e.project_id_normalized = row.project_id_normalized,
                e.project_name = row.project_name,
                e.language = row.language,
                e.repo = row.repo,
                e.build_system = row.build_system,
                e.updated_at = datetime()
            RETURN count(e) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("enums", enums, write_batch, state, state_writer)

    async def write_constants_full(
        self,
        constants: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write constant nodes with full project metadata in batches."""
        if not constants:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (c:Constant {id: row.id})
            SET c.name = row.name,
                c.qualified_name = row.qualified_name,
                c.kind = row.kind,
                c.scope_name = row.scope_name,
                c.class_name = row.class_name,
                c.package_name = row.package_name,
                c.file_path = row.file_path,
                c.line_number = row.line_number,
                c.value = row.value,
                c.type_name = row.type_name,
                c.code = row.code,
                c.comment = row.comment,
                c.summary = row.summary,
                c.note = row.note,
                c.project_id = row.project_id,
                c.project_id_normalized = row.project_id_normalized,
                c.project_name = row.project_name,
                c.language = row.language,
                c.repo = row.repo,
                c.build_system = row.build_system,
                c.updated_at = datetime()
            RETURN count(c) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("constants", constants, write_batch, state, state_writer)

    async def write_variables_full(
        self,
        variables: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write variable nodes with full project metadata in batches."""
        if not variables:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (v:Variable {id: row.id})
            SET v.name = row.name,
                v.qualified_name = row.qualified_name,
                v.kind = row.kind,
                v.scope_name = row.scope_name,
                v.class_name = row.class_name,
                v.package_name = row.package_name,
                v.file_path = row.file_path,
                v.line_number = row.line_number,
                v.type_name = row.type_name,
                v.is_global = row.is_global,
                v.is_shared = row.is_shared,
                v.code = row.code,
                v.comment = row.comment,
                v.summary = row.summary,
                v.note = row.note,
                v.project_id = row.project_id,
                v.project_id_normalized = row.project_id_normalized,
                v.project_name = row.project_name,
                v.language = row.language,
                v.repo = row.repo,
                v.build_system = row.build_system,
                v.updated_at = datetime()
            RETURN count(v) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("variables", variables, write_batch, state, state_writer)

    async def write_relations_typed(
        self,
        relations: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write typed relationships using per-type batching.

        Each relation dict must have: source_id, target_id, rel_type, properties.
        Relations are grouped by (source_label, target_label, rel_type). Unlabeled
        endpoint matching is rejected because it is both ambiguous and unindexable.
        """
        if not relations:
            return 0

        # Strip (Project)-[:CONTAINS]->(anything) edges regardless of call site.
        # Android analyzer calls this method directly (not through write_all) so
        # the write_all-level filter does not apply here.  We detect these edges
        # by the explicit source_label field that the Android builder sets.
        relations = [
            r for r in relations
            if not (r.get("source_label") == "Project" and r.get("rel_type") == "CONTAINS")
        ]
        if not relations:
            return 0

        groups = group_typed_relations(relations)

        total_written = 0
        for relationship_group, rows in groups.items():
            state_key = relationship_group.state_key
            start_index = state.get(state_key, 0) if state else 0
            if start_index >= len(rows):
                continue

            async def write_batch(
                batch: List[Dict[str, Any]], _group=relationship_group
            ) -> int:
                query = compile_relationship_upsert(_group)
                records, _, _ = await self.driver.execute_query(
                    query, {"rows": batch}, self.database
                )
                count = int(records[0]["count"]) if records else 0
                if count != len(batch):
                    raise RuntimeError(
                        f"relationship batch integrity failure for {_group.state_key}: "
                        f"expected={len(batch)} matched={count} unresolved_or_ambiguous="
                        f"{abs(len(batch) - count)}"
                    )
                return count

            written = await self.write_batches(
                state_key, rows, write_batch, state, state_writer
            )
            total_written += written

        return total_written

    async def write_nodes_batch(
        self,
        key: str,
        cypher: str,
        rows: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write nodes using a caller-provided Cypher query.

        Useful for custom node types (e.g. Android-specific types) that supply
        their own ``MERGE … SET`` Cypher.  The query must accept ``$rows`` as
        the parameter name.  Because most such queries do not include a
        ``RETURN`` clause the written count is estimated as ``len(batch)``.
        """
        if not rows:
            return 0

        _cypher = cypher  # capture for closure

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            records, _, _ = await self.driver.execute_query(
                _cypher, {"rows": batch}, self.database
            )
            if records and "count" in records[0]:
                return int(records[0]["count"])
            return len(batch)

        operation = operation_for_custom_query(key, _cypher)
        return await self.write_batches(
            key, rows, write_batch, state, state_writer, operation
        )

    async def enqueue_deferred_relations(
        self, relations: List[Dict[str, Any]], *, barrier: str
    ) -> int:
        """Durably enqueue typed edges before their endpoint production closes."""

        if not relations:
            return 0
        if self._journal_runtime is None:
            raise RuntimeError("deferred durable relations require journal mode")
        self._journal_runtime.open_barrier(barrier)
        enqueued = 0
        for relationship_group, rows in group_typed_relations(relations).items():
            operation = GraphWriteOperation.for_label(relationship_group.state_key)

            async def write_batch(
                batch: List[Dict[str, Any]], _group=relationship_group
            ) -> int:
                query = compile_relationship_upsert(_group)
                records, _, _ = await self.driver.execute_query(
                    query, {"rows": batch}, self.database
                )
                return int(records[0]["count"]) if records else 0

            for offset in range(0, len(rows), self.batch_size):
                batch = rows[offset : offset + self.batch_size]
                ticket = self._journal_runtime.prepare(
                    label=relationship_group.state_key,
                    rows=batch,
                    sequence=offset,
                    operation=operation,
                    additional_required_barriers=(barrier,),
                    defer=True,
                )
                if ticket.batch.status is not BatchStatus.DONE:
                    self._deferred_journal_writes.append((ticket, write_batch))
                enqueued += len(batch)
        return enqueued

    async def close_barrier_and_drain(self, barrier: str) -> int:
        """Close one producer barrier and execute its now-eligible durable jobs."""

        if self._journal_runtime is None:
            raise RuntimeError("deferred durable relations require journal mode")
        self._journal_runtime.close_barrier(barrier)
        written = 0
        remaining = []
        for ticket, write_fn in self._deferred_journal_writes:
            if barrier not in ticket.batch.required_barriers:
                remaining.append((ticket, write_fn))
                continue
            claimed = self._journal_runtime.claim_deferred(ticket)
            if not claimed.execute:
                written += claimed.batch.expected_count
                continue
            started = time.monotonic()
            try:
                with journaled_mutation(
                    claimed.batch.job_id, claimed.operation.operation_key
                ):
                    count = int(await write_fn(list(claimed.rows)))
                self._journal_runtime.acknowledge(
                    claimed, count, int((time.monotonic() - started) * 1000)
                )
                written += count
            except BaseException:
                self._journal_runtime.mark_ambiguous(claimed)
                raise
        self._deferred_journal_writes = remaining
        return written

    async def write_calls_with_site(
        self,
        calls: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write CALLS edges that include a site_id (for C++/Android-style calls)."""
        if not calls:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            CALL {
                WITH row
                MATCH (caller:Function {id: row.caller_id})
                RETURN caller
                LIMIT 1
            }
            CALL {
                WITH row
                MATCH (callee:Function {id: row.callee_id})
                RETURN callee
                LIMIT 1
            }
            MERGE (caller)-[r:CALLS {site_id: row.site_id}]->(callee)
            SET r += row.props
            RETURN count(r) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches(
            "calls:site", calls, write_batch, state, state_writer
        )

    async def write_navigators(
        self,
        navigators: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """MERGE :Navigator nodes (React Navigation factory declarations)."""
        if not navigators:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (n:Navigator {id: row.id})
            SET n.var_name        = row.var_name,
                n.nav_type        = row.nav_type,
                n.factory         = row.factory,
                n.param_list_ref  = row.param_list_ref,
                n.file_path       = row.file_path,
                n.start_line      = row.start_line,
                n.project_id      = row.project_id,
                n.project_name    = row.project_name,
                n.updated_at      = datetime()
            RETURN count(n) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("navigators", navigators, write_batch, state, state_writer)

    async def write_has_routes(
        self,
        routes: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """MERGE (:Navigator)-[:HAS_ROUTE {name, param_schema}]->(:Function) edges."""
        if not routes:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MATCH (n:Navigator {id: row.navigator_id})
            MATCH (s:Function  {id: row.screen_id})
            MERGE (n)-[r:HAS_ROUTE {name: row.route_name}]->(s)
            SET r.param_schema = row.param_schema,
                r.updated_at   = datetime()
            RETURN count(r) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("has_routes", routes, write_batch, state, state_writer)

    async def write_param_lists(
        self,
        param_lists: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """MERGE :RouteParam nodes carrying per-route type schemas."""
        if not param_lists:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (p:RouteParam {id: row.symbol_id + '::' + row.route_name})
            SET p.param_list_name = row.name,
                p.route           = row.route_name,
                p.type_str        = row.type_str,
                p.file_path       = row.file_path,
                p.project_id      = row.project_id,
                p.updated_at      = datetime()
            RETURN count(p) AS count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("param_lists", param_lists, write_batch, state, state_writer)

    async def write_workflows(
        self,
        workflows: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write :Workflow nodes via MERGE in batches."""
        if not workflows:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MERGE (w:Workflow {workflow_id: row.workflow_id})
            SET w.name          = row.workflow_name,
                w.domain        = row.domain,
                w.description   = row.description,
                w.confidence    = row.confidence,
                w.entrypoint_id = row.entrypoint_id,
                w.language      = row.language,
                w.project       = row.project,
                w.kind          = row.kind,
                w.updated_at    = datetime()
            RETURN count(w) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("workflows", workflows, write_batch, state, state_writer)

    async def write_workflow_steps(
        self,
        step_rows: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write :HAS_STEP edges between :Workflow and :Function nodes."""
        if not step_rows:
            return 0

        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MATCH (w:Workflow  {workflow_id: row.workflow_id})
            MATCH (f:Function  {id:          row.function_id})
            MERGE (w)-[s:HAS_STEP {order: row.step_order}]->(f)
            RETURN count(s) as count
            """
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            return records[0]["count"] if records else 0

        return await self.write_batches("workflow_steps", step_rows, write_batch, state, state_writer)

    async def write_all(
        self,
        projects: List[Dict[str, Any]] = None,
        packages: List[Dict[str, Any]] = None,
        namespaces: List[Dict[str, Any]] = None,
        files: List[Dict[str, Any]] = None,
        classes: List[Dict[str, Any]] = None,
        types: List[Dict[str, Any]] = None,
        function_types: List[Dict[str, Any]] = None,
        functions: List[Dict[str, Any]] = None,
        fields: List[Dict[str, Any]] = None,
        aliases: List[Dict[str, Any]] = None,
        templates: List[Dict[str, Any]] = None,
        relations: List[Dict[str, Any]] = None,
        calls: List[Dict[str, Any]] = None,
        calls_with_site: List[Dict[str, Any]] = None,
        # VB-specific symbol types
        properties: List[Dict[str, Any]] = None,
        events: List[Dict[str, Any]] = None,
        interfaces: List[Dict[str, Any]] = None,
        enums: List[Dict[str, Any]] = None,
        constants: List[Dict[str, Any]] = None,
        variables: List[Dict[str, Any]] = None,
        # React Navigation navigator graph
        navigators: List[Dict[str, Any]] = None,
        has_routes: List[Dict[str, Any]] = None,
        param_lists: List[Dict[str, Any]] = None,
        workflows: List[Dict[str, Any]] = None,
        workflow_steps: List[Dict[str, Any]] = None,
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
        # Selector flags – set to True to use the *_full inline-Cypher variants
        use_full_writers: bool = False,
        files_variant: str = "default",  # "default" | "with_package" | "with_imports" | "with_jsx"
    ) -> Dict[str, int]:
        """
        Write all entities in the correct order.

        Args:
            projects: Project definitions (new – written first)
            packages: Package definitions
            namespaces: Namespace definitions
            files: File definitions
            classes: Class definitions
            types: Type definitions
            function_types: C++ function-type (typedef/using) definitions
            functions: Function definitions
            fields: C++ field/member-variable definitions
            aliases: C++ typedef/using alias definitions
            templates: C++ template definitions
            relations: Generic relationships (use write_relations_typed for typed rels)
            calls: Function call relationships
            calls_with_site: Call relationships that carry a ``site_id`` (Android/C++)
            properties: VB.NET property definitions
            events: VB.NET event definitions
            interfaces: VB.NET interface definitions
            enums: Enum definitions (VB.NET, VB6, VBA)
            constants: Constant definitions (VB6, VBA)
            variables: Variable definitions (VB6, VBA)
            state: State dict for resume
            state_writer: Function to persist state
            use_full_writers: When True use the *_full inline-Cypher methods that
                               include project_id/language/repo/build_system fields.

        Returns:
            Dict with counts per entity type
        """
        optional_unresolved_relations = 0
        if relations:
            project_ids = {
                str(project["id"])
                for project in projects or []
                if project.get("id")
            }
            project_ids.update(
                str(file_row["project_id"])
                for file_row in files or []
                if file_row.get("project_id")
            )
            candidate_relations = [
                dict(relation)
                for relation in relations
                if not (
                    str(relation.get("source_id") or "") in project_ids
                    and relation.get("rel_type") == "CONTAINS"
                )
            ]
            relations = []
            for relation in candidate_relations:
                explicitly_optional = (
                    relation.get("required") is False
                    or relation.get("properties", {}).get("resolved") is False
                )
                if explicitly_optional:
                    optional_unresolved_relations += 1
                    continue
                relations.append(relation)

            identity_rows = (
                (packages, "Package"),
                (namespaces, "Namespace"),
                (files, "File"),
                (classes, "Class"),
                (types, "Type"),
                (function_types, "FunctionType"),
                (functions, "Function"),
                (fields, "Field"),
                (aliases, "Alias"),
                (templates, "Template"),
                (properties, "Property"),
                (events, "Event"),
                (interfaces, "Interface"),
                (enums, "Enum"),
                (constants, "Constant"),
                (variables, "Variable"),
                (navigators, "Navigator"),
            )
            labels_by_id: Dict[str, set[str]] = {}
            for rows, label in identity_rows:
                for row in rows or []:
                    identity = row.get("id", row.get("symbol_id"))
                    if identity:
                        labels_by_id.setdefault(str(identity), set()).add(label)

            resolved_relations: List[Dict[str, Any]] = []
            for position, relation in enumerate(relations):
                skip_optional = False
                for role in ("source", "target"):
                    label_key = f"{role}_label"
                    if relation.get(label_key):
                        continue
                    identity = str(relation.get(f"{role}_id") or "")
                    candidates = labels_by_id.get(identity, set())
                    if len(candidates) != 1:
                        if (
                            role == "target"
                            and not candidates
                            and relation.get("rel_type") in _OPTIONAL_EXTERNAL_RELATION_TYPES
                        ):
                            optional_unresolved_relations += 1
                            skip_optional = True
                            break
                        raise ValueError(
                            f"cannot infer {label_key} for relationship row {position} "
                            f"identity={identity!r}; candidates={sorted(candidates)}"
                        )
                    relation[label_key] = next(iter(candidates))
                if not skip_optional:
                    resolved_relations.append(relation)
            relations = resolved_relations

            # Validate the complete relation contract before schema or node
            # mutations, so a bad producer cannot leave a partial stream.
            group_typed_relations(relations)

        counts = {}
        if optional_unresolved_relations:
            counts["unresolved_relations"] = optional_unresolved_relations
            self._emit_progress(
                "optional_unresolved",
                "relations",
                skipped=optional_unresolved_relations,
            )

        # --- Projects (always inline-Cypher) ---
        if projects:
            counts["projects"] = await self.write_projects(projects, state, state_writer)

        # --- Packages ---
        if packages:
            if use_full_writers:
                counts["packages"] = await self.write_packages_full(packages, state, state_writer)
            else:
                counts["packages"] = await self.write_packages(packages, state, state_writer)

        # --- Namespaces ---
        if namespaces:
            if use_full_writers:
                counts["namespaces"] = await self.write_namespaces_full(namespaces, state, state_writer)
            else:
                counts["namespaces"] = await self.write_namespaces(namespaces, state, state_writer)

        # --- Files ---
        if files:
            if files_variant == "with_package":
                counts["files"] = await self.write_files_with_package(files, state, state_writer)
            elif files_variant == "with_imports":
                counts["files"] = await self.write_files_with_imports(files, state, state_writer)
            elif files_variant == "with_jsx":
                counts["files"] = await self.write_files_jsx(files, state, state_writer)
            else:
                counts["files"] = await self.write_files(files, state, state_writer)
            # Attach each File to its owning Repository node.
            # Runs after files are written so the File nodes are guaranteed to exist.
            counts["repo_file_edges"] = await self.write_repo_file_edges(files, state, state_writer)

        # --- Classes ---
        if classes:
            if use_full_writers:
                counts["classes"] = await self.write_classes_full(classes, state, state_writer)
            else:
                counts["classes"] = await self.write_classes(classes, state, state_writer)

        # --- Types ---
        if types:
            if use_full_writers:
                counts["types"] = await self.write_types_full(types, state, state_writer)
            else:
                counts["types"] = await self.write_types(types, state, state_writer)

        if function_types:
            counts["function_types"] = await self.write_function_types(function_types, state, state_writer)

        # --- Functions ---
        if functions:
            if use_full_writers:
                counts["functions"] = await self.write_functions_full(functions, state, state_writer)
            else:
                counts["functions"] = await self.write_functions(functions, state, state_writer)

        # --- React Navigation: Navigator nodes + HAS_ROUTE edges + RouteParam nodes ---
        # Written after functions so that HAS_ROUTE MATCH on :Function will resolve.
        if navigators:
            counts["navigators"] = await self.write_navigators(navigators, state, state_writer)

        if has_routes:
            counts["has_routes"] = await self.write_has_routes(has_routes, state, state_writer)

        if param_lists:
            counts["param_lists"] = await self.write_param_lists(param_lists, state, state_writer)

        # --- Properties (VB-specific) ---
        if properties:
            if use_full_writers:
                counts["properties"] = await self.write_properties_full(properties, state, state_writer)

        # --- Events (VB-specific) ---
        if events:
            if use_full_writers:
                counts["events"] = await self.write_events_full(events, state, state_writer)

        # --- Interfaces (VB-specific) ---
        if interfaces:
            if use_full_writers:
                counts["interfaces"] = await self.write_interfaces_full(interfaces, state, state_writer)

        # --- Enums (VB-specific) ---
        if enums:
            if use_full_writers:
                counts["enums"] = await self.write_enums_full(enums, state, state_writer)

        # --- Constants (VB-specific) ---
        if constants:
            if use_full_writers:
                counts["constants"] = await self.write_constants_full(constants, state, state_writer)

        # --- Variables (VB-specific) ---
        if variables:
            if use_full_writers:
                counts["variables"] = await self.write_variables_full(variables, state, state_writer)

        if fields:
            counts["fields"] = await self.write_fields(fields, state, state_writer)

        if aliases:
            counts["aliases"] = await self.write_aliases(aliases, state, state_writer)

        if templates:
            counts["templates"] = await self.write_templates(templates, state, state_writer)

        # --- Relationships ---
        if relations:
            if use_full_writers:
                counts["relations"] = await self.write_relations_typed(relations, state, state_writer)
            else:
                counts["relations"] = await self.write_relations(relations, state, state_writer)

        if calls:
            counts["calls"] = await self.write_calls(calls, state, state_writer)

        if calls_with_site:
            counts["calls_with_site"] = await self.write_calls_with_site(calls_with_site, state, state_writer)

        # --- Workflows (written after functions so FK constraints are satisfied) ---
        if workflows:
            counts["workflows"] = await self.write_workflows(workflows, state, state_writer)

        if workflow_steps:
            counts["workflow_steps"] = await self.write_workflow_steps(workflow_steps, state, state_writer)

        return counts
