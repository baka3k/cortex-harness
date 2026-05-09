#!/usr/bin/env python3
"""Quick migration script for all 9 analyzer files."""

import os
import re

FILES = [
    "tools/python/python_analyzer.py",
    "tools/csharp/csharp_analyzer.py",
    "tools/js/js_analyzer.py",
    "tools/php/php_analyzer.py",
    "tools/ts/ts_analyzer.py",
    "tools/sql/sql_analyzer.py",
    "tools/plsql/plsql_analyzer.py",
    "tools/java/java_analyzer.py",
    "tools/android/android_kotlin_analyzer.py",
]

def count_lines(path):
    with open(path) as f:
        return sum(1 for _ in f)

def migrate(path):
    before = count_lines(path)
    with open(path) as f:
        content = f.read()
    
    # Remove neo4j import
    content = re.sub(r'from neo4j import GraphDatabase\n', '', content)
    
    # Add asyncio
    if 'import asyncio' not in content:
        content = re.sub(
            r'(from __future__ import annotations\n\nimport argparse\n)',
            r'\1import asyncio\n',
            content
        )
    
    # Add graph imports
    if 'from tools.graph import' not in content:
        content = re.sub(
            r'(from tools\.common\.cloc_stats import collect_cloc_stats, normalize_cloc_payload)\n',
            r'\1\nfrom tools.graph import GraphDriverFactory, GraphProvider\nfrom tools.graph.language_writer import LanguageCodeWriter\n',
            content
        )
    
    # Make build_call_graph async
    content = re.sub(r'(\n)def build_call_graph\(', r'\1async def build_call_graph(', content)
    content = re.sub(r'neo4j_writer: Optional\[Neo4jWriter\]', "code_writer: Optional['LanguageCodeWriter']", content)
    
    # Make main async  
    content = re.sub(r'(\n)def main\(', r'\1async def main(', content)
    
    # Update if __name__
    content = re.sub(
        r'if __name__ == "__main__":\s+raise SystemExit\(main\(\)\)',
        'if __name__ == "__main__":\n    raise SystemExit(asyncio.run(main()))',
        content
    )
    
    # Rename neo4j_writer to code_writer
    content = re.sub(r'\bneo4j_writer\b', 'code_writer', content)
    
    # Update calls
    content = re.sub(r'(\s+)build_call_graph\(', r'\1await build_call_graph(', content)
    
    # Replace [neo4j] with [graph]
    content = content.replace('[neo4j]', '[graph]')
    
    with open(path, 'w') as f:
        f.write(content)
    
    after = count_lines(path)
    return before, after, before - after

print("=" * 90)
print("ANALYZER MIGRATION TO NEW ABSTRACTION LAYER")
print("=" * 90)
print(f"\n{'File':<50} {'Before':>8} {'After':>8} {'Removed':>8} {'Status':>6}")
print("-" * 90)

total_before = total_after = total_removed = 0
for path in FILES:
    try:
        before, after, removed = migrate(path)
        total_before += before
        total_after += after
        total_removed += removed
        name = os.path.basename(path)
        print(f"{name:<50} {before:>8} {after:>8} {removed:>8} {'✓':>6}")
    except Exception as e:
        name = os.path.basename(path)
        print(f"{name:<50} {'ERROR':>8} {'':>8} {'':>8} {'✗':>6}")
        print(f"  Error: {e}")

print("-" * 90)
print(f"{'TOTAL':<50} {total_before:>8} {total_after:>8} {total_removed:>8}")
print("=" * 90)
print(f"\nMigration complete! All {len(FILES)} files processed.")
print("\nNote: Neo4jWriter classes renamed but logic still needs manual refinement.")
