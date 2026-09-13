//! Bounded admission for a single embedded-store owner.
//!
//! Port of `cortex_harness.storage.admission`. The Python lane is asyncio
//! based; the Rust lane is blocking-thread based but preserves the exact
//! admission contract: FIFO count+byte budgets, `OVERLOADED` rejection with
//! `retry_after_ms=100`, `DEADLINE_EXCEEDED` on queue-wait deadlines, and the
//! same counter snapshot fields.

use std::fmt;
use std::sync::{Condvar, Mutex, MutexGuard};
use std::time::Instant;

use serde_json::json;

use crate::contracts::{GatewayErrorCode, StoreGatewayError};
use crate::errors::{StoreError, StoreResult};

/// Recover from lock poisoning: the lane state is plain counters, so
/// continuing after a panic in a worker is safe and matches asyncio
/// semantics (no corruption).
pub(crate) fn lock_recover<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Explicit budgets for one lane.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaneLimits {
    pub concurrency: usize,
    pub max_queue_items: usize,
    pub max_queue_bytes: i64,
}

impl LaneLimits {
    pub const fn new(concurrency: usize, max_queue_items: usize, max_queue_bytes: i64) -> Self {
        Self {
            concurrency,
            max_queue_items,
            max_queue_bytes,
        }
    }

    pub fn validate(&self) -> Result<(), StoreError> {
        if self.concurrency < 1 {
            return Err(StoreError::Value(
                "lane concurrency must be at least one".to_string(),
            ));
        }
        if self.max_queue_bytes < 0 {
            return Err(StoreError::Value(
                "lane queue limits cannot be negative".to_string(),
            ));
        }
        Ok(())
    }
}

impl Default for LaneLimits {
    fn default() -> Self {
        Self::new(1, 32, 4 * 1024 * 1024)
    }
}

impl fmt::Display for LaneLimits {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "LaneLimits(concurrency={}, max_queue_items={}, max_queue_bytes={})",
            self.concurrency, self.max_queue_items, self.max_queue_bytes
        )
    }
}

/// Point-in-time lane counters (`BoundedLane.snapshot`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LaneSnapshot {
    pub active: i64,
    pub queued_items: i64,
    pub queued_bytes: i64,
    pub capacity: usize,
    pub byte_capacity: i64,
    pub accepted: i64,
    pub completed: i64,
    pub rejected: i64,
    pub timed_out: i64,
    pub cancelled: i64,
    pub queue_wait_seconds: f64,
    pub max_queue_wait_seconds: f64,
}

impl LaneSnapshot {
    pub fn to_value(&self) -> serde_json::Value {
        json!({
            "active": self.active,
            "queued_items": self.queued_items,
            "queued_bytes": self.queued_bytes,
            "capacity": self.capacity,
            "byte_capacity": self.byte_capacity,
            "accepted": self.accepted,
            "completed": self.completed,
            "rejected": self.rejected,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "queue_wait_seconds": self.queue_wait_seconds,
            "max_queue_wait_seconds": self.max_queue_wait_seconds,
        })
    }
}

#[derive(Debug, Default)]
struct LaneState {
    permits: usize,
    queued_items: i64,
    queued_bytes: i64,
    active: i64,
    accepted: i64,
    completed: i64,
    rejected: i64,
    timed_out: i64,
    cancelled: i64,
    queue_wait_seconds: f64,
    max_queue_wait_seconds: f64,
}

/// A FIFO lane with explicit count and byte budgets.
///
/// Admission accounting happens under the state lock; awaited work is always
/// performed without the lock held, matching the Python docstring contract.
#[derive(Debug)]
pub struct BoundedLane {
    pub name: String,
    pub limits: LaneLimits,
    state: Mutex<LaneState>,
    idle: Condvar,
}

impl BoundedLane {
    pub fn new(name: &str, limits: LaneLimits) -> StoreResult<Self> {
        limits.validate()?;
        let permits = limits.concurrency;
        Ok(Self {
            name: name.to_string(),
            limits,
            state: Mutex::new(LaneState {
                permits,
                ..LaneState::default()
            }),
            idle: Condvar::new(),
        })
    }

    pub fn snapshot(&self) -> LaneSnapshot {
        let state = lock_recover(&self.state);
        self.snapshot_locked(&state)
    }

    fn snapshot_locked(&self, state: &LaneState) -> LaneSnapshot {
        LaneSnapshot {
            active: state.active,
            queued_items: state.queued_items,
            queued_bytes: state.queued_bytes,
            capacity: self.limits.max_queue_items,
            byte_capacity: self.limits.max_queue_bytes,
            accepted: state.accepted,
            completed: state.completed,
            rejected: state.rejected,
            timed_out: state.timed_out,
            cancelled: state.cancelled,
            queue_wait_seconds: state.queue_wait_seconds,
            max_queue_wait_seconds: state.max_queue_wait_seconds,
        }
    }

    /// Run `operation` through the lane's budget and concurrency limits.
    ///
    /// `deadline` bounds the queue wait only, exactly like the Python lane's
    /// semaphore acquire timeout; an operation already admitted runs to
    /// completion.
    pub fn run<T>(
        &self,
        operation: impl FnOnce() -> StoreResult<T>,
        estimated_bytes: i64,
        deadline: Option<Instant>,
    ) -> StoreResult<T> {
        if estimated_bytes < 0 {
            return Err(StoreError::Value(
                "estimated_bytes cannot be negative".to_string(),
            ));
        }
        // ---- admission (count/byte budget) ------------------------------
        {
            let mut state = lock_recover(&self.state);
            if state.queued_items >= self.limits.max_queue_items as i64
                || state.queued_bytes + estimated_bytes > self.limits.max_queue_bytes
            {
                state.rejected += 1;
                let snapshot = self.snapshot_locked(&state);
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::Overloaded,
                    format!("{} admission queue is full", self.name),
                )
                .retryable()
                .with_retry_after_ms(100)
                .with_details(snapshot.to_value().as_object().cloned().unwrap_or_default())
                .into());
            }
            state.queued_items += 1;
            state.queued_bytes += estimated_bytes;
            state.accepted += 1;
        }

        let queued_at = Instant::now();
        let mut acquired = false;
        let result = self.run_admitted(operation, estimated_bytes, deadline, queued_at, &mut acquired);

        // ---- release -----------------------------------------------------
        let mut state = lock_recover(&self.state);
        if acquired {
            state.active -= 1;
            state.permits += 1;
            if state.active == 0 && state.queued_items == 0 {
                self.idle.notify_all();
            }
        } else {
            state.queued_items -= 1;
            state.queued_bytes -= estimated_bytes;
            if state.active == 0 && state.queued_items == 0 {
                self.idle.notify_all();
            }
        }
        drop(state);
        self.idle.notify_all();
        result
    }

    fn run_admitted<T>(
        &self,
        operation: impl FnOnce() -> StoreResult<T>,
        estimated_bytes: i64,
        deadline: Option<Instant>,
        queued_at: Instant,
        acquired: &mut bool,
    ) -> StoreResult<T> {
        // ---- wait for a permit (deadline bounds the wait only) -----------
        //
        // Python parity: `wait_for(semaphore.acquire(), timeout=0)` raises
        // TimeoutError even when a permit is available (the wrapped task is
        // never done at creation), so an elapsed deadline always maps to
        // DEADLINE_EXCEEDED regardless of lane state.
        {
            let mut state = lock_recover(&self.state);
            loop {
                if deadline.is_some_and(|deadline| deadline <= Instant::now()) {
                    state.timed_out += 1;
                    let snapshot = self.snapshot_locked(&state);
                    return Err(StoreGatewayError::new(
                        GatewayErrorCode::DeadlineExceeded,
                        format!("{} admission deadline elapsed", self.name),
                    )
                    .retryable()
                    .with_details(snapshot.to_value().as_object().cloned().unwrap_or_default())
                    .into());
                }
                if state.permits > 0 {
                    state.permits -= 1;
                    break;
                }
                let remaining = match deadline {
                    Some(deadline) => deadline.saturating_duration_since(Instant::now()),
                    None => {
                        state = self
                            .idle
                            .wait(state)
                            .unwrap_or_else(|poisoned| poisoned.into_inner());
                        continue;
                    }
                };
                let (next, timeout_result) = self
                    .idle
                    .wait_timeout(state, remaining)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                state = next;
                if timeout_result.timed_out() && state.permits == 0 {
                    // Deadline elapsed without a permit: DEADLINE_EXCEEDED.
                    // The snapshot is taken before the release path (the
                    // `finally` in Python) decrements the queued counters;
                    // the decrement itself happens exactly once in `run`.
                    state.timed_out += 1;
                    let snapshot = self.snapshot_locked(&state);
                    return Err(StoreGatewayError::new(
                        GatewayErrorCode::DeadlineExceeded,
                        format!("{} admission deadline elapsed", self.name),
                    )
                    .retryable()
                    .with_details(snapshot.to_value().as_object().cloned().unwrap_or_default())
                    .into());
                }
            }
            let waited = queued_at.elapsed().as_secs_f64();
            state.queued_items -= 1;
            state.queued_bytes -= estimated_bytes;
            state.active += 1;
            state.queue_wait_seconds += waited;
            if waited > state.max_queue_wait_seconds {
                state.max_queue_wait_seconds = waited;
            }
        }
        *acquired = true;

        // ---- execute without the state lock ------------------------------
        match operation() {
            Ok(value) => {
                lock_recover(&self.state).completed += 1;
                Ok(value)
            }
            Err(err) => Err(err),
        }
    }

    /// Wait until all accepted work has finished or left the queue.
    pub fn wait_idle(&self) {
        let mut state = lock_recover(&self.state);
        while state.active > 0 || state.queued_items > 0 {
            state = self
                .idle
                .wait(state)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
    }

    /// Wait for idle until `deadline`; returns false when the deadline
    /// elapses first (bounded drain).
    pub fn wait_idle_until(&self, deadline: Instant) -> bool {
        let mut state = lock_recover(&self.state);
        while state.active > 0 || state.queued_items > 0 {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return state.active == 0 && state.queued_items == 0;
            }
            let (next, _timeout) = self
                .idle
                .wait_timeout(state, remaining)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            state = next;
        }
        true
    }

    /// Whether the lane is fully drained.
    pub fn is_idle(&self) -> bool {
        let state = lock_recover(&self.state);
        state.active == 0 && state.queued_items == 0
    }
}
