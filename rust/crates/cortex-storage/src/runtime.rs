//! Process-local observability and drain coordination for store gateways.
//!
//! Port of `cortex_harness.storage.runtime`. The registry is deliberately
//! not a factory and never opens storage; it only tracks gateways that have
//! already acquired their leases.

use std::collections::BTreeMap;
use std::sync::{Mutex, OnceLock, Weak};

use serde_json::json;

use crate::gateway::StoreGateway;

pub const ENV_STORE_GATEWAY_ENABLED: &str = "CORTEX_STORE_GATEWAY_ENABLED";

struct Registry {
    gateways: Vec<Weak<StoreGateway>>,
    gateway_mode_activated: bool,
}

fn registry() -> &'static Mutex<Registry> {
    static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        Mutex::new(Registry {
            gateways: Vec::new(),
            gateway_mode_activated: false,
        })
    })
}

fn lock() -> std::sync::MutexGuard<'static, Registry> {
    registry()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn flag_enabled(value: Option<&str>) -> bool {
    matches!(
        value.unwrap_or("").trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Return whether generation-gateway mode was explicitly requested
/// (`store_gateway_enabled`).
pub fn store_gateway_enabled(env: Option<&BTreeMap<String, String>>) -> bool {
    match env {
        Some(env) => flag_enabled(env.get(ENV_STORE_GATEWAY_ENABLED).map(String::as_str)),
        None => flag_enabled(std::env::var(ENV_STORE_GATEWAY_ENABLED).ok().as_deref()),
    }
}

/// Publish an already-started gateway to process health consumers
/// (`register_gateway`).
pub fn register_gateway(gateway: std::sync::Arc<StoreGateway>) {
    let mut registry_lock = lock();
    registry_lock
        .gateways
        .retain(|entry| entry.strong_count() > 0);
    registry_lock.gateways.push(std::sync::Arc::downgrade(&gateway));
    registry_lock.gateway_mode_activated = true;
}

/// Remove a stopped or failed gateway from process health consumers
/// (`unregister_gateway`).
pub fn unregister_gateway(gateway: &StoreGateway) {
    let mut registry_lock = lock();
    registry_lock
        .gateways
        .retain(|entry| entry.strong_count() > 0 && !entry.weak_points_to(gateway));
}

trait WeakPointsTo {
    fn weak_points_to(&self, candidate: &StoreGateway) -> bool;
}

impl WeakPointsTo for Weak<StoreGateway> {
    fn weak_points_to(&self, candidate: &StoreGateway) -> bool {
        let Some(upgraded) = self.upgrade() else {
            return false;
        };
        std::ptr::eq(std::sync::Arc::as_ptr(&upgraded), candidate as *const StoreGateway)
    }
}

/// Return a stable snapshot of strongly owned process gateways
/// (`active_gateways`).
pub fn active_gateways() -> Vec<std::sync::Arc<StoreGateway>> {
    let mut registry_lock = lock();
    registry_lock.gateways.retain(|entry| entry.strong_count() > 0);
    registry_lock
        .gateways
        .iter()
        .filter_map(std::sync::Weak::upgrade)
        .collect()
}

/// Stop new admission on every active gateway without store I/O
/// (`begin_gateway_drain`).
pub fn begin_gateway_drain() -> usize {
    let gateways = active_gateways();
    for gateway in &gateways {
        gateway.begin_drain();
    }
    gateways.len()
}

/// Drain and close every registered owner (`close_active_gateways`).
///
/// The Python version is async; this blocking variant drains each gateway in
/// turn and surfaces the first failure after closing the rest.
#[allow(clippy::result_large_err)]
pub fn close_active_gateways(
    timeout_seconds: Option<f64>,
) -> Result<usize, (usize, crate::errors::StoreError)> {
    let mut gateways = active_gateways();
    gateways.sort_by_key(|gateway| gateway.target.value());
    let total = gateways.len();
    let mut first_failure: Option<crate::errors::StoreError> = None;
    for gateway in gateways {
        if let Err(err) = gateway.close(timeout_seconds)
            && first_failure.is_none() {
                first_failure = Some(err);
            }
    }
    match first_failure {
        Some(err) => Err((total, err)),
        None => Ok(total),
    }
}

fn target_fingerprint(value: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    format!("{:x}", hasher.finalize())[..16].to_string()
}

/// Bounded, credential-free owner status projection
/// (`storage_runtime_status`).
pub fn storage_runtime_status(env: Option<&BTreeMap<String, String>>) -> serde_json::Value {
    let activated = lock().gateway_mode_activated;
    let mut gateways = active_gateways();
    gateways.sort_by_key(|gateway| gateway.target.value());
    let requested = store_gateway_enabled(env);
    let effective = requested || activated;
    let mut snapshots = Vec::new();
    for gateway in &gateways {
        let health = gateway.health();
        let metrics = gateway.metrics();
        snapshots.push(json!({
            "instance_id": health.target.instance_id,
            "owner_id": health.target.owner_id,
            "target_fingerprint": target_fingerprint(&health.target.value()),
            "lifecycle": health.lifecycle.as_str(),
            "active_generation": health.active_generation,
            "active_readers": health.active_readers,
            "queued_reads": health.queued_reads,
            "queued_writes": health.queued_writes,
            "ready": health.ready,
            "probe_generation": health.probe_generation,
            "last_probe_at": health.last_probe_at,
            "probe_error": health.probe_error,
            "updated_at": health.updated_at,
            "lanes": metrics.get("lanes").cloned().unwrap_or_default(),
            "jobs": metrics.get("jobs").cloned().unwrap_or_default(),
        }));
    }
    let all_ready = snapshots
        .iter()
        .all(|item| item.get("ready").and_then(serde_json::Value::as_bool) == Some(true))
        && !snapshots.is_empty();
    let any_draining = snapshots.iter().any(|item| {
        item.get("lifecycle").and_then(serde_json::Value::as_str) == Some("DRAINING")
    });
    let (ready, state) = if !effective {
        (true, "rollback_ready")
    } else if snapshots.is_empty() {
        (false, "owner_missing")
    } else if any_draining {
        (all_ready, "draining")
    } else if all_ready {
        (true, "ready")
    } else {
        (false, "warming")
    };
    json!({
        "mode": if effective { "generation_gateway" } else { "pause_restart" },
        "feature_requested": requested,
        "liveness": true,
        "readiness": ready,
        "state": state,
        "gateway_count": snapshots.len(),
        "gateways": snapshots,
    })
}

/// Reset an idle registry; production code must never call this helper
/// (`_reset_runtime_state_for_tests`).
pub fn reset_runtime_state_for_tests() -> Result<(), crate::errors::StoreError> {
    let mut registry_lock = lock();
    if registry_lock
        .gateways
        .iter()
        .any(|entry| entry.strong_count() > 0)
    {
        return Err(crate::errors::StoreError::Runtime(
            "cannot reset runtime state while gateways are active".to_string(),
        ));
    }
    registry_lock.gateways.clear();
    registry_lock.gateway_mode_activated = false;
    Ok(())
}
