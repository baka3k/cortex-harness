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
}

impl LocalClient {
    fn open(path: &Path) -> StoreResult<Self> {
        std::fs::create_dir_all(path).map_err(StoreError::Io)?;
        let store_file = path.join(STORE_FILE_NAME);
        let data = match std::fs::read_to_string(&store_file) {
            Ok(text) => {
                let payload: Value = serde_json::from_str(&text).map_err(|err| {
                    StoreError::Value(format!("invalid local qdrant store: {err}"))
                })?;
                let mut collections = BTreeMap::new();
                if let Some(entries) = payload.get("collections").and_then(Value::as_object) {
                    for (name, value) in entries {
                        collections.insert(name.clone(), collection_from_value(value)?);
                    }
                }
                StoreData { collections }
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => StoreData::default(),
            Err(err) => return Err(StoreError::Io(err)),
        };
        Ok(Self {
            path: path.to_path_buf(),
            data,
        })
    }

    fn flush(&self) -> StoreResult<()> {
        let mut collections = Map::new();
        for (name, collection) in &self.data.collections {
            collections.insert(name.clone(), collection_to_value(collection));
        }
        let payload = json!({"collections": Value::Object(collections)});
        crate::util::write_atomic(
            &self.path.join(STORE_FILE_NAME),
            &crate::util::canonical_json(&payload),
        )?;
        Ok(())
    }
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

fn collection_from_value(value: &Value) -> StoreResult<Collection> {
    let config = value
        .get("vectors_config")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut points = Vec::new();
    for item in value
        .get("points")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
    {
        let id = PointId::parse(item.get("id").unwrap_or(&Value::Null))?;
        let vector_value = item.get("vector").cloned().unwrap_or(Value::Null);
        let vector = parse_vector(&vector_value)?;
        let payload = item
            .get("payload")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        points.push(Point {
            id,
            vector,
            payload,
        });
    }
    Ok(Collection {
        vectors_config: config,
        points,
        payload_indexes: value
            .get("payload_indexes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
    })
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

fn point_matches(point: &Point, filter: Option<&Value>) -> bool {
    let Some(filter) = filter else {
        return true;
    };
    let must = filter
        .get("must")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for condition in must {
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
        Ok(json!({
            "status": "green",
            "result": {
                "name": name,
                "points_count": collection.points.len(),
                "vectors_count": collection.points.len(),
                "payload_indexes": collection.payload_indexes,
            },
        }))
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
            true,
        )?;
        Ok(json!({"status": "completed", "operation_id": null}))
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
            |collection| {
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
            },
            false,
        )
    }

    /// Exact point count (`count`).
    pub fn count(&self, collection_name: &str, count_filter: Option<&Value>) -> StoreResult<i64> {
        self.with_collection(
            collection_name,
            |collection| {
                Ok(collection
                    .points
                    .iter()
                    .filter(|point| point_matches(point, count_filter))
                    .count() as i64)
            },
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
            true,
        )?;
        Ok(json!({"status": "completed", "operation_id": null}))
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
                collection.payload_indexes.push(json!({
                    "field_name": field_name,
                    "field_schema": field_schema
                        .cloned()
                        .unwrap_or_else(|| json!("keyword")),
                }));
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

fn normalize_vectors_config(vectors_config: &Value) -> Map<String, Value> {
    // Accept VectorParams-shaped dicts or {"vectors": {...}} collection
    // configs; store under an empty name for the unnamed vector.
    if let Some(named) = vectors_config.get("vectors").and_then(Value::as_object) {
        return named.clone();
    }
    let mut out = Map::new();
    out.insert(String::new(), vectors_config.clone());
    out
}

/// Translate a plain `{"field": "x", "match": {"value": ...}}` filter list
/// into a `models.Filter`-shaped value (`build_filter`).
pub fn build_filter(conditions: &[Value]) -> Value {
    let must: Vec<Value> = conditions.to_vec();
    json!({"must": must})
}
