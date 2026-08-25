from types import SimpleNamespace

from tools.graph.driver.falkordb_driver import _normalize_falkordb_value


def test_node_without_provider_graph_id_does_not_emit_null_graph_id() -> None:
    node = SimpleNamespace(properties={"id": "symbol-a"}, labels={"Function"}, id=None)

    assert _normalize_falkordb_value(node) == {"id": "symbol-a"}


def test_node_with_provider_graph_id_preserves_it() -> None:
    node = SimpleNamespace(properties={"id": "symbol-a"}, labels={"Function"}, id=42)

    assert _normalize_falkordb_value(node) == {"id": "symbol-a", "_graph_id": 42}
