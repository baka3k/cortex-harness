//! Port `shlex.shlex(line, posix=True, punctuation_chars=";&|")` với
//! `whitespace_split=True`, `commenters="#"` — state machine mirror thuật toán
//! `read_token` của CPython (đã probeBehavior byte-exact trên 20+ case).

const WHITESPACE: [char; 4] = [' ', '\t', '\r', '\n'];
const QUOTES: [char; 2] = ['"', '\''];
const ESCAPED_QUOTES: [char; 1] = ['"'];
const ESCAPE: char = '\\';
const COMMENTER: char = '#';
const PUNCTUATION: [char; 3] = [';', '&', '|'];

#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
enum State {
    Whitespace,
    Word,
    PunctRun,
    Quote(char),
    Escape,
    Done,
}

fn is_whitespace(c: char) -> bool {
    WHITESPACE.contains(&c)
}

pub fn is_punctuation(c: char) -> bool {
    PUNCTUATION.contains(&c)
}

/// Tokenize 1 dòng; Err(message) khớp ValueError của Python
/// ("No closing quotation" / "No escaped character").
pub fn posix_shlex_split(line: &str) -> Result<Vec<String>, String> {
    let chars: Vec<char> = line.chars().collect();
    let mut tokens: Vec<String> = Vec::new();
    let mut pushback: Vec<char> = Vec::new();
    let mut state = State::Whitespace;
    let mut escapedstate = ' ';
    let mut token = String::new();
    // `quoted` scope theo CPython: được reset mỗi read_token (mỗi token một call).
    let mut next_idx = 0usize;

    loop {
        // read_char với pushback
        let nextchar: Option<char> = if let Some(c) = pushback.pop() {
            Some(c)
        } else if next_idx < chars.len() {
            let c = chars[next_idx];
            next_idx += 1;
            Some(c)
        } else {
            None
        };

        let nextchar = match nextchar {
            Some(c) => c,
            None => {
                // EOF
                match state {
                    State::Quote(_) => return Err("No closing quotation".to_string()),
                    State::Escape => return Err("No escaped character".to_string()),
                    State::Whitespace => {
                        // state None; token rỗng → None → không emit
                    }
                    State::Word | State::PunctRun => {
                        // EOF trong word/punct state: emit token hiện hành rồi dừng.
                        tokens.push(std::mem::take(&mut token));
                    }
                    State::Done => {}
                }
                break;
            }
        };

        match state {
            State::Whitespace => {
                if is_whitespace(nextchar) {
                    // token rỗng & chưa quoted → continue
                    continue;
                } else if nextchar == COMMENTER {
                    // instream.readline() — với input 1 dòng nghĩa là bỏ hết phần còn lại
                    next_idx = chars.len();
                    pushback.clear();
                    continue;
                } else if nextchar == ESCAPE {
                    escapedstate = 'a';
                    state = State::Escape;
                } else if is_punctuation(nextchar) {
                    token = nextchar.to_string();
                    state = State::PunctRun;
                } else if QUOTES.contains(&nextchar) {
                    state = State::Quote(nextchar);
                } else {
                    // whitespace_split=True → accumulate
                    token = nextchar.to_string();
                    state = State::Word;
                }
            }
            State::Quote(q) => {
                if nextchar == q {
                    state = State::Word; // posix: về word state, không emit ngay
                } else if ESCAPE == nextchar && ESCAPED_QUOTES.contains(&q) {
                    escapedstate = q;
                    state = State::Escape;
                } else {
                    token.push(nextchar);
                }
            }
            State::Escape => {
                // posix: trong quote chỉ escape quote hoặc escape char
                if escapedstate != ' ' && QUOTES.contains(&escapedstate)
                    && nextchar != ESCAPE
                    && nextchar != escapedstate
                {
                    token.push(ESCAPE);
                }
                token.push(nextchar);
                state = if escapedstate == 'a' || escapedstate == ' ' {
                    State::Word
                } else {
                    State::Quote(escapedstate)
                };
                escapedstate = ' ';
            }
            State::Word | State::PunctRun => {
                if is_whitespace(nextchar) {
                    // emit token nếu non-empty (token luôn non-empty ở 2 state này)
                    tokens.push(std::mem::take(&mut token));
                    state = State::Whitespace;
                } else if nextchar == COMMENTER {
                    next_idx = chars.len();
                    pushback.clear();
                    tokens.push(std::mem::take(&mut token));
                    state = State::Whitespace;
                } else if state == State::PunctRun {
                    if is_punctuation(nextchar) {
                        token.push(nextchar);
                    } else {
                        if !is_whitespace(nextchar) {
                            pushback.push(nextchar);
                        }
                        state = State::Whitespace;
                        tokens.push(std::mem::take(&mut token));
                    }
                } else if QUOTES.contains(&nextchar) {
                    state = State::Quote(nextchar);
                } else if nextchar == ESCAPE {
                    escapedstate = 'a';
                    state = State::Escape;
                } else if is_punctuation(nextchar) {
                    // whitespace_split accumulate không nhận punctuation →
                    // pushback + emit word
                    pushback.push(nextchar);
                    state = State::Whitespace;
                    tokens.push(std::mem::take(&mut token));
                } else {
                    token.push(nextchar);
                }
            }
            State::Done => break,
        }
    }
    Ok(tokens)
}

#[cfg(test)]
mod tests {
    use super::posix_shlex_split;

    #[test]
    fn matches_cpython_probes() {
        let cases: Vec<(&str, Result<Vec<&str>, &str>)> = vec![
            ("echo hello", Ok(vec!["echo", "hello"])),
            ("run.sh ;; stop.sh", Ok(vec!["run.sh", ";;", "stop.sh"])),
            ("a;b|c", Ok(vec!["a", ";", "b", "|", "c"])),
            ("grep 'x;y' file;z", Ok(vec!["grep", "x;y", "file", ";", "z"])),
            ("cmd \"a;b\" c", Ok(vec!["cmd", "a;b", "c"])),
            ("a\\;b c", Ok(vec!["a;b", "c"])),
            ("foo # comment ; here", Ok(vec!["foo"])),
            (
                "if [ -f x ]; then run.sh; fi",
                Ok(vec!["if", "[", "-f", "x", "]", ";", "then", "run.sh", ";", "fi"]),
            ),
            ("VAR=1 cmd arg", Ok(vec!["VAR=1", "cmd", "arg"])),
            ("nohup ./start.sh &", Ok(vec!["nohup", "./start.sh", "&"])),
            ("$(date) ${X}.sh", Ok(vec!["$(date)", "${X}.sh"])),
            ("echo 'it\\'s'", Err("No closing quotation")),
            ("a(b)c d<e>f", Ok(vec!["a(b)c", "d<e>f"])),
            ("cmd arg #trail", Ok(vec!["cmd", "arg"])),
            ("svc.sh||fallback.sh", Ok(vec!["svc.sh", "||", "fallback.sh"])),
            ("  ;  ", Ok(vec![";"])),
            ("", Ok(vec![])),
            ("VAR='a b' svc --flag=v", Ok(vec!["VAR=a b", "svc", "--flag=v"])),
            ("a#b c", Ok(vec!["a"])),
            ("cmd\\", Err("No escaped character")),
            ("'a';'b'", Ok(vec!["a", ";", "b"])),
            ("\"x\"y", Ok(vec!["xy"])),
            ("a;b#c", Ok(vec!["a", ";", "b"])),
            ("\\\\;x", Ok(vec!["\\", ";", "x"])),
            ("run;;;", Ok(vec!["run", ";;;"])),
            ("pre;'p;q';post", Ok(vec!["pre", ";", "p;q", ";", "post"])),
        ];
        for (input, expected) in cases {
            let got = posix_shlex_split(input);
            let expected = expected
                .map(|v| v.iter().map(|s| s.to_string()).collect())
                .map_err(str::to_string);
            assert_eq!(got, expected, "input={input:?}");
        }
    }
}
