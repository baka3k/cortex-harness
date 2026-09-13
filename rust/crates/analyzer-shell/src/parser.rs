//! Port `tools/shell/parser.py` — regex parse: functions (brace depth),
//! assignments→variables, source/direct .sh CALLS, grep .ini REFERENCES,
//! command segments qua shlex port, invocations.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use regex::Regex;

use crate::models::{ShellDiagnostic, ShellFile, ShellFunction, ShellInvocation, ShellRelation};
use crate::shlex::posix_shlex_split;

pub struct ShellParser {
    function_re: Regex,
    assignment_re: Regex,
    source_re: Regex,
    direct_re: Regex,
    grep_re: Regex,
    variable_re: Regex,
    assignment_token_re: Regex,
}

const SHELL_BUILTINS: [&str; 40] = [
    ".",
    ":",
    "alias",
    "bg",
    "break",
    "cd",
    "command",
    "continue",
    "echo",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "fc",
    "fg",
    "getopts",
    "hash",
    "jobs",
    "kill",
    "local",
    "printf",
    "pwd",
    "read",
    "readonly",
    "return",
    "set",
    "shift",
    "source",
    "test",
    "times",
    "trap",
    "true",
    "type",
    "typeset",
    "ulimit",
    "umask",
    "unalias",
    "unset",
    "wait",
];

const CONTROL_WORDS: [&str; 15] = [
    "case", "do", "done", "elif", "else", "esac", "fi", "for", "function", "in", "select",
    "then", "until", "while", "{",
];
// Python _CONTROL_WORDS có cả "}" — tách ra để đủ 16 phần tử.
const CONTROL_WORD_CLOSE: [&str; 1] = ["}"];

const LEADING_CONDITIONALS: [&str; 4] = ["if", "elif", "until", "while"];
const COMMAND_WRAPPERS: [&str; 2] = ["env", "nohup"];
const WRAPPER_EXTRA: [&str; 2] = ["command", "exec"];
const SHELL_INTERPRETERS: [&str; 5] = ["bash", "dash", "ksh", "sh", "zsh"];

impl ShellParser {
    pub fn new() -> Self {
        Self {
            function_re: Regex::new(
                r"^\s*(?:function\s+([A-Za-z_][A-Za-z0-9_]*)\s*|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\))\s*\{",
            )
            .unwrap(),
            assignment_re: Regex::new(
                r#"^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|'([^']*)'|([^\s#;]+))"#,
            )
            .unwrap(),
            source_re: Regex::new(r"^\s*(?:\.|source|sh|bash)\s+([^\s;&|]+\.sh)\b").unwrap(),
            direct_re: Regex::new(
                r#"^\s*(\.?\.?/[^\s;&|]+\.sh|\$\{[A-Za-z_][A-Za-z0-9_]*\}\.sh)\b"#,
            )
            .unwrap(),
            grep_re: Regex::new(
                r#"\bgrep\s+(?:-[A-Za-z]+\s+)*(?:"([^"]+)"|'([^']+)'|([^\s]+))\s+(?:"([^"]+)"|'([^']+)'|([^\s|;&]+))"#,
            )
            .unwrap(),
            variable_re: Regex::new(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
                .unwrap(),
            assignment_token_re: Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*=").unwrap(),
        }
    }

    fn is_control_word(&self, word: &str) -> bool {
        CONTROL_WORDS.contains(&word) || CONTROL_WORD_CLOSE.contains(&word)
    }

    /// `_substitute` — thay ${VAR}/$VAR nếu có trong variables; trả
    /// (text, complete) với complete = không có unresolved variable.
    fn substitute(&self, raw: &str, variables: &HashMap<String, String>) -> (String, bool) {
        let mut unresolved = false;
        let result = self
            .variable_re
            .replace_all(raw, |caps: &regex::Captures| {
                let name = caps.get(1).or_else(|| caps.get(2)).map(|m| m.as_str());
                match name.and_then(|name| variables.get(name)) {
                    Some(value) => value.clone(),
                    None => {
                        unresolved = true;
                        caps.get(0).unwrap().as_str().to_string()
                    }
                }
            })
            .to_string();
        (result, !unresolved)
    }

    fn normalize_lexical(&self, path: PathBuf) -> PathBuf {
        let mut out = PathBuf::new();
        for component in path.components() {
            match component {
                std::path::Component::CurDir => {}
                std::path::Component::ParentDir => {
                    out.pop();
                }
                other => out.push(other.as_os_str()),
            }
        }
        out
    }

    /// `_resolve_path` — thử (file_dir, target) rồi (root, target); realpath +
    /// commonpath-under-root + isfile ⇒ rel-posix + resolved=true.
    fn resolve_path(
        &self,
        raw: &str,
        file_path: &str,
        project_root: &Path,
        root_real: &Path,
        variables: &HashMap<String, String>,
    ) -> (String, bool) {
        let stripped = raw.trim_matches(|c| c == '"' || c == '\'');
        let (substituted, complete) = self.substitute(stripped, variables);
        let file_dir = Path::new(file_path).parent().unwrap_or(Path::new(""));
        let candidates = [
            project_root.join(file_dir).join(&substituted),
            project_root.join(&substituted),
        ];
        for candidate in candidates {
            // os.path.realpath: resolve symlink nếu tồn tại, ngược lại lexical.
            let normalized = if candidate.exists() {
                std::fs::canonicalize(&candidate).unwrap_or_else(|_| candidate.clone())
            } else {
                self.normalize_lexical(candidate)
            };
            if !normalized.starts_with(root_real) {
                continue;
            }
            if normalized.is_file() {
                let rel = normalized
                    .strip_prefix(root_real)
                    .unwrap_or(&normalized)
                    .to_string_lossy()
                    .replace('\\', "/");
                return (rel, complete);
            }
        }
        (substituted.replace('\\', "/"), false)
    }

    /// `_command_segments` — tách token runs `;&|` thành segment riêng.
    fn command_segments(&self, line: &str) -> Result<Vec<Vec<String>>, String> {
        let tokens = posix_shlex_split(line)?;
        let mut segments: Vec<Vec<String>> = Vec::new();
        let mut current: Vec<String> = Vec::new();
        for token in tokens {
            if !token.is_empty() && token.chars().all(|c| c == ';' || c == '&' || c == '|') {
                if !current.is_empty() {
                    segments.push(std::mem::take(&mut current));
                }
                continue;
            }
            current.push(token);
        }
        if !current.is_empty() {
            segments.push(current);
        }
        Ok(segments)
    }

    /// `_command_token` — bỏ leading VAR=, conditional, wrapper; trả token lệnh.
    fn command_token(&self, segment: &[String]) -> Option<String> {
        let mut tokens: Vec<String> = segment.to_vec();
        while !tokens.is_empty() && self.assignment_token_re.is_match(&tokens[0]) {
            tokens.remove(0);
        }
        if tokens.is_empty() {
            return None;
        }
        if LEADING_CONDITIONALS.contains(&tokens[0].as_str()) {
            tokens.remove(0);
        } else if self.is_control_word(&tokens[0]) {
            return None;
        }
        loop {
            let head = tokens.first()?;
            if COMMAND_WRAPPERS.contains(&head.as_str())
                || WRAPPER_EXTRA.contains(&head.as_str())
            {
                tokens.remove(0);
                while tokens.first().map(|t| t.starts_with('-')).unwrap_or(false) {
                    tokens.remove(0);
                }
                while tokens
                    .first()
                    .map(|t| self.assignment_token_re.is_match(t))
                    .unwrap_or(false)
                {
                    tokens.remove(0);
                }
            } else {
                break;
            }
        }
        tokens.into_iter().next()
    }

    /// `_command_name` — substitute + bỏ variable + basename.
    fn command_name(
        &self,
        raw_command: &str,
        variables: &HashMap<String, String>,
    ) -> (String, bool) {
        let stripped = raw_command.trim_matches(|c| c == '"' || c == '\'');
        let (substituted, complete) = self.substitute(stripped, variables);
        let without_variables = self
            .variable_re
            .replace_all(&substituted, "")
            .trim_end_matches('/')
            .to_string();
        let name = without_variables
            .rsplit('/')
            .next()
            .unwrap_or("")
            .trim()
            .to_string();
        let dynamic = !complete || name.is_empty();
        (name, dynamic)
    }

    /// `_functions` — brace-depth function extraction.
    fn functions(&self, lines: &[&str], file_path: &str) -> Vec<ShellFunction> {
        let mut functions: Vec<ShellFunction> = Vec::new();
        let mut active: Option<(String, usize)> = None; // (name, start_line 1-based)
        let mut brace_depth = 0i64;
        for (idx, line) in lines.iter().enumerate() {
            let line_number = idx + 1;
            if let Some(caps) = self.function_re.captures(line)
                && active.is_none() {
                    let name = caps
                        .get(1)
                        .or_else(|| caps.get(2))
                        .map(|m| m.as_str().to_string())
                        .unwrap_or_default();
                    functions.push(ShellFunction {
                        symbol_id: format!("shell-function::{file_path}:{name}:{line_number}"),
                        name: name.clone(),
                        file_path: file_path.to_string(),
                        start_line: line_number as i64,
                        end_line: line_number as i64,
                        code: line.to_string(),
                    });
                    active = Some((name, line_number));
                    brace_depth = line.matches('{').count() as i64 - line.matches('}').count() as i64;
                    continue;
                }
            if active.is_some() {
                brace_depth +=
                    line.matches('{').count() as i64 - line.matches('}').count() as i64;
                if brace_depth <= 0 {
                    if let Some(function) = functions.last_mut() {
                        function.end_line = line_number as i64;
                    }
                    active = None;
                }
            }
        }
        functions
    }

    /// `parse_shell_text`.
    pub fn parse_shell_text(
        &self,
        source_text: &str,
        file_path: &str,
        project_root: &Path,
    ) -> ShellFile {
        let lines: Vec<&str> = py_splitlines(source_text);
        let mut variables: HashMap<String, String> = HashMap::new();
        let functions = self.functions(&lines, file_path);
        let mut invocations: Vec<ShellInvocation> = Vec::new();
        let mut relations: Vec<ShellRelation> = Vec::new();
        let mut diagnostics: Vec<ShellDiagnostic> = Vec::new();
        let root_real = std::fs::canonicalize(project_root)
            .unwrap_or_else(|_| project_root.to_path_buf());

        for (idx, line) in lines.iter().enumerate() {
            let line_number = idx + 1;

            if let Some(caps) = self.assignment_re.captures(line) {
                let name = caps.get(1).unwrap().as_str().to_string();
                if let Some(value) = caps
                    .get(2)
                    .or_else(|| caps.get(3))
                    .or_else(|| caps.get(4))
                {
                    variables.insert(name, value.as_str().to_string());
                }
            }

            let active_function = functions
                .iter()
                .find(|function| {
                    let line = line_number as i64;
                    function.start_line < line && line < function.end_line
                })
                .cloned();
            let source_id = active_function
                .as_ref()
                .map(|function| function.symbol_id.clone())
                .unwrap_or_else(|| file_path.to_string());
            let source_label = if active_function.is_some() {
                "ShellFunction"
            } else {
                "ShellScript"
            };

            let source_match = self
                .source_re
                .captures(line)
                .map(|caps| caps.get(1).unwrap().as_str().to_string())
                .or_else(|| {
                    self.direct_re
                        .captures(line)
                        .map(|caps| caps.get(1).unwrap().as_str().to_string())
                });
            if let Some(raw_target) = source_match.clone() {
                let (target_id, resolved) = self.resolve_path(
                    &raw_target,
                    file_path,
                    project_root,
                    &root_real,
                    &variables,
                );
                relations.push(ShellRelation {
                    source_id: source_id.clone(),
                    source_label: source_label.to_string(),
                    target_id,
                    target_label: "ShellScript".to_string(),
                    rel_type: "CALLS".to_string(),
                    line: line_number as i64,
                    raw_target: raw_target.clone(),
                    resolved,
                });
                if !resolved {
                    diagnostics.push(ShellDiagnostic {
                        code: "shell-call-unresolved".to_string(),
                        message: format!("Unable to resolve {raw_target}"),
                        file_path: file_path.to_string(),
                        line: line_number as i64,
                        severity: "warning".to_string(),
                    });
                }
            }

            if let Some(grep_caps) = self.grep_re.captures(line) {
                let raw_path = grep_caps
                    .get(4)
                    .or_else(|| grep_caps.get(5))
                    .or_else(|| grep_caps.get(6))
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default();
                let (substituted, complete) = self.substitute(&raw_path, &variables);
                let lowered = substituted.to_lowercase();
                if lowered.ends_with(".ini") || lowered.contains(".ini") {
                    let (target_id, resolved) = self.resolve_path(
                        &raw_path,
                        file_path,
                        project_root,
                        &root_real,
                        &variables,
                    );
                    relations.push(ShellRelation {
                        source_id: source_id.clone(),
                        source_label: source_label.to_string(),
                        target_id,
                        target_label: "File".to_string(),
                        rel_type: "REFERENCES".to_string(),
                        line: line_number as i64,
                        raw_target: raw_path.clone(),
                        resolved: resolved && complete,
                    });
                    if !resolved || !complete {
                        diagnostics.push(ShellDiagnostic {
                            code: "shell-ini-unresolved".to_string(),
                            message: format!("Unable to resolve {raw_path}"),
                            file_path: file_path.to_string(),
                            line: line_number as i64,
                            severity: "warning".to_string(),
                        });
                    }
                }
            }

            if self.assignment_re.is_match(line) || self.function_re.is_match(line) {
                continue;
            }
            let segments = match self.command_segments(line) {
                Ok(segments) => segments,
                Err(error) => {
                    diagnostics.push(ShellDiagnostic {
                        code: "shell-tokenize-unresolved".to_string(),
                        message: error,
                        file_path: file_path.to_string(),
                        line: line_number as i64,
                        severity: "warning".to_string(),
                    });
                    continue;
                }
            };
            for (seg_idx, segment) in segments.iter().enumerate() {
                let ordinal = seg_idx + 1;
                let Some(raw_command) = self.command_token(segment) else {
                    continue;
                };
                // Python `if not raw_command: continue` — token rỗng (vd `''`)
                // cũng bị skip.
                if raw_command.is_empty() {
                    continue;
                }
                let (name, dynamic) = self.command_name(&raw_command, &variables);
                if SHELL_BUILTINS.contains(&name.as_str())
                    || self.is_control_word(&name)
                {
                    continue;
                }
                let is_interpreter_skip = source_match.is_some()
                    && (SHELL_INTERPRETERS.contains(&name.as_str()) || name.ends_with(".sh"));
                if is_interpreter_skip {
                    continue;
                }
                // Python dict comprehension {f.name: f ...} — LAST trùng tên thắng.
                let internal = functions
                    .iter()
                    .rev()
                    .find(|function| function.name == name)
                    .cloned();
                if let Some(internal) = internal {
                    relations.push(ShellRelation {
                        source_id: source_id.clone(),
                        source_label: source_label.to_string(),
                        target_id: internal.symbol_id,
                        target_label: "ShellFunction".to_string(),
                        rel_type: "CALLS".to_string(),
                        line: line_number as i64,
                        raw_target: raw_command.clone(),
                        resolved: true,
                    });
                    continue;
                }
                invocations.push(ShellInvocation {
                    symbol_id: format!("shell-invocation::{file_path}:{line_number}:{ordinal}"),
                    source_id: source_id.clone(),
                    source_label: source_label.to_string(),
                    file_path: file_path.to_string(),
                    line: line_number as i64,
                    ordinal: ordinal as i64,
                    raw_command,
                    command_name: name,
                    dynamic,
                });
            }
        }

        ShellFile {
            file_path: file_path.to_string(),
            line_count: std::cmp::max(1, lines.len() as i64),
            encoding: String::new(),
            functions,
            invocations,
            relations,
            diagnostics,
        }
    }
}

impl Default for ShellParser {
    fn default() -> Self {
        Self::new()
    }
}

/// Python `str.splitlines()` — đủ ranh giới dòng Unicode.
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut out: Vec<&str> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        let b = bytes[i];
        let boundary_len = match b {
            b'\n' | b'\r' | 0x0b | 0x0c | 0x1c | 0x1d | 0x1e => 1,
            0xc2 if bytes.get(i + 1) == Some(&0x85) => 2, // NEL
            0xe2 if bytes.get(i + 1) == Some(&0x80)
                && matches!(bytes.get(i + 2), Some(0xa8) | Some(0xa9)) =>
            {
                3 // LS/PS
            }
            _ => 0,
        };
        if boundary_len > 0 {
            // \r\n là MỘT ranh giới
            if b == b'\r' && bytes.get(i + 1) == Some(&b'\n') {
                out.push(&text[start..i]);
                i += 2;
            } else {
                out.push(&text[start..i]);
                i += boundary_len;
            }
            start = i;
            continue;
        }
        // bước qua byte tiếp theo của multi-byte UTF-8
        i += if b < 0x80 {
            1
        } else {
            (b.leading_ones().min(4) as usize).max(1)
        };
    }
    // Python splitlines: không tạo phần tử cuối rỗng khi text kết thúc bằng
    // ranh giới dòng ("a\nb\n" → ["a","b"]).
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}
