//! Stress suite for the Rust storage port (Phase 10 Scope B).
//!
//! Four mandatory scenarios, each mirroring the Python storage layer:
//!
//! 1. Lease race: N=8 processes race for the same instance lease → exactly
//!    one winner; losers fail immediately with a conflict error (no wait).
//! 2. Generation swap with pinned readers: a reader pinned to the old
//!    generation must not observe the new store until it re-pins; retire is
//!    refused while pins are held.
//! 3. BoundedLane saturation + timeout: admission rejection with the same
//!    error codes as Python (`OVERLOADED` / `DEADLINE_EXCEEDED`).
//! 4. Kill -9 during lease: the kernel releases the `flock` when the owner
//!    dies and `recover_expired_leases` reclaims the stale lease exactly
//!    like a fresh Python acquirer.
//!
//! The summary of all scenarios is printed as `RUST_SCENARIO <name> {json}`
//! lines and aggregated into the file named by `CORTEX_STORAGE_STRESS_OUT`,
//! where `scripts/rust_parity/storage_stress.py` picks it up for the
//! Python comparison.

use std::io::{BufRead, BufReader, Write as _};
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use cortex_storage::contracts::PhysicalTargetKey;
use cortex_storage::contracts::GenerationState;
use cortex_storage::{
    BoundedLane, GenerationManager, LaneLimits, StoreError, StorageLease, recover_expired_leases,
};

const CHILD_ENV: &str = "CORTEX_STORAGE_STRESS_CHILD";
const RACE_PROCESSES: usize = 8;

fn summary_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn temp_root(label: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "cortex-storage-stress-{}-{}-{}",
        label,
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).expect("create temp root");
    dir
}

fn spawn_child(mode: &str, extra_env: &[(&str, String)]) -> Child {
    let exe = std::env::current_exe().expect("current_exe");
    let mut command = Command::new(exe);
    command
        .args(["stress_child", "--exact", "--nocapture", "--test-threads=1"])
        .env(CHILD_ENV, mode)
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    for (key, value) in extra_env {
        command.env(key, value);
    }
    command.spawn().expect("spawn stress child")
}

fn child_stdout_lines(child: &mut Child) -> Vec<String> {
    let stdout = child.stdout.take().expect("child stdout");
    let mut lines = Vec::new();
    for line in BufReader::new(stdout).lines() {
        match line {
            Ok(text) => lines.push(text),
            Err(_) => break,
        }
    }
    lines
}

fn stress_target(root: &std::path::Path) -> PhysicalTargetKey {
    PhysicalTargetKey::from_paths(
        "stress",
        "code",
        &root.join("graph.rdb"),
        &root.join("vectors"),
    )
}

// ---------------------------------------------------------------------------
// Scenario 1 — lease race (N=8 processes, exactly one winner).
// ---------------------------------------------------------------------------

#[test]
fn stress_lease_race() {
    let root = temp_root("lease-race");
    let lock_target = root.join("race.rdb");
    let lock_target_string = lock_target.to_string_lossy().into_owned();
    let go_file = root.join("GO");

    let mut children = Vec::new();
    for index in 0..RACE_PROCESSES {
        let env = vec![
            ("CORTEX_STRESS_LOCK", lock_target_string.clone()),
            ("CORTEX_STRESS_GO", go_file.to_string_lossy().into_owned()),
            ("CORTEX_STRESS_INDEX", index.to_string()),
        ];
        children.push(spawn_child("lease-race", &env));
    }
    // Release all racers at once so their acquisitions genuinely race.
    std::thread::sleep(Duration::from_millis(200));
    std::fs::write(&go_file, b"go").expect("write GO barrier");
    let mut winners = 0usize;
    let mut conflicts = 0usize;
    let mut immediate = true;
    for child in &mut children {
        for line in child_stdout_lines(child) {
            if line.contains("RESULT WINNER") {
                winners += 1;
            } else if line.contains("RESULT CONFLICT") {
                conflicts += 1;
                // The Python path uses timeout=0 + LOCK_NB, so a loser fails
                // immediately rather than waiting.
                let elapsed = line
                    .split_whitespace()
                    .last()
                    .and_then(|token| token.parse::<f64>().ok())
                    .unwrap_or(f64::INFINITY);
                if elapsed > 2.0 {
                    immediate = false;
                }
            }
        }
    }
    for mut child in children {
        let _ = child.wait();
    }

    assert_eq!(
        winners, 1,
        "exactly one lease winner expected among {RACE_PROCESSES} racers"
    );
    assert_eq!(
        conflicts,
        RACE_PROCESSES - 1,
        "all losers must fail with a conflict error"
    );
    assert!(immediate, "losers must fail immediately (nonblocking)");

    let _ = std::fs::remove_dir_all(&root);
    write_summary(
        "lease_race",
        serde_json::json!({
            "processes": RACE_PROCESSES,
            "winners": winners,
            "losers": conflicts,
            "loser_behavior": "immediate-conflict-error",
        }),
    );
}

// ---------------------------------------------------------------------------
// Scenario 2 — generation swap with pinned readers.
// ---------------------------------------------------------------------------

#[test]
fn stress_generation_swap_pinned() {
    let root = temp_root("gen-swap");
    let generations_root = root.join("generations");
    let target = stress_target(&root);
    let manager =
        GenerationManager::new(&generations_root, target, 1, None, None).expect("manager");

    let first = manager
        .allocate("revision-1", Some("generation-1"))
        .expect("allocate gen1");
    let published_first = manager.publish(&first, |_| Ok(())).expect("publish gen1");
    assert_eq!(published_first.state, GenerationState::Published);

    // Pin a reader to the old generation.
    let pinned = manager.pin_active().expect("pin");
    assert_eq!(pinned.generation_id(), "generation-1");
    assert_eq!(manager.reference_count("generation-1"), 1);

    // Swap the store underneath the pinned reader.
    let second = manager
        .allocate("revision-2", Some("generation-2"))
        .expect("allocate gen2");
    let published_second = manager.publish(&second, |_| Ok(())).expect("publish gen2");

    // The pinned reader must not observe the new store until it re-pins.
    assert_eq!(
        pinned.generation_id(),
        "generation-1",
        "pinned reader must keep observing the old generation"
    );
    let repinned = manager.pin_active().expect("re-pin");
    assert_eq!(
        repinned.generation_id(),
        "generation-2",
        "a fresh reader must select the new active generation"
    );
    assert_eq!(published_second.generation_id, "generation-2");
    drop(repinned);

    // Retirement waits for pins: refuse while pinned, succeed after drop.
    let retire_while_pinned = manager.retire(&first).expect("retire while pinned");
    assert!(
        !retire_while_pinned,
        "retire must be refused while readers are pinned"
    );
    drop(pinned);
    let retire_after_unpin = manager.retire(&first).expect("retire after unpin");
    assert!(
        retire_after_unpin,
        "retire must succeed once the pin is released"
    );
    assert!(!root.join("generations/generation-1").exists());

    let active = manager.load_active().expect("load active");
    assert_eq!(
        active.map(|manifest| manifest.generation_id),
        Some("generation-2".to_string())
    );

    let _ = std::fs::remove_dir_all(&root);
    write_summary(
        "generation_swap",
        serde_json::json!({
            "pinned_reader_generation": "generation-1",
            "new_reader_generation": "generation-2",
            "retire_while_pinned": retire_while_pinned,
            "retire_after_unpin": retire_after_unpin,
            "active_after_swap": "generation-2",
        }),
    );
}

// ---------------------------------------------------------------------------
// Scenario 3 — BoundedLane saturation + deadline timeout.
// ---------------------------------------------------------------------------

#[test]
fn stress_bounded_lane_saturation() {
    let lane = BoundedLane::new("stress-lane", LaneLimits::new(1, 1, 4 * 1024 * 1024))
        .expect("lane");
    let lane = &lane;
    let mut overloaded_code = "";
    let mut overloaded_retry_after_ms = None;

    std::thread::scope(|scope| {
        // Hold the single execution slot with a blocking op.
        let (holder_tx, holder_rx) = std::sync::mpsc::channel::<()>();
        let holder = scope.spawn(move || {
            lane.run(
                || {
                    let _ = holder_rx.recv_timeout(Duration::from_secs(30));
                    Ok(42_i32)
                },
                0,
                None,
            )
        });
        while lane.snapshot().active == 0 {
            std::thread::sleep(Duration::from_millis(2));
        }

        // Queue one item behind the active op (queue budget is exactly 1).
        let (waiter_tx, waiter_rx) = std::sync::mpsc::channel::<()>();
        let waiter = scope.spawn(move || {
            lane.run(
                || {
                    let _ = waiter_rx.recv_timeout(Duration::from_secs(30));
                    Ok(7_i32)
                },
                0,
                None,
            )
        });
        while lane.snapshot().queued_items == 0 {
            std::thread::sleep(Duration::from_millis(2));
        }

        // Third admission: queue budget exhausted -> OVERLOADED with the
        // exact Python error code and retry hint.
        let rejection = lane.run(|| Ok(1_i32), 0, None).expect_err("rejected");
        overloaded_code = rejection.gateway_code_str().expect("gateway code");
        assert_eq!(overloaded_code, "OVERLOADED", "Python parity: OVERLOADED");
        let StoreError::Gateway(rejection_details) = &rejection else {
            panic!("expected gateway error");
        };
        assert!(rejection_details.retryable);
        assert_eq!(rejection_details.retry_after_ms, Some(100));
        overloaded_retry_after_ms = rejection_details.retry_after_ms;

        // Free the slot: the queued op takes the permit and starts running.
        holder_tx.send(()).expect("release holder");
        holder.join().expect("holder join").expect("holder ok");
        while lane.snapshot().active == 0 || lane.snapshot().queued_items != 0 {
            std::thread::sleep(Duration::from_millis(2));
        }

        // Fourth admission with a short deadline: the permit is held by the
        // waiter op, so the wait times out -> DEADLINE_EXCEEDED.
        let deadline = Instant::now() + Duration::from_millis(40);
        let timeout_error = lane
            .run(|| Ok(2_i32), 0, Some(deadline))
            .expect_err("timed out");
        assert_eq!(
            timeout_error.gateway_code_str(),
            Some("DEADLINE_EXCEEDED"),
            "Python parity: DEADLINE_EXCEEDED"
        );

        waiter_tx.send(()).expect("release waiter");
        let waiter_value = waiter.join().expect("waiter join").expect("waiter ok");
        assert_eq!(waiter_value, 7);
    });
    assert!(lane.is_idle());

    // Python parity: an elapsed deadline fails even on an idle lane
    // (wait_for(timeout=0) never observes the completed acquire).
    let elapsed_deadline = Instant::now().checked_sub(Duration::from_secs(1));
    let elapsed_error = lane
        .run(|| Ok(5_i32), 0, elapsed_deadline)
        .expect_err("elapsed deadline must fail like Python");
    assert_eq!(elapsed_error.gateway_code_str(), Some("DEADLINE_EXCEEDED"));

    // A still-future deadline with a free permit succeeds immediately.
    let tiny_deadline = Instant::now() + Duration::from_millis(100);
    let immediate = lane
        .run(|| Ok(5_i32), 0, Some(tiny_deadline))
        .expect("future deadline with free permit succeeds");
    assert_eq!(immediate, 5);

    let snapshot = lane.snapshot();
    assert_eq!(snapshot.accepted, 5);
    assert_eq!(snapshot.completed, 3);
    assert_eq!(snapshot.rejected, 1);
    assert_eq!(snapshot.timed_out, 2);
    assert_eq!(snapshot.active, 0);
    assert_eq!(snapshot.queued_items, 0);

    write_summary(
        "bounded_lane",
        serde_json::json!({
            "overloaded_code": overloaded_code,
            "overloaded_retry_after_ms": overloaded_retry_after_ms,
            "deadline_code": "DEADLINE_EXCEEDED",
            "accepted": snapshot.accepted,
            "completed": snapshot.completed,
            "rejected": snapshot.rejected,
            "timed_out": snapshot.timed_out,
        }),
    );
}

// ---------------------------------------------------------------------------
// Scenario 4 — kill -9 while a lease is held, then recover.
// ---------------------------------------------------------------------------

#[test]
fn stress_lease_kill9_recovery() {
    let root = temp_root("lease-kill");
    let lock_target = root.join("killed.rdb");
    let mut child = spawn_child(
        "lease-hold",
        &[(
            "CORTEX_STRESS_LOCK",
            lock_target.to_string_lossy().into_owned(),
        )],
    );

    // Wait for the child to confirm it holds the lease.
    let stdout = child.stdout.take().expect("child stdout");
    let mut held_metadata = String::new();
    for line in BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        // libtest prefixes the child's println with "test stress_child ... ".
        if let Some(index) = line.find("RESULT HELD ") {
            held_metadata = line[index + "RESULT HELD ".len()..].to_string();
            break;
        }
    }
    assert!(
        held_metadata.contains("instance_id"),
        "child must confirm the lease it holds: {held_metadata}"
    );

    // SIGKILL the lease holder.
    child.kill().expect("kill -9 child");
    let status = child.wait().expect("wait for killed child");
    assert!(!status.success(), "killed child must not exit cleanly");

    // The kernel dropped the flock; the expired lease is recoverable.
    let recovery = recover_expired_leases(&lock_target, "stress", "code", "falkordb")
        .expect("recover expired lease");
    assert!(recovery.recovered, "expired lease must be recoverable");
    let stale = recovery
        .stale_metadata
        .as_deref()
        .expect("stale lease metadata must survive the kill");
    assert!(
        stale.contains("instance_id"),
        "stale metadata must be the dead owner's payload: {stale}"
    );

    // A normal lease acquisition works again afterwards.
    let lease = StorageLease::new(&lock_target, "stress", "code", "falkordb");
    let mut reacquired = lease.acquire().expect("re-acquire after kill");
    reacquired.release();

    let _ = std::fs::remove_dir_all(&root);
    write_summary(
        "lease_kill9",
        serde_json::json!({
            "recovered": recovery.recovered,
            "stale_metadata_present": recovery.stale_metadata.is_some(),
            "reacquire_after_recovery": true,
        }),
    );
}

// ---------------------------------------------------------------------------
// Child entry point (re-invokes this test binary in a special mode).
// ---------------------------------------------------------------------------

#[test]
fn stress_child() {
    let Ok(mode) = std::env::var(CHILD_ENV) else {
        return; // normal cargo-test invocation: no-op
    };
    match mode.as_str() {
        "lease-race" => child_lease_race(),
        "lease-hold" => child_lease_hold(),
        _ => {}
    }
}

fn child_lease_race() {
    let lock_path = std::env::var("CORTEX_STRESS_LOCK").expect("CORTEX_STRESS_LOCK");
    let go_path = std::env::var("CORTEX_STRESS_GO").unwrap_or_default();
    while !go_path.is_empty() && !std::path::Path::new(&go_path).exists() {
        std::thread::sleep(Duration::from_millis(1));
    }
    let started = Instant::now();
    let lease = StorageLease::new(
        std::path::Path::new(&lock_path),
        "race-instance",
        "race-owner",
        "falkordb",
    );
    match lease.acquire() {
        Ok(mut lease) => {
            // The winner holds the lease across the whole race window so the
            // other racers observe the conflict (acquire is nonblocking,
            // mirroring the portalocker timeout=0 contract).
            std::thread::sleep(Duration::from_millis(3000));
            println!("RESULT WINNER");
            lease.release();
        }
        Err((_, conflict)) => {
            println!(
                "RESULT CONFLICT {} {:.3}",
                conflict.message,
                started.elapsed().as_secs_f64()
            );
        }
    }
}

fn child_lease_hold() {
    let lock_path = std::env::var("CORTEX_STRESS_LOCK").expect("CORTEX_STRESS_LOCK");
    let lease = StorageLease::new(
        std::path::Path::new(&lock_path),
        "stress",
        "code",
        "falkordb",
    );
    let mut lease = match lease.acquire() {
        Ok(lease) => lease,
        Err((_, conflict)) => {
            println!("RESULT HELD-FAILED {}", conflict.message);
            return;
        }
    };
    println!("RESULT HELD {}", lease.metadata());
    let _ = std::io::stdout().flush();
    // Hold until the parent sends SIGKILL.
    std::thread::sleep(Duration::from_secs(120));
    lease.release();
}

// ---------------------------------------------------------------------------
// Summary plumbing for scripts/rust_parity/storage_stress.py.
// ---------------------------------------------------------------------------

fn write_summary(scenario: &str, payload: serde_json::Value) {
    println!("RUST_SCENARIO {scenario} {payload}");
    let Ok(out_path) = std::env::var("CORTEX_STORAGE_STRESS_OUT") else {
        return;
    };
    let path = std::path::PathBuf::from(out_path);
    let _guard = summary_lock().lock();
    let mut combined: BTreeMap<String, serde_json::Value> = std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    combined.insert(scenario.to_string(), payload);
    if let Ok(mut file) = std::fs::File::create(&path) {
        let _ = writeln!(
            file,
            "{}",
            serde_json::to_string_pretty(&combined).unwrap_or_default()
        );
    }
}

use std::collections::BTreeMap;
