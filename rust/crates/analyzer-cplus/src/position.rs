//! Path helpers khớp semantics `os.path` của Python (lexical, không resolve
//! symlink): `normpath`, `relpath`, `commonpath`, `basename`.

/// `posixpath.normpath` — gộp `//`, `./`, xử lý `..` (lexical; `..` đầu
/// relative path được giữ nếu vượt root).
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let absolute = path.starts_with('/');
    let mut comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        match comp {
            "" | "." => {}
            ".." => {
                if let Some(last) = comps.last()
                    && *last != ".." {
                        comps.pop();
                        continue;
                    }
                if !absolute {
                    comps.push("..");
                }
            }
            other => comps.push(other),
        }
    }
    let joined = comps.join("/");
    if absolute {
        format!("/{joined}")
    } else if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

fn split_components(path: &str) -> (bool, Vec<String>) {
    let absolute = path.starts_with('/');
    let comps: Vec<String> = path
        .split('/')
        .filter(|c| !c.is_empty() && *c != ".")
        .map(str::to_string)
        .collect();
    (absolute, comps)
}

/// `os.path.relpath(path, start)` — lexical. Cả 2 đường mặc định absolute
/// (các call site trong analyzer đều truyền absolute hoặc cùng-root rel).
pub fn relpath(path: &str, start: &str) -> String {
    let (abs_p, mut pc) = split_components(path);
    let (abs_s, mut sc) = split_components(start);
    if abs_p != abs_s {
        // Python raise ValueError; analyzer không bao giờ rơi vào đây vì
        // cả hai đều absolute cùng root — trả path nguyên bản cho an toàn.
        let _ = (&mut pc, &mut sc);
        return path.to_string();
    }
    let mut i = 0;
    while i < pc.len() && i < sc.len() && pc[i] == sc[i] {
        i += 1;
    }
    let ups = sc.len() - i;
    let mut parts: Vec<String> = vec!["..".to_string(); ups];
    parts.extend(pc[i..].iter().cloned());
    if parts.is_empty() {
        return ".".to_string();
    }
    parts.join("/")
}

/// `os.path.commonpath([a, b])` cho 2 absolute posix path — component-wise.
/// Trả "" khi không có common prefix (Python raise ValueError đối với mix
/// abs/rel; analyzer chỉ dùng cho cặp absolute).
pub fn commonpath2(a: &str, b: &str) -> String {
    let (abs_a, ca) = split_components(a);
    let (abs_b, cb) = split_components(b);
    if abs_a != abs_b {
        return String::new();
    }
    let mut common: Vec<String> = Vec::new();
    for (x, y) in ca.iter().zip(cb.iter()) {
        if x != y {
            break;
        }
        common.push(x.clone());
    }
    if common.is_empty() {
        return if abs_a { "/".to_string() } else { String::new() };
    }
    format!("/{}", common.join("/"))
}

/// `os.path.basename`.
pub fn basename(path: &str) -> String {
    let trimmed = path.trim_end_matches('/');
    match trimmed.rsplit_once('/') {
        Some((_, name)) => name.to_string(),
        None => trimmed.to_string(),
    }
}

/// `os.path.dirname`.
pub fn dirname(path: &str) -> String {
    match path.rsplit_once('/') {
        Some((dir, _)) => {
            if dir.is_empty() {
                "/".to_string()
            } else {
                dir.to_string()
            }
        }
        None => {
            if path.is_empty() {
                String::new()
            } else {
                ".".to_string()
            }
        }
    }
}

/// `os.path.splitext`.
pub fn splitext(path: &str) -> (String, String) {
    let name = basename(path);
    match name.rfind('.') {
        Some(idx) if idx > 0 => (
            path[..path.len() - (name.len() - idx)].to_string(),
            name[idx..].to_string(),
        ),
        _ => (path.to_string(), String::new()),
    }
}
