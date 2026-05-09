import argparse
import os
import re
import sys

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError


def get_env(name, default=None):
    return os.getenv(name, default)


def require_token(label, value, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"):
    if not value or not re.match(pattern, value):
        print(f"Invalid {label}: {value}", file=sys.stderr)
        sys.exit(2)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run GDS Louvain on Function nodes and materialize InfraNode communities."
    )
    parser.add_argument("--neo4j-uri", default=get_env("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=get_env("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=get_env("NEO4J_PASS"))

    parser.add_argument("--project-id", default=get_env("PROJECT_ID"))
    parser.add_argument("--graph-name", default=get_env("GDS_GRAPH_NAME", "functionGraph"))
    parser.add_argument("--node-label", default=get_env("NODE_LABEL", "Function"))
    parser.add_argument("--rel-type", default=get_env("REL_TYPE", "CALLS"))
    parser.add_argument("--orientation", default=get_env("ORIENTATION", "UNDIRECTED"))
    parser.add_argument("--write-property", default=get_env("WRITE_PROPERTY", "communityId"))

    parser.add_argument("--min-community-size", type=int, default=int(get_env("MIN_COMMUNITY_SIZE", "4")))
    parser.add_argument("--infra-label", default=get_env("INFRA_LABEL", "InfraNode"))
    parser.add_argument("--infra-id-field", default=get_env("INFRA_ID_FIELD", "id"))
    parser.add_argument("--infra-status", default=get_env("INFRA_STATUS", "pending_summary"))
    parser.add_argument("--belongs-rel", default=get_env("BELONGS_REL", "BELONGS_TO"))

    parser.add_argument(
        "--drop-graph",
        default=get_env("DROP_GRAPH", "0"),
        help="Set to 1 to drop existing in-memory graph before projecting.",
    )
    parser.add_argument(
        "--drop-after",
        default=get_env("DROP_AFTER", "0"),
        help="Set to 1 to drop the in-memory graph after Louvain finishes.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()
    missing = []
    if not args.neo4j_uri:
        missing.append("NEO4J_URI/--neo4j-uri")
    if not args.neo4j_user:
        missing.append("NEO4J_USER/--neo4j-user")
    if not args.NEO4J_PASS:
        missing.append("NEO4J_PASS/--neo4j-pass")
    if missing:
        print("Missing required options: " + ", ".join(missing), file=sys.stderr)
        sys.exit(2)

    require_token("node label", args.node_label)
    require_token("relationship type", args.rel_type)
    require_token("write property", args.write_property)
    require_token("infra label", args.infra_label)
    require_token("infra id field", args.infra_id_field)
    require_token("belongs relationship", args.belongs_rel)
    require_token("graph name", args.graph_name, pattern=r"^[A-Za-z0-9_]+$")

    orientation = args.orientation.upper()
    if orientation not in ("UNDIRECTED", "NATURAL", "REVERSE"):
        print("Invalid orientation. Use UNDIRECTED, NATURAL, or REVERSE.", file=sys.stderr)
        sys.exit(2)
    args.orientation = orientation

    if args.min_community_size < 1:
        print("min-community-size must be >= 1", file=sys.stderr)
        sys.exit(2)
    return args


def gds_graph_exists(session, graph_name):
    try:
        record = session.run(
            "CALL gds.graph.exists($name) YIELD exists", {"name": graph_name}
        ).single()
        return bool(record and record.get("exists"))
    except ClientError as exc:
        if "ProcedureNotFound" not in str(exc):
            raise

    try:
        record = session.run(
            """
            CALL gds.graph.list()
            YIELD graphName
            WHERE graphName = $name
            RETURN count(graphName) AS count
            """,
            {"name": graph_name},
        ).single()
        return bool(record and record.get("count"))
    except ClientError as exc:
        if "ProcedureNotFound" in str(exc):
            print(
                "GDS procedures not found. Please install/enable the Neo4j GDS plugin.",
                file=sys.stderr,
            )
            sys.exit(2)
        raise


def ensure_graph(
    session,
    graph_name,
    node_label,
    rel_type,
    orientation,
    drop_graph,
    project_id,
    verbose: bool = False,
):
    exists = gds_graph_exists(session, graph_name)
    if exists and drop_graph:
        session.run("CALL gds.graph.drop($name) YIELD graphName", {"name": graph_name})
        exists = False

    if not exists:
        if project_id:
            where_clause = f"n.project_id CONTAINS $project_id AND m.project_id CONTAINS $project_id"
            node_query = f"""
            MATCH (n:{node_label})
            WHERE n.project_id CONTAINS $project_id
            RETURN id(n) AS id
            """
            if orientation == "REVERSE":
                rel_query = f"""
                MATCH (n:{node_label})-[r:{rel_type}]->(m:{node_label})
                WHERE {where_clause}
                RETURN id(m) AS source, id(n) AS target
                """
            elif orientation == "UNDIRECTED":
                rel_query = f"""
                MATCH (n:{node_label})-[r:{rel_type}]->(m:{node_label})
                WHERE {where_clause}
                RETURN id(n) AS source, id(m) AS target
                UNION ALL
                MATCH (n:{node_label})-[r:{rel_type}]->(m:{node_label})
                WHERE {where_clause}
                RETURN id(m) AS source, id(n) AS target
                """
            else:
                rel_query = f"""
                MATCH (n:{node_label})-[r:{rel_type}]->(m:{node_label})
                WHERE {where_clause}
                RETURN id(n) AS source, id(m) AS target
                """
            query = """
            CALL gds.graph.project.cypher(
              $graph_name,
              $node_query,
              $relationship_query,
              $config
            )
            """
            config = {"parameters": {"project_id": project_id}}
            session.run(
                query,
                {
                    "graph_name": graph_name,
                    "node_query": node_query,
                    "relationship_query": rel_query,
                    "config": config,
                },
            )
            if verbose:
                print(f"[gds] Projected cypher graph '{graph_name}' for project_id='{project_id}'")
        else:
            query = f"""
            CALL gds.graph.project(
              $graph_name,
              '{node_label}',
              {{
                {rel_type}: {{
                  type: '{rel_type}',
                  orientation: '{orientation}'
                }}
              }}
            )
            """
            session.run(query, {"graph_name": graph_name})
            if verbose:
                print(f"[gds] Projected graph '{graph_name}' nodes={node_label} rel={rel_type} orientation={orientation}")
        return "created"
    return "reused"


def run_louvain(session, graph_name, write_property):
    query = """
    CALL gds.louvain.write(
      $graph_name,
      { writeProperty: $write_property }
    )
    YIELD communityCount, modularity, ranLevels
    """
    return session.run(query, {"graph_name": graph_name, "write_property": write_property}).single()


# def materialize_infra(
#     session,
#     node_label,
#     community_property,
#     infra_label,
#     infra_id_field,
#     min_size,
#     status,
#     belongs_rel,
#     project_id,
# ):
#     where_clause = f"f.{community_property} IS NOT NULL"
#     if project_id:
#         where_clause += " AND f.project_id CONTAINS $project_id"
#     query = f"""
#     MATCH (f:{node_label})
#     WHERE {where_clause}
#     WITH f.{community_property} AS cid, collect(f) AS functions
#     WHERE size(functions) >= $min_size
#     MERGE (infra:{infra_label} {{ {infra_id_field}: toString(cid) }})
#     SET infra.status = coalesce(infra.status, $status)
#     FOREACH (func IN functions |
#       MERGE (func)-[:{belongs_rel}]->(infra)
#     )
#     RETURN count(DISTINCT infra) AS infra_nodes, count(DISTINCT cid) AS communities
#     """
#     return session.run(
#         query,
#         {"min_size": min_size, "status": status, "project_id": project_id},
#     ).single()
def materialize_infra(
    session,
    node_label,
    community_property,
    infra_label,
    infra_id_field,
    min_size,
    status,
    belongs_rel,
    project_id,
    verbose: bool = False,
):
    where_clause = f"f.{community_property} IS NOT NULL"
    if project_id:
        where_clause += " AND f.project_id CONTAINS $project_id"
    # InfraNode id includes project_id prefix to avoid cross-project collisions.
    # e.g. "digital_key_main:4441" instead of bare "4441".
    infra_id_expr = (
        f"$project_id + ':' + toString(cid)"
        if project_id
        else f"toString(cid)"
    )
    query = f"""
    MATCH (f:{node_label})
    WHERE {where_clause}
    WITH f.{community_property} AS cid, collect(f) AS functions
    WHERE size(functions) >= $min_size

    // 1. Tạo/Merge InfraNode scoped by project
    MERGE (infra:{infra_label} {{ {infra_id_field}: {infra_id_expr} }})
    SET infra.status = coalesce(infra.status, $status),
        infra.project_id = coalesce(infra.project_id, $project_id),
        infra.community_id = cid

    WITH infra, functions, cid
    UNWIND functions AS func

    // 2. Xóa liên kết cũ của function này (đảm bảo 1 function chỉ thuộc 1 InfraNode)
    OPTIONAL MATCH (func)-[old_rel:{belongs_rel}]->(:{infra_label})
    DELETE old_rel

    // 3. Tạo liên kết mới
    WITH infra, func, cid
    MERGE (func)-[:{belongs_rel}]->(infra)

    // 4. Aggregate lại kết quả để return
    RETURN count(DISTINCT infra) AS infra_nodes, count(DISTINCT cid) AS communities
    """
    if verbose:
        print(f"[infra] Materializing infra nodes: min_size={min_size} status={status} project_id={project_id}")
    res = session.run(
        query,
        {"min_size": min_size, "status": status, "project_id": project_id},
    ).single()
    if verbose and res:
        print(f"[infra] Materialize result: infra_nodes={res.get('infra_nodes')} communities={res.get('communities')}")
    return res

def drop_graph(session, graph_name):
    session.run("CALL gds.graph.drop($name) YIELD graphName", {"name": graph_name})


def main():
    args = parse_args()
    drop_graph_flag = str(args.drop_graph) != "0"
    drop_after = str(args.drop_after) != "0"

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.NEO4J_PASS))
    try:
        with driver.session() as session:
            action = ensure_graph(
                session,
                args.graph_name,
                args.node_label,
                args.rel_type,
                args.orientation,
                drop_graph_flag,
                args.project_id,
                verbose=args.verbose,
            )
            print(f"GDS graph {args.graph_name}: {action}")

            result = run_louvain(session, args.graph_name, args.write_property)
            if result:
                print(
                    "Louvain: communities=%s modularity=%s levels=%s"
                    % (result.get("communityCount"), result.get("modularity"), result.get("ranLevels"))
                )

            materialized = materialize_infra(
                session,
                args.node_label,
                args.write_property,
                args.infra_label,
                args.infra_id_field,
                args.min_community_size,
                args.infra_status,
                args.belongs_rel,
                args.project_id,
                verbose=args.verbose,
            )
            if materialized:
                print(
                    "InfraNodes: %s communities=%s"
                    % (materialized.get("infra_nodes"), materialized.get("communities"))
                )

            if drop_after:
                drop_graph(session, args.graph_name)
                print(f"GDS graph {args.graph_name}: dropped")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
