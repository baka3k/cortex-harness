//! Embedding-input artifact — the phase-01 frozen contract, activated by
//! phase-06 (`plans/260915-analyzer-layer-rust-cutover`).
//!
//! Rust children in the orchestrator-level embedding pass serialize the same
//! writer rows they would hand the graph writer (minus `relations`/`calls`,
//! which never become vector documents) so the orchestrator — not the child
//! — owns text construction, redaction, point ids and qdrant writes
//! (vector_sync.rs in cortex-sync). The payload crosses as plaintext, so the
//! file is written atomically with mode 0600 at the orchestrator-chosen path
//! (phase-01 contract: format + cache location + 0600 + version field).

use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Artifact schema version. Bump on any field-meaning change; the reader
/// hard-errors on versions it does not know (key decision #4 — never
/// silently drop a vector lane over a stale binary).
pub const EMBEDDING_ARTIFACT_SCHEMA_VERSION: u64 = 1;

/// One `(category, rows)` pair serialized as the JSON tuple
/// `["files", [ ... ]]`. An array of pairs (not an object) because the
/// category ORDER is parity-relevant: `documents_from_rows` consumes Python's
/// dict insertion order, and JSON objects must not be trusted to carry it.
pub type EmbeddingCategory = (String, Vec<Value>);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbeddingInputArtifact {
    pub schema_version: u64,
    pub generated_at: String,
    pub parser: String,
    pub project_id: String,
    pub root_scope: String,
    /// Python `full_replace = not args.incremental and _scanned_directory`.
    pub full_replace: bool,
    /// Python `_scanned_directory` — the cleanup edge case below needs it.
    pub scanned_directory: bool,
    /// Python `_selected_rel_paths` (extension-filtered, as scanned).
    pub files_selected: Vec<String>,
    /// Python `_deleted_rel_paths`.
    pub files_deleted: Vec<String>,
    pub categories: Vec<EmbeddingCategory>,
}

/// What a child supplies; `generated_at`/`schema_version` are filled in.
#[derive(Debug, Clone)]
pub struct EmbeddingEmission<'a> {
    pub parser: &'a str,
    pub project_id: &'a str,
    pub root_scope: &'a str,
    pub full_replace: bool,
    pub scanned_directory: bool,
    pub files_selected: Vec<String>,
    pub files_deleted: Vec<String>,
    pub categories: Vec<EmbeddingCategory>,
}

impl EmbeddingInputArtifact {
    /// Parse + version-check a child-emitted artifact.
    pub fn from_json_str(text: &str) -> Result<Self, String> {
        let artifact: Self =
            serde_json::from_str(text).map_err(|error| format!("embedding artifact: {error}"))?;
        if artifact.schema_version != EMBEDDING_ARTIFACT_SCHEMA_VERSION {
            return Err(format!(
                "embedding artifact schema_version {} unsupported (expected {EMBEDDING_ARTIFACT_SCHEMA_VERSION}) — rebuild the analyzer binaries",
                artifact.schema_version
            ));
        }
        Ok(artifact)
    }
}

fn iso_utc_now() -> String {
    // Days-from-civil (Howard Hinnant) — UTC only, second resolution.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or_default();
    let z = secs.div_euclid(86_400) + 719_468;
    let era = z / 146_097;
    let doe = z % 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - yoe * 365 - yoe / 4 + yoe / 100;
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u64;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u64;
    let y = (yoe + era * 400 + if m <= 2 { 1 } else { 0 }) as u64;
    let tod = secs.rem_euclid(86_400) as u64;
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        tod / 3600,
        (tod % 3600) / 60,
        tod % 60
    )
}

/// Atomic 0600 write — same envelope as cortex-sync's `write_summary`
/// (tmp + chmod + rename); the artifact embeds source plaintext, world- or
/// group-readable is a finding (red-team S6).
pub fn write_embedding_artifact(path: &Path, artifact: &EmbeddingInputArtifact) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("embedding artifact dir: {error}"))?;
    }
    let encoded = serde_json::to_string(artifact)
        .map_err(|error| format!("embedding artifact encode: {error}"))?;
    let temporary = path.with_file_name(format!(
        ".{}.{}.{}.tmp",
        path.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default(),
    ));
    let mut handle = std::fs::File::create(&temporary)
        .map_err(|error| format!("embedding artifact tmp: {error}"))?;
    handle
        .write_all(encoded.as_bytes())
        .and_then(|()| handle.sync_all())
        .map_err(|error| {
            let _ = std::fs::remove_file(&temporary);
            format!("embedding artifact write: {error}")
        })?;
    drop(handle);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("embedding artifact mode: {error}"))?;
    }
    std::fs::rename(&temporary, path).map_err(|error| {
        let _ = std::fs::remove_file(&temporary);
        format!("embedding artifact rename: {error}")
    })
}

/// Emit the artifact when (and only when) the orchestrator asked for it via
/// `--embedding-input-output`. Returns the path written. Children call this
/// where they build graph rows; nothing about the graph pass changes.
pub fn maybe_emit_embedding_artifact(
    output_path: Option<&str>,
    emission: EmbeddingEmission<'_>,
) -> Result<Option<PathBuf>, String> {
    let Some(path) = output_path.filter(|value| !value.trim().is_empty()) else {
        return Ok(None);
    };
    let artifact = EmbeddingInputArtifact {
        schema_version: EMBEDDING_ARTIFACT_SCHEMA_VERSION,
        generated_at: iso_utc_now(),
        parser: emission.parser.to_string(),
        project_id: emission.project_id.to_string(),
        root_scope: emission.root_scope.to_string(),
        full_replace: emission.full_replace,
        scanned_directory: emission.scanned_directory,
        files_selected: emission.files_selected,
        files_deleted: emission.files_deleted,
        categories: emission.categories,
    };
    write_embedding_artifact(Path::new(path), &artifact)?;
    println!(
        "[embedding] artifact written path={} categories={} documents={}",
        path,
        artifact.categories.len(),
        artifact
            .categories
            .iter()
            .map(|(_, rows)| rows.len())
            .sum::<usize>(),
    );
    Ok(Some(PathBuf::from(path)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn emission() -> EmbeddingEmission<'static> {
        EmbeddingEmission {
            parser: "go",
            project_id: "proj",
            root_scope: "proj/repo",
            full_replace: false,
            scanned_directory: true,
            files_selected: vec!["a.go".to_string()],
            files_deleted: vec![],
            categories: vec![(
                "functions".to_string(),
                vec![json!({"id": "f1", "project_id": "proj"})],
            )],
        }
    }

    #[test]
    fn writes_artifact_at_mode_0600_and_roundtrips() {
        let dir = std::env::temp_dir().join(format!("cortex-embed-art-{}", std::process::id()));
        let path = dir.join("go_embedding_input.json");
        maybe_emit_embedding_artifact(Some(path.to_str().unwrap()), emission())
            .unwrap()
            .expect("flag set → written");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600, "artifact must be 0600, got {mode:#o}");
        }
        let text = std::fs::read_to_string(&path).unwrap();
        let parsed = EmbeddingInputArtifact::from_json_str(&text).unwrap();
        assert_eq!(parsed.schema_version, EMBEDDING_ARTIFACT_SCHEMA_VERSION);
        assert_eq!(parsed.parser, "go");
        assert_eq!(parsed.categories[0].0, "functions");
        assert_eq!(parsed.files_selected, vec!["a.go".to_string()]);
        assert!(!parsed.full_replace);
        assert!(parsed.generated_at.ends_with('Z'));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn no_flag_is_no_op() {
        assert!(maybe_emit_embedding_artifact(None, emission()).unwrap().is_none());
        assert!(maybe_emit_embedding_artifact(Some("   "), emission()).unwrap().is_none());
    }

    #[test]
    fn unknown_schema_version_fails_loud() {
        let mut value = json!({
            "schema_version": 99,
            "generated_at": "", "parser": "go", "project_id": "p",
            "root_scope": "r", "full_replace": false, "scanned_directory": true,
            "files_selected": [], "files_deleted": [], "categories": []
        });
        let error =
            EmbeddingInputArtifact::from_json_str(&serde_json::to_string(&value).unwrap())
                .unwrap_err();
        assert!(error.contains("schema_version 99 unsupported"), "{error}");
        value.as_object_mut().unwrap().insert("schema_version".into(), json!(1));
        assert!(
            EmbeddingInputArtifact::from_json_str(&serde_json::to_string(&value).unwrap()).is_ok()
        );
    }
}
