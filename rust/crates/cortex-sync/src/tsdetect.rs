//! Port of `tools/ts/ts_project_detector.py` — frontend/backend/fullstack
//! scoring used to pick ts_analyzer.py vs ts_backend_analyzer.py.

use std::collections::BTreeMap;
use std::path::Path;

use crate::walk;

const PACKAGE_SIGNALS: [(&str, i64, &str, bool); 45] = [
    ("express", 4, "express", true),
    ("fastify", 4, "fastify", true),
    ("koa", 4, "koa", true),
    ("hapi", 4, "hapi", true),
    ("@hapi/hapi", 4, "hapi", true),
    ("@nestjs/core", 5, "nestjs", true),
    ("@nestjs/common", 5, "nestjs", true),
    ("@nestjs/platform-express", 4, "nestjs", true),
    ("restify", 3, "restify", true),
    ("polka", 3, "polka", true),
    ("prisma", 3, "", true),
    ("@prisma/client", 3, "", true),
    ("typeorm", 3, "", true),
    ("sequelize", 3, "", true),
    ("mongoose", 3, "", true),
    ("drizzle-orm", 3, "", true),
    ("knex", 2, "", true),
    ("kysely", 2, "", true),
    ("pg", 2, "", true),
    ("mysql2", 2, "", true),
    ("mongodb", 2, "", true),
    ("ioredis", 2, "", true),
    ("redis", 2, "", true),
    ("neo4j-driver", 2, "", true),
    ("graphql", 2, "graphql", true),
    ("apollo-server", 3, "apollo", true),
    ("@apollo/server", 3, "apollo", true),
    ("@trpc/server", 4, "trpc", true),
    ("react", 4, "react", false),
    ("react-dom", 3, "react", false),
    ("vue", 4, "vue", false),
    ("@vue/core", 4, "vue", false),
    ("svelte", 4, "svelte", false),
    ("@sveltejs/kit", 4, "sveltekit", false),
    ("solid-js", 4, "solid", false),
    ("@angular/core", 5, "angular", false),
    ("next", 4, "next", false),
    ("nuxt", 4, "nuxt", false),
    ("@remix-run/react", 4, "remix", false),
    ("gatsby", 4, "gatsby", false),
    ("vite", 2, "", false),
    ("@vitejs/plugin-react", 2, "", false),
    ("react-native", 5, "react-native", false),
    ("expo", 4, "expo", false),
    ("@react-navigation/native", 3, "react-native", false),
];

const DIR_SIGNALS: [(&str, i64, bool); 27] = [
    ("controllers", 2, true),
    ("controller", 2, true),
    ("services", 1, true),
    ("service", 1, true),
    ("repositories", 2, true),
    ("repository", 2, true),
    ("middleware", 1, true),
    ("middlewares", 1, true),
    ("guards", 2, true),
    ("guard", 1, true),
    ("interceptors", 2, true),
    ("interceptor", 1, true),
    ("routes", 1, true),
    ("routers", 1, true),
    ("dto", 2, true),
    ("dtos", 2, true),
    ("entities", 2, true),
    ("entity", 2, true),
    ("migrations", 2, true),
    ("modules", 1, true),
    ("components", 1, false),
    ("pages", 1, false),
    ("screens", 2, false),
    ("views", 1, false),
    ("hooks", 1, false),
    ("assets", 1, false),
    ("layouts", 1, false),
];

const TS_SKIP_DIRS: [&str; 11] = [
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", ".next", ".nuxt", ".cache",
    "__pycache__",
];

const ENTRY_CANDIDATES: [&str; 8] = [
    "main.ts", "index.ts", "server.ts", "app.ts", "src/main.ts", "src/index.ts", "src/server.ts",
    "src/app.ts",
];

pub struct ProjectTypeResult {
    pub project_type: String,
    pub framework: String,
    pub backend_score: i64,
    pub frontend_score: i64,
}

fn re_match(pattern: &str, text: &str) -> bool {
    regex::Regex::new(pattern).map(|re| re.is_match(text)).unwrap_or(false)
}

/// `detect_project_type`.
pub fn detect_project_type(root: &Path) -> ProjectTypeResult {
    let mut backend_score: i64 = 0;
    let mut frontend_score: i64 = 0;
    let mut framework_votes: BTreeMap<String, i64> = BTreeMap::new();

    // Pass 1: package.json dependency scoring.
    let pkg_path = root.join("package.json");
    if pkg_path.is_file() {
        if let Ok(text) = std::fs::read_to_string(&pkg_path) {
            if let Ok(pkg) = serde_json::from_str::<serde_json::Value>(&text) {
                let mut all_deps: Vec<String> = Vec::new();
                for key in ["dependencies", "devDependencies"] {
                    if let Some(map) = pkg.get(key).and_then(|v| v.as_object()) {
                        all_deps.extend(map.keys().cloned());
                    }
                }
                for (name, weight, hint, is_backend) in PACKAGE_SIGNALS {
                    if all_deps.iter().any(|dep| dep == name) {
                        if is_backend {
                            backend_score += weight;
                        } else {
                            frontend_score += weight;
                        }
                        if !hint.is_empty() {
                            *framework_votes.entry(hint.to_string()).or_default() += weight;
                        }
                    }
                }
                // Script analysis.
                if let Some(scripts) = pkg.get("scripts").and_then(|v| v.as_object()) {
                    for (_name, value) in scripts {
                        let script_val = value.as_str().unwrap_or_default();
                        if re_match(r"\b(?:nest|express|fastify)\b", script_val) {
                            backend_score += 2;
                        }
                        if re_match(r"\b(?:vite|react-scripts|next\s+dev)\b", script_val) {
                            frontend_score += 2;
                        }
                    }
                }
            }
        }
    }

    // Pass 2: directory structure scoring.
    score_directories(root, &mut backend_score, &mut frontend_score);

    // Pass 3: entry-point file content signals.
    for candidate in ENTRY_CANDIDATES {
        let entry = root.join(candidate);
        if !entry.is_file() {
            continue;
        }
        let Ok(handle) = std::fs::File::open(&entry) else { continue };
        use std::io::Read;
        let mut content = String::new();
        let mut limit = handle.take(8192);
        if limit.read_to_string(&mut content).is_err() {
            continue;
        }
        if re_match(r"NestFactory\.create|createNestApplication", &content) {
            backend_score += 5;
            *framework_votes.entry("nestjs".to_string()).or_default() += 5;
        }
        if re_match(r"express\s*\(\)|new\s+Fastify", &content) {
            backend_score += 4;
        }
        if re_match(r"ReactDOM\.render|createRoot\s*\(", &content) {
            frontend_score += 5;
            *framework_votes.entry("react".to_string()).or_default() += 5;
        }
        if re_match(r"createApp\s*\(|createSSRApp\s*\(", &content) {
            frontend_score += 5;
            *framework_votes.entry("vue".to_string()).or_default() += 5;
        }
    }

    // Decision.
    let (project_type, _recommended) = if backend_score == 0 && frontend_score == 0 {
        ("unknown", "ts_analyzer")
    } else if backend_score > 0 && frontend_score > 0 {
        if (backend_score as f64) >= frontend_score as f64 * 1.5 {
            ("backend", "ts_backend_analyzer")
        } else if (frontend_score as f64) >= backend_score as f64 * 1.5 {
            ("frontend", "ts_analyzer")
        } else {
            ("fullstack", "both")
        }
    } else if backend_score > 0 {
        ("backend", "ts_backend_analyzer")
    } else {
        ("frontend", "ts_analyzer")
    };

    let framework = framework_votes
        .iter()
        .max_by(|a, b| a.1.cmp(b.1).then_with(|| b.0.cmp(a.0)))
        .map(|(name, _)| name.clone())
        .unwrap_or_default();

    ProjectTypeResult {
        project_type: project_type.to_string(),
        framework,
        backend_score,
        frontend_score,
    }
}

fn score_directories(root: &Path, backend_score: &mut i64, frontend_score: &mut i64) {
    let Ok(read) = std::fs::read_dir(root) else { return };
    let mut subdirs = Vec::new();
    for entry in read.flatten() {
        let Ok(file_type) = entry.file_type() else { continue };
        if file_type.is_dir() {
            subdirs.push(entry.path());
        }
    }
    for subdir in subdirs {
        let name = subdir
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if TS_SKIP_DIRS.contains(&name.as_str()) || walk::matches_extra_ignore(&name) {
            continue;
        }
        let lower = name.to_lowercase();
        for (segment, weight, is_backend) in DIR_SIGNALS {
            if lower == segment {
                if is_backend {
                    *backend_score += weight;
                } else {
                    *frontend_score += weight;
                }
            }
        }
        score_directories(&subdir, backend_score, frontend_score);
    }
}
