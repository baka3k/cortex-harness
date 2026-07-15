import unittest

from cortex_harness.dev import FRAMEWORK_ANALYZERS, LANG_ANALYZERS


class DevFrameworkParserDiscoveryTests(unittest.TestCase):
    def test_frameworks_are_listed_separately_from_primary_languages(self):
        self.assertEqual(
            set(FRAMEWORK_ANALYZERS),
            {
                "aspnet_core",
                "aspnet_framework",
                "spring",
                "servlet_jsp",
                "mybatis",
                "struts",
                "flutter",
            },
        )
        self.assertTrue(set(FRAMEWORK_ANALYZERS).isdisjoint(LANG_ANALYZERS))
        self.assertTrue(all(path.exists() for path in FRAMEWORK_ANALYZERS.values()))


if __name__ == "__main__":
    unittest.main()
