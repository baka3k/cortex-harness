"""File-system utilities for TypeScript source scanning.

Constants and helpers for:
- Which directories to skip during scanning
- Which file extensions to include
- Screen/service directory detection
- File path → route normalisation
"""
from __future__ import annotations

import os
from typing import List, Optional

# ─── Parse-cache version (bump when payload schema changes) ──────────────────
_PARSE_CACHE_VERSION = "ts-v2026-04-06-6"

# ─── Source extensions ────────────────────────────────────────────────────────
_TS_SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")

# ─── Directories excluded from recursive scanning ─────────────────────────────
_SCAN_SKIP_DIRS = {
    # Version control
    ".git", ".hg", ".svn",
    # Node.js package manager
    "node_modules",
    # Build outputs
    "dist", "build", "out", ".next", ".nuxt", ".output",
    # TypeScript cache
    ".cache", ".parcel-cache", "__pycache__",
    # Testing
    "coverage", ".nyc_output", "test-results", ".test-results",
    # IDE
    ".idea", ".vscode",
    # Temporary
    "tmp", "temp", ".tmp", "tmpdir",
    # OS specific
    ".DS_Store", "Thumbs.db",
    # Build artifacts
    "target", ".serverless",
}

# ─── Directory segments for role detection ────────────────────────────────────
# NOTE: "navigation" is intentionally excluded — a navigation/ folder contains
# navigator components (AppNavigator, HomeStack …), NOT screens.
_SCREEN_DIR_SEGMENTS = {"screens", "screen", "pages", "page", "views", "routes", "route"}

_SERVICE_DIR_SEGMENTS = {
    "api", "apis", "services", "service", "middleware",
    "http", "network", "repository", "repositories",
}

# Basenames that are index modules — the parent folder is the real module name
_INDEX_BASENAMES = {"index.ts", "index.tsx", "index.js", "index.jsx"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _index_module_name(file_path: str) -> Optional[str]:
    """If the file is an index.{ts,tsx,js,jsx}, return the immediate parent folder name.

    Example: 'src/screens/Login/index.tsx'  -> 'Login'
             'src/components/Button/index.ts' -> 'Button'
    Returns None for non-index files or index files at the repository root.
    """
    parts = file_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[-1] in _INDEX_BASENAMES:
        return parts[-2]
    return None


def _is_screen_file(file_path: str) -> bool:
    """Return True if any path segment indicates a screens/pages/views directory."""
    segments = file_path.replace("\\", "/").split("/")
    return any(seg.lower() in _SCREEN_DIR_SEGMENTS for seg in segments)


def _is_service_file(file_path: str) -> bool:
    """Return True if any path segment indicates a service/api/middleware directory."""
    segments = file_path.replace("\\", "/").split("/")
    return any(seg.lower() in _SERVICE_DIR_SEGMENTS for seg in segments)


def _scan_ts_files(root: str) -> List[str]:
    """Recursively collect TypeScript source files under *root*."""
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SCAN_SKIP_DIRS]
        for name in filenames:
            if name.endswith(_TS_SOURCE_EXTENSIONS):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def _file_path_to_route(file_path: str) -> Optional[str]:
    """Normalize an Expo Router / Next.js App Router file path to a route string.

    Examples:
    - app/home.tsx          → /home
    - app/(tabs)/profile.tsx → /profile   (route group stripped)
    - app/(auth)/login/index.tsx → /login
    - pages/about.tsx       → /about
    """
    parts = file_path.replace("\\", "/").split("/")
    for i, seg in enumerate(parts):
        if seg in ("app", "pages") and i < len(parts) - 1:
            route_parts = parts[i + 1:]
            last = route_parts[-1] if route_parts else ""
            last_stem = last.rsplit(".", 1)[0] if "." in last else last
            if last_stem == "index":
                route_parts = route_parts[:-1]
            else:
                route_parts[-1] = last_stem
            # Strip route groups like (tabs), (auth)
            route_parts = [p for p in route_parts if not (p.startswith("(") and p.endswith(")"))]
            if route_parts:
                return "/" + "/".join(route_parts)
    return None
