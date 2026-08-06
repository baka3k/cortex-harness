import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Legacy Neo4j/OpenAI compatibility values. Local Qdrant and FalkorDB paths
# are resolved by the active adapters rather than this import-only module.
neo4j_uri = os.getenv("NEO4J_URI")
neo4j_username = os.getenv("NEO4J_USERNAME")
neo4j_password = os.getenv("NEO4J_PASS")
openai_key = os.getenv("OPENAI_API_KEY")

# This compatibility module intentionally has no import-time output.  Earlier
# versions printed credentials and API keys verbatim, which was both unsafe
# and surprising for callers that merely imported the legacy loader.
