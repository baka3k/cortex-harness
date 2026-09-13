//! Port `tools/spring/extractors/security.py`.

use regex::Regex;
use serde_json::{json, Value};

use super::common::{fact, first_annotation, rel, stable_hash, FactProps};
use crate::spring::models::SpringFact;
use crate::spring::models::SpringRelationship;
use crate::spring::source_scanner::SourceUnit;
use crate::spring::annotation_catalog::security_method_annotations;
use crate::spring::value_resolver::ValueResolver;

pub fn extract_security_facts(
    units: &[SourceUnit],
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let resolver = ValueResolver::new();
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    for unit in units {
        for cls in &unit.classes {
            for method in &cls.methods {
                if method.return_type.contains("SecurityFilterChain")
                    || method.params.contains("HttpSecurity")
                    || method.code.contains("authorizeHttpRequests")
                {
                    let chain_id = format!("spring_security_chain::{project_id}::{}", stable_hash(&method.symbol_id()));
                    facts.push(fact(
                        "SecurityFilterChain",
                        &chain_id,
                        &format!("{}.{}", cls.name, method.name),
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![
                            ("matcher".into(), json!(first_matcher(&method.code))),
                            ("chain_bean".into(), json!(method.name)),
                        ]),
                    ));
                    relationships.push(rel(
                        "SEMANTIC_OF",
                        "SecurityFilterChain",
                        &chain_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "SecurityFilterChain bean anchor",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    for (idx, rule) in security_rules(&method.code).iter().enumerate() {
                        let rule_id = format!("spring_security_rule::{chain_id}::{idx}");
                        facts.push(fact(
                            "SecurityRule",
                            &rule_id,
                            if rule.matcher.is_empty() { "anyRequest" } else { &rule.matcher },
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            1.0,
                            "resolved",
                            &rule.raw,
                            "",
                            "",
                            FactProps(vec![
                                ("matcher".into(), json!(rule.matcher)),
                                ("decision".into(), json!(rule.decision)),
                                ("order".into(), json!(idx as i64)),
                            ]),
                        ));
                        relationships.push(rel(
                            "HAS_RULE",
                            "SecurityFilterChain",
                            &chain_id,
                            "SecurityRule",
                            &rule_id,
                            project_id,
                            &method.source,
                            "Ordered Spring Security rule",
                            1.0,
                            "resolved",
                            FactProps(vec![("order".into(), json!(idx as i64))]),
                        ));
                        for (authority_kind, authority_value) in authorities_from_expression(&rule.raw) {
                            let auth_id = authority_id(project_id, &authority_kind, &authority_value);
                            facts.push(fact(
                                "Authority",
                                &auth_id,
                                &authority_value,
                                &method.source,
                                project_id,
                                project_name,
                                &method.language,
                                1.0,
                                "resolved",
                                "",
                                "",
                                "",
                                FactProps(vec![
                                    ("authority_kind".into(), json!(authority_kind)),
                                    ("canonical_value".into(), json!(authority_value)),
                                    ("raw_value".into(), json!(authority_value)),
                                ]),
                            ));
                            relationships.push(rel(
                                "REQUIRES_AUTHORITY",
                                "SecurityRule",
                                &rule_id,
                                "Authority",
                                &auth_id,
                                project_id,
                                &method.source,
                                "Security rule authority",
                                1.0,
                                "resolved",
                                FactProps(vec![]),
                            ));
                        }
                    }
                }

                if let Some(method_sec) = first_annotation(&method.annotations, &security_method_annotations()) {
                    let rule_id = format!(
                        "spring_security_rule::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), method_sec.raw))
                    );
                    facts.push(fact(
                        "SecurityRule",
                        &rule_id,
                        &format!("{}.{}", cls.name, method.name),
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        &method_sec.raw_args,
                        "",
                        "",
                        FactProps(vec![
                            ("decision".into(), Value::String(method_sec.short_name())),
                            (
                                "expression".into(),
                                match method_sec.args.get("value") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            ("method_rule".into(), json!(true)),
                        ]),
                    ));
                    relationships.push(rel(
                        "PROTECTS",
                        "SecurityRule",
                        &rule_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "Method security annotation",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    for (authority_kind, authority_value) in authorities_from_annotation(method_sec, &resolver) {
                        let auth_id = authority_id(project_id, &authority_kind, &authority_value);
                        facts.push(fact(
                            "Authority",
                            &auth_id,
                            &authority_value,
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            1.0,
                            "resolved",
                            "",
                            "",
                            "",
                            FactProps(vec![
                                ("authority_kind".into(), json!(authority_kind)),
                                ("canonical_value".into(), json!(authority_value)),
                                ("raw_value".into(), json!(authority_value)),
                            ]),
                        ));
                        relationships.push(rel(
                            "REQUIRES_AUTHORITY",
                            "SecurityRule",
                            &rule_id,
                            "Authority",
                            &auth_id,
                            project_id,
                            &method.source,
                            "Method security authority",
                            1.0,
                            "resolved",
                            FactProps(vec![]),
                        ));
                    }
                }
            }
        }
    }
    (facts, relationships)
}

pub struct SecurityRuleRow {
    pub matcher: String,
    pub decision: String,
    pub raw: String,
}

fn security_rules(code: &str) -> Vec<SecurityRuleRow> {
    let mut rules: Vec<SecurityRuleRow> = Vec::new();
    let re = Regex::new(
        r#"(requestMatchers|securityMatcher)\s*\(([^)]*)\)([\s\S]{0,220}?)(permitAll|denyAll|authenticated|anonymous|hasRole|hasAnyRole|hasAuthority|hasAnyAuthority)\s*(?:\(([^)]*)\))?"#,
    )
    .unwrap();
    let resolver = ValueResolver::new();
    for captures in re.captures_iter(code) {
        let matcher_text = captures.get(2).map(|m| m.as_str()).unwrap_or("");
        let matcher_values = resolver.extract_string_literals(matcher_text);
        let decision = captures.get(4).map(|m| m.as_str()).unwrap_or("");
        rules.push(SecurityRuleRow {
            matcher: if let Some(first) = matcher_values.first() {
                first.clone()
            } else {
                matcher_text.trim().to_string()
            },
            decision: decision.to_string(),
            raw: captures.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
        });
    }
    if code.contains("anyRequest()") {
        let terminal_re = Regex::new(r"anyRequest\(\)([\s\S]{0,120}?)(permitAll|denyAll|authenticated|anonymous)").unwrap();
        match terminal_re.captures(code) {
            Some(terminal) => rules.push(SecurityRuleRow {
                matcher: "anyRequest".to_string(),
                decision: terminal.get(2).map(|m| m.as_str()).unwrap_or("unknown").to_string(),
                raw: terminal.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
            }),
            None => rules.push(SecurityRuleRow {
                matcher: "anyRequest".to_string(),
                decision: "unknown".to_string(),
                raw: "anyRequest()".to_string(),
            }),
        }
    }
    rules
}

fn first_matcher(code: &str) -> String {
    let re = Regex::new(r"securityMatcher\s*\(([^)]*)\)").unwrap();
    re.captures(code)
        .and_then(|captures| captures.get(1))
        .map(|m| m.as_str().trim().to_string())
        .unwrap_or_default()
}

fn authorities_from_expression(raw: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    let re = Regex::new(r"(hasRole|hasAnyRole|hasAuthority|hasAnyAuthority)\s*\(([^)]*)\)").unwrap();
    let resolver = ValueResolver::new();
    for captures in re.captures_iter(raw) {
        let func = captures.get(1).map(|m| m.as_str()).unwrap_or("");
        let args = captures.get(2).map(|m| m.as_str()).unwrap_or("");
        let kind = if func.contains("Role") { "role" } else { "authority" }.to_string();
        for value in resolver.extract_string_literals(args) {
            out.push((kind.clone(), canonical_authority(&kind, &value)));
        }
    }
    out
}

fn authorities_from_annotation(
    annotation: &crate::spring::source_scanner::SourceAnnotation,
    resolver: &ValueResolver,
) -> Vec<(String, String)> {
    let short = annotation.short_name();
    if short == "Secured" || short == "RolesAllowed" {
        return resolver
            .list_arg(&annotation.args, &["value"])
            .into_iter()
            .map(|value| ("role".to_string(), canonical_authority("role", &value)))
            .collect();
    }
    // `str(annotation.args.get("value") or annotation.raw_args)`
    let base = match annotation.args.get("value") {
        Some(value) if crate::pyjson::py_truthy(value) => crate::pyjson::py_str(value),
        _ => annotation.raw_args.clone(),
    };
    authorities_from_expression(&base)
}

fn canonical_authority(kind: &str, value: &str) -> String {
    let text = value.trim();
    if kind == "role" && !text.starts_with("ROLE_") {
        return format!("ROLE_{text}");
    }
    text.to_string()
}

fn authority_id(project_id: &str, kind: &str, value: &str) -> String {
    format!("spring_authority::{project_id}::{kind}::{}", stable_hash(value))
}
