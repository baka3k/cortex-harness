#!/usr/bin/env python3
"""Python-vs-Rust storage stress parity comparison (Phase 10 Scope B).

Runs the same four stress scenarios through the reference Python storage
layer (`cortex_harness.storage`, harness venv) and through the Rust port
(`rust/crates/cortex-storage/tests/stress.rs`), then asserts matching
behavior:

1. Lease race: N=8 processes race for the same instance lease -> exactly one
   winner; losers fail immediately with a conflict error (no wait).
2. Generation swap with pinned readers: the pinned reader keeps observing the
   old generation; retire is refused while pinned, allowed after unpin.
3. BoundedLane saturation + deadline: OVERLOADED / DEADLINE_EXCEEDED with the
   same codes, retry hint, and counters.
4. Kill -9 during lease: the expired lease is recoverable and the stale
   metadata is observable before the reclaim.

Usage: .venv/bin/python scripts/rust_parity/storage_stress.py [--skip-cargo]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cortex_harness.storage import GenerationManager  # noqa: E402
from cortex_harness.storage.admission import BoundedLane, LaneLimits  # noqa: E402
from cortex_harness.storage.contracts import (  # noqa: E402
    GenerationState,
    GatewayErrorCode,
    PhysicalTargetKey,
    StoreGatewayError,
)
from cortex_harness.storage.lease import (  # noqa: E402
    StorageLease,
    StorageLeaseConflictError,
)

RACE_PROCESSES = 8

RACE_CHILD_SCRIPT = """
import sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from cortex_harness.storage.lease import StorageLease, StorageLeaseConflictError

lock = sys.argv[1]
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.001)
start = time.monotonic()
lease = StorageLease(Path(lock), instance_id="race-instance", owner_id="race-owner", backend="falkordb")
try:
    handle = lease.acquire()
    # The winner holds the lease across the whole race window so the other
    # racers observe the conflict (acquire is nonblocking, mirroring the
    # portalocker timeout=0 contract).
    time.sleep(3.0)
    handle.close() if hasattr(handle, "close") else lease.release()
    print("RESULT WINNER")
except StorageLeaseConflictError as exc:
    print(f"RESULT CONFLICT {{exc}} {{time.monotonic()-start:.3f}}")
""".format(root=str(REPO_ROOT))

HOLD_CHILD_SCRIPT = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from cortex_harness.storage.lease import StorageLease

lock = sys.argv[1]
lease = StorageLease(Path(lock), instance_id="stress", owner_id="code", backend="falkordb")
handle = lease.acquire()
print("RESULT HELD " + json.dumps(lease.metadata, sort_keys=True), flush=True)
time.sleep(120)
handle.close() if hasattr(handle, "close") else lease.release()
""".format(root=str(REPO_ROOT))

results: dict[str, dict] = {}


def scenario_lease_race() -> dict:
    with tempfile.TemporaryDirectory(prefix="cortex-py-lease-race-") as tmp:
        lock = str(Path(tmp) / "race.rdb")
        go = Path(tmp) / "GO"
        children = [
            subprocess.Popen(
                [sys.executable, "-c", RACE_CHILD_SCRIPT, lock, str(go)],
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(RACE_PROCESSES)
        ]
        # Release all racers at once so their acquisitions genuinely race.
        time.sleep(0.2)
        go.write_text("go")
        winners = conflicts = 0
        immediate = True
        for child in children:
            out = child.communicate(timeout=60)[0]
            for line in out.splitlines():
                if "RESULT WINNER" in line:
                    winners += 1
                elif "RESULT CONFLICT" in line:
                    conflicts += 1
                    elapsed = float(line.split()[-1])
                    if elapsed > 2.0:
                        immediate = False
        assert winners == 1, f"expected exactly 1 winner, got {winners}"
        assert conflicts == RACE_PROCESSES - 1, f"expected {RACE_PROCESSES - 1} conflicts, got {conflicts}"
        assert immediate, "losers must fail immediately (nonblocking portalocker)"
        return {
            "processes": RACE_PROCESSES,
            "winners": winners,
            "losers": conflicts,
            "loser_behavior": "immediate-conflict-error",
        }


def scenario_generation_swap() -> dict:
    async def run() -> dict:
        with tempfile.TemporaryDirectory(prefix="cortex-py-gen-swap-") as tmp:
            root = Path(tmp)
            target = PhysicalTargetKey.from_paths(
                instance_id="stress",
                owner_id="code",
                graph_path=root / "graph.rdb",
                vector_path=root / "vectors",
            )
            manager = GenerationManager(root / "generations", target)

            first = manager.allocate("revision-1", generation_id="generation-1")
            published_first = manager.publish(first, lambda _: None)
            assert published_first.state is GenerationState.PUBLISHED

            # Pin a reader to the old generation.
            with manager.pin_active() as pin:
                assert pin.generation_id == "generation-1"
                assert manager.reference_count("generation-1") == 1

                # Swap the store underneath the pinned reader.
                second = manager.allocate("revision-2", generation_id="generation-2")
                published_second = manager.publish(second, lambda _: None)

                assert pin.generation_id == "generation-1", (
                    "pinned reader must keep observing the old generation"
                )
                with manager.pin_active() as repinned:
                    assert repinned.generation_id == "generation-2", (
                        "a fresh reader must select the new active generation"
                    )
                assert published_second.generation_id == "generation-2"

                retire_while_pinned = manager.retire(first)
                assert retire_while_pinned is False, "retire refused while pinned"
            # Exiting the pin context releases the reader reference.
            retire_after_unpin = manager.retire(first)
            assert retire_after_unpin is True, "retire allowed after unpin"
            assert not (root / "generations" / "generation-1").exists()

            active = manager.load_active()
            assert active is not None and active.generation_id == "generation-2"
            return {
                "pinned_reader_generation": "generation-1",
                "new_reader_generation": "generation-2",
                "retire_while_pinned": retire_while_pinned,
                "retire_after_unpin": retire_after_unpin,
                "active_after_swap": "generation-2",
            }

    return asyncio.run(run())


def scenario_bounded_lane() -> dict:
    async def run() -> dict:
        lane = BoundedLane("stress-lane", LaneLimits(concurrency=1, max_queue_items=1))
        loop = asyncio.get_running_loop()

        holder_release = asyncio.Event()
        waiter_release = asyncio.Event()

        async def holder_op() -> int:
            await holder_release.wait()
            return 42

        async def waiter_op() -> int:
            await waiter_release.wait()
            return 7

        holder = asyncio.create_task(lane.run(holder_op))
        while lane.snapshot["active"] == 0:
            await asyncio.sleep(0.001)

        waiter = asyncio.create_task(lane.run(waiter_op))
        while lane.snapshot["queued_items"] == 0:
            await asyncio.sleep(0.001)

        # Third admission: queue budget exhausted -> OVERLOADED.
        try:
            await lane.run(_noop)
            raise AssertionError("expected OVERLOADED")
        except StoreGatewayError as exc:
            overloaded = exc
        assert overloaded.code is GatewayErrorCode.OVERLOADED, overloaded.code
        assert overloaded.retryable is True
        assert overloaded.retry_after_ms == 100

        # Free the slot; the queued op starts running.
        holder_release.set()
        assert await holder == 42
        while lane.snapshot["active"] == 0 or lane.snapshot["queued_items"] != 0:
            await asyncio.sleep(0.001)

        # Fourth admission with a short deadline -> DEADLINE_EXCEEDED.
        deadline = loop.time() + 0.04
        try:
            await lane.run(_noop, deadline=deadline)
            raise AssertionError("expected DEADLINE_EXCEEDED")
        except StoreGatewayError as exc:
            assert exc.code is GatewayErrorCode.DEADLINE_EXCEEDED, exc.code

        waiter_release.set()
        assert await waiter == 7

        # Python parity: an elapsed deadline fails even on an idle lane
        # (wait_for(timeout=0) never observes the completed acquire).
        try:
            await lane.run(_noop, deadline=loop.time() - 1)
            raise AssertionError("expected DEADLINE_EXCEEDED on elapsed deadline")
        except StoreGatewayError as exc:
            assert exc.code is GatewayErrorCode.DEADLINE_EXCEEDED, exc.code

        # A still-future deadline with a free permit succeeds immediately.
        await lane.run(_noop, deadline=loop.time() + 1)

        snapshot = lane.snapshot
        assert snapshot["accepted"] == 5
        assert snapshot["completed"] == 3
        assert snapshot["rejected"] == 1
        assert snapshot["timed_out"] == 2
        assert snapshot["active"] == 0
        assert snapshot["queued_items"] == 0
        return {
            "overloaded_code": "OVERLOADED",
            "overloaded_retry_after_ms": 100,
            "deadline_code": "DEADLINE_EXCEEDED",
            "accepted": snapshot["accepted"],
            "completed": snapshot["completed"],
            "rejected": snapshot["rejected"],
            "timed_out": snapshot["timed_out"],
        }

    async def _noop() -> None:
        return None

    return asyncio.run(run())


def scenario_lease_kill9() -> dict:
    with tempfile.TemporaryDirectory(prefix="cortex-py-lease-kill-") as tmp:
        lock = str(Path(tmp) / "killed.rdb")
        child = subprocess.Popen(
            [sys.executable, "-c", HOLD_CHILD_SCRIPT, lock],
            stdout=subprocess.PIPE,
            text=True,
        )
        held_metadata = ""
        assert child.stdout is not None
        for line in child.stdout:
            if "RESULT HELD " in line:
                held_metadata = line.split("RESULT HELD ", 1)[1].strip()
                break
        assert "instance_id" in held_metadata, f"child never held the lease: {held_metadata!r}"

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=30)

        # The kernel released the flock; stale metadata is still on disk.
        lock_path = Path(lock).parent / f".{Path(lock).name}.cortex-owner.lock"
        stale_metadata = lock_path.read_text(encoding="utf-8").strip() or None

        # Recovery = a fresh acquirer succeeds and replaces the stale payload.
        lease = StorageLease(Path(lock), instance_id="stress", owner_id="code", backend="falkordb")
        handle = lease.acquire()
        recovered = True
        handle.close() if hasattr(handle, "close") else lease.release()
        return {
            "recovered": recovered,
            "stale_metadata_present": stale_metadata is not None,
            "reacquire_after_recovery": True,
        }


def run_rust_suite(out_path: Path) -> dict:
    env = dict(os.environ)
    env["CORTEX_STORAGE_STRESS_OUT"] = str(out_path)
    env.setdefault("HOME", str(Path.home()))
    proc = subprocess.run(
        ["cargo", "test", "-p", "cortex-storage", "--test", "stress"],
        cwd=REPO_ROOT / "rust",
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0 and "cortex-storage" not in proc.stderr:
        # Workspace contention: another agent's crate may be mid-scaffold and
        # break workspace resolution. cortex-storage has no workspace path
        # dependencies, so retry from a standalone copy.
        import shutil

        with tempfile.TemporaryDirectory(prefix="cortex-storage-standalone-") as tmp:
            standalone = Path(tmp) / "cortex-storage"
            shutil.copytree(
                REPO_ROOT / "rust" / "crates" / "cortex-storage",
                standalone,
                ignore=shutil.ignore_patterns("target"),
            )
            proc = subprocess.run(
                ["cargo", "test", "--test", "stress"],
                cwd=standalone,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
        raise SystemExit("Rust stress suite failed")
    if not out_path.exists():
        raise SystemExit("Rust stress suite produced no summary file")
    return json.loads(out_path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-cargo",
        action="store_true",
        help="reuse an existing Rust summary JSON instead of running cargo",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("Python storage-layer stress scenarios (reference behavior)")
    print("=" * 72)
    results["lease_race"] = scenario_lease_race()
    print(f"  lease_race:        {results['lease_race']}")
    results["generation_swap"] = scenario_generation_swap()
    print(f"  generation_swap:   {results['generation_swap']}")
    results["bounded_lane"] = scenario_bounded_lane()
    print(f"  bounded_lane:      {results['bounded_lane']}")
    results["lease_kill9"] = scenario_lease_kill9()
    print(f"  lease_kill9:       {results['lease_kill9']}")

    if args.skip_cargo:
        summary_path = Path(os.environ.get("CORTEX_STORAGE_STRESS_OUT", "/tmp/cortex-storage-stress-rust.json"))
        rust = json.loads(summary_path.read_text())
    else:
        with tempfile.NamedTemporaryFile(prefix="cortex-storage-stress-rust-", suffix=".json", delete=False) as handle:
            summary_path = Path(handle.name)
        try:
            print()
            print("=" * 72)
            print("Rust storage-port stress scenarios (cargo test -p cortex-storage)")
            print("=" * 72)
            rust = run_rust_suite(summary_path)
        finally:
            pass
    for name, payload in rust.items():
        print(f"  {name:18} {payload}")

    # ---- behavior comparison ------------------------------------------------
    comparisons = [
        (
            "lease-race winners (8 racers)",
            results["lease_race"]["winners"],
            rust["lease_race"]["winners"],
        ),
        (
            "lease-race losers (immediate conflict)",
            results["lease_race"]["losers"],
            rust["lease_race"]["losers"],
        ),
        (
            "loser behavior identical",
            results["lease_race"]["loser_behavior"],
            rust["lease_race"]["loser_behavior"],
        ),
        (
            "pinned reader keeps old generation",
            results["generation_swap"]["pinned_reader_generation"],
            rust["generation_swap"]["pinned_reader_generation"],
        ),
        (
            "fresh reader sees new generation",
            results["generation_swap"]["new_reader_generation"],
            rust["generation_swap"]["new_reader_generation"],
        ),
        (
            "retire refused while pinned",
            results["generation_swap"]["retire_while_pinned"],
            rust["generation_swap"]["retire_while_pinned"],
        ),
        (
            "retire allowed after unpin",
            results["generation_swap"]["retire_after_unpin"],
            rust["generation_swap"]["retire_after_unpin"],
        ),
        (
            "OVERLOADED error code",
            results["bounded_lane"]["overloaded_code"],
            rust["bounded_lane"]["overloaded_code"],
        ),
        (
            "OVERLOADED retry_after_ms",
            results["bounded_lane"]["overloaded_retry_after_ms"],
            rust["bounded_lane"]["overloaded_retry_after_ms"],
        ),
        (
            "DEADLINE_EXCEEDED error code",
            results["bounded_lane"]["deadline_code"],
            rust["bounded_lane"]["deadline_code"],
        ),
        (
            "lane accepted counter",
            results["bounded_lane"]["accepted"],
            rust["bounded_lane"]["accepted"],
        ),
        (
            "lane completed counter",
            results["bounded_lane"]["completed"],
            rust["bounded_lane"]["completed"],
        ),
        (
            "lane rejected counter",
            results["bounded_lane"]["rejected"],
            rust["bounded_lane"]["rejected"],
        ),
        (
            "lane timed_out counter",
            results["bounded_lane"]["timed_out"],
            rust["bounded_lane"]["timed_out"],
        ),
        (
            "kill -9 lease recovered",
            results["lease_kill9"]["recovered"],
            rust["lease_kill9"]["recovered"],
        ),
        (
            "stale metadata observable",
            results["lease_kill9"]["stale_metadata_present"],
            rust["lease_kill9"]["stale_metadata_present"],
        ),
    ]

    failures = 0
    print()
    print("=" * 72)
    print(f"{'Behavior':44} {'Python':>14} {'Rust':>14}  match")
    print("-" * 72)
    for name, py, rs in comparisons:
        ok = py == rs
        failures += 0 if ok else 1
        print(f"{name:44} {str(py):>14} {str(rs):>14}  {'OK' if ok else 'MISMATCH'}")
    print("=" * 72)

    if failures:
        print(f"FAIL: {failures} behavior mismatch(es)")
        return 1
    print("PASS: Python and Rust storage layers behave identically across all scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
