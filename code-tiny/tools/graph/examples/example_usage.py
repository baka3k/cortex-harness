"""
Example: How to refactor an existing analyzer to use the new abstraction layer

This file demonstrates the migration from old hardcoded Neo4j pattern
to the new abstraction layer.
"""

# ============================================================================
# OLD PATTERN (Before Abstraction)
# ============================================================================

def old_kotlin_analyzer():
    """Old way - hardcoded Neo4j driver"""
    from neo4j import GraphDatabase
    
    # Hardcoded connection
    driver = GraphDatabase.driver(
        "bolt://localhost:7687",
        auth=("neo4j", "password")
    )
    
    # Direct Cypher queries scattered everywhere
    with driver.session(database="neo4j") as session:
        # Create function node
        session.run("""
            CREATE (f:Function {
                id: $id,
                name: $name,
                code: $code
            })
        """, id="func_1", name="myFunc", code="fun myFunc() {}")
        
        # Create call relationship
        session.run("""
            MATCH (caller:Function {id: $caller_id})
            MATCH (callee:Function {id: $callee_id})
            MERGE (caller)-[:CALLS]->(callee)
        """, caller_id="func_1", callee_id="func_2")
    
    driver.close()


# ============================================================================
# NEW PATTERN (After Abstraction)
# ============================================================================

import asyncio
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.operations import (
    FunctionNodeOperations,
    DocumentNodeOperations,
    InfraNodeOperations,
    CrossEdgeOperations
)


async def new_kotlin_analyzer():
    """New way - using abstraction layer"""
    
    # 1. Create driver through factory (env variables or config)
    driver = GraphDriverFactory.create_from_env(GraphProvider.NEO4J)
    
    # Or from explicit config:
    # config = {
    #     "uri": "bolt://localhost:7687",
    #     "user": "neo4j", 
    #     "password": "password",
    #     "database": "neo4j"
    # }
    # driver = GraphDriverFactory.create_driver(GraphProvider.NEO4J, config)
    
    try:
        # 2. Use semantic operations instead of raw queries
        func_ops = FunctionNodeOperations()
        
        # Create function node (semantic, not Cypher)
        function_id = await func_ops.create_function_node(
            driver,
            {
                "id": "func_1",
                "name": "myFunc",
                "qualified_name": "com.example.MainActivity.myFunc",
                "code": "fun myFunc() { println(\"Hello\") }",
                "language": "kotlin",
                "file_path": "MainActivity.kt",
                "start_line": 10,
                "end_line": 12,
                "comment": "// Main function",
                "summary": ""  # Will be filled by LLM later
            }
        )
        
        # Create function call relationship
        await func_ops.link_function_call(
            driver,
            caller_id="func_1",
            callee_id="func_2",
            call_data={"line_number": 11}
        )
        
        # Batch create multiple functions (more efficient)
        functions = [
            {
                "id": f"func_{i}",
                "name": f"function{i}",
                "qualified_name": f"com.example.Class.function{i}",
                "code": f"fun function{i}() {{}}",
                "language": "kotlin",
                "file_path": "Class.kt",
                "start_line": i * 10,
                "end_line": i * 10 + 2,
            }
            for i in range(10)
        ]
        count = await func_ops.batch_create_functions(driver, functions)
        print(f"Created {count} function nodes")
        
        # 3. Work with documentation
        doc_ops = DocumentNodeOperations()
        
        doc_id = await doc_ops.create_document_node(
            driver,
            {
                "id": "doc_readme",
                "title": "README",
                "file_path": "README.md",
                "content": "# Project Documentation\nThis is...",
                "doc_type": "readme"
            }
        )
        
        # Link code to documentation
        cross_ops = CrossEdgeOperations()
        await cross_ops.link_code_to_document(
            driver,
            code_id="func_1",
            document_id=doc_id,
            link_type="DOCUMENTED_BY",
            confidence=0.9
        )
        
        # 4. Infrastructure operations (Phase 3)
        infra_ops = InfraNodeOperations()
        
        # Run community detection
        communities = await infra_ops.run_louvain_clustering(
            driver,
            label="Function",
            relationship="CALLS",
            min_community_size=3
        )
        
        # Create infrastructure nodes for each community
        for community in communities:
            infra_id = await infra_ops.create_infra_node(
                driver,
                {
                    "id": f"module_{community['communityId']}",
                    "name": f"Module {community['communityId']}",
                    "type": "module",
                    "description": "Auto-detected module",
                    "module_path": "com.example",
                    "cohesion_score": 0.0,
                    "coupling_score": 0.0,
                    "status": "pending_summary"
                }
            )
            
            # Link functions to module
            for member in community['members']:
                await infra_ops.link_node_to_infra(
                    driver,
                    node_id=member['id'],
                    infra_id=infra_id
                )
        
        # 5. Query operations
        # Get functions needing summary
        pending_functions = await func_ops.get_functions_without_summary(
            driver,
            limit=50
        )
        
        print(f"Found {len(pending_functions)} functions needing summary")
        
        # Get function call graph
        call_graph = await func_ops.get_function_calls(
            driver,
            function_id="func_1",
            direction="outgoing",
            max_depth=2
        )
        
        for call in call_graph:
            print(f"  -> {call['related_name']} (depth: {call['depth']})")
        
        # Verify connection
        is_connected = await driver.verify_connection()
        print(f"Database connected: {is_connected}")
        
        # Get statistics
        node_count = await driver.get_node_count(label="Function")
        edge_count = await driver.get_edge_count(relationship_type="CALLS")
        print(f"Functions: {node_count}, Calls: {edge_count}")
        
    finally:
        # Always close the driver
        driver.close()


# ============================================================================
# MIGRATION CHECKLIST
# ============================================================================

"""
To migrate an existing analyzer file (e.g., kotlin_analyzer.py):

1. Replace imports:
   OLD: from neo4j import GraphDatabase
   NEW: from tools.graph import GraphDriverFactory, GraphProvider
        from tools.graph.operations import FunctionNodeOperations

2. Replace driver creation:
   OLD: driver = GraphDatabase.driver(uri, auth=(user, password))
   NEW: driver = GraphDriverFactory.create_from_env(GraphProvider.NEO4J)

3. Replace raw Cypher with semantic operations:
   OLD: session.run("CREATE (f:Function {...})", ...)
   NEW: await FunctionNodeOperations.create_function_node(driver, {...})

4. Replace direct session usage:
   OLD: with driver.session() as session:
            session.run(query, params)
   NEW: await driver.execute_query(query, params)
        # Or better: use operation classes

5. Add async/await:
   OLD: def analyze_kotlin_file(...)
   NEW: async def analyze_kotlin_file(...)

6. Benefits you get:
   - Database independence (can switch to Kuzu later)
   - Better testability (mock drivers)
   - Cleaner code (semantic operations vs raw Cypher)
   - Centralized query logic
   - Type safety
   - Better separation of concerns
"""


# ============================================================================
# USAGE IN MCP SERVER
# ============================================================================

async def mcp_server_example():
    """How to use abstraction in MCP server"""
    from fastmcp import FastMCP
    
    mcp = FastMCP("Kotlin Code Analyzer")
    
    # Initialize driver once at startup
    driver = GraphDriverFactory.create_from_env(GraphProvider.NEO4J)
    func_ops = FunctionNodeOperations()
    
    @mcp.tool()
    async def analyze_kotlin_code(file_path: str) -> str:
        """Analyze Kotlin code and store in graph"""
        # Your parsing logic here...
        functions = parse_kotlin_file(file_path)
        
        # Store using operations
        for func in functions:
            await func_ops.create_function_node(driver, func)
        
        return f"Analyzed {len(functions)} functions"
    
    @mcp.tool()
    async def get_call_graph(function_name: str) -> dict:
        """Get call graph for a function"""
        # Query using operations
        calls = await func_ops.get_function_calls(
            driver,
            function_id=function_name,
            direction="both",
            max_depth=3
        )
        
        return {"function": function_name, "calls": calls}
    
    # Clean up on shutdown
    @mcp.on_shutdown
    async def cleanup():
        driver.close()


def parse_kotlin_file(file_path: str):
    """Placeholder for actual parsing logic"""
    return []


# ============================================================================
# RUN EXAMPLE
# ============================================================================

if __name__ == "__main__":
    print("Running new abstraction layer example...")
    asyncio.run(new_kotlin_analyzer())
    print("Done!")
