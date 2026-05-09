"""
Migration Example: kotlin_analyzer.py

This file demonstrates the exact changes needed to migrate from hardcoded
Neo4jWriter to the new abstraction layer.

BEFORE/AFTER comparison for kotlin_analyzer.py
"""

# ============================================================================
# STEP 1: Update imports at the top of the file
# ============================================================================

# OLD (lines 1-18): Remove this import
"""
from neo4j import GraphDatabase
"""

# NEW: Add these imports instead
"""
import asyncio  # Add this - needed for async operations
from tools.graph import GraphDriverFactory, GraphProvider, LanguageCodeWriter
"""

# Complete new imports section should be:
"""
from __future__ import annotations

import argparse
import asyncio  # NEW - add this
import json
import hashlib
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import torch
# from neo4j import GraphDatabase  # REMOVE this line
from transformers import AutoModel, AutoTokenizer
from tree_sitter import Language, Parser

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    load_state,
    safe_cache_root,
    write_parse_cache,
    write_state,
)
from tools.common.cloc_stats import collect_cloc_stats, normalize_cloc_payload
from tools.graph import GraphDriverFactory, GraphProvider, LanguageCodeWriter  # NEW - add this

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None
"""


# ============================================================================
# STEP 2: Remove the entire Neo4jWriter class (lines 824-1331)
# ============================================================================

# DELETE this entire class (300-500 lines):
"""
class Neo4jWriter:
    def __init__(self, uri: str, user: str, password: str, database: Optional[str]) -> None:
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self) -> None:
        self.driver.close()

    def write(
        self,
        packages: List[PackageDef],
        namespaces: List[NamespaceDef],
        ...
    ) -> None:
        # 300+ lines of Cypher queries
        ...
    
    @staticmethod
    def write_cloc_stats(...):
        ...
"""

# REPLACE with one line - nothing! The LanguageCodeWriter handles this now.


# ============================================================================
# STEP 3: Update build_call_graph function signature
# ============================================================================

# OLD signature (line ~1340):
"""
def build_call_graph(
    root: str,
    neo4j_writer: Optional[Neo4jWriter],  # OLD type
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
) -> None:
"""

# NEW signature - change to async and update parameter type:
"""
async def build_call_graph(  # Make it async
    root: str,
    code_writer: Optional[LanguageCodeWriter],  # NEW - changed from neo4j_writer
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
) -> None:
"""


# ============================================================================
# STEP 4: Update Neo4j writer logic inside build_call_graph
# ============================================================================

# OLD (around line 1975-2300):
"""
    if neo4j_writer:
        # Lots of code to collect nodes into batches
        nodes = {
            "packages": [],
            "namespaces": [],
            "files": [],
            ...
        }
        
        # Add nodes to batches
        for payload in iter_payloads(...):
            for pkg in payload["packages"]:
                nodes["packages"].append({...})
        
        # Write using old Neo4jWriter
        neo4j_writer.write(
            packages=[asdict(p) for p in packages],
            namespaces=[asdict(n) for n in namespaces],
            files=[asdict(f) for f in files],
            classes=[asdict(c) for c in classes],
            function_types=[asdict(ft) for ft in function_types],
            functions=[asdict(fn) for fn in functions],
            relations=[asdict(r) for r in relations],
            calls=[asdict(c) for c in calls],
            verbose=verbose,
            batch_size=neo4j_batch_size,
            state_path=neo4j_state_path,
        )
"""

# NEW - much simpler:
"""
    if code_writer:
        # Collect nodes (same as before)
        nodes = {
            "packages": [],
            "namespaces": [],
            "files": [],
            "classes": [],
            "function_types": [],
            "functions": [],
            "relations": [],
            "calls": [],
        }
        
        # Add nodes to batches (same as before)
        for payload in iter_payloads(log_parse=True):
            for pkg in payload["packages"]:
                nodes["packages"].append({...})
            # ... same collection logic
        
        # Load state for resume capability
        state = load_state(neo4j_state_path) if neo4j_state_path else {}
        
        def state_writer(s: Dict[str, int]) -> None:
            if neo4j_state_path:
                write_state(neo4j_state_path, s)
        
        # NEW - use LanguageCodeWriter
        counts = await code_writer.write_all(
            packages=nodes["packages"],
            namespaces=nodes["namespaces"],
            files=nodes["files"],
            classes=nodes["classes"],
            types=nodes["function_types"],
            functions=nodes["functions"],
            relations=nodes["relations"],
            calls=nodes["calls"],
            state=state,
            state_writer=state_writer,
        )
        
        if verbose:
            print(f"[neo4j] Written: {counts}")
"""


# ============================================================================
# STEP 5: Update write_cloc_stats call
# ============================================================================

# OLD (in main function, around line 2550):
"""
    if neo4j_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            neo4j_writer.write_cloc_stats(project_id, project_name, args.root, repo, language, cloc_stats)
"""

# NEW - use driver directly:
"""
    if code_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            # Write CLOC stats directly using driver
            query = '''
            MERGE (p:Project {id: $project_id})
            SET p.name = $project_name,
                p.root = $root,
                p.repo = $repo,
                p.language = $language,
                p.cloc_stats = $cloc_stats,
                p.updated_at = datetime()
            '''
            await code_writer.driver.execute_query(
                query,
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "root": args.root,
                    "repo": repo,
                    "language": language,
                    "cloc_stats": cloc_stats,
                }
            )
"""


# ============================================================================
# STEP 6: Update main() function
# ============================================================================

# OLD main() (around line 2507):
"""
def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    neo4j_writer = None
    if args.neo4j_uri and args.neo4j_user and args.NEO4J_PASS:
        neo4j_writer = Neo4jWriter(args.neo4j_uri, args.neo4j_user, args.NEO4J_PASS, args.neo4j_db)

    # ... rest of setup ...

    try:
        if args.dry_run:
            kotlin_files = _scan_kotlin_files(args.root)
            print(f"Dry run: {len(kotlin_files)} Kotlin files found")
            return 0
        build_call_graph(
            args.root,
            neo4j_writer=neo4j_writer,
            # ... other params
        )
    finally:
        if neo4j_writer:
            neo4j_writer.close()
    return 0
"""

# NEW main() - use async wrapper:
"""
async def async_main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    # NEW - Create driver and writer using factory
    code_writer = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.NEO4J_PASS:
        driver = GraphDriverFactory.create_driver(
            GraphProvider.NEO4J,
            {
                "uri": args.neo4j_uri,
                "user": args.neo4j_user,
                "password": args.NEO4J_PASS,
                "database": args.neo4j_db,
            }
        )
        code_writer = LanguageCodeWriter(
            driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )

    # ... rest of setup (qdrant, embedder) stays the same ...

    try:
        if args.dry_run:
            kotlin_files = _scan_kotlin_files(args.root)
            print(f"Dry run: {len(kotlin_files)} Kotlin files found")
            return 0
        
        # Call async build_call_graph
        await build_call_graph(
            args.root,
            code_writer=code_writer,  # NEW - changed parameter
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=args.cache_dir,
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
        )
    finally:
        if driver:
            driver.close()  # Close driver, not writer
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    '''Synchronous entry point that wraps async_main'''
    return asyncio.run(async_main(argv))
"""


# ============================================================================
# SUMMARY OF CHANGES
# ============================================================================

"""
Files to modify: tools/kotlin/kotlin_analyzer.py

Lines to change:
1. Line ~16: Remove "from neo4j import GraphDatabase"
2. Line ~16: Add "import asyncio"
3. Line ~33: Add "from tools.graph import GraphDriverFactory, GraphProvider, LanguageCodeWriter"
4. Lines 824-1331: DELETE entire Neo4jWriter class
5. Line ~1340: Change build_call_graph to async, rename neo4j_writer param to code_writer
6. Lines 1975-2300: Replace neo4j_writer.write() with code_writer.write_all()
7. Line ~2515: Replace Neo4jWriter instantiation with GraphDriverFactory
8. Line ~2507: Rename main() to async_main(), create new sync main() wrapper

Total lines removed: ~500 (the Neo4jWriter class)
Total lines added: ~50 (new driver setup + async wrapper)
Net reduction: ~450 lines of code!

Benefits:
- ✅ Database agnostic (can swap to Kuzu later)
- ✅ Cleaner code (no 500-line writer class)
- ✅ Reusable across all analyzers
- ✅ Better tested (operations are unit-testable)
- ✅ Consistent behavior across languages
"""

# After migration, the same pattern applies to ALL other analyzers:
# - android_kotlin_analyzer.py
# - java_analyzer.py
# - python_analyzer.py
# - cplus_analyzer.py
# - csharp_analyzer.py
# - ts_analyzer.py
# - js_analyzer.py
# - php_analyzer.py
# - sql_analyzer.py
# - plsql_analyzer.py
