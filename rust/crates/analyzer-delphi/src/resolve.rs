//! Port `delphi_analyzer.py` phần liên kết uses + call resolution:
//! `_collect_unit_and_uses_index`, `_resolve_uses_by_file`,
//! `_expand_impacted_files_by_uses`, `uses_closure` (closure có cache, giữ
//! nguyên semantics partial-cache trong cycle), `_resolve_calls`.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use crate::dparse::{
    extract_unit_name, extract_uses_units, strip_comments_and_strings, CallEdge, FunctionDef,
};

/// rel-posix path khớp `os.path.relpath(abs_path, root).replace("\\", "/")`.
fn rel_of(root: &Path, abs_path: &str) -> String {
    cortex_analyzer_framework::scan::rel_posix(root, Path::new(abs_path))
}

/// `os.path.splitext(basename)[0]` — dot ĐẦU file không tính là extension.
fn basename_stem(rel_path: &str) -> String {
    let base = rel_path.rsplit('/').next().unwrap_or(rel_path);
    match base.rfind('.') {
        Some(idx) if idx > 0 => base[..idx].to_string(),
        _ => base.to_string(),
    }
}

/// `_collect_unit_and_uses_index` — đọc TOÀN BỘ file scan được (không range
/// filter cho uses), map rel-posix path → unit name / uses list.
pub fn collect_unit_and_uses_index(
    all_scanned_files: &[String],
    root: &Path,
) -> (HashMap<String, String>, HashMap<String, Vec<String>>) {
    let mut unit_name_by_file: HashMap<String, String> = HashMap::new();
    let mut uses_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for abs_path in all_scanned_files {
        let rel_path = rel_of(root, abs_path);
        let text = match std::fs::read(abs_path) {
            Ok(raw) => cortex_analyzer_framework::ts::decode_ignore(&raw),
            Err(_) => {
                unit_name_by_file.insert(rel_path.clone(), basename_stem(&rel_path));
                uses_by_file.insert(rel_path, Vec::new());
                continue;
            }
        };
        unit_name_by_file.insert(rel_path.clone(), extract_unit_name(&text, &rel_path));
        let masked = strip_comments_and_strings(&text);
        uses_by_file.insert(rel_path, extract_uses_units(&text, &masked, None));
    }
    (unit_name_by_file, uses_by_file)
}

/// `_resolve_uses_by_file` — resolve unit names → rel path (theo unit header
/// trước, basename stem sau; case-insensitive; first-wins theo thứ tự scan).
pub fn resolve_uses_by_file(
    uses_by_file: &HashMap<String, Vec<String>>,
    unit_name_by_file: &HashMap<String, String>,
    all_scanned_files: &[String],
    root: &Path,
) -> HashMap<String, Vec<String>> {
    let mut file_lookup_by_basename: HashMap<String, String> = HashMap::new();
    let mut file_lookup_by_unit: HashMap<String, String> = HashMap::new();
    for abs_path in all_scanned_files {
        let rel_path = rel_of(root, abs_path);
        let base = rel_path.rsplit('/').next().unwrap_or(&rel_path);
        let stem = match base.rfind('.') {
            Some(idx) if idx > 0 => base[..idx].to_lowercase(),
            _ => base.to_lowercase(),
        };
        file_lookup_by_basename
            .entry(stem)
            .or_insert_with(|| rel_path.clone());
    }
    for (rel_path, unit_name) in unit_name_by_file {
        let raw = unit_name.trim().to_lowercase();
        if !raw.is_empty() {
            file_lookup_by_unit.entry(raw).or_insert_with(|| rel_path.clone());
        }
    }

    let mut resolved_uses_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for (file_path, units) in uses_by_file {
        let mut resolved: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for unit_name in units {
            let raw = unit_name.trim().to_lowercase();
            if raw.is_empty() {
                continue;
            }
            let mut candidates = vec![raw.clone()];
            if raw.contains('.')
                && let Some(last) = raw.rsplit('.').next()
            {
                candidates.push(last.to_string());
            }
            let mut target: Option<String> = None;
            for cand in &candidates {
                target = file_lookup_by_unit
                    .get(cand)
                    .cloned()
                    .or_else(|| file_lookup_by_basename.get(cand).cloned());
                if target.is_some() {
                    break;
                }
            }
            if let Some(target) = target {
                if target == *file_path || seen.contains(&target) {
                    continue;
                }
                seen.insert(target.clone());
                resolved.push(target);
            }
        }
        resolved_uses_by_file.insert(file_path.clone(), resolved);
    }
    resolved_uses_by_file
}

/// `_expand_impacted_files_by_uses` — BFS trên reverse map (dependents),
/// impacted KHÔNG chứa seeds.
pub fn expand_impacted_files_by_uses(
    changed_existing: &HashSet<String>,
    resolved_uses_by_file: &HashMap<String, Vec<String>>,
) -> HashSet<String> {
    let mut reverse_map: HashMap<String, Vec<String>> = HashMap::new();
    let mut reverse_sources: Vec<String> = resolved_uses_by_file.keys().cloned().collect();
    reverse_sources.sort();
    for source_file in reverse_sources {
        for dep in &resolved_uses_by_file[&source_file] {
            reverse_map.entry(dep.clone()).or_default().push(source_file.clone());
        }
    }

    let mut impacted: HashSet<String> = HashSet::new();
    let mut queue: Vec<String> = changed_existing.iter().cloned().collect();
    let mut seen: HashSet<String> = changed_existing.clone();
    // Python queue.pop(0) — FIFO.
    let mut head = 0usize;
    while head < queue.len() {
        let current = queue[head].clone();
        head += 1;
        let mut dependents = reverse_map.get(&current).cloned().unwrap_or_default();
        dependents.sort();
        dependents.dedup();
        for dependent in dependents {
            if seen.contains(&dependent) {
                continue;
            }
            seen.insert(dependent.clone());
            impacted.insert(dependent.clone());
            queue.push(dependent);
        }
    }
    impacted
}

/// `uses_closure` — transitive closure với cache + cycle guard; giữ nguyên
/// semantics Python: kết quả tính dưới non-empty stack vẫn được cache.
pub fn build_uses_closure_by_file(
    selected_files: &[String],
    resolved_uses_by_file_all: &HashMap<String, Vec<String>>,
) -> HashMap<String, HashSet<String>> {
    let mut closure_cache: HashMap<String, HashSet<String>> = HashMap::new();

    fn uses_closure(
        file_path: &str,
        resolved: &HashMap<String, Vec<String>>,
        cache: &mut HashMap<String, HashSet<String>>,
        stack: &mut HashSet<String>,
    ) -> HashSet<String> {
        if let Some(hit) = cache.get(file_path) {
            return hit.clone();
        }
        if stack.contains(file_path) {
            return HashSet::new();
        }
        stack.insert(file_path.to_string());
        let mut result: HashSet<String> = HashSet::new();
        for dep in resolved.get(file_path).cloned().unwrap_or_default() {
            result.insert(dep.clone());
            result.extend(uses_closure(&dep, resolved, cache, stack));
        }
        stack.remove(file_path);
        cache.insert(file_path.to_string(), result.clone());
        result
    }

    let mut out: HashMap<String, HashSet<String>> = HashMap::new();
    for fp in selected_files {
        let mut stack: HashSet<String> = HashSet::new();
        out.insert(fp.clone(), uses_closure(fp, resolved_uses_by_file_all, &mut closure_cache, &mut stack));
    }
    out
}

/// `_resolve_calls` — scoring candidate callee theo symbol_id; tie-break
/// (score, qualified_name) lấy max đầu tiên gặp (khớp Python `max`).
pub fn resolve_calls(
    functions: &[FunctionDef],
    calls: &mut [CallEdge],
    uses_closure_by_file: &HashMap<String, HashSet<String>>,
) {
    let mut by_name: HashMap<&str, Vec<&FunctionDef>> = HashMap::new();
    let mut by_name_arity: HashMap<(&str, usize), Vec<&FunctionDef>> = HashMap::new();
    let mut by_scope_name: HashMap<(Option<&str>, &str), Vec<&FunctionDef>> = HashMap::new();
    let mut by_scope_name_arity: HashMap<(Option<&str>, &str, usize), Vec<&FunctionDef>> = HashMap::new();
    let mut by_file_name: HashMap<(&str, &str), Vec<&FunctionDef>> = HashMap::new();
    let mut by_file_name_arity: HashMap<(&str, &str, usize), Vec<&FunctionDef>> = HashMap::new();
    let mut by_qualified: HashMap<&str, &FunctionDef> = HashMap::new();
    let mut by_qualified_arity: HashMap<(&str, usize), &FunctionDef> = HashMap::new();

    for func in functions {
        by_name.entry(func.name.as_str()).or_default().push(func);
        by_name_arity
            .entry((func.name.as_str(), func.arity))
            .or_default()
            .push(func);
        by_scope_name
            .entry((func.scope_name.as_deref(), func.name.as_str()))
            .or_default()
            .push(func);
        by_scope_name_arity
            .entry((func.scope_name.as_deref(), func.name.as_str(), func.arity))
            .or_default()
            .push(func);
        by_file_name
            .entry((func.file_path.as_str(), func.name.as_str()))
            .or_default()
            .push(func);
        by_file_name_arity
            .entry((func.file_path.as_str(), func.name.as_str(), func.arity))
            .or_default()
            .push(func);
        by_qualified.entry(func.qualified_name.as_str()).or_insert(func);
        by_qualified_arity
            .entry((func.qualified_name.as_str(), func.arity))
            .or_insert(func);
    }

    fn scope_chain(scope: Option<&str>) -> Vec<Option<String>> {
        match scope {
            None => vec![None],
            Some(scope) => {
                let parts: Vec<&str> = scope.split("::").collect();
                // Python: ["::".join(parts[:idx]) for idx in range(len, 0, -1)]
                // = [full, trừ part cuối, ..., part đầu] rồi append None.
                let mut chain: Vec<Option<String>> = (1..=parts.len())
                    .rev()
                    .map(|end| Some(parts[..end].join("::")))
                    .collect();
                chain.push(None);
                chain
            }
        }
    }

    struct Candidate {
        score: i64,
        tie: String,
    }

    for call in calls.iter_mut() {
        let mut candidates: HashMap<String, Candidate> = HashMap::new();
        let mut order: Vec<String> = Vec::new();

        let mut add = |items: Vec<&FunctionDef>, base_score: i64| {
            for item in items {
                let mut score = base_score;
                if item.file_path == call.caller_file {
                    score += 15;
                }
                let closure = uses_closure_by_file
                    .get(&call.caller_file)
                    .cloned()
                    .unwrap_or_default();
                if closure.contains(&item.file_path) {
                    score += 7;
                }
                if let Some(caller_scope) = &call.caller_scope
                    && Some(caller_scope.as_str()) == item.scope_name.as_deref()
                {
                    score += 10;
                }
                let tie = item.qualified_name.clone();
                match candidates.get_mut(&item.symbol_id) {
                    Some(existing) => {
                        if (score, tie.clone()) > (existing.score, existing.tie.clone()) {
                            existing.score = score;
                            existing.tie = tie;
                        }
                    }
                    None => {
                        order.push(item.symbol_id.clone());
                        candidates.insert(item.symbol_id.clone(), Candidate { score, tie });
                    }
                }
            }
        };

        let raw = call.callee_raw.clone();
        if raw.contains("::") {
            if let Some(direct) = by_qualified_arity.get(&(raw.as_str(), call.call_arity)) {
                add(vec![*direct], 130);
            }
            if let Some(direct) = by_qualified.get(raw.as_str()) {
                add(vec![*direct], 120);
            }
        }

        let short_name = call.callee_name.clone();
        add(
            by_file_name_arity
                .get(&(call.caller_file.as_str(), short_name.as_str(), call.call_arity))
                .cloned()
                .unwrap_or_default(),
            115,
        );
        add(
            by_file_name
                .get(&(call.caller_file.as_str(), short_name.as_str()))
                .cloned()
                .unwrap_or_default(),
            105,
        );

        for (depth, scope) in scope_chain(call.caller_scope.as_deref()).into_iter().enumerate() {
            let scope_ref = scope.as_deref();
            add(
                by_scope_name_arity
                    .get(&(scope_ref, short_name.as_str(), call.call_arity))
                    .cloned()
                    .unwrap_or_default(),
                95 - (depth.min(20) as i64),
            );
            add(
                by_scope_name
                    .get(&(scope_ref, short_name.as_str()))
                    .cloned()
                    .unwrap_or_default(),
                85 - (depth.min(20) as i64),
            );
        }

        add(
            by_name_arity
                .get(&(short_name.as_str(), call.call_arity))
                .cloned()
                .unwrap_or_default(),
            70,
        );
        add(
            by_name.get(short_name.as_str()).cloned().unwrap_or_default(),
            55,
        );

        if !candidates.is_empty() {
            // Python: max(items, key=(score, tie)) — max ĐẦU TIÊN đạt max.
            let mut best: Option<(&String, &Candidate)> = None;
            for sid in &order {
                let cand = &candidates[sid];
                let is_better = match best {
                    None => true,
                    Some((_, b)) => (cand.score, cand.tie.clone()) > (b.score, b.tie.clone()),
                };
                if is_better {
                    best = Some((sid, cand));
                }
            }
            if let Some((sid, _)) = best {
                call.callee_id = Some(sid.clone());
            }
        }
    }
}
