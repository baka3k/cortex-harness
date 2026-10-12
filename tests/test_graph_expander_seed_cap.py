import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(ROOT, "code-tiny")
for path in (CODE_TINY,):
    if path not in sys.path:
        sys.path.insert(0, path)

from tools.common.graph_expander import GraphExpander  # noqa: E402


class _RecordingDriver:
    def __init__(self):
        self.params: list[dict] = []

    def execute_query_sync(self, cypher, params, database):
        self.params.append(dict(params or {}))
        return [], [], None


class GraphExpanderSeedCapTest(unittest.TestCase):
    def test_expand_caps_seed_count_in_cypher_params(self):
        driver = _RecordingDriver()
        expander = GraphExpander(driver, database="hyper_graph")
        seeds = [f"seed-{i}" for i in range(30)]

        with patch.dict(os.environ, {"MCP_GRAPH_EXPANSION_MAX_SEEDS": "12"}):
            expander.expand(seeds, depth=2, include_seeds=False)

        self.assertEqual(len(driver.params), 1)
        sent = driver.params[0]["seed_ids"]
        self.assertEqual(sent, [f"seed-{i}" for i in range(12)])

    def test_expand_below_cap_sends_all_seeds(self):
        driver = _RecordingDriver()
        expander = GraphExpander(driver, database="hyper_graph")

        expander.expand(["seed-a", "seed-b"], depth=2, include_seeds=False)

        self.assertEqual(driver.params[0]["seed_ids"], ["seed-a", "seed-b"])


if __name__ == "__main__":
    unittest.main()
