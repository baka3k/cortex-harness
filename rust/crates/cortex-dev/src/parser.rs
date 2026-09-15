//! Click-equivalent argument parser for the dev command tree.
//!
//! Mirrors the Click behaviours the Python CLI relies on: `--opt=value`,
//! `--opt value`, flag pairs (`--verbose/--no-verbose`), option aliases
//! (`--database, --db`), choices, IntRange validation, group options must
//! appear before the subcommand token, `--help` anywhere, and variadic
//! positional arguments.

use crate::spec::{ArgSpec, Cmd, Opt, OptMeta};
use std::collections::HashMap;

#[derive(Debug)]
pub struct Matches {
    /// canonical option name (e.g. `--project-dir`) -> collected values.
    pub opts: HashMap<String, Vec<String>>,
    pub positionals: Vec<String>,
}

impl Matches {
    fn new() -> Self {
        Matches { opts: HashMap::new(), positionals: Vec::new() }
    }

    pub fn has(&self, name: &str) -> bool {
        self.opts.contains_key(name)
    }

    /// Value of a value-taking option or the resolved boolean of a flag pair,
    /// falling back to `default` when absent.
    pub fn value(&self, name: &str) -> Option<&str> {
        self.opts.get(name).and_then(|v| v.last()).map(|s| s.as_str())
    }

    pub fn value_or(&self, name: &str, default: &str) -> String {
        self.value(name).unwrap_or(default).to_string()
    }

    pub fn values(&self, name: &str) -> Vec<String> {
        self.opts.get(name).cloned().unwrap_or_default()
    }

    pub fn positionals(&self) -> &[String] {
        &self.positionals
    }

    /// Click group semantics: a subcommand handler sees its own options with
    /// the parent group's options as fallback (dev.py reads
    /// `ctx.parent.params` for `--project-dir` on `sync code all/stop/add`).
    pub fn merged(parent: &Matches, child: &Matches) -> Matches {
        let mut opts = parent.opts.clone();
        for (key, values) in &child.opts {
            opts.insert(key.clone(), values.clone());
        }
        Matches { opts, positionals: child.positionals.clone() }
    }

    /// True when a Flag/FlagPair option was given (or given as `--no-x`).
    pub fn flag(&self, name: &str) -> bool {
        matches!(self.opts.get(name), Some(vals) if vals.last().map(|v| v == "1").unwrap_or(false))
    }
}

/// One level of the resolved command path with its parsed options.
#[derive(Debug)]
pub struct Level<'a> {
    pub cmd: &'a Cmd,
    pub matches: Matches,
}

#[derive(Debug)]
pub enum ParseOutcome {
    /// `--help` requested for this path, or a bare group (Click exits 2 for
    /// no-args groups but 0 for explicit `--help`).
    Help {
        path: Vec<&'static Cmd>,
        code: i32,
    },
    Run(Vec<Level<'static>>),
}

#[derive(Debug)]
pub struct ParseError {
    pub message: String,
    /// Click usage errors exit with code 2; runtime errors use 1.
    pub code: i32,
}

pub fn parse(root: &'static Cmd, argv: &[String], prog: &str) -> Result<ParseOutcome, ParseError> {
    let mut path: Vec<&'static Cmd> = vec![root];
    let mut levels: Vec<Level<'static>> = vec![Level { cmd: root, matches: Matches::new() }];
    let mut positional_idx = 0usize;
    let mut i = 0usize;

    while i < argv.len() {
        let tok = argv[i].clone();
        i += 1;
        let cur = *path.last().unwrap();

        if tok == "--help" {
            return Ok(ParseOutcome::Help { path, code: 0 });
        }

        if tok.starts_with("--") && tok.len() > 2 {
            let (name, inline_val) = match tok.split_once('=') {
                Some((n, v)) => (n.to_string(), Some(v.to_string())),
                None => (tok.clone(), None),
            };
            let opt = match cur.find_opt(&name) {
                Some(o) => o,
                None => {
                    return Err(ParseError {
                        message: format!("Error: No such option '{}'", name),
                        code: 2,
                    })
                }
            };
            let level = levels.last_mut().unwrap();
            store_option(level, opt, &name, inline_val, argv, &mut i, prog)?;
            continue;
        }

        // Subcommand dispatch: group options must precede the subcommand.
        if !cur.subs.is_empty() {
            match cur.find_sub(&tok) {
                Some(sub) => {
                    path.push(sub);
                    levels.push(Level { cmd: sub, matches: Matches::new() });
                    positional_idx = 0;
                    continue;
                }
                None => {
                    return Err(ParseError {
                        message: format!("Error: No such command '{}'. Try '{} --help' for help.", tok, prog),
                        code: 2,
                    });
                }
            }
        }

        // Positional argument.
        let specs: &[ArgSpec] = cur.args;
        let spec: Option<&ArgSpec> = specs.get(positional_idx).or_else(|| {
            specs
                .last()
                .filter(|s| s.variadic && positional_idx >= specs.len())
        });
        match spec {
            Some(s) => {
                levels.last_mut().unwrap().matches.positionals.push(tok);
                if !s.variadic {
                    positional_idx += 1;
                }
            }
            None => {
                return Err(ParseError {
                    message: format!("Error: Got unexpected extra argument ({})", tok),
                    code: 2,
                });
            }
        }
    }

    // No subcommand given on a group.
    let cur = *path.last().unwrap();
    if !cur.subs.is_empty() {
        if cur.runs_without_sub {
            return Ok(ParseOutcome::Run(levels));
        }
        return Ok(ParseOutcome::Help { path, code: 2 });
    }

    // Required positionals.
    let mut given = levels.last().unwrap().matches.positionals.len();
    for spec in cur.args {
        if spec.required && given == 0 {
            return Err(ParseError {
                message: format!("Error: Missing argument '{}'.", spec.metavar),
                code: 2,
            });
        }
        given = given.saturating_sub(if spec.variadic { 0 } else { 1 });
    }

    // Required options.
    for opt in cur.opts {
        if opt.required && !levels.last().unwrap().matches.has(opt.canonical()) {
            return Err(ParseError {
                message: format!("Error: Missing option '{}'.", opt.names[0]),
                code: 2,
            });
        }
    }

    Ok(ParseOutcome::Run(levels))
}

fn store_option(
    level: &mut Level<'static>,
    opt: &'static Opt,
    name: &str,
    inline: Option<String>,
    argv: &[String],
    i: &mut usize,
    _prog: &str,
) -> Result<(), ParseError> {
    let value: Option<String> = match &opt.meta {
        OptMeta::Flag => {
            if inline.is_some() {
                return Err(ParseError {
                    message: format!("Error: Option '{}' does not take a value.", name),
                    code: 2,
                });
            }
            Some("1".to_string())
        }
        OptMeta::FlagPair(default_on) => {
            let on = name == opt.names[0];
            Some(if on == *default_on { "1".to_string() } else { "0".to_string() })
        }
        OptMeta::Value(_) | OptMeta::Choice(_) | OptMeta::IntRange(_, _) | OptMeta::IntMin(_) => {
            match inline {
                Some(v) => Some(v),
                None => {
                    if *i < argv.len() {
                        let v = argv[*i].clone();
                        *i += 1;
                        Some(v)
                    } else {
                        return Err(ParseError {
                            message: format!("Error: Option '{}' requires an argument.", name),
                            code: 2,
                        });
                    }
                }
            }
        }
    };

    let value = value.unwrap();
    if let OptMeta::Choice(choices) = opt.meta {
        let ok = choices.iter().any(|c| c.eq_ignore_ascii_case(&value));
        if !ok {
            let quoted: Vec<String> = choices.iter().map(|c| format!("'{}'", c)).collect();
            return Err(ParseError {
                message: format!(
                    "Error: Invalid value for '{}': '{}' is not one of {}.",
                    name,
                    value,
                    quoted.join(", ")
                ),
                code: 2,
            });
        }
    }
    if let OptMeta::IntRange(lo, hi) = opt.meta {
        let parsed: i64 = value.parse().map_err(|_| ParseError {
            message: format!("Error: Invalid value for '{}': '{}' is not a valid integer.", name, value),
            code: 2,
        })?;
        if parsed < lo || parsed > hi {
            return Err(ParseError {
                message: format!(
                    "Error: Invalid value for '{}': {} is not in the range {}<=x<={}.",
                    name, parsed, lo, hi
                ),
                code: 2,
            });
        }
    }
    if let OptMeta::IntMin(lo) = opt.meta {
        let parsed: i64 = value.parse().map_err(|_| ParseError {
            message: format!("Error: Invalid value for '{}': '{}' is not a valid integer.", name, value),
            code: 2,
        })?;
        if parsed < lo {
            return Err(ParseError {
                message: format!("Error: Invalid value for '{}': {} is smaller than the minimum {}.", name, parsed, lo),
                code: 2,
            });
        }
    }

    let entry = level.matches.opts.entry(opt.canonical().to_string()).or_default();
    if opt.multiple {
        entry.push(value);
    } else {
        entry.clear();
        entry.push(value);
    }
    Ok(())
}
