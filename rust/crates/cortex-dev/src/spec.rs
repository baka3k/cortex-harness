//! Click-equivalent command/option specification model.
//!
//! The dev CLI surface must stay byte-compatible with `cortex_harness/dev.py`
//! (a Click program): same command names, same option names/aliases, same
//! defaults. The parity harness (`scripts/rust_parity/dev_cli_parity.py`)
//! compares the option surface parsed from `--help` output on both sides.

/// Value shape of an option, mirroring Click's parameter types.
#[derive(Debug)]
pub enum OptMeta {
    /// Boolean flag (`--dry-run`).
    Flag,
    /// Dual flag `--verbose / --no-verbose` with a default boolean.
    FlagPair(bool),
    /// Option taking a value; payload is the type label shown in help
    /// (`TEXT`, `PATH`, `FILE`, `DIRECTORY`, `FLOAT`, `INTEGER`).
    Value(&'static str),
    /// Option taking a value restricted to a fixed choice set.
    Choice(&'static [&'static str]),
    /// Integer with a closed range (Click `IntRange`).
    IntRange(i64, i64),
    /// Integer with only a lower bound (`IntRange(min=n)`).
    IntMin(i64),
}

#[derive(Debug)]
pub struct Opt {
    /// All spellings; `names[0]` is the canonical name used for lookup keys.
    pub names: &'static [&'static str],
    pub meta: OptMeta,
    pub required: bool,
    /// Repeatable option (Click `multiple=True`).
    pub multiple: bool,
    /// Default rendered in help (Click `show_default=True` only where used).
    pub default: Option<&'static str>,
    /// Default is a dynamic value supplied at render time (repo root path).
    pub default_dynamic: bool,
    pub help: &'static str,
}

impl Opt {
    pub fn canonical(&self) -> &'static str {
        self.names[0]
    }

    pub fn takes_value(&self) -> bool {
        !matches!(self.meta, OptMeta::Flag | OptMeta::FlagPair(_))
    }

    /// Left help column, e.g. `--database, --db TEXT` or `--verbose / --no-verbose`.
    pub fn help_column(&self) -> String {
        let mut col = String::new();
        match self.meta {
            OptMeta::FlagPair(_) => {
                col.push_str(self.names[0]);
                col.push_str(" / ");
                col.push_str(self.names[1]);
            }
            _ => {
                for (i, name) in self.names.iter().enumerate() {
                    if i > 0 {
                        col.push_str(", ");
                    }
                    col.push_str(name);
                }
            }
        }
        if self.takes_value() {
            col.push(' ');
            col.push_str(&self.meta_label());
        }
        col
    }

    pub fn meta_label(&self) -> String {
        match self.meta {
            OptMeta::Flag | OptMeta::FlagPair(_) => String::new(),
            OptMeta::Value(label) => (*label).to_string(),
            OptMeta::Choice(choices) => format!("[{}]", choices.join("|")),
            OptMeta::IntRange(lo, hi) => format!("INTEGER RANGE [{}<=x<={}]", lo, hi),
            OptMeta::IntMin(lo) => format!("INTEGER RANGE [x>={}]", lo),
        }
    }

    /// Help-column suffix: `[default: x]` / `[required]`.
    pub fn default_note(&self, dynamic_default: Option<&str>) -> String {
        if self.required {
            return "[required]".to_string();
        }
        let effective = self.default.or(if self.default_dynamic {
            dynamic_default
        } else {
            None
        });
        if let Some(d) =
            effective.filter(|_| self.takes_value() || matches!(self.meta, OptMeta::FlagPair(_)))
        {
            return format!("[default: {}]", d);
        }
        String::new()
    }
}

#[derive(Debug)]
pub struct ArgSpec {
    /// Display metavar, e.g. `PATH`, `<FOLDER>...`, `[FOLDER...]`.
    pub metavar: &'static str,
    pub required: bool,
    pub variadic: bool,
}

#[derive(Debug)]
pub struct Cmd {
    pub name: &'static str,
    /// Long description printed under the usage line.
    pub desc: &'static str,
    pub opts: &'static [Opt],
    pub args: &'static [ArgSpec],
    pub subs: &'static [&'static Cmd],
    /// Click group without `invoke_without_command` prints help when no
    /// arguments are given; `sync code` / `sync doc` run interactively instead.
    pub runs_without_sub: bool,
}

impl Cmd {
    pub fn find_sub(&self, name: &str) -> Option<&'static Cmd> {
        self.subs.iter().copied().find(|c| c.name == name)
    }

    pub fn find_opt(&self, token: &str) -> Option<&'static Opt> {
        self.opts
            .iter()
            .find(|o| o.names.contains(&token))
    }

    /// Short help line used in the parent's `Commands:` listing (first line
    /// of the description, Click truncates but the parity gate ignores prose).
    pub fn short_help(&self) -> &'static str {
        self.desc.lines().next().unwrap_or("")
    }
}
