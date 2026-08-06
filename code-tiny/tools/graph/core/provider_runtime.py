"""Compatibility layer for analyzer graph-provider arguments.

The canonical normalization and driver construction live in
``tools.graph.cli``.  Framework analyzers historically imported the plural
``add_graph_provider_arguments`` name from this module, so keep that surface
without maintaining a second provider implementation.
"""

from __future__ import annotations

import argparse

from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    normalize_graph_provider,
    prepare_graph_args,
)


def add_graph_provider_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the canonical local graph-provider arguments."""

    add_graph_provider_args(parser)


__all__ = [
    "add_graph_provider_arguments",
    "add_graph_provider_args",
    "create_graph_driver_from_args",
    "normalize_graph_provider",
    "prepare_graph_args",
]
