//! Port `tools/project_topology/parsers/*` — bounded static descriptor parsers.

pub mod ant;
pub mod android;
pub mod cmake;
pub mod common;
pub mod gradle;
pub mod ini;
pub mod make;
pub mod manifest;
pub mod maven;
pub mod protobuf;

use crate::models::{DependencyFact, DescriptorFact, EndpointFact, AnalysisDiagnostic};

/// `DescriptorParseOutput`.
pub struct DescriptorParseOutput {
    pub descriptor: DescriptorFact,
    pub dependencies: Vec<DependencyFact>,
    pub endpoints: Vec<EndpointFact>,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}
