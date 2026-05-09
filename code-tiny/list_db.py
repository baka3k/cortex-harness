
import os
from neo4j import GraphDatabase

driver = GraphDatabase.driver(os.environ['NEO4J_URI'], auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']))
with driver.session() as session:
    result = session.run('SHOW DATABASES')
    databases = [record['name'] for record in result]
    print(databases)
driver.close()