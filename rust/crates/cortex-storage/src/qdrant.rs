//! Shared Qdrant local-mode adapter for Cortex Harness.
//!
//! Port of `cortex_harness.storage.qdrant` at the adapter boundary. The
//! Python adapter delegates to `qdrant_client.QdrantClient(path=...)`, which
//! embeds a full Qdrant engine that cannot be linked into this crate; the
//! Rust adapter keeps the exact same public surface (method names, argument
//! conventions, per-path client cache + `StorageLease` ownership, and
//! error shapes) over a small embedded JSON-file engine with exact
//! brute-force similarity search.
//!
//! Documented divergences: no HNSW approximation, no mmap/quantization, and
//! points are ordered by id (numeric ids ascending, then string ids) rather
//! than by internal segment order.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use serde_json::{json, Map, Value};

use crate::config::ResolvedStorage;
use crate::errors::{StoreError, StoreResult};
use crate::lease::StorageLease;

pub const STORE_FILE_NAME: &str = "cortex-local-store.json";

#[derive(Debug, Clone, PartialEq)]
enum PointId {
    Number(u64),
    Text(String),
}

impl PointId {
    fn parse(value: &Value) -> StoreResult<PointId> {
        if let Some(number) = value.as_u64() {
            return Ok(PointId::Number(number));
        }
        if let Some(text) = value.as_str() {
            return Ok(PointId::Text(text.to_string()));
        }
        Err(StoreError::Value(
            "point id must be an unsigned integer or a string".to_string(),
        ))
    }

    fn to_value(&self) -> Value {
        match self {
            PointId::Number(number) => json!(number),
            PointId::Text(text) => json!(text),
        }
    }

    fn sort_key(&self) -> (u8, u64, String) {
        match self {
            PointId::Number(number) => (0, *number, String::new()),
            PointId::Text(text) => (1, 0, text.clone()),
        }
    }
}

#[derive(Debug, Clone)]
struct Point {
    id: PointId,
    vector: BTreeMap<String, Vec<f64>>,
    payload: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq)]
enum Distance {
    Cosine,
    Euclid,
    Dot,
}

impl Distance {
    fn parse(value: &str) -> StoreResult<Distance> {
        match value {
            "Cosine" | "cosine" => Ok(Distance::Cosine),
            "Euclid" | "euclid" => Ok(Distance::Euclid),
            "Dot" | "dot" => Ok(Distance::Dot),
            other => Err(StoreError::Value(format!(
                "'{other}' is not a valid Distance"
            ))),
        }
    }
}

#[derive(Debug, Clone)]
struct Collection {
    vectors_config: Map<String, Value>,
    points: Vec<Point>,
    payload_indexes: Vec<Value>,
}

#[derive(Debug, Default)]
pub struct StoreData {
    collections: BTreeMap<String, Collection>,
}

pub struct LocalClient {
    pub(crate) path: PathBuf,
    pub data: StoreData,
    /// Bytes serialized by this client's flushes — the per-store
    /// serialization-volume signal for the persist-mode benchmark.
    pub(crate) flush_bytes: std::sync::atomic::AtomicU64,
}

impl LocalClient {
    fn open(path: &Path) -> StoreResult<Self> {
        legacy_store_guard(path)?;
        std::fs::create_dir_all(path).map_err(StoreError::Io)?;
        let store_file = path.join(STORE_FILE_NAME);
        let data = match std::fs::File::open(&store_file) {
            Ok(file) => parse_store_from_reader(std::io::BufReader::new(file))?,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => StoreData::default(),
            Err(err) => return Err(StoreError::Io(err)),
        };
        Ok(Self {
            path: path.to_path_buf(),
            data,
            flush_bytes: std::sync::atomic::AtomicU64::new(0),
        })
    }

    fn flush(&self) -> StoreResult<()> {
        let mut collections = Map::new();
        for (name, collection) in &self.data.collections {
            collections.insert(name.clone(), collection_to_value(collection));
        }
        let payload = json!({"collections": Value::Object(collections)});
        let text = crate::util::canonical_json(&payload);
        self.flush_bytes
            .fetch_add(text.len() as u64, std::sync::atomic::Ordering::Relaxed);
        crate::util::write_atomic(&self.path.join(STORE_FILE_NAME), &text)?;
        Ok(())
    }
}

/// Legacy qdrant-client pickle layout guard (compound key): a store root that
/// carries the legacy markers (`collection/` subtree or an old single-file
/// `storage.sqlite` at the root) but no `cortex-local-store.json` is legacy
/// data this engine must NOT touch — fail loudly with the re-index recipe.
/// When the JSON store exists the markers are allowed to coexist during the
/// re-index window (quarantine removes them afterwards).
fn legacy_store_guard(path: &Path) -> StoreResult<()> {
    if path.join(STORE_FILE_NAME).exists() {
        return Ok(());
    }
    let legacy_subtree = path.join("collection");
    let legacy_root_sqlite = path.join("storage.sqlite");
    if legacy_subtree.is_dir() || legacy_root_sqlite.is_file() {
        return Err(StoreError::Value(format!(
            "legacy qdrant-client pickle store detected at {} (no {STORE_FILE_NAME}): the \
             native JSON engine refuses to touch it. Re-index with `dev sync code --full-scan` \
             (into a fresh root or after quarantining: rename <root>/collection aside), then \
             quarantine the legacy subtree.",
            path.display()
        )));
    }
    Ok(())
}

fn collection_to_value(collection: &Collection) -> Value {
    json!({
        "vectors_config": Value::Object(collection.vectors_config.clone()),
        "points": collection.points.iter().map(point_to_value).collect::<Vec<_>>(),
        "payload_indexes": collection.payload_indexes,
    })
}

fn point_to_value(point: &Point) -> Value {
    let vector = if point.vector.len() == 1 && point.vector.contains_key("") {
        json!(point.vector[""])
    } else {
        let mut named = Map::new();
        for (name, values) in &point.vector {
            named.insert(name.clone(), json!(values));
        }
        Value::Object(named)
    };
    json!({
        "id": point.id.to_value(),
        "vector": vector,
        "payload": Value::Object(point.payload.clone()),
    })
}

// ---------------------------------------------------------------------------
// Memory-lean store deserialization: the store file is parsed straight from
// the reader into typed points — vectors never materialize as a JSON Value
// tree (≈5× the typed size at 132MB-store scale), which keeps the reader
// inside the phase-03 RSS gate (Δ < 300MB over a 132MB store).
// ---------------------------------------------------------------------------

#[derive(serde::Deserialize)]
struct StoreFileDe {
    #[serde(default)]
    collections: BTreeMap<String, CollectionDe>,
}

#[derive(serde::Deserialize)]
struct CollectionDe {
    #[serde(default)]
    vectors_config: Map<String, Value>,
    #[serde(default)]
    points: Vec<PointDe>,
    #[serde(default)]
    payload_indexes: Vec<Value>,
}

#[derive(serde::Deserialize)]
struct PointDe {
    id: PointIdDe,
    #[serde(default)]
    vector: PointVectorsDe,
    #[serde(default)]
    payload: Map<String, Value>,
}

struct PointIdDe(PointId);

impl<'de> serde::Deserialize<'de> for PointIdDe {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl serde::de::Visitor<'_> for Visitor {
            type Value = PointIdDe;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("an unsigned integer or a string point id")
            }
            fn visit_u64<E: serde::de::Error>(self, value: u64) -> Result<Self::Value, E> {
                Ok(PointIdDe(PointId::Number(value)))
            }
            fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(PointIdDe(PointId::Text(value.to_string())))
            }
        }
        deserializer.deserialize_any(Visitor)
    }
}

struct PointVectorsDe(BTreeMap<String, Vec<f64>>);impl Default for PointVectorsDe {
    fn default() -> Self {
        Self(BTreeMap::new())
    }
}

impl<'de> serde::Deserialize<'de> for PointVectorsDe {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = BTreeMap<String, Vec<f64>>;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("an unnamed vector array or a named-vector map")
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<Self::Value, A::Error> {
                let mut values = Vec::new();
                while let Some(item) = seq.next_element::<f64>()? {
                    values.push(item);
                }
                let mut map = BTreeMap::new();
                map.insert(String::new(), values);
                Ok(map)
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> Result<Self::Value, A::Error> {
                let mut out = BTreeMap::new();
                while let Some((name, values)) = map.next_entry::<String, Vec<f64>>()? {
                    out.insert(name, values);
                }
                Ok(out)
            }
        }
        deserializer.deserialize_any(Visitor).map(PointVectorsDe)
    }
}

fn parse_store_from_reader<R: std::io::Read>(reader: R) -> StoreResult<StoreData> {
    let file: StoreFileDe = serde_json::from_reader(std::io::BufReader::new(reader))
        .map_err(|err| StoreError::Value(format!("invalid local qdrant store: {err}")))?;
    let mut collections = BTreeMap::new();
    for (name, collection) in file.collections {
        collections.insert(
            name,
            Collection {
                vectors_config: collection.vectors_config,
                points: collection
                    .points
                    .into_iter()
                    .map(|point| Point {
                        id: point.id.0,
                        vector: point.vector.0,
                        payload: point.payload,
                    })
                    .collect(),
                payload_indexes: collection.payload_indexes,
            },
        );
    }
    Ok(StoreData { collections })
}

fn parse_vector(value: &Value) -> StoreResult<BTreeMap<String, Vec<f64>>> {
    if let Some(values) = value.as_array() {
        let mut converted = Vec::with_capacity(values.len());
        for item in values {
            converted.push(item.as_f64().unwrap_or_default());
        }
        let mut map = BTreeMap::new();
        map.insert(String::new(), converted);
        return Ok(map);
    }
    if let Some(named) = value.as_object() {
        let mut map = BTreeMap::new();
        for (name, values) in named {
            let converted = values
                .as_array()
                .map(|items| items.iter().map(|item| item.as_f64().unwrap_or_default()).collect())
                .unwrap_or_default();
            map.insert(name.clone(), converted);
        }
        return Ok(map);
    }
    Err(StoreError::Value("point vector must be an array or named map".to_string()))
}

fn distance_of(collection: &Collection, vector_name: Option<&str>) -> StoreResult<Distance> {
    let config = if collection.vectors_config.len() == 1 {
        collection.vectors_config.values().next()
    } else {
        vector_name.and_then(|name| collection.vectors_config.get(name))
    };
    let distance = config
        .and_then(|config| config.get("distance"))
        .and_then(Value::as_str)
        .unwrap_or("Cosine");
    Distance::parse(distance)
}

fn cosine_score(a: &[f64], b: &[f64]) -> f64 {
    let mut dot = 0.0;
    let mut norm_a = 0.0;
    let mut norm_b = 0.0;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot / (norm_a.sqrt() * norm_b.sqrt())
}

fn dot_score(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

fn euclid_score(a: &[f64], b: &[f64]) -> f64 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| (x - y) * (x - y))
        .sum::<f64>()
        .sqrt()
}

fn score(distance: &Distance, a: &[f64], b: &[f64]) -> f64 {
    match distance {
        Distance::Cosine => cosine_score(a, b),
        Distance::Dot => dot_score(a, b),
        Distance::Euclid => euclid_score(a, b),
    }
}

/// Qdrant filter semantics: `must` (all match), `must_not` (none match),
/// `should` (at least `min_should`, default 1, match) and `has_id` (id
/// membership). Conditions may be field conditions (`key` + `match`/`range`)
/// or nested filters — `stale_filter` emits `must_not: [{has_id: [...]}]`, so
/// honoring that shape is a delete-correctness requirement, not a nicety.
fn point_matches(point: &Point, filter: Option<&Value>) -> bool {
    let Some(filter) = filter else {
        return true;
    };
    filter_matches(point, filter)
}

fn filter_matches(point: &Point, filter: &Value) -> bool {
    if let Some(ids) = filter.get("has_id").and_then(Value::as_array) {
        let matched = ids
            .iter()
            .any(|id| PointId::parse(id).map(|parsed| parsed == point.id).unwrap_or(false));
        if !matched {
            return false;
        }
    }
    if let Some(should) = filter.get("should").and_then(Value::as_array) {
        let min = filter
            .get("min_should")
            .and_then(Value::as_u64)
            .unwrap_or(1) as usize;
        let matched = should
            .iter()
            .filter(|condition| condition_matches(point, condition))
            .count();
        if matched < min {
            return false;
        }
    }
    for condition in filter
        .get("must")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        if !condition_matches(point, condition) {
            return false;
        }
    }
    for sub_filter in filter
        .get("must_not")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        if condition_matches(point, sub_filter) {
            return false;
        }
    }
    true
}

/// A `must`/`must_not`/`should` element: either a nested filter object or a
/// field condition (`key` + `match`/`range`).
fn condition_matches(point: &Point, condition: &Value) -> bool {
    if condition.get("has_id").is_some()
        || condition.get("must").is_some()
        || condition.get("must_not").is_some()
        || condition.get("should").is_some()
    {
        return filter_matches(point, condition);
    }
    field_condition_matches(point, condition)
}

fn field_condition_matches(point: &Point, condition: &Value) -> bool {
    let key = condition.get("key").and_then(Value::as_str).unwrap_or("");
    let mut actual = point.payload.clone();
    for part in key.split('.').filter(|part| !part.is_empty()) {
        let next = actual.get(part).cloned();
        match next {
            Some(Value::Object(nested)) => actual = nested,
            Some(other) => {
                let mut wrapper = Map::new();
                wrapper.insert("__value__".to_string(), other);
                actual = wrapper;
                continue;
            }
            None => return false,
        }
    }
    let actual_value = actual.get("__value__").cloned().unwrap_or(Value::Null);
    if let Some(matcher) = condition.get("match") {
        if let Some(expected) = matcher.get("value")
            && &actual_value != expected {
                return false;
            }
        if let Some(expected) = matcher.get("any").and_then(Value::as_array)
            && !expected.contains(&actual_value) {
                return false;
            }
    }
    if let Some(range) = condition.get("range") {
        let Some(actual_number) = actual_value.as_f64() else {
            return false;
        };
        if let Some(gte) = range.get("gte").and_then(Value::as_f64)
            && actual_number < gte {
                return false;
            }
        if let Some(gt) = range.get("gt").and_then(Value::as_f64)
            && actual_number <= gt {
                return false;
            }
        if let Some(lte) = range.get("lte").and_then(Value::as_f64)
            && actual_number > lte {
                return false;
            }
        if let Some(lt) = range.get("lt").and_then(Value::as_f64)
            && actual_number >= lt {
                return false;
            }
    }
    true
}

// ---------------------------------------------------------------------------
// Per-process client cache (mirrors the Python module-level cache).
// ---------------------------------------------------------------------------

struct CachedClient {
    client: Arc<Mutex<LocalClient>>,
    #[allow(dead_code)]
    lease: StorageLease,
}

fn client_cache() -> &'static Mutex<BTreeMap<String, CachedClient>> {
    static CACHE: OnceLock<Mutex<BTreeMap<String, CachedClient>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// Return a cached client for the resolved path, acquiring the storage lease
/// exactly like the Python `get_client` (`_client_lock` semantics included).
pub fn get_client(resolved: &ResolvedStorage, role: &str) -> StoreResult<Arc<Mutex<LocalClient>>> {
    let path = resolved.path_for_role(role)?;
    let key = path.to_string_lossy().into_owned();
    let mut cache = client_cache()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(existing) = cache.get(&key) {
        return Ok(existing.client.clone());
    }
    let owner_id = if role == "doc" {
        resolved.doc_owner_id.clone()
    } else {
        resolved.code_owner_id.clone()
    };
    let lease = StorageLease::new(path, &resolved.instance_id, &owner_id, "qdrant");
    let lease = match lease.acquire() {
        Ok(lease) => lease,
        Err((_, conflict)) => return Err(StoreError::Runtime(conflict.message)),
    };
    let client = match LocalClient::open(path) {
        Ok(client) => client,
        Err(err) => {
            drop(lease);
            return Err(err);
        }
    };
    let client = Arc::new(Mutex::new(client));
    cache.insert(
        key,
        CachedClient {
            client: client.clone(),
            lease,
        },
    );
    Ok(client)
}

/// Drop cached clients and release their leases (test-only convenience).
pub fn reset_clients() {
    let mut cache = client_cache()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    cache.clear();
}

/// Single owner of vector operations for one storage role (`LocalQdrantStore`).
pub struct LocalQdrantStore {
    resolved: ResolvedStorage,
    role: String,
    client: Arc<Mutex<LocalClient>>,
}

impl LocalQdrantStore {
    pub fn open(resolved: &ResolvedStorage, role: &str) -> StoreResult<Self> {
        Ok(Self {
            resolved: resolved.clone(),
            role: role.to_string(),
            client: get_client(resolved, role)?,
        })
    }

    pub fn role(&self) -> &str {
        &self.role
    }

    pub fn path(&self) -> StoreResult<PathBuf> {
        Ok(self.resolved.path_for_role(&self.role)?.to_path_buf())
    }

    fn with_collection<T>(
        &self,
        name: &str,
        body: impl FnOnce(&mut Collection) -> StoreResult<T>,
        persist: bool,
    ) -> StoreResult<T> {
        let mut client = lock_client(&self.client);
        let result = {
            let collection = client
                .data
                .collections
                .get_mut(name)
                .ok_or_else(|| StoreError::Value(format!("Collection `{name}` doesn't exist!")))?;
            body(collection)?
        };
        if persist {
            client.flush()?;
        }
        Ok(result)
    }

    // ── collections ─────────────────────────────────────────────────────────

    pub fn list_collection_names(&self) -> StoreResult<Vec<String>> {
        let client = lock_client(&self.client);
        Ok(client.data.collections.keys().cloned().collect())
    }

    pub fn collection_exists(&self, name: &str) -> bool {
        lock_client(&self.client)
            .data
            .collections
            .contains_key(name)
    }

    pub fn get_collection_info(&self, name: &str) -> StoreResult<Value> {
        let client = lock_client(&self.client);
        let collection = client
            .data
            .collections
            .get(name)
            .ok_or_else(|| StoreError::Value(format!("Collection `{name}` doesn't exist!")))?;
        Ok(collection_info_value(name, collection))
    }

    pub fn create_collection(&self, name: &str, vectors_config: &Value) -> StoreResult<()> {
        let mut client = lock_client(&self.client);
        if client.data.collections.contains_key(name) {
            return Err(StoreError::Value(format!(
                "Collection `{name}` already exists!"
            )));
        }
        client.data.collections.insert(
            name.to_string(),
            Collection {
                vectors_config: normalize_vectors_config(vectors_config),
                points: Vec::new(),
                payload_indexes: Vec::new(),
            },
        );
        client.flush()
    }

    pub fn recreate_collection(&self, name: &str, vectors_config: &Value) -> StoreResult<()> {
        let mut client = lock_client(&self.client);
        client.data.collections.remove(name);
        client.data.collections.insert(
            name.to_string(),
            Collection {
                vectors_config: normalize_vectors_config(vectors_config),
                points: Vec::new(),
                payload_indexes: Vec::new(),
            },
        );
        client.flush()
    }

    pub fn delete_collection(&self, name: &str) -> StoreResult<()> {
        let mut client = lock_client(&self.client);
        client.data.collections.remove(name);
        client.flush()
    }

    // ── points ──────────────────────────────────────────────────────────────

    pub fn upsert(&self, collection_name: &str, points: &[Value]) -> StoreResult<Value> {
        self.upsert_impl(collection_name, points, true)?;
        Ok(json!({"status": "completed", "operation_id": null}))
    }

    /// Bulk ingest WITHOUT the per-call full-store flush — the persist-mode
    /// bulk seam (phase-01): a sync pass defers every batch and calls
    /// [`LocalQdrantStore::flush`] once at the end, killing the O(n²)
    /// serialize-between-ops cost. Unflushed mutations vanish if the process
    /// dies before [`LocalQdrantStore::flush`] — the pass-level durability
    /// contract (only the final flush matters).
    pub fn upsert_deferred(&self, collection_name: &str, points: &[Value]) -> StoreResult<()> {
        self.upsert_impl(collection_name, points, false)
    }

    fn upsert_impl(
        &self,
        collection_name: &str,
        points: &[Value],
        persist: bool,
    ) -> StoreResult<()> {
        self.with_collection(
            collection_name,
            |collection| {
                for point_value in points {
                    let id = PointId::parse(point_value.get("id").unwrap_or(&Value::Null))?;
                    let vector = parse_vector(&point_value.get("vector").cloned().unwrap_or(Value::Null))?;
                    let payload = point_value
                        .get("payload")
                        .and_then(Value::as_object)
                        .cloned()
                        .unwrap_or_default();
                    let point = Point {
                        id: id.clone(),
                        vector,
                        payload,
                    };
                    match collection
                        .points
                        .iter_mut()
                        .find(|existing| existing.id == point.id)
                    {
                        Some(existing) => *existing = point,
                        None => collection.points.push(point),
                    }
                }
                Ok(())
            },
            persist,
        )
    }

    /// Serialize the whole store now (one atomic rename) — the single flush
    /// that closes a deferred persist-mode pass.
    pub fn flush(&self) -> StoreResult<()> {
        self.client.lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .flush()
    }

    /// Total bytes this store's client has serialized to disk — benchmark
    /// signal for the persist-mode gate (bytes + wall-time, not flush
    /// counts).
    pub fn flush_bytes_written(&self) -> u64 {
        self.client
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .flush_bytes
            .load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn upload_points(&self, collection_name: &str, points: &[Value]) -> StoreResult<Value> {
        self.upsert(collection_name, points)
    }

    /// KNN search with exact brute-force scoring (`search`).
    #[allow(clippy::too_many_arguments)]
    pub fn search(
        &self,
        collection_name: &str,
        query_vector: &[f64],
        limit: usize,
        query_filter: Option<&Value>,
        with_payload: bool,
        with_vectors: bool,
        vector_name: Option<&str>,
    ) -> StoreResult<Vec<Value>> {
        self.with_collection(
            collection_name,
            |collection| {
                search_in_collection(
                    collection,
                    query_vector,
                    limit,
                    query_filter,
                    with_payload,
                    with_vectors,
                    vector_name,
                )
            },
            false,
        )
    }

    /// Paginated scan ordered by point id (`scroll`).
    pub fn scroll(
        &self,
        collection_name: &str,
        scroll_filter: Option<&Value>,
        limit: usize,
        with_payload: bool,
        with_vectors: bool,
        offset: Option<&Value>,
    ) -> StoreResult<(Vec<Value>, Option<Value>)> {
        self.with_collection(
            collection_name,
            |collection| {
                scroll_in_collection(collection, scroll_filter, limit, with_payload, with_vectors, offset)
            },
            false,
        )
    }

    /// Fetch points by id (`retrieve`).
    pub fn retrieve(
        &self,
        collection_name: &str,
        ids: &[Value],
        with_payload: bool,
        with_vectors: bool,
    ) -> StoreResult<Vec<Value>> {
        self.with_collection(
            collection_name,
            |collection| retrieve_in_collection(collection, ids, with_payload, with_vectors),
            false,
        )
    }

    /// Exact point count (`count`).
    pub fn count(&self, collection_name: &str, count_filter: Option<&Value>) -> StoreResult<i64> {
        self.with_collection(
            collection_name,
            |collection| count_in_collection(collection, count_filter),
            false,
        )
    }

    /// Delete by ids or filter (`delete`).
    pub fn delete(
        &self,
        collection_name: &str,
        points_selector_ids: Option<&[Value]>,
        filter_selector: Option<&Value>,
    ) -> StoreResult<Value> {
        self.delete_impl(collection_name, points_selector_ids, filter_selector, true)?;
        Ok(json!({"status": "completed", "operation_id": null}))
    }

    /// Deferred (unflushed) delete for persist-mode bulk passes — the
    /// closing [`LocalQdrantStore::flush`] persists upserts and deletes
    /// together in one serialization.
    pub fn delete_deferred(
        &self,
        collection_name: &str,
        points_selector_ids: Option<&[Value]>,
        filter_selector: Option<&Value>,
    ) -> StoreResult<()> {
        self.delete_impl(collection_name, points_selector_ids, filter_selector, false)
    }

    fn delete_impl(
        &self,
        collection_name: &str,
        points_selector_ids: Option<&[Value]>,
        filter_selector: Option<&Value>,
        persist: bool,
    ) -> StoreResult<()> {
        if points_selector_ids.is_none() && filter_selector.is_none() {
            return Err(StoreError::Value(
                "delete requires point IDs or a filter selector".to_string(),
            ));
        }
        self.with_collection(
            collection_name,
            |collection| {
                let ids: Vec<PointId> = match points_selector_ids {
                    Some(ids) => ids
                        .iter()
                        .map(PointId::parse)
                        .collect::<StoreResult<_>>()?,
                    None => Vec::new(),
                };
                collection.points.retain(|point| {
                    if !ids.is_empty() {
                        !ids.contains(&point.id)
                    } else {
                        !point_matches(point, filter_selector)
                    }
                });
                Ok(())
            },
            persist,
        )
    }

    /// Merge payload onto points (`set_payload`).
    pub fn set_payload(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
    ) -> StoreResult<Value> {
        if points.is_none() && filter.is_none() {
            return Err(StoreError::Value(
                "set_payload requires point IDs or a filter".to_string(),
            ));
        }
        self.apply_payload(collection_name, payload, points, filter, false)?;
        Ok(json!({"status": "completed", "operation_id": null}))
    }

    /// Replace payload of points (`overwrite_payload`).
    pub fn overwrite_payload(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
    ) -> StoreResult<Value> {
        if points.is_none() && filter.is_none() {
            return Err(StoreError::Value(
                "overwrite_payload requires point IDs or a filter".to_string(),
            ));
        }
        self.apply_payload(collection_name, payload, points, filter, true)?;
        Ok(json!({"status": "completed", "operation_id": null}))
    }

    fn apply_payload(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
        overwrite: bool,
    ) -> StoreResult<()> {
        self.with_collection(
            collection_name,
            |collection| {
                let ids: Vec<PointId> = match points {
                    Some(ids) => ids
                        .iter()
                        .map(PointId::parse)
                        .collect::<StoreResult<_>>()?,
                    None => Vec::new(),
                };
                let payload_object = payload.as_object().cloned().unwrap_or_default();
                for point in &mut collection.points {
                    let selected = if !ids.is_empty() {
                        ids.contains(&point.id)
                    } else {
                        point_matches(point, filter)
                    };
                    if !selected {
                        continue;
                    }
                    if overwrite {
                        point.payload = payload_object.clone();
                    } else {
                        for (key, value) in &payload_object {
                            point.payload.insert(key.clone(), value.clone());
                        }
                    }
                }
                Ok(())
            },
            true,
        )
    }

    pub fn create_payload_index(
        &self,
        collection_name: &str,
        field_name: &str,
        field_schema: Option<&Value>,
    ) -> StoreResult<()> {
        self.with_collection(
            collection_name,
            |collection| {
                // Idempotent like the remote server: re-issuing the same
                // index must not accumulate duplicate payload_indexes rows
                // across syncs.
                let exists = collection.payload_indexes.iter().any(|index| {
                    index.get("field_name").and_then(Value::as_str) == Some(field_name)
                });
                if !exists {
                    collection.payload_indexes.push(json!({
                        "field_name": field_name,
                        "field_schema": field_schema
                            .cloned()
                            .unwrap_or_else(|| json!("keyword")),
                    }));
                }
                Ok(())
            },
            true,
        )
    }

    // ── lifecycle ───────────────────────────────────────────────────────────

    /// Close only the client owned by *role*; the process-level cache is
    /// cleared explicitly via [`reset_clients`].
    pub fn close(&self) {
        let mut cache = client_cache()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Ok(path) = self.path() {
            cache.remove(&path.to_string_lossy().into_owned());
        }
    }
}

impl std::fmt::Debug for LocalQdrantStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "LocalQdrantStore(role={:?})", self.role)
    }
}

fn lock_client(
    client: &Arc<Mutex<LocalClient>>,
) -> std::sync::MutexGuard<'_, LocalClient> {
    client
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

// ---------------------------------------------------------------------------
// Shared read paths — LocalQdrantStore (lease-holding writer) and
// LocalQdrantReader (lock-free reader) must score/shape hits identically.
// ---------------------------------------------------------------------------

#[allow(clippy::too_many_arguments)]
fn search_in_collection(
    collection: &Collection,
    query_vector: &[f64],
    limit: usize,
    query_filter: Option<&Value>,
    with_payload: bool,
    with_vectors: bool,
    vector_name: Option<&str>,
) -> StoreResult<Vec<Value>> {
    let distance = distance_of(collection, vector_name)?;
    let stored_key = match vector_name {
        Some(name) => name.to_string(),
        None => collection
            .points
            .first()
            .and_then(|point| point.vector.keys().next().cloned())
            .unwrap_or_default(),
    };
    let mut scored: Vec<(f64, &Point)> = collection
        .points
        .iter()
        .filter(|point| point_matches(point, query_filter))
        .filter_map(|point| {
            point
                .vector
                .get(&stored_key)
                .map(|vector| (score(&distance, query_vector, vector), point))
        })
        .collect();
    scored.sort_by(|a, b| {
        let ordering = b
            .0
            .partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal);
        if ordering == std::cmp::Ordering::Equal && !matches!(distance, Distance::Euclid) {
            a.1.id.sort_key().cmp(&b.1.id.sort_key())
        } else if ordering == std::cmp::Ordering::Equal {
            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
        } else {
            ordering
        }
    });
    Ok(scored
        .into_iter()
        .take(limit)
        .map(|(score_value, point)| {
            let mut entry = Map::new();
            entry.insert("id".to_string(), point.id.to_value());
            entry.insert("score".to_string(), json!(score_value));
            if with_payload {
                entry.insert(
                    "payload".to_string(),
                    Value::Object(point.payload.clone()),
                );
            } else {
                entry.insert("payload".to_string(), Value::Null);
            }
            if with_vectors {
                entry.insert("vector".to_string(), point_to_value(point)["vector"].clone());
            } else {
                entry.insert("vector".to_string(), Value::Null);
            }
            Value::Object(entry)
        })
        .collect())
}

fn scroll_in_collection(
    collection: &Collection,
    scroll_filter: Option<&Value>,
    limit: usize,
    with_payload: bool,
    with_vectors: bool,
    offset: Option<&Value>,
) -> StoreResult<(Vec<Value>, Option<Value>)> {
    let mut ordered: Vec<&Point> = collection
        .points
        .iter()
        .filter(|point| point_matches(point, scroll_filter))
        .collect();
    ordered.sort_by_key(|point| point.id.sort_key());
    let start = match offset {
        Some(offset) => {
            let offset_id = PointId::parse(offset)?;
            ordered
                .iter()
                .position(|point| point.id.sort_key() > offset_id.sort_key())
                .unwrap_or(ordered.len())
        }
        None => 0,
    };
    let page: Vec<Value> = ordered
        .iter()
        .skip(start)
        .take(limit)
        .map(|point| {
            let mut entry = Map::new();
            entry.insert("id".to_string(), point.id.to_value());
            entry.insert(
                "payload".to_string(),
                if with_payload {
                    Value::Object(point.payload.clone())
                } else {
                    Value::Null
                },
            );
            entry.insert(
                "vector".to_string(),
                if with_vectors {
                    point_to_value(point)["vector"].clone()
                } else {
                    Value::Null
                },
            );
            Value::Object(entry)
        })
        .collect();
    let next_offset = ordered
        .get(start + limit)
        .map(|point| point.id.to_value());
    Ok((page, next_offset))
}

fn retrieve_in_collection(
    collection: &Collection,
    ids: &[Value],
    with_payload: bool,
    with_vectors: bool,
) -> StoreResult<Vec<Value>> {
    let mut parsed: Vec<PointId> = Vec::with_capacity(ids.len());
    for id in ids {
        parsed.push(PointId::parse(id)?);
    }
    let mut found: Vec<Value> = Vec::new();
    for id in parsed {
        if let Some(point) = collection.points.iter().find(|point| point.id == id) {
            let mut entry = Map::new();
            entry.insert("id".to_string(), point.id.to_value());
            entry.insert(
                "payload".to_string(),
                if with_payload {
                    Value::Object(point.payload.clone())
                } else {
                    Value::Null
                },
            );
            entry.insert(
                "vector".to_string(),
                if with_vectors {
                    point_to_value(point)["vector"].clone()
                } else {
                    Value::Null
                },
            );
            found.push(Value::Object(entry));
        }
    }
    Ok(found)
}

fn count_in_collection(collection: &Collection, count_filter: Option<&Value>) -> StoreResult<i64> {
    Ok(collection
        .points
        .iter()
        .filter(|point| point_matches(point, count_filter))
        .count() as i64)
}

// ---------------------------------------------------------------------------
// Read-only client (phase-03 reader wire): NO lease, mtime+size revalidated
// snapshots. Durability of the writer is `write_atomic` (temp + fsync +
// rename), so a reader without a lock always observes either the previous or
// the new store file, never a torn one — the long-lived MCP process must not
// hold the exclusive flock or every `dev sync code` would fail-fast (D4).
// ---------------------------------------------------------------------------

/// Lock-free reader over one `cortex-local-store.json`. The parsed snapshot
/// is cached and revalidated against (mtime, len) on every burst; a write
/// always lands via atomic rename so (mtime, len) changes together.
pub struct LocalQdrantReader {
    path: PathBuf,
    snapshot: std::sync::Mutex<Option<(std::time::SystemTime, u64, std::sync::Arc<StoreData>)>>,
}

impl LocalQdrantReader {
    /// Open by store-root path. Loud errors: missing store dir and
    /// legacy-pickle-only roots (the re-index guard) — never a silent empty
    /// read.
    pub fn open(path: &Path) -> StoreResult<Self> {
        let path = crate::util::resolve_path(path);
        if !path.is_dir() {
            return Err(StoreError::Value(format!(
                "qdrant local store not found: {}",
                path.display()
            )));
        }
        legacy_store_guard(&path)?;
        Ok(Self {
            path,
            snapshot: std::sync::Mutex::new(None),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Current store snapshot, revalidated against the store file's
    /// (mtime, len). An absent store file means an empty store (fresh root
    /// or wiped root) — never the previously cached data.
    fn data(&self) -> StoreResult<std::sync::Arc<StoreData>> {
        let store_file = self.path.join(STORE_FILE_NAME);
        let token = match std::fs::metadata(&store_file) {
            Ok(meta) => Some((
                meta.modified()
                    .map_err(StoreError::Io)
                    .unwrap_or(std::time::SystemTime::UNIX_EPOCH),
                meta.len(),
            )),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
            Err(err) => return Err(StoreError::Io(err)),
        };
        let Some((mtime, len)) = token else {
            *self
                .snapshot
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
            return Ok(std::sync::Arc::new(StoreData::default()));
        };
        let mut cached = self
            .snapshot
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some((cached_mtime, cached_len, data)) = cached.as_ref() {
            if *cached_mtime == mtime && *cached_len == len {
                return Ok(data.clone());
            }
        }
        let file = match std::fs::File::open(&store_file) {
            Ok(file) => file,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                // Raced with a delete between metadata and read — report
                // honestly; the next call serves the empty store.
                return Err(StoreError::Value(
                    "local qdrant store file vanished mid-read".to_string(),
                ));
            }
            Err(err) => return Err(StoreError::Io(err)),
        };
        let collections = parse_store_from_reader(std::io::BufReader::new(file))?.collections;
        let data = std::sync::Arc::new(StoreData { collections });
        *cached = Some((mtime, len, data.clone()));
        Ok(data)
    }

    pub fn list_collection_names(&self) -> StoreResult<Vec<String>> {
        let data = self.data()?;
        Ok(data.collections.keys().cloned().collect())
    }

    pub fn collection_exists(&self, name: &str) -> StoreResult<bool> {
        let data = self.data()?;
        Ok(data.collections.contains_key(name))
    }

    pub fn get_collection_info(&self, name: &str) -> StoreResult<Value> {
        let data = self.data()?;
        let collection = data
            .collections
            .get(name)
            .ok_or_else(|| StoreError::Value(format!("Collection `{name}` doesn't exist!")))?;
        Ok(collection_info_value(name, collection))
    }

    /// KNN search — identical scoring and hit shape to
    /// [`LocalQdrantStore::search`]. Native hits carry no `version` field
    /// (the sidecar's QdrantLocal hits do) — shape-audit phase01-engine.md.
    #[allow(clippy::too_many_arguments)]
    pub fn search(
        &self,
        collection_name: &str,
        query_vector: &[f64],
        limit: usize,
        query_filter: Option<&Value>,
        with_payload: bool,
        with_vectors: bool,
        vector_name: Option<&str>,
    ) -> StoreResult<Vec<Value>> {
        let data = self.data()?;
        let collection = data
            .collections
            .get(collection_name)
            .ok_or_else(|| StoreError::Value(format!("Collection `{collection_name}` doesn't exist!")))?;
        search_in_collection(
            collection,
            query_vector,
            limit,
            query_filter,
            with_payload,
            with_vectors,
            vector_name,
        )
    }

    pub fn count(&self, collection_name: &str, count_filter: Option<&Value>) -> StoreResult<i64> {
        let data = self.data()?;
        let collection = data
            .collections
            .get(collection_name)
            .ok_or_else(|| StoreError::Value(format!("Collection `{collection_name}` doesn't exist!")))?;
        count_in_collection(collection, count_filter)
    }
}

impl std::fmt::Debug for LocalQdrantReader {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "LocalQdrantReader(path={:?})", self.path)
    }
}

// ---------------------------------------------------------------------------
// CORTEX_VECTOR_BACKEND hatch (D5): the local-lane backend selector shared by
// the sync writer and the MCP reader.
// ---------------------------------------------------------------------------

pub const VECTOR_BACKEND_ENV: &str = "CORTEX_VECTOR_BACKEND";

/// Phase-05 flip point: before it only an explicit `CORTEX_VECTOR_BACKEND=rust`
/// opts the local lane into the native JSON engine; after it an unset value
/// means native and only `=python` keeps the lane frozen (writer) /
/// sidecar-routed (reader).
pub const LOCAL_NATIVE_DEFAULT: bool = false;

/// Whether the LOCAL code-vector lane runs native right now. `=rust` → yes,
/// `=python` → never, unset/unknown → [`LOCAL_NATIVE_DEFAULT`]. The REMOTE
/// lane is unaffected by this flag.
pub fn local_native_enabled() -> bool {
    let flag = std::env::var(VECTOR_BACKEND_ENV)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    match flag.as_deref() {
        Some("rust") => true,
        Some("python") => false,
        _ => LOCAL_NATIVE_DEFAULT,
    }
}

fn normalize_vectors_config(vectors_config: &Value) -> Map<String, Value> {
    // Accept VectorParams-shaped dicts or {"vectors": {...}} collection
    // configs; store under an empty name for the unnamed vector.
    if let Some(named) = vectors_config.get("vectors").and_then(Value::as_object) {
        // `{"vectors": {"size": N, "distance": ...}}` is the ANONYMOUS
        // vector (ensure_collection's create body), not a named-vector map
        // — keys "size"/"distance" would otherwise be mistaken for vector
        // names and the drift guard would lose the dimension.
        if named.contains_key("size") {
            let mut out = Map::new();
            out.insert(String::new(), Value::Object(named.clone()));
            return out;
        }
        return named.clone();
    }
    let mut out = Map::new();
    out.insert(String::new(), vectors_config.clone());
    out
}

/// `vectors_config` map → REST `config.params.vectors` value: the unnamed
/// vector serializes as the bare VectorParams (`{"size": N, ...}`), named
/// vectors as the name→params map. `vector_sizes` (cortex-sync) and the MCP
/// sizes parser consume exactly this shape.
fn vectors_config_to_rest(vectors_config: &Map<String, Value>) -> Value {
    if vectors_config.len() == 1 && vectors_config.contains_key("") {
        return vectors_config[""].clone();
    }
    Value::Object(vectors_config.clone())
}

fn collection_info_value(name: &str, collection: &Collection) -> Value {
    json!({
        "status": "green",
        "result": {
            "name": name,
            "points_count": collection.points.len(),
            "vectors_count": collection.points.len(),
            "config": {
                "params": {
                    "vectors": vectors_config_to_rest(&collection.vectors_config),
                },
            },
            "payload_indexes": collection.payload_indexes,
        },
    })
}

/// Translate a plain `{"field": "x", "match": {"value": ...}}` filter list
/// into a `models.Filter`-shaped value (`build_filter`).
pub fn build_filter(conditions: &[Value]) -> Value {
    let must: Vec<Value> = conditions.to_vec();
    json!({"must": must})
}
