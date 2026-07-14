import unittest

from cortex_harness.dev import FRAMEWORK_ANALYZERS, LANG_ANALYZERS


class DevFrameworkParserDiscoveryTests(unittest.TestCase):
    def test_frameworks_are_listed_separately_from_primary_languages(self):
        self.assertEqual(set(FRAMEWORK_ANALYZERS), {"spring", "servlet_jsp", "mybatis"})
        self.assertTrue(set(FRAMEWORK_ANALYZERS).isdisjoint(LANG_ANALYZERS))
        self.assertTrue(all(path.exists() for path in FRAMEWORK_ANALYZERS.values()))


if __name__ == "__main__":
    unittest.main()
