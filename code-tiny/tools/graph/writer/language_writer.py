"""
Language Code Writer

Unified writer for all language analyzers with state management and batching.
Replaces the duplicated Neo4jWriter classes across all analyzer files.
"""

from typing import Any, Dict, List, Optional, Callable
import logging

from tools.graph.core.base import GraphDriver
from tools.graph.operations.package_ops import PackageNodeOperations
from tools.graph.operations.class_ops import ClassNodeOperations
from tools.graph.operations.namespace_ops import NamespaceNodeOperations
from tools.graph.operations.type_ops import TypeNodeOperations
from tools.graph.operations.function_ops import FunctionNodeOperations

logger = logging.getLogger(__name__)


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
        
        # Initialize operations
        self.package_ops = PackageNodeOperations()
        self.class_ops = ClassNodeOperations()
        self.namespace_ops = NamespaceNodeOperations()
        self.type_ops = TypeNodeOperations()
        self.function_ops = FunctionNodeOperations()
    
    def _log_progress(self, label: str, current: int, total: int) -> None:
        """Log batch progress"""
        first_checkpoint = min(total, max(1, self.batch_size))
        if self.verbose and (current == first_checkpoint or current % 1000 == 0 or current == total):
            logger.info(f"[{self.driver.provider.value}] {label} {current}/{total}")
            if self.verbose:
                print(f"[{self.driver.provider.value}] {label} {current}/{total}")
    
    async def write_batches(
        self,
        label: str,
        rows: List[Dict[str, Any]],
        write_fn: Callable,
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
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
        start_index = state.get(label, 0) if state else 0
        total = len(rows)
        
        if start_index >= total:
            if self.verbose:
                logger.info(f"[{label}] Already completed ({total} items)")
            return 0
        
        written = 0
        for offset in range(start_index, total, self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            
            # Write batch
            count = await write_fn(batch)
            written += count
            
            # Update state
            next_index = offset + len(batch)
            if state is not None:
                state[label] = next_index
                if state_writer:
                    state_writer(state)
            
            self._log_progress(label, next_index, total)
        
        return written
    
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
        """Write generic relationships in batches"""
        if not relations:
            return 0
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MATCH (source {id: row.source_id})
            MATCH (target {id: row.target_id})
            CALL apoc.merge.relationship(
                source,
                row.rel_type,
                {},
                row.properties,
                target,
                {}
            ) YIELD rel
            RETURN count(rel) as count
            """
            
            # Fallback for systems without APOC
            fallback_query = """
            UNWIND $rows AS row
            MATCH (source {id: row.source_id})
            MATCH (target {id: row.target_id})
            CREATE (source)-[r:RELATION]->(target)
            SET r = row.properties,
                r.rel_type = row.rel_type
            RETURN count(r) as count
            """
            
            try:
                records, _, _ = await self.driver.execute_query(
                    query,
                    {"rows": batch},
                    self.database
                )
            except Exception:
                # Fallback if APOC not available
                records, _, _ = await self.driver.execute_query(
                    fallback_query,
                    {"rows": batch},
                    self.database
                )
            
            return records[0]["count"] if records else 0
        
        return await self.write_batches(
            "relations",
            relations,
            write_batch,
            state,
            state_writer
        )
    
    async def write_calls(
        self,
        calls: List[Dict[str, Any]],
        state: Optional[Dict[str, int]] = None,
        state_writer: Optional[Callable] = None,
    ) -> int:
        """Write function call relationships in batches"""
        if not calls:
            return 0
        
        async def write_batch(batch: List[Dict[str, Any]]) -> int:
            query = """
            UNWIND $rows AS row
            MATCH (caller:Function {id: row.caller_id})
            MATCH (callee:Function {id: row.callee_id})
            MERGE (caller)-[r:CALLS]->(callee)
            ON CREATE SET r.count = 1
            ON MATCH SET r.count = COALESCE(r.count, 0) + 1
            SET r.call_type = row.call_type,
                r.updated_at = datetime()
            """

            await self.driver.execute_query(
                query,
                {"rows": batch},
                self.database
            )

            return len(batch)
        
        return await self.write_batches(
            "calls",
            calls,
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
            SET ft.type_signature = row.type_signature,
                ft.file_path      = row.file_path,
                ft.start_line     = row.start_line,
                ft.end_line       = row.end_line,
                ft.code           = row.code,
                ft.project_id     = row.project_id,
                ft.project_name   = row.project_name,
                ft.language       = row.language,
                ft.repo           = row.repo,
                ft.build_system   = row.build_system
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
            SET f.name           = row.name,
                f.qualified_name = row.qualified_name,
                f.scope_name     = row.scope_name,
                f.type_signature = row.type_signature,
                f.file_path      = row.file_path,
                f.start_line     = row.start_line,
                f.end_line       = row.end_line,
                f.code           = row.code,
                f.project_id     = row.project_id,
                f.project_name   = row.project_name,
                f.language       = row.language,
                f.repo           = row.repo,
                f.build_system   = row.build_system
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
            SET a.name           = row.name,
                a.qualified_name = row.qualified_name,
                a.kind           = row.kind,
                a.target_name    = row.target_name,
                a.file_path      = row.file_path,
                a.start_line     = row.start_line,
                a.end_line       = row.end_line,
                a.code           = row.code,
                a.project_id     = row.project_id,
                a.project_name   = row.project_name,
                a.language       = row.language,
                a.repo           = row.repo,
                a.build_system   = row.build_system
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
            SET t.name         = row.name,
                t.file_path    = row.file_path,
                t.start_line   = row.start_line,
                t.end_line     = row.end_line,
                t.code         = row.code,
                t.project_id   = row.project_id,
                t.project_name = row.project_name,
                t.language     = row.language,
                t.repo         = row.repo,
                t.build_system = row.build_system
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
                c.project_id = row.project_id,
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
                f.external = coalesce(row.external, false),
                f.builtin = coalesce(row.builtin, false),
                f.react_role = coalesce(row.react_role, ''),
                f.middleware_kind = coalesce(row.middleware_kind, ''),
                f.project_id = row.project_id,
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
        Relations are grouped by (source_label, target_label, rel_type) if those fields
        are present, otherwise matched by id only.
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

        from collections import defaultdict
        groups: dict = defaultdict(list)
        for rel in relations:
            key = rel.get("rel_type", "RELATION")
            groups[key].append(rel)

        total_written = 0
        for rel_type, group in groups.items():
            state_key = f"relations:{rel_type}"
            start_index = state.get(state_key, 0) if state else 0
            if start_index >= len(group):
                continue

            async def write_batch(batch: List[Dict[str, Any]], _rel_type: str = rel_type) -> int:
                query = (
                    "UNWIND $rows AS row "
                    "MATCH (a {id: row.source_id}), (b {id: row.target_id}) "
                    f"MERGE (a)-[r:{_rel_type}]->(b) "
                    "SET r += row.properties "
                    "RETURN count(r) as count"
                )
                records, _, _ = await self.driver.execute_query(
                    query, {"rows": batch}, self.database
                )
                return records[0]["count"] if records else 0

            written = await self.write_batches(state_key, group, write_batch, state, state_writer)
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
            await self.driver.execute_query(_cypher, {"rows": batch}, self.database)
            return len(batch)

        return await self.write_batches(key, rows, write_batch, state, state_writer)

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

        return await self.write_batches("calls", calls, write_batch, state, state_writer)

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
        counts = {}

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
            # Strip (Project)-[:CONTAINS]->(anything) edges.
            # Project nodes must ONLY connect to Repository nodes via HAS_REPOSITORY.
            # The full intended hierarchy is:
            #   (Project)-[:HAS_REPOSITORY]->(Repository)
            #       -[:HAS_FILE]->(File)-[:CONTAINS]->(Function/Class/…)
            # All child nodes carry project_id as a property so project-scoped
            # queries can still find them without traversing from Project.
            #
            # Build the project ID set from BOTH the explicit `projects` list AND
            # the `project_id` field on every file node.  This ensures the filter
            # works even when a caller omits the `projects` argument (e.g.
            # cplus_analyzer, kotlin_analyzer).
            _project_ids: set = set()
            if projects:
                _project_ids.update(p["id"] for p in projects if p.get("id"))
            if files:
                _project_ids.update(f["project_id"] for f in files if f.get("project_id"))
            if _project_ids:
                relations = [
                    r for r in relations
                    if not (
                        r.get("source_id") in _project_ids
                        and r.get("rel_type") == "CONTAINS"
                    )
                ]
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
