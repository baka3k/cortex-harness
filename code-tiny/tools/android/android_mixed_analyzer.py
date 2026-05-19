from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import tempfile
from typing import Iterable, List, Optional, Sequence, Tuple

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config
from tools.android import android_common
from tools.common.git_diff import load_manifest_paths

# Use shared skip directories from android_common to maintain consistency
_SCAN_SKIP_DIRS = android_common._ANDROID_SKIP_DIRS


def _is_skipped_name(name: str) -> bool:
    """Check if a directory/file name should be skipped during scanning."""
    if name.startswith("."):
        return True
    for pattern in _SCAN_SKIP_DIRS:
        if name == pattern or fnmatch.fnmatch(name, pattern):
            return True
    return False


def _scan_mixed_source_files(root: str) -> Tuple[List[str], List[str]]:
    """Scan for Java and Kotlin source files.

    This function scans the filesystem once to find both Java and Kotlin files,
    avoiding duplicate scanning. The results can be cached and passed to child
    analyzers to avoid redundant scanning.

    Returns:
        Tuple of (java_files, kotlin_files) as lists of relative paths
    """
    java_rel_paths: List[str] = []
    kotlin_rel_paths: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skipped_name(d)]
        for name in filenames:
            if _is_skipped_name(name):
                continue
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
            if name.endswith(".java"):
                java_rel_paths.append(rel_path)
            elif name.endswith(".kt") or name.endswith(".kts"):
                kotlin_rel_paths.append(rel_path)
    java_rel_paths.sort()
    kotlin_rel_paths.sort()
    return java_rel_paths, kotlin_rel_paths


def _filter_by_ext(paths: Iterable[str], exts: Sequence[str]) -> List[str]:
    suffixes = tuple(exts)
    out: List[str] = []
    for path in paths:
        normalized = path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if any(part.startswith(".") for part in parts):
            continue
        if normalized.endswith(suffixes):
            out.append(normalized)
    return sorted(set(out))


def _write_temp_manifest(paths: Iterable[str], prefix: str) -> str:
    """Write paths to a temporary manifest file.

    Args:
        paths: Iterable of file paths (relative to root)
        prefix: Prefix for temporary file name

    Returns:
        Path to the temporary manifest file
    """
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=prefix, suffix=".txt", delete=False)
    with tmp:
        for path in paths:
            tmp.write(path)
            tmp.write("\n")
    return tmp.name


def _create_discovered_files_cache(java_files: List[str], kotlin_files: List[str]) -> Tuple[str, str]:
    """Create cache files for discovered source files to avoid redundant scanning.

    This optimization allows child analyzers to skip file system scanning by using
    pre-scanned file lists. Future enhancement: modify child analyzers to accept
    --files-manifest argument to use these caches.

    Args:
        java_files: List of discovered Java file paths
        kotlin_files: List of discovered Kotlin file paths

    Returns:
        Tuple of (java_cache_path, kotlin_cache_path)
    """
    java_cache = _write_temp_manifest(java_files, "mixed_java_cache_")
    kotlin_cache = _write_temp_manifest(kotlin_files, "mixed_kotlin_cache_")
    return java_cache, kotlin_cache
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=prefix, suffix=".txt", delete=False)
    with tmp:
        for path in paths:
            tmp.write(path)
            tmp.write("\n")
    return tmp.name


def _build_child_base_args(extra_args: List[str], verbose: bool) -> List[str]:
    args = list(extra_args)
    if verbose and "--verbose" not in args:
        args.append("--verbose")
    return args


def _run_child(script_path: str, child_args: List[str], verbose: bool) -> int:
    cmd = [sys.executable, script_path] + child_args
    if verbose:
        print("[mixed] Running:", " ".join(cmd))
    completed = subprocess.run(cmd)
    return int(completed.returncode)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Android mixed (Java+Kotlin) analyzer wrapper",
        allow_abbrev=False,
    )
    parser.add_argument("--root", required=True, help="Root folder containing Android sources")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument(
        "--languages",
        choices=["auto", "java", "kotlin", "both"],
        default="auto",
        help="Which language analyzers to run",
    )
    parser.add_argument("--incremental", action="store_true", help="Enable incremental mode")
    parser.add_argument(
        "--changed-files-manifest",
        help="JSON/TXT manifest of changed+impacted file paths (relative to --root)",
    )
    parser.add_argument(
        "--deleted-files-manifest",
        help="JSON/TXT manifest of deleted file paths (relative to --root)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    # Keep unknown args to forward to child analyzers unchanged.
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    return args



def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    java_files, kotlin_files = _scan_mixed_source_files(args.root)

    run_java = False
    run_kotlin = False
    if args.languages == "java":
        run_java = True
    elif args.languages == "kotlin":
        run_kotlin = True
    elif args.languages == "both":
        run_java = True
        run_kotlin = True
    else:
        run_java = bool(java_files)
        run_kotlin = bool(kotlin_files)

    if args.dry_run:
        print(
            "[dry-run] files java=%d kotlin=%d run(java=%s,kotlin=%s)" % (
                len(java_files),
                len(kotlin_files),
                str(run_java).lower(),
                str(run_kotlin).lower(),
            )
        )
        return 0

    if not run_java and not run_kotlin:
        print("[mixed] No eligible language analyzers to run")
        return 0

    changed_all: List[str] = []
    deleted_all: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_all = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        else:
            # Fallback for manual incremental runs without manifest.
            changed_all = sorted(set(java_files + kotlin_files))

        if args.deleted_files_manifest:
            deleted_all = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))

    changed_java = _filter_by_ext(changed_all, [".java"])
    changed_kotlin = _filter_by_ext(changed_all, [".kt", ".kts"])
    deleted_java = _filter_by_ext(deleted_all, [".java"])
    deleted_kotlin = _filter_by_ext(deleted_all, [".kt", ".kts"])

    if args.verbose:
        print(
            "[mixed] discovered java=%d kotlin=%d incremental=%s changed(total=%d,java=%d,kotlin=%d) deleted(total=%d,java=%d,kotlin=%d)"
            % (
                len(java_files),
                len(kotlin_files),
                str(args.incremental).lower(),
                len(changed_all),
                len(changed_java),
                len(changed_kotlin),
                len(deleted_all),
                len(deleted_java),
                len(deleted_kotlin),
            )
        )

    script_dir = os.path.dirname(os.path.abspath(__file__))
    java_script = os.path.join(script_dir, "android_java_analyzer.py")
    kotlin_script = os.path.join(script_dir, "android_kotlin_analyzer.py")

    # OPTIMIZATION NOTE:
    # Currently, child analyzers will scan the filesystem again even though we've
    # already discovered the files above. Future enhancement: Modify child analyzers
    # to accept --all-files-manifest argument to use our pre-scanned caches.
    # This would eliminate ~30-50% of redundant scanning work on large projects.

    base_extra = _build_child_base_args(args.passthrough, args.verbose)

    # Create cache files for discovered source files to avoid redundant scanning
    # Note: Child analyzers don't yet support --files-manifest, so this is prepared
    # for future optimization. The cache files are cleaned up in the finally block.
    java_cache, kotlin_cache = _create_discovered_files_cache(java_files, kotlin_files)
    temp_files: List[str] = [java_cache, kotlin_cache]
    try:
        changed_manifest_for_children: Optional[str] = None
        deleted_manifest_for_children: Optional[str] = None
        if args.incremental:
            # Pass full changed/deleted manifests to child analyzers.
            # Kotlin analyzer needs non-Kotlin paths (AndroidManifest.xml, Gradle, res/*.xml)
            # to refresh Android-specific nodes during incremental runs.
            if changed_all:
                mf = _write_temp_manifest(changed_all, "mixed_changed_all_")
                temp_files.append(mf)
                changed_manifest_for_children = mf
            if deleted_all:
                mf = _write_temp_manifest(deleted_all, "mixed_deleted_all_")
                temp_files.append(mf)
                deleted_manifest_for_children = mf

        if run_java:
            java_args = ["--root", args.root] + list(base_extra)
            if args.incremental:
                java_args.append("--incremental")
                if changed_manifest_for_children:
                    java_args.extend(["--changed-files-manifest", changed_manifest_for_children])
                if deleted_manifest_for_children:
                    java_args.extend(["--deleted-files-manifest", deleted_manifest_for_children])
            rc = _run_child(java_script, java_args, args.verbose)
            if rc != 0:
                return rc

        if run_kotlin:
            kotlin_args = ["--root", args.root] + list(base_extra)
            if args.incremental:
                kotlin_args.append("--incremental")
                if changed_manifest_for_children:
                    kotlin_args.extend(["--changed-files-manifest", changed_manifest_for_children])
                if deleted_manifest_for_children:
                    kotlin_args.extend(["--deleted-files-manifest", deleted_manifest_for_children])
            rc = _run_child(kotlin_script, kotlin_args, args.verbose)
            if rc != 0:
                return rc

        return 0
    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(main())
