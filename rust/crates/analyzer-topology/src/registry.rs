//! Port `tools/project_topology/registry.py` — descriptor spec registry với
//! fnmatch semantics (`fnmatch.fnmatch` POSIX: case-sensitive, `*` ăn cả '/').
//! Bảng specs giữ nguyên thứ tự — `descriptor_spec_for_path` trả spec đầu tiên
//! khớp, nên thứ tự là một phần của contract.

use crate::models::{descriptor_role, parse_depth};

#[derive(Debug, Clone, Copy)]
pub struct DescriptorSpec {
    pub name: &'static str,
    pub patterns: &'static [&'static str],
    pub parser: ParserKind,
    pub role: &'static str,
    pub parse_depth: &'static str,
    pub max_bytes: usize,
    pub secret_bearing: bool,
    pub generated: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParserKind {
    GradleSettings,
    GradleBuild,
    Maven,
    Ant,
    Cmake,
    Make,
    Ini,
    Protobuf,
    AndroidManifest,
    AndroidResource,
    IdentityManifest,
}

impl DescriptorSpec {
    /// `DescriptorSpec.matches` — fnmatch(full_path) OR fnmatch(basename).
    pub fn matches(&self, path: &str) -> bool {
        let normalized = path.replace('\\', "/");
        let basename = normalized.rsplit('/').next().unwrap_or("");
        self.patterns
            .iter()
            .any(|pattern| fnmatch(pattern, &normalized) || fnmatch(pattern, basename))
    }
}

pub const DESCRIPTOR_SPECS: &[DescriptorSpec] = &[
    DescriptorSpec {
        name: "gradle_settings",
        patterns: &["settings.gradle", "settings.gradle.kts"],
        parser: ParserKind::GradleSettings,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::TOPOLOGY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "gradle_build",
        patterns: &["build.gradle", "build.gradle.kts"],
        parser: ParserKind::GradleBuild,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::DEPENDENCY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "maven",
        patterns: &["pom.xml"],
        parser: ParserKind::Maven,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::DEPENDENCY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "ant",
        patterns: &["build.xml"],
        parser: ParserKind::Ant,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::TOPOLOGY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "cmake",
        patterns: &["CMakeLists.txt"],
        parser: ParserKind::Cmake,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::DEPENDENCY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "make",
        patterns: &["Makefile", "makefile", "GNUmakefile"],
        parser: ParserKind::Make,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::DEPENDENCY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "ini",
        patterns: &["*.ini"],
        parser: ParserKind::Ini,
        role: descriptor_role::CONFIGURATION,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: true,
        generated: false,
    },
    DescriptorSpec {
        name: "protobuf",
        patterns: &["*.proto"],
        parser: ParserKind::Protobuf,
        role: descriptor_role::INTERFACE,
        parse_depth: parse_depth::SEMANTIC,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "android_manifest",
        patterns: &["AndroidManifest.xml"],
        parser: ParserKind::AndroidManifest,
        role: descriptor_role::FRAMEWORK,
        parse_depth: parse_depth::SEMANTIC,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "android_resource",
        patterns: &["*/res/*.xml", "*/res/**/*.xml"],
        parser: ParserKind::AndroidResource,
        role: descriptor_role::RESOURCE,
        parse_depth: parse_depth::SEMANTIC,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "go",
        patterns: &["go.mod", "go.work"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "rust",
        patterns: &["Cargo.toml"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "dart",
        patterns: &["pubspec.yaml"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "swift",
        patterns: &["Package.swift", "project.pbxproj"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "dotnet",
        patterns: &["*.csproj", "*.vbproj", "*.sln", "*.slnx"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "python",
        patterns: &["pyproject.toml", "setup.cfg", "setup.py", "Pipfile"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "javascript",
        patterns: &["package.json", "jsconfig.json", "tsconfig.json"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "php",
        patterns: &["composer.json"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "perl",
        patterns: &["cpanfile", "META.json", "META.yml", "dist.ini", "Makefile.PL", "Build.PL"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPENDENCY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "delphi",
        patterns: &["*.dproj", "*.groupproj", "*.dpk", "*.dpr"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "visual_basic",
        patterns: &["*.vbp", "*.vbg", "*.vbw"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::TOPOLOGY,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "database_migration",
        patterns: &["dbt_project.yml", "liquibase*.xml", "flyway*.conf"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::DEPLOYMENT,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: false,
        generated: false,
    },
    DescriptorSpec {
        name: "framework_config",
        patterns: &[
            "application*.properties",
            "application*.yml",
            "application*.yaml",
            "web.xml",
            "struts*.xml",
            "mybatis*.xml",
            "appsettings*.json",
        ],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::FRAMEWORK,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: true,
        generated: false,
    },
    DescriptorSpec {
        name: "runtime_secret_keys",
        patterns: &[".env", ".env.*", "secrets.json", "gradle.properties"],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::SECRET_BEARING,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 2 * 1024 * 1024,
        secret_bearing: true,
        generated: false,
    },
    DescriptorSpec {
        name: "generated_lock",
        patterns: &[
            "*.lock",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "go.sum",
            "Package.resolved",
        ],
        parser: ParserKind::IdentityManifest,
        role: descriptor_role::GENERATED,
        parse_depth: parse_depth::IDENTITY,
        max_bytes: 8 * 1024 * 1024,
        secret_bearing: false,
        generated: true,
    },
];

/// `descriptor_spec_for_path` — spec đầu tiên khớp theo thứ tự bảng.
pub fn descriptor_spec_for_path(path: &str) -> Option<&'static DescriptorSpec> {
    DESCRIPTOR_SPECS.iter().find(|spec| spec.matches(path))
}

/// `descriptor_candidates` — sorted unique paths có spec, backslash → slash.
pub fn descriptor_candidates(paths: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut normalized: Vec<String> = paths
        .into_iter()
        .map(|path| path.replace('\\', "/"))
        .filter(|path| descriptor_spec_for_path(path).is_some())
        .collect();
    normalized.sort();
    normalized.dedup();
    normalized
}

/// `fnmatch.fnmatch` trên POSIX (case-sensitive) — `*` khớp mọi ký tự kể cả
/// '/', `?` một ký tự, `[seq]` character class (fnmatch.translate semantics).
pub fn fnmatch(pattern: &str, name: &str) -> bool {
    let regex_source = fnmatch_translate(pattern);
    match regex::Regex::new(&regex_source) {
        Ok(re) => re.is_match(name),
        Err(_) => false,
    }
}

/// `fnmatch.translate` — tương đương Python (không anchored dollar newline).
pub fn fnmatch_translate(pattern: &str) -> String {
    let mut out = String::from("(?s:");
    out.push_str("(?i:"); // không — POSIX fnmatch case-sensitive; bỏ (?i:
    out.clear();
    out.push_str("(?s:");
    let mut index = 0usize;
    let bytes = pattern.as_bytes();
    while index < bytes.len() {
        let ch = pattern[index..].chars().next().unwrap();
        let ch_len = ch.len_utf8();
        match ch {
            '*' => out.push_str(".*"),
            '?' => out.push('.'),
            '[' => {
                let mut j = index + 1;
                let mut negated = false;
                if j < bytes.len() && (bytes[j] == b'!' || bytes[j] == b'^') {
                    negated = true;
                    j += 1;
                }
                let mut class = String::from("[^");
                if !negated {
                    class = String::from("[");
                }
                let mut first = true;
                let mut closed = false;
                while j < bytes.len() {
                    let c = pattern[j..].chars().next().unwrap();
                    if c == ']' && !first {
                        closed = true;
                        j += c.len_utf8();
                        break;
                    }
                    first = false;
                    match c {
                        '\\' => {
                            class.push_str("\\\\");
                            j += 1;
                        }
                        ']' => {
                            class.push_str("\\]");
                            j += 1;
                        }
                        '^' => {
                            class.push_str("\\^");
                            j += 1;
                        }
                        _ => {
                            class.push(c);
                            j += c.len_utf8();
                        }
                    }
                }
                if closed {
                    class.push(']');
                    out.push_str(&class);
                    index = j;
                    continue;
                }
                out.push_str("\\[");
            }
            c => {
                if "\\.+()|^${}".contains(c) {
                    out.push('\\');
                }
                out.push(c);
            }
        }
        index += ch_len;
    }
    out.push_str(")$");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spec_matches_full_path_or_basename() {
        let spec = descriptor_spec_for_path("app/src/main/res/values/strings.xml").unwrap();
        assert_eq!(spec.name, "android_resource");
        let spec = descriptor_spec_for_path("app/src/main/AndroidManifest.xml").unwrap();
        assert_eq!(spec.name, "android_manifest");
        let spec = descriptor_spec_for_path("sub/settings.gradle.kts").unwrap();
        assert_eq!(spec.name, "gradle_settings");
        assert!(descriptor_spec_for_path("plain.xml").is_none());
    }

    #[test]
    fn fnmatch_star_crosses_slashes() {
        assert!(fnmatch("*/res/*.xml", "a/b/res/c.xml"));
        assert!(fnmatch("*/res/**/*.xml", "a/b/res/deep/c.xml"));
        assert!(!fnmatch("*/res/*.xml", "res/c.xml"));
        assert!(fnmatch("*.lock", "a/b/deep.lock"));
        assert!(fnmatch(".env.*", ".env.local"));
    }

    #[test]
    fn candidates_sorted_filtered() {
        let picked = descriptor_candidates([
            "b/build.gradle".to_string(),
            "a/pom.xml".to_string(),
            "random.txt".to_string(),
            "b/build.gradle".to_string(),
        ]);
        assert_eq!(picked, vec!["a/pom.xml".to_string(), "b/build.gradle".to_string()]);
    }
}
