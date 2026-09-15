//! Port `parsers/ini.py` — flat key:value INI descriptor.

use crate::models::{
    confidence, descriptor_role, descriptor_type, parse_depth, safe_summary, DescriptorFact,
    PyValue, SourceEvidence,
};
use crate::parsers::common::module_path_for_file;
use crate::parsers::DescriptorParseOutput;

pub fn parse_ini(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let mut entries: Vec<PyValue> = Vec::new();
    for (line_number_value, raw_line) in text.split('\n').enumerate() {
        let raw = raw_line.strip_suffix('\r').unwrap_or(raw_line);
        let line = raw.trim();
        if line.is_empty()
            || line.starts_with('#')
            || line.starts_with(';')
            || !line.contains(':')
        {
            continue;
        }
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let mut entry = PyValue::dict();
        entry.set("key", PyValue::Str(key.trim().to_string()));
        entry.set("value", PyValue::Str(value.trim().to_string()));
        entry.set("line", PyValue::Int(line_number_value as i64 + 1));
        entries.push(entry);
    }
    let mut properties = PyValue::dict();
    properties.set("entries", PyValue::List(entries.clone()));
    properties.set("format", PyValue::Str("key-value-colon".to_string()));
    let summary = safe_summary(&format!(
        "Flat INI configuration with {} entries",
        entries.len()
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path_for_file(path),
        path,
        descriptor_type::RUNTIME_CONFIG,
        descriptor_role::CONFIGURATION,
        "ini",
        parse_depth::IDENTITY,
        summary,
        properties,
        confidence::HIGH,
        vec![SourceEvidence::at_line(path, 1)],
        vec![],
    )
    .expect("ini descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics: vec![],
    }
}
