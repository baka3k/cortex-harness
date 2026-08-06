from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.graph import GraphDriverFactory, GraphProvider, add_require_neo4j_argument, resolve_require_neo4j
from tools.graph.core.provider_runtime import add_graph_provider_arguments, create_graph_driver_from_args
from tools.graph.writer.mybatis_writer import MyBatisFactWriter
from tools.mybatis.cache import default_dependency_index_path, default_fact_artifact_path, write_dependency_index, write_fact_artifact
from tools.mybatis.pipeline import run_mybatis_foundation


@dataclass(frozen=True)
class MyBatisAnalyzerConfig:
    root: str
    project_id: str
    project_name: str
    languages: Sequence[str]
    cache_dir: Optional[str] = None
    incremental: bool = False
    changed_files_manifest: str = ""
    deleted_files_manifest: str = ""


class MyBatisAnalyzer:
    def __init__(self, config: MyBatisAnalyzerConfig) -> None:
        self.config = config

    def analyze(self):
        return run_mybatis_foundation(
            root=self.config.root,
            project_id=self.config.project_id,
            project_name=self.config.project_name,
            languages=self.config.languages,
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MyBatis semantic foundation analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--languages", choices=["auto", "java", "kotlin", "both"], default="auto")
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
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest", default="")
    parser.add_argument("--deleted-files-manifest", default="")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--mybatis-facts-output", default="")
    parser.add_argument("--mybatis-dependency-output", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_CODE_PATH"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION"))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    add_graph_provider_arguments(parser)
    add_require_neo4j_argument(parser)
    return parser.parse_args(argv)


def _languages_from_arg(value: str) -> Sequence[str]:
    if value == "java":
        return ("java",)
    if value == "kotlin":
        return ("kotlin",)
    return ("java", "kotlin")


async def _write_graph(args: argparse.Namespace, result) -> int:
    provider = getattr(args, "graph_provider", "neo4j")
    driver = None
    database = args.neo4j_db
    provider_label = provider
    if provider == "falkordb":
        try:
            driver = await create_graph_driver_from_args(args)
            verify = getattr(driver, "verify_connection", None)
            if verify is not None and not await verify():
                raise RuntimeError("FalkorDB connection verification failed")
            database = args.falkordb_graph
        except Exception as exc:  # noqa: BLE001
            print(
                "[mybatis] ERROR: FalkorDB driver creation failed: "
                f"{exc!r}. Path={args.falkordb_path} graph={args.falkordb_graph}.",
                file=sys.stderr,
            )
            if driver is not None:
                await _close_driver(driver)
            return 3
    else:
        require_neo4j = resolve_require_neo4j(args)
        creds_complete = bool(args.neo4j_uri and args.neo4j_user and args.neo4j_password)
        if not creds_complete:
            if require_neo4j:
                print(
                    "[mybatis] ERROR: --require-neo4j is on but Neo4j credentials are incomplete",
                    file=sys.stderr,
                )
                return 2
            return 0
        try:
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.NEO4J,
                {
                    "uri": args.neo4j_uri,
                    "user": args.neo4j_user,
                    "password": args.neo4j_password,
                    "database": args.neo4j_db,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[mybatis] ERROR: Neo4j driver creation failed: {exc!r}", file=sys.stderr)
            return 3
    try:
        writer = MyBatisFactWriter(driver, database=database, batch_size=args.neo4j_batch_size, verbose=args.verbose)
        cleanup_paths: List[str] = []
        if args.incremental:
            if args.changed_files_manifest:
                cleanup_paths.extend(load_manifest_paths(args.changed_files_manifest, args.root))
            if args.deleted_files_manifest:
                cleanup_paths.extend(load_manifest_paths(args.deleted_files_manifest, args.root))
        if cleanup_paths:
            cleanup = await writer.cleanup_files(args.project_id or os.path.basename(args.root), cleanup_paths)
            print(
                f"[cleanup][{provider_label}] deleted_nodes=%d deleted_unknown_functions=0"
                % cleanup.get("deleted_nodes", 0)
            )
        written = await writer.write_fact_nodes([fact.to_graph_node() for fact in result.semantic_facts])
        print(f"[{provider_label}] mybatis_facts {written}/{len(result.semantic_facts)}")
        rel_written = await writer.write_relationships([rel.to_graph_relationship() for rel in result.relationships])
        print(f"[{provider_label}] mybatis_relationships {rel_written}/{len(result.relationships)}")
    finally:
        await _close_driver(driver)
    return 0


async def _close_driver(driver) -> None:
    close_result = driver.close()
    if hasattr(close_result, "__await__"):
        await close_result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    project_id = args.project_id or os.path.basename(root)
    project_name = args.project_name or project_id
    analyzer = MyBatisAnalyzer(
        MyBatisAnalyzerConfig(
            root=root,
            project_id=project_id,
            project_name=project_name,
            languages=_languages_from_arg(args.languages),
            cache_dir=args.cache_dir,
            incremental=args.incremental,
            changed_files_manifest=args.changed_files_manifest,
            deleted_files_manifest=args.deleted_files_manifest,
        )
    )
    result = analyzer.analyze()
    artifact_path = args.mybatis_facts_output or default_fact_artifact_path(args.cache_dir, root, project_id)
    dependency_path = args.mybatis_dependency_output or default_dependency_index_path(args.cache_dir, root, project_id)
    write_fact_artifact(artifact_path, result)
    write_dependency_index(dependency_path, result.dependency_index)
    print(
        "[mybatis] modules=%d artifacts=%d parser_capabilities=%d semantic_facts=%d relationships=%d diagnostics=%d artifact=%s"
        % (
            len(result.modules),
            len(result.artifacts),
            len(result.parser_capabilities),
            len(result.semantic_facts),
            len(result.relationships),
            len(result.diagnostics),
            artifact_path,
        )
    )
    if args.dry_run:
        return 0
    return asyncio.run(_write_graph(args, result))


if __name__ == "__main__":
    raise SystemExit(main())
