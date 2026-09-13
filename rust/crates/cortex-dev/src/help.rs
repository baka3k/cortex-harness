//! Click-style `--help` renderer. The parity gate parses the option surface
//! out of help text, so option names/aliases/choices must appear exactly;
//! prose wrapping differences are tolerated.

use crate::spec::Cmd;

/// Width of the option column before the help text starts (Click pads
/// dynamically; we use the same two-space separator rule).
pub fn render_help(path: &[&'static Cmd], prog: &str, dynamic_defaults: &dyn Fn(&str) -> Option<String>) -> String {
    let cmd = path.last().unwrap();
    let mut out = String::new();

    // Usage line.
    let mut usage = format!("Usage: {}{}", prog, {
        let mut s = String::new();
        for c in &path[1..] {
            s.push(' ');
            s.push_str(c.name);
        }
        s
    });
    usage.push_str(" [OPTIONS]");
    for arg in cmd.args {
        usage.push(' ');
        usage.push_str(arg.metavar);
    }
    if !cmd.subs.is_empty() {
        usage.push_str(" COMMAND [ARGS]...");
    }
    out.push_str(&usage);
    out.push_str("\n\n");

    // Description, indented by two spaces.
    for line in cmd.desc.lines() {
        if line.is_empty() {
            out.push('\n');
        } else {
            out.push_str("  ");
            out.push_str(line);
            out.push('\n');
        }
    }

    // Options.
    out.push_str("\nOptions:\n");
    for opt in cmd.opts {
        let dynamic = dynamic_defaults(opt.canonical());
        let mut left = format!("  {}", opt.help_column());
        let note = opt.default_note(dynamic.as_deref());
        let help = opt.help;
        if help.is_empty() && note.is_empty() {
            out.push_str(&left);
            out.push('\n');
            continue;
        }
        let mut right_parts: Vec<String> = Vec::new();
        if !help.is_empty() {
            right_parts.push(help.to_string());
        }
        if !note.is_empty() {
            right_parts.push(note);
        }
        let right = right_parts.join("  ");
        if left.len() + 2 + right.len() <= 78 && !left.contains('\n') {
            while left.len() < 26 {
                left.push(' ');
            }
            out.push_str(&left);
            out.push_str("  ");
            out.push_str(&right);
            out.push('\n');
        } else {
            out.push_str(&left);
            out.push('\n');
            // Continuation lines, indented past the option column.
            for part in right.split("\n") {
                out.push_str("                                  ");
                out.push_str(part);
                out.push('\n');
            }
        }
    }

    // Commands.
    if !cmd.subs.is_empty() {
        out.push_str("\nCommands:\n");
        let mut names: Vec<&'static Cmd> = cmd.subs.to_vec();
        names.sort_by_key(|c| c.name);
        let width = names.iter().map(|c| c.name.len()).max().unwrap_or(0);
        for sub in names {
            let mut left = format!("  {}", sub.name);
            while left.len() < width + 4 {
                left.push(' ');
            }
            out.push_str(&left);
            out.push_str(&truncate(sub.short_help(), 44));
            out.push('\n');
        }
    }

    out
}

fn truncate(s: &str, max: usize) -> String {
    let mut chars = s.chars();
    let mut out = String::new();
    for _ in 0..max {
        match chars.next() {
            Some(c) => out.push(c),
            None => return out,
        }
    }
    if chars.next().is_some() {
        out.push_str("...");
    }
    out
}
