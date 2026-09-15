//! Port `tools/project_topology/detector.py` — descriptor discovery và bounded
//! file loading (skip dirs, symlink guard, path-escape guard, size limit).

use std::path::Path;

use crate::models::{
    confidence, descriptor_type, normalize_module_path, parse_depth, AnalysisDiagnostic,
    DescriptorFact, PyValue,
};
use crate::parsers;
use crate::registry::{descriptor_spec_for_path, DescriptorSpec, ParserKind};

pub const SKIP_DIRS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    ".gradle",
    ".dart_tool",
];

pub const MAX_DESCRIPTOR_FILES: usize = 10_000;

/// `iter_descriptor_paths` — os.walk topdown, dirnames pruned + sorted,
/// filenames sorted, symlink bị bỏ qua; trả (rel_path, spec) theo discovery
/// order. `matches_extra_ignore` từ framework scan (env CORTEX_EXTRA_IGNORE_DIRS).
pub fn iter_descriptor_paths(root: &Path) -> Vec<(String, &'static DescriptorSpec)> {
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out
}

fn walk(root: &Path, current: &Path, out: &mut Vec<(String, &'static DescriptorSpec)>) {
    let mut dir_names: Vec<String> = Vec::new();
    let mut file_names: Vec<String> = Vec::new();
    let Ok(entries) = std::fs::read_dir(current) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(file_type) = entry.file_type() else { continue };
        let Ok(name) = entry.file_name().into_string() else { continue };
        if file_type.is_dir() {
            dir_names.push(name);
        } else {
            file_names.push(name);
        }
    }
    dir_names.sort();
    dir_names.retain(|name| {
        !SKIP_DIRS.contains(&name.as_str())
            && !name.starts_with('.')
            && !cortex_analyzer_framework::scan::matches_extra_ignore(name)
    });
    file_names.sort();
    for file_name in file_names {
        let absolute = current.join(&file_name);
        // Python: absolute.is_symlink() → skip (bao gồm symlink-to-file).
        if symlink_metadata_is_symlink(&absolute) {
            continue;
        }
        let relative = match absolute.strip_prefix(root) {
            Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        if let Some(spec) = descriptor_spec_for_path(&relative) {
            out.push((relative, spec));
        }
    }
    for dir_name in dir_names {
        walk(root, &current.join(dir_name), out);
    }
}

fn symlink_metadata_is_symlink(path: &Path) -> bool {
    match std::fs::symlink_metadata(path) {
        Ok(meta) => meta.file_type().is_symlink(),
        Err(_) => false,
    }
}

fn error_output(
    project_id: &str,
    path: &str,
    spec: &DescriptorSpec,
    diagnostic: AnalysisDiagnostic,
) -> parsers::DescriptorParseOutput {
    let parent = Path::new(path)
        .parent()
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default();
    let module_path = if parent.is_empty() { ".".to_string() } else { parent };
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::UNKNOWN,
        spec.role,
        spec.name,
        parse_depth::UNSUPPORTED,
        diagnostic.message.clone(),
        PyValue::dict(),
        confidence::LOW,
        vec![],
        vec![diagnostic.clone()],
    )
    .unwrap_or_else(|_| fallback_error_descriptor(project_id, path, spec));
    parsers::DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics: vec![diagnostic],
    }
}

fn fallback_error_descriptor(
    _project_id: &str,
    _path: &str,
    _spec: &DescriptorSpec,
) -> DescriptorFact {
    // Chỉ đạt khi normalize/stable_fact_id lỗi trên path hỏng — giữ sentinel
    // để không panic (Python sẽ raise; binary fail-soft ở this branch).
    DescriptorFact {
        id: "project-descriptor:invalid".to_string(),
        project_id: String::new(),
        module_path: ".".to_string(),
        path: String::new(),
        descriptor_type: descriptor_type::UNKNOWN,
        role: crate::models::descriptor_role::IDENTITY,
        parser: String::new(),
        parse_depth: parse_depth::UNSUPPORTED,
        parser_version: crate::models::ANALYZER_VERSION,
        canonical: true,
        generated: false,
        secret_bearing: false,
        redacted: false,
        summary: String::new(),
        properties: PyValue::dict(),
        confidence: confidence::LOW,
        evidence: vec![],
        diagnostics: vec![],
    }
}

/// `parse_descriptor_file` — mirror từng nhánh lỗi của Python.
pub fn parse_descriptor_file(
    root: &Path,
    project_id: &str,
    path: &str,
    spec: &DescriptorSpec,
) -> parsers::DescriptorParseOutput {
    let normalized = match normalize_module_path(path) {
        Ok(value) => value,
        Err(_) => {
            // Python: normalize_file_path(path) raise ValueError (propagate);
            // caller pipeline chỉ gọi với path từ discovery/manifest nên đã
            // chuẩn — giữ fail-loud qua error output.
            return error_output(
                project_id,
                path,
                spec,
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::MODULE_PATH_ESCAPE,
                    "Descriptor path escapes the repository root.",
                )
                .severity("error")
                .file_path(path)
                .module_path("."),
            );
        }
    };
    let unresolved = root.join(&normalized);
    if symlink_metadata_is_symlink(&unresolved) {
        let module_path = parent_of(&normalized);
        return error_output(
            project_id,
            &normalized,
            spec,
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::MODULE_PATH_ESCAPE,
                "Symbolic-link descriptors are not followed.",
            )
            .severity("error")
            .file_path(&normalized)
            .module_path(&module_path),
        );
    }
    // Python dùng Path.resolve() non-strict: file KHÔNG tồn tại không lỗi ở
    // bước resolve — lỗi đến ở stat. Dùng lexical join với root đã resolve.
    let root_canonical = root
        .canonicalize()
        .unwrap_or_else(|_| root.to_path_buf());
    let absolute = root_canonical.join(&normalized);
    if let Ok(resolved) = absolute.canonicalize()
        && !resolved.starts_with(&root_canonical)
    {
        return error_output(
            project_id,
            &normalized,
            spec,
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::MODULE_PATH_ESCAPE,
                "Descriptor path escapes the repository root.",
            )
            .severity("error")
            .file_path(&normalized)
            .module_path("."),
        );
    }
    let size = match std::fs::metadata(&absolute) {
        Ok(meta) => meta.len() as usize,
        Err(error) => {
            let module_path = parent_of(&normalized);
            return error_output(
                project_id,
                &normalized,
                spec,
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::IO_ERROR,
                    &format!(
                        "Unable to read descriptor metadata: {}",
                        py_io_error(&absolute, &error)
                    ),
                )
                .severity("error")
                .file_path(&normalized)
                .module_path(&module_path),
            );
        }
    };
    if size > spec.max_bytes {
        let module_path = parent_of(&normalized);
        let mut details = PyValue::dict();
        details.set("size", PyValue::Int(size as i64));
        details.set("limit", PyValue::Int(spec.max_bytes as i64));
        return error_output(
            project_id,
            &normalized,
            spec,
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::DESCRIPTOR_TOO_LARGE,
                &format!(
                    "Descriptor exceeds the {}-byte safety limit.",
                    spec.max_bytes
                ),
            )
            .file_path(&normalized)
            .module_path(&module_path)
            .details(details),
        );
    }
    let bytes = match std::fs::read(&absolute) {
        Ok(value) => value,
        Err(error) => {
            let module_path = parent_of(&normalized);
            return error_output(
                project_id,
                &normalized,
                spec,
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::IO_ERROR,
                    &format!(
                        "Unable to read descriptor: {}",
                        py_io_error(&absolute, &error)
                    ),
                )
                .severity("error")
                .file_path(&normalized)
                .module_path(&module_path),
            );
        }
    };
    let text = if spec.name == "ini" {
        legacy_decode(&bytes)
    } else {
        String::from_utf8_lossy(&bytes).into_owned()
    };
    dispatch_parse(spec, project_id, &normalized, &text)
}

fn parent_of(path: &str) -> String {
    let parent = Path::new(path)
        .parent()
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default();
    if parent.is_empty() {
        ".".to_string()
    } else {
        normalize_ok_or_dot(&parent)
    }
}

/// OSError message của Python: "[Errno 2] No such file or directory: '<path>'".
fn py_io_error(path: &Path, error: &std::io::Error) -> String {
    let errno = error.raw_os_error().unwrap_or(0);
    let text = match errno {
        1 => "Operation not permitted",
        2 => "No such file or directory",
        9 => "Bad file descriptor",
        13 => "Permission denied",
        20 => "Not a directory",
        21 => "Is a directory",
        36 => "File name too long",
        62 => "Too many levels of symbolic links",
        _ => return error.to_string(),
    };
    format!("[Errno {errno}] {text}: '{}'", path.display())
}

fn normalize_ok_or_dot(value: &str) -> String {
    normalize_module_path(value).unwrap_or_else(|_| ".".to_string())
}

fn dispatch_parse(
    spec: &DescriptorSpec,
    project_id: &str,
    path: &str,
    text: &str,
) -> parsers::DescriptorParseOutput {
    match spec.parser {
        ParserKind::GradleSettings => parsers::gradle::parse_gradle_settings(project_id, path, text),
        ParserKind::GradleBuild => parsers::gradle::parse_gradle_build(project_id, path, text),
        ParserKind::Maven => parsers::maven::parse_maven(project_id, path, text),
        ParserKind::Ant => parsers::ant::parse_ant(project_id, path, text),
        ParserKind::Cmake => parsers::cmake::parse_cmake(project_id, path, text),
        ParserKind::Make => parsers::make::parse_make(project_id, path, text),
        ParserKind::Ini => parsers::ini::parse_ini(project_id, path, text),
        ParserKind::Protobuf => parsers::protobuf::parse_protobuf(project_id, path, text),
        ParserKind::AndroidManifest => {
            parsers::android::parse_android_manifest(project_id, path, text)
        }
        ParserKind::AndroidResource => {
            parsers::android::parse_android_resource(project_id, path, text)
        }
        ParserKind::IdentityManifest => parsers::manifest::parse_identity_manifest(
            project_id,
            path,
            text,
            spec.name,
            spec.role,
            spec.parse_depth,
            spec.secret_bearing,
            spec.generated,
        ),
    }
}

/// `tools/common/legacy_encoding.py::decode_legacy_bytes` — BOM + NUL heuristic
/// + utf-8 strict + cp932 + cp1252(replace).
pub fn legacy_decode(data: &[u8]) -> String {
    if data.starts_with(&[0xff, 0xfe]) {
        return decode_utf16(&data[2..], true);
    }
    if data.starts_with(&[0xfe, 0xff]) {
        return decode_utf16(&data[2..], false);
    }
    if data.starts_with(&[0xef, 0xbb, 0xbf]) {
        return String::from_utf8_lossy(&data[3..]).into_owned();
    }
    let sample = &data[..data.len().min(512)];
    if !sample.is_empty() {
        let odd_nuls = sample.iter().skip(1).step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = (sample.len() / 8).max(2);
        if odd_nuls >= threshold {
            return decode_utf16(data, true);
        }
        if even_nuls >= threshold {
            return decode_utf16(data, false);
        }
    }
    if let Ok(text) = std::str::from_utf8(data) {
        return text.to_string();
    }
    // cp932 (Shift-JIS Windows variant) — encoding_rs decode strict (Python
    // .decode() strict mặc định).
    let (text, _, had_errors) = encoding_rs::SHIFT_JIS.decode(data);
    if !had_errors {
        return text.into_owned();
    }
    // cp1252 errors=replace
    let (text, _, _) = encoding_rs::WINDOWS_1252.decode(data);
    text.into_owned()
}

fn decode_utf16(data: &[u8], little_endian: bool) -> String {
    let mut units = Vec::with_capacity(data.len() / 2);
    for chunk in data.chunks_exact(2) {
        units.push(if little_endian {
            u16::from_le_bytes([chunk[0], chunk[1]])
        } else {
            u16::from_be_bytes([chunk[0], chunk[1]])
        });
    }
    // Python .decode trên lone surrogate raise; Rust thay U+FFFD — chấp nhận
    // (lone surrogate byte-stream gần như không xuất hiện trong descriptor).
    String::from_utf16_lossy(&units)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_decode_utf8_and_bom() {
        assert_eq!(legacy_decode(b"plain"), "plain");
        assert_eq!(legacy_decode(b"\xef\xbb\xbfsig"), "sig");
        // utf-16-le BOM "hi"
        let encoded = vec![0xff, 0xfe, b'h', 0, b'i', 0];
        assert_eq!(legacy_decode(&encoded), "hi");
    }
}
