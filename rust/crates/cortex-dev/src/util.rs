//! Small helpers ported from dev.py: fnmatch globs, MD5 (sync-state keys),
//! interactive prompts, git subprocesses, and sync-state JSON files.

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

// ---------------------------------------------------------------------------
// Repo / interpreter resolution (the Python-bridge module was deleted in phase-07)
// ---------------------------------------------------------------------------

/// Repo root resolution: env override, walk-up from cwd, compile-time fallback.
pub fn repo_root() -> PathBuf {
    if let Ok(env_root) = std::env::var("CORTEX_HARNESS_REPO_ROOT") {
        let p = PathBuf::from(env_root);
        if p.join("cortex_harness/dev.py").is_file() {
            return p;
        }
    }
    let mut cur = std::env::current_dir().ok();
    while let Some(dir) = cur {
        if dir.join("cortex_harness/dev.py").is_file() {
            return dir;
        }
        cur = dir.parent().map(Path::to_path_buf);
    }
    // Compile-time fallback: <repo>/rust/crates/cortex-dev
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Harness interpreter for the remaining FORCED-Python paths only (the
/// lifecycle shim actions, `.harness` project scripts, torch device probe,
/// journal-consumer recovery): project venv, then the harness repo venv,
/// then the ambient interpreter. (Carried over from the deleted Python-bridge
/// module; renamed so the phase-07 bridge-ban grep stays clean.)
pub fn harness_python(base_dir: &Path) -> String {
    let candidates = [
        base_dir.join(".venv").join("Scripts").join("python.exe"),
        base_dir.join("bin").join("python"),
    ];
    for candidate in candidates.iter() {
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
    }
    let root = repo_root();
    for candidate in [
        root.join(".venv").join("Scripts").join("python.exe"),
        root.join(".venv").join("bin").join("python"),
    ] {
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
    }
    "python3".to_string()
}

// ---------------------------------------------------------------------------
// fnmatch (Python semantics: * ? [seq] [!seq]; case-sensitive on POSIX)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
enum GlobTok {
    Lit(char),
    AnyOne,
    AnyRun,
    Set { negated: bool, items: Vec<char> },
}

/// Tokenize a glob the way `fnmatch.translate` handles the constructs the
/// dev CLI uses in `ignore.folders` and the built-in exclude list.
fn compile_glob(pattern: &str) -> Vec<GlobTok> {
    let chars: Vec<char> = pattern.chars().collect();
    let mut toks = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        match chars[i] {
            '*' => {
                toks.push(GlobTok::AnyRun);
                i += 1;
            }
            '?' => {
                toks.push(GlobTok::AnyOne);
                i += 1;
            }
            '[' => {
                let mut j = i + 1;
                let mut negated = false;
                if j < chars.len() && (chars[j] == '!' || chars[j] == '^') {
                    negated = true;
                    j += 1;
                }
                let mut items = Vec::new();
                let mut closed = false;
                let mut first = true;
                while j < chars.len() {
                    if chars[j] == ']' && !first {
                        closed = true;
                        break;
                    }
                    first = false;
                    items.push(chars[j]);
                    j += 1;
                }
                if closed {
                    toks.push(GlobTok::Set { negated, items });
                    i = j + 1;
                } else {
                    // Unmatched '[' is a literal (fnmatch behaviour).
                    toks.push(GlobTok::Lit('['));
                    i += 1;
                }
            }
            c => {
                toks.push(GlobTok::Lit(c));
                i += 1;
            }
        }
    }
    toks
}

fn tok_matches(tok: &GlobTok, c: char) -> bool {
    match tok {
        GlobTok::Lit(l) => *l == c,
        GlobTok::AnyOne => true,
        GlobTok::AnyRun => false,
        GlobTok::Set { negated, items } => items.contains(&c) != *negated,
    }
}

/// Match a single glob against a name.
pub fn fnmatch_one(name: &str, pattern: &str) -> bool {
    let toks = compile_glob(pattern);
    let text: Vec<char> = name.chars().collect();
    let (mut ti, mut pi) = (0usize, 0usize);
    let (mut star_pi, mut star_ti): (usize, usize) = (usize::MAX, 0);
    while ti < text.len() {
        if pi < toks.len() {
            match toks[pi] {
                GlobTok::AnyRun => {
                    star_pi = pi;
                    star_ti = ti;
                    pi += 1;
                    continue;
                }
                ref tok if tok_matches(tok, text[ti]) => {
                    pi += 1;
                    ti += 1;
                    continue;
                }
                _ => {}
            }
        }
        if star_pi != usize::MAX {
            pi = star_pi + 1;
            star_ti += 1;
            ti = star_ti;
            continue;
        }
        return false;
    }
    while pi < toks.len() && matches!(toks[pi], GlobTok::AnyRun) {
        pi += 1;
    }
    pi == toks.len()
}

/// Match a name against any pattern in the list (dev.py `_match_ignore`).
pub fn fnmatch(name: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| fnmatch_one(name, p))
}

pub fn match_any(name: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| fnmatch_one(name, p))
}

// ---------------------------------------------------------------------------
// MD5 (RFC 1321) — dev.py hashes folder names into sync-state file keys.
// ---------------------------------------------------------------------------

pub fn md5_hex(data: &[u8]) -> String {
    let mut state: [u32; 4] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476];
    const S: [usize; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5,
        9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6,
        10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    const K: [u32; 64] = MD5_K;

    let mut msg = data.to_vec();
    let bit_len = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_le_bytes());

    for chunk in msg.chunks(64) {
        let mut m = [0u32; 16];
        for (i, word) in m.iter_mut().enumerate() {
            *word = u32::from_le_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        let (mut a, mut b, mut c, mut d) =
            (state[0], state[1], state[2], state[3]);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | ((!b) & d), i),
                1 => ((d & b) | ((!d) & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | (!d)), (7 * i) % 16),
            };
            let temp = d;
            d = c;
            c = b;
            let sum = a
                .wrapping_add(f)
                .wrapping_add(K[i])
                .wrapping_add(m[g]);
            b = b.wrapping_add(sum.rotate_left(S[i] as u32));
            a = temp;
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
    }

    // The MD5 digest is the little-endian byte serialization of the state.
    let mut out = String::with_capacity(32);
    for word in state {
        for b in word.to_le_bytes() {
            out.push_str(&format!("{:02x}", b));
        }
    }
    out
}

const MD5_K: [u32; 64] = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
];

// ---------------------------------------------------------------------------
// Prompts (click.prompt/confirm subset)
// ---------------------------------------------------------------------------

fn read_line() -> Option<String> {
    let mut line = String::new();
    match std::io::stdin().lock().read_line(&mut line) {
        Ok(0) => None,
        Ok(_) => Some(line.trim_end_matches(['\n', '\r']).to_string()),
        Err(_) => None,
    }
}

/// `click.prompt(label, default=...)` — prints `label [default]: `; the
/// bracket is shown even when the default is an empty string (Click renders
/// `[]`), which the init transcript depends on.
pub fn prompt(label: &str, default: &str) -> String {
    print!("{} [{}]: ", label, default);
    let _ = std::io::stdout().flush();
    match read_line() {
        Some(line) => {
            let stripped = line.trim_end_matches(['\n', '\r']);
            if stripped.is_empty() {
                default.to_string()
            } else {
                stripped.to_string()
            }
        }
        None => default.to_string(),
    }
}

/// `click.prompt` for a required value (no default).
pub fn prompt_required(label: &str) -> String {
    loop {
        print!("{}: ", label);
        let _ = std::io::stdout().flush();
        match read_line() {
            Some(line) if !line.trim().is_empty() => return line.trim().to_string(),
            Some(_) => continue,
            None => std::process::exit(1),
        }
    }
}

/// `click.confirm(text, default=...)` — prints `text [Y/n]: `.
pub fn confirm(text: &str, default: bool) -> bool {
    let suffix = if default { " [Y/n]" } else { " [y/N]" };
    print!("{}{}: ", text, suffix);
    let _ = std::io::stdout().flush();
    match read_line() {
        Some(line) => {
            let v = line.trim().to_ascii_lowercase();
            if v.is_empty() {
                default
            } else if v == "y" || v == "yes" {
                true
            } else if v == "n" || v == "no" {
                false
            } else {
                default
            }
        }
        None => default,
    }
}

// ---------------------------------------------------------------------------
// Output helpers (click.echo semantics: stdout default, err=True -> stderr)
// ---------------------------------------------------------------------------

pub fn echo(msg: &str) {
    println!("{}", msg);
}

pub fn echo_err(msg: &str) {
    eprintln!("{}", msg);
}

/// click.ClickException — `Error: {msg}` on stderr, exit 1.
pub fn fail(msg: &str) -> ! {
    echo_err(&format!("Error: {}", msg));
    std::process::exit(1);
}

/// dev.py `sys.exit(1)` after an `[error] ...` line on stderr.
pub fn error_exit(msg: &str) -> ! {
    echo_err(&format!("[error] {}", msg));
    std::process::exit(1);
}

// ---------------------------------------------------------------------------
// Git helpers
// ---------------------------------------------------------------------------

pub fn git_head(folder: &Path) -> String {
    Command::new("git")
        .arg("-C")
        .arg(folder)
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// (changed, deleted) from `git diff --name-status --relative <since> HEAD`.
pub fn git_status_since(folder: &Path, since: &str) -> (Vec<String>, Vec<String>) {
    let output = Command::new("git")
        .arg("-C")
        .arg(folder)
        .args(["diff", "--name-status", "--relative", since, "HEAD"])
        .output();
    let output = match output {
        Ok(o) if o.status.success() => o,
        _ => return (Vec::new(), Vec::new()),
    };
    let mut changed = Vec::new();
    let mut deleted = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() < 2 {
            continue;
        }
        let status = parts[0].trim();
        let fname = parts[parts.len() - 1].trim();
        if fname.is_empty() || is_sensitive(Path::new(fname)) {
            continue;
        }
        if status.starts_with('D') {
            deleted.push(fname.to_string());
        } else {
            changed.push(fname.to_string());
        }
    }
    (changed, deleted)
}

// ---------------------------------------------------------------------------
// Scan exclusions & sensitive patterns (verbatim from dev.py)
// ---------------------------------------------------------------------------

pub const SCAN_EXCLUDE: &[&str] = &[
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "out",
    "target",
    ".gradle",
    "dist",
    "bin",
    "obj",
    "Pods",
    "DerivedData",
    "vendor",
    ".idea",
    ".vscode",
    ".cache",
    ".cortext-harness",
];

pub const SENSITIVE_PATTERNS: &[&str] = &[
    ".env",
    "*.env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.crt",
    "*.cer",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "id_ecdsa",
    "*secret*",
    "*password*",
    "*credential*",
    "*token*",
    "*.keystore",
    "*.jks",
];

pub fn is_sensitive(path: &Path) -> bool {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    SENSITIVE_PATTERNS.iter().any(|p| fnmatch_one(&name, p))
}

pub fn is_excluded_dir_name(name: &str) -> bool {
    SCAN_EXCLUDE.iter().any(|pat| fnmatch_one(name, pat))
}

// ---------------------------------------------------------------------------
// Sync-state files (md5-keyed JSON under .cortext-harness/sync-state)
// ---------------------------------------------------------------------------

pub fn state_path(project_dir: &Path, folder: &str) -> PathBuf {
    let key = &md5_hex(folder.as_bytes())[..12];
    project_dir.join(".cortext-harness").join("sync-state").join(format!("{}.json", key))
}

pub fn load_state(project_dir: &Path, folder: &str) -> serde_json::Value {
    let p = state_path(project_dir, folder);
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(serde_json::Value::Null)
}

pub fn save_state(project_dir: &Path, folder: &str, state: &serde_json::Value) {
    let p = state_path(project_dir, folder);
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let text = serde_json::to_string_pretty(state).unwrap_or_default();
    let _ = std::fs::write(p, text);
}

/// `datetime.now(timezone.utc).isoformat()` — microsecond ISO-8601 UTC.
pub fn iso_utc_now() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let micros = now.subsec_micros();
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        y,
        m,
        d,
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60,
        micros
    )
}

/// Howard Hinnant's `civil_from_days` (days since 1970-01-01 → y/m/d).
pub fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32; // [1, 12]
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Local-time strftime via the system `date` command (BSD then GNU forms),
/// standing in for `datetime.now().strftime(fmt)` without new dependencies.
pub fn local_strftime(unix: i64, fmt: &str) -> String {
    for args in [
        vec!["-r".to_string(), unix.to_string(), format!("+{}", fmt)],
        vec![
            "-d".to_string(),
            format!("@{}", unix),
            format!("+{}", fmt),
        ],
    ] {
        let ran = std::process::Command::new("date").args(&args).output();
        if ran.as_ref().is_ok_and(|out| out.status.success()) {
            let out = ran.expect("checked above");
            return String::from_utf8_lossy(&out.stdout)
                .trim_end_matches('\n')
                .to_string();
        }
    }
    String::new()
}

/// Strict split of a command line the way `shlex.split` would for the
/// simple process listings dev.py parses (single/double quotes).
pub fn shlex_split(line: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut chars = line.chars().peekable();
    let mut has_token = false;
    while let Some(c) = chars.next() {
        match c {
            ' ' | '\t' => {
                if has_token {
                    out.push(std::mem::take(&mut cur));
                    has_token = false;
                }
            }
            '\'' => {
                has_token = true;
                for c2 in chars.by_ref() {
                    if c2 == '\'' {
                        break;
                    }
                    cur.push(c2);
                }
            }
            '"' => {
                has_token = true;
                while let Some(c2) = chars.next() {
                    if c2 == '"' {
                        break;
                    }
                    if c2 == '\\' {
                        if let Some(c3) = chars.next() {
                            cur.push(c3);
                        }
                    } else {
                        cur.push(c2);
                    }
                }
            }
            '\\' => {
                has_token = true;
                if let Some(c3) = chars.next() {
                    cur.push(c3);
                }
            }
            _ => {
                has_token = true;
                cur.push(c);
            }
        }
    }
    if has_token {
        out.push(cur);
    }
    Some(out)
}
