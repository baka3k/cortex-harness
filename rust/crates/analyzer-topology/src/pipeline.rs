//! Port `tools/project_topology/pipeline.py` + `detector.iter_descriptor_paths`
//! + `registry.descriptor_candidates` kết hợp — analyze_project.

use std::path::Path;

use crate::detector::{iter_descriptor_paths, parse_descriptor_file, MAX_DESCRIPTOR_FILES};
use crate::models::{
    diagnostic_code, AnalysisDiagnostic, DescriptorFact, PyValue, TopologyAnalysisResult,
};
use crate::registry::{descriptor_candidates, descriptor_spec_for_path};
use crate::resolver::resolve_topology;

pub struct AnalyzeInput<'a> {
    pub root: &'a Path,
    pub project_id: &'a str,
    pub changed_paths: Option<&'a [String]>,
    pub deleted_paths: Option<&'a [String]>,
}

/// `analyze_project` — root phải đã resolve (Path.resolve() phía caller).
pub fn analyze_project(input: &AnalyzeInput) -> Result<TopologyAnalysisResult, String> {
    let root_path = input.root;
    let project_id = input.project_id.trim().to_string();
    if project_id.is_empty() {
        return Err("project_id is required".to_string());
    }
    let mut outputs = Vec::new();
    let mut scan_diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    let candidates: Vec<(String, &'static crate::registry::DescriptorSpec)> =
        match input.changed_paths {
        None => {
            let discovered = iter_descriptor_paths(root_path);
            if discovered.len() > MAX_DESCRIPTOR_FILES {
                let mut details = PyValue::dict();
                details.set("limit", PyValue::Int(MAX_DESCRIPTOR_FILES as i64));
                scan_diagnostics.push(
                    AnalysisDiagnostic::new(
                        diagnostic_code::LIMIT_EXCEEDED,
                        "Descriptor discovery reached the project file-count limit.",
                    )
                    .details(details),
                );
            }
            discovered.into_iter().take(MAX_DESCRIPTOR_FILES).collect()
        }
        Some(changed) => {
            let selected = descriptor_candidates(changed.iter().cloned());
            if selected.len() > MAX_DESCRIPTOR_FILES {
                let mut details = PyValue::dict();
                details.set("limit", PyValue::Int(MAX_DESCRIPTOR_FILES as i64));
                scan_diagnostics.push(
                    AnalysisDiagnostic::new(
                        diagnostic_code::LIMIT_EXCEEDED,
                        "Changed descriptor manifest reached the file-count limit.",
                    )
                    .details(details),
                );
            }
            let mut picked = Vec::new();
            for path in selected.into_iter().take(MAX_DESCRIPTOR_FILES) {
                if !root_path.join(&path).is_file() {
                    continue;
                }
                if let Some(spec) = descriptor_spec_for_path(&path) {
                    picked.push((path, spec));
                }
            }
            picked
        }
    };
    for (path, spec) in &candidates {
        outputs.push(parse_descriptor_file(root_path, &project_id, path, spec));
    }
    let mut descriptors: Vec<DescriptorFact> =
        outputs.iter().map(|output| output.descriptor.clone()).collect();
    descriptors.sort_by(|a, b| a.path.cmp(&b.path));
    let dependencies: Vec<crate::models::DependencyFact> = outputs
        .iter()
        .flat_map(|output| output.dependencies.iter().cloned())
        .collect();
    let mut endpoints: Vec<crate::models::EndpointFact> = outputs
        .iter()
        .flat_map(|output| output.endpoints.iter().cloned())
        .collect();
    endpoints.sort_by(|a, b| a.id.cmp(&b.id));
    let extraction_diagnostics: Vec<AnalysisDiagnostic> = outputs
        .iter()
        .flat_map(|output| output.diagnostics.iter().cloned())
        .collect();
    let resolved = resolve_topology(&project_id, &descriptors, &dependencies)?;
    let deleted: Vec<String> = input
        .deleted_paths
        .map(|paths| descriptor_candidates(paths.iter().cloned()))
        .unwrap_or_default();
    let mut diagnostics = scan_diagnostics;
    diagnostics.extend(extraction_diagnostics);
    diagnostics.extend(resolved.diagnostics);
    if !deleted.is_empty() {
        let mut details = PyValue::dict();
        details.set(
            "deleted_paths",
            PyValue::List(deleted.iter().cloned().map(PyValue::Str).collect()),
        );
        diagnostics.push(
            AnalysisDiagnostic::new(
                diagnostic_code::UNRESOLVED_REFERENCE,
                "Deleted descriptor paths require topology-owned graph cleanup before write.",
            )
            .details(details),
        );
    }
    Ok(TopologyAnalysisResult {
        project_id,
        root: root_path.to_string_lossy().to_string(),
        modules: resolved.modules,
        descriptors,
        dependencies: resolved.dependencies,
        endpoints,
        special_files: resolved.special_files,
        frameworks: resolved.frameworks,
        diagnostics,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_project() -> tempdir::TempDir {
        tempdir::TempDir::new("topology")
    }

    #[test]
    fn minimal_root_project() {
        let dir = temp_project();
        std::fs::write(dir.path().join("Cargo.toml"), "[package]\nname = \"demo\"\n").unwrap();
        let result = analyze_project(&AnalyzeInput {
            root: dir.path(),
            project_id: "demo",
            changed_paths: None,
            deleted_paths: None,
        })
        .unwrap();
        assert_eq!(result.descriptors.len(), 1);
        assert_eq!(result.modules.len(), 1);
        assert_eq!(result.modules[0].module_path, ".");
    }
}

/// Minimal temp-dir helper (không thêm dependency tempfile).
#[cfg(test)]
mod tempdir {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    pub struct TempDir {
        path: PathBuf,
    }

    impl TempDir {
        pub fn new(prefix: &str) -> TempDir {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "{prefix}-{}-{unique}",
                std::process::id()
            ));
            std::fs::create_dir_all(&path).expect("temp dir");
            TempDir { path }
        }

        pub fn path(&self) -> &std::path::Path {
            &self.path
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}
