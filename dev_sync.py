#!/usr/bin/env python3
"""
Dev sync command - Auto tracking incremental sync state.

Lần 1: Full scan → lưu state
Lần 2+: Auto detect changes → incremental sync → update state
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent / "code-tiny" / "tools" / "sync"
INCREMENTAL_SYNC = SCRIPT_DIR / "incremental_sync.py"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dev sync: Auto incremental sync with state tracking",
        epilog="""
Examples:
  # Lần 1: Full scan toàn bộ project
  python dev_sync.py --root .

  # Lần 2+: Chỉ scan phần thay đổi (auto detect)
  python dev_sync.py --root .

  # Force full scan lại từ đầu
  python dev_sync.py --root . --reset

  # Xem state hiện tại
  python dev_sync.py --root . --status
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    parser.add_argument("--project-id", help="Project ID (default: basename of root)")
    parser.add_argument("--project-name", help="Project name (default: project_id)")
    parser.add_argument("--reset", action="store_true", help="Reset state và force full scan")
    parser.add_argument("--status", action="store_true", help="Show current sync state")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without actually syncing")

    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: Root not found: {root}", file=sys.stderr)
        return 1

    # Build incremental_sync command
    cmd = [
        sys.executable,
        str(INCREMENTAL_SYNC),
        "--root", str(root),
    ]

    if args.project_id:
        cmd.extend(["--project-id", args.project_id])
    if args.project_name:
        cmd.extend(["--project-name", args.project_name])

    # Reset state: Force scan from HEAD^ (parent of HEAD)
    if args.reset:
        cmd.extend(["--before-sha", "HEAD^", "--after-sha", "HEAD"])

    if args.verbose:
        cmd.append("--verbose")

    if args.dry_run:
        # For dry run, we'd need to add this flag to incremental_sync
        # For now, just show the command
        print("Would run:")
        print(" ".join(cmd))
        return 0

    # Show status
    if args.status:
        return show_status(root, args.project_id, args.verbose)

    # Run incremental sync
    print(f"[dev_sync] Starting sync for: {root}")
    print(f"[dev_sync] Using state: {'RESET (full scan)' if args.reset else 'AUTO'}")
    print()

    result = subprocess.run(cmd, check=False)
    return result.returncode


def show_status(root: Path, project_id: str | None, verbose: bool) -> int:
    """Show current sync state"""
    import json
    import os

    project_id = project_id or root.name
    state_file = find_state_file(root, project_id)

    if not state_file or not state_file.exists():
        print(f"[dev_sync] No sync state found for project: {project_id}")
        print(f"[dev_sync] First run will do a full scan")
        return 0

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        print(f"[dev_sync] Project: {state.get('project_id', project_id)}")
        print(f"[dev_sync] Root: {state.get('root', root)}")
        print(f"[dev_sync] Last successful SHA: {state.get('last_good_sha', 'N/A')}")
        print(f"[dev_sync] Dirty (has error): {state.get('dirty', False)}")
        print(f"[dev_sync] Last error: {state.get('last_error', 'None') or 'None'}")
        print(f"[dev_sync] Last run: {state.get('last_run_before', '?')} → {state.get('last_run_after', '?')}")
        print(f"[dev_sync] Updated at: {state.get('updated_at', 'N/A')}")

        if verbose:
            print(f"\n[dev_sync] State file: {state_file}")
            import subprocess
            try:
                current_sha = subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    text=True, stderr=subprocess.DEVNULL
                ).strip()
                last_sha = state.get('last_good_sha', '')
                if last_sha:
                    behind = subprocess.check_output(
                        ["git", "-C", str(root), "rev-list", "--count", f"{last_sha}..HEAD"],
                        text=True, stderr=subprocess.DEVNULL
                    ).strip()
                    print(f"[dev_sync] Commits behind: {behind}")
            except:
                pass

        return 1 if state.get('dirty', False) else 0

    except Exception as e:
        print(f"[dev_sync] Error reading state: {e}", file=sys.stderr)
        return 1


def find_state_file(root: Path, project_id: str) -> Path | None:
    """Find state file for project"""
    # Try cache directories
    cache_candidates = [
        root / ".cache" / "incremental_sync" / f"{project_id}.json",
        Path.home() / ".cache" / "cortex-harness" / "incremental_sync" / f"{project_id}.json",
    ]

    for candidate in cache_candidates:
        if candidate.exists():
            return candidate

    # Check if cache dir exists
    cache_root = root / ".cache" / "incremental_sync"
    if cache_root.exists():
        for state_file in cache_root.glob("*.json"):
            return state_file

    return None


if __name__ == "__main__":
    sys.exit(main())
