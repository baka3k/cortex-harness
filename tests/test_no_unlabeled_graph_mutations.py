from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "code-tiny" / "tools"

_MUTATION = re.compile(r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|FOREACH)\b", re.I)
_UNLABELED_IDENTITY = re.compile(
    r"\b(?:MATCH|MERGE)\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\{\{?\s*"
    r"(?:id|symbol_id)\s*:",
    re.I,
)


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    return None


class NoUnlabeledGraphMutationTests(unittest.TestCase):
    def test_mutating_cypher_never_uses_an_unlabeled_identity_lookup(self) -> None:
        violations = []
        for path in sorted(TOOLS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                value = _string_value(node)
                if not value or not _MUTATION.search(value):
                    continue
                if _UNLABELED_IDENTITY.search(value):
                    violations.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}")
        self.assertEqual(
            violations,
            [],
            "mutating Cypher must use a concrete endpoint label: " + ", ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
