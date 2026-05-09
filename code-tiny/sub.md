# Reset data (Neo4j + Qdrant + local cache)

1) Remove local cache

```powershell
Remove-Item -Recurse -Force .cache
```

2) Clear Neo4j database (default `neo4j`)

```powershell
cypher-shell -u neo4j -p "abcd1234" -d neo4j "MATCH (n) DETACH DELETE n"
MATCH (n) DETACH DELETE n;
```

3) Delete Qdrant collections

```powershell
Invoke-RestMethod -Method Delete -Uri "http://localhost:6333/collections/kotlin_functions"
Invoke-RestMethod -Method Delete -Uri "http://localhost:6333/collections/java_functions"
Invoke-RestMethod -Method Delete -Uri "http://localhost:6333/collections/cplus_functions"
Invoke-RestMethod -Method Delete -Uri "http://localhost:6333/collections/csharp_functions"
```
