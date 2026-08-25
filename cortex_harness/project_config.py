"""Locate the project configuration used by MCP launchers."""

from __future__ import annotations

from pathlib import Path


DEV_CONFIG = Path(".cortext-harness") / "config" / "dev.json"


def resolve_start_config(start: Path, fallback_root: Path) -> tuple[Path, Path]:
    """Return the nearest ``dev.json`` and fall back to the harness install."""
    current = Path(start).absolute()
    if current.is_file():
        current = current.parent
    for candidate_root in (current, *current.parents):
        config_path = candidate_root / DEV_CONFIG
        if config_path.is_file():
            return candidate_root, config_path

    root = Path(fallback_root).absolute()
    return root, root / DEV_CONFIG
