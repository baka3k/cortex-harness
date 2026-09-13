//! Port `tools/spring/extractors/messaging.py`.

use regex::Regex;
use serde_json::{json, Value};

use super::common::{fact, first_annotation, rel, stable_hash, FactProps};
use crate::spring::models::SpringFact;
use crate::spring::models::SpringRelationship;
use crate::spring::source_scanner::{SourceAnnotation, SourceUnit};
use crate::spring::annotation_catalog::{async_annotations, event_listener_annotations, message_listener_annotations, scheduled_annotations};
use crate::spring::value_resolver::ValueResolver;

pub fn extract_messaging_facts(
    units: &[SourceUnit],
    project_id: &str,
    project_name: &str,
    config_index: &std::collections::BTreeMap<String, Vec<String>>,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let resolver = ValueResolver::new();
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    for unit in units {
        for cls in &unit.classes {
            for method in &cls.methods {
                if let Some(listener) = first_annotation(&method.annotations, &message_listener_annotations()) {
                    let protocol = if listener.short_name().starts_with("Kafka") { "kafka" } else { "rabbit" };
                    let destinations = listener_destinations(listener, config_index, &resolver);
                    let endpoint_id = format!(
                        "spring_message_endpoint::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), listener.raw))
                    );
                    facts.push(fact(
                        "MessageEndpoint",
                        &endpoint_id,
                        &format!("{}:{}.{}", protocol, cls.name, method.name),
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
                            ("protocol".into(), json!(protocol)),
                            ("direction".into(), json!("consumer")),
                            (
                                "destination".into(),
                                Value::Array(destinations.iter().map(|(_, dest, _)| json!(dest)).collect()),
                            ),
                            (
                                "group_id".into(),
                                truthy_or(listener.args.get("groupId"), listener.args.get("group")),
                            ),
                            ("concurrency".into(), truthy_or(listener.args.get("concurrency"), None)),
                        ]),
                    ));
                    relationships.push(rel(
                        "HANDLED_BY",
                        "MessageEndpoint",
                        &endpoint_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "Message listener handler",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    // `destinations or [("", "", "unresolved")]`
                    let effective_destinations: Vec<(String, String, String)> = if destinations.is_empty() {
                        vec![(String::new(), String::new(), "unresolved".to_string())]
                    } else {
                        destinations.clone()
                    };
                    for (raw_destination, destination, destination_status) in &effective_destinations {
                        let dest_id = format!(
                            "spring_destination::{project_id}::{protocol}::{}",
                            stable_hash(if destination.is_empty() { &endpoint_id } else { destination })
                        );
                        facts.push(fact(
                            "MessageDestination",
                            &dest_id,
                            if destination.is_empty() { "<unresolved>" } else { destination },
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            if destination_status == "resolved" { 1.0 } else { 0.55 },
                            if destination.is_empty() {
                                "unresolved"
                            } else {
                                destination_status
                            },
                            raw_destination,
                            if destination_status == "resolved" { destination } else { "" },
                            "",
                            FactProps(vec![("protocol".into(), json!(protocol))]),
                        ));
                        relationships.push(rel(
                            "CONSUMES_FROM",
                            "MessageEndpoint",
                            &endpoint_id,
                            "MessageDestination",
                            &dest_id,
                            project_id,
                            &method.source,
                            "Listener destination",
                            if destination_status == "resolved" { 1.0 } else { 0.55 },
                            if destination.is_empty() {
                                "unresolved"
                            } else {
                                destination_status
                            },
                            FactProps(vec![]),
                        ));
                    }
                }

                if let Some(scheduled) = first_annotation(&method.annotations, &scheduled_annotations()) {
                    let task_id = format!(
                        "spring_scheduled::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), scheduled.raw))
                    );
                    facts.push(fact(
                        "ScheduledTask",
                        &task_id,
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
                            ("cron".into(), truthy_or(scheduled.args.get("cron"), None)),
                            (
                                "fixed_delay".into(),
                                truthy_or(scheduled.args.get("fixedDelay"), scheduled.args.get("fixedDelayString")),
                            ),
                            (
                                "fixed_rate".into(),
                                truthy_or(scheduled.args.get("fixedRate"), scheduled.args.get("fixedRateString")),
                            ),
                            ("zone".into(), truthy_or(scheduled.args.get("zone"), None)),
                            ("scheduler".into(), truthy_or(scheduled.args.get("scheduler"), None)),
                        ]),
                    ));
                    relationships.push(rel(
                        "RUNS",
                        "ScheduledTask",
                        &task_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "Scheduled method",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }

                let async_ann = first_annotation(&method.annotations, &async_annotations())
                    .or_else(|| first_annotation(&cls.annotations, &async_annotations()));
                if let Some(async_ann) = async_ann {
                    let async_id = format!(
                        "spring_async::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), async_ann.raw))
                    );
                    facts.push(fact(
                        "AsyncBoundary",
                        &async_id,
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
                            ("executor".into(), truthy_or(async_ann.args.get("value"), None)),
                            ("target_kind".into(), json!("method")),
                        ]),
                    ));
                    relationships.push(rel(
                        "EXECUTES_ASYNC",
                        "AsyncBoundary",
                        &async_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "@Async boundary",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }

                if let Some(event_listener) = first_annotation(&method.annotations, &event_listener_annotations()) {
                    let event_type = event_type_from_method(method);
                    let event_id = format!(
                        "spring_event::{project_id}::{}",
                        {
                            let anchor = if event_type.is_empty() {
                                method.symbol_id()
                            } else {
                                event_type.clone()
                            };
                            stable_hash(&anchor)
                        }
                    );
                    facts.push(fact(
                        "ApplicationEvent",
                        &event_id,
                        if event_type.is_empty() { "<unresolved>" } else { &event_type },
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        if event_type.is_empty() { 0.6 } else { 1.0 },
                        if event_type.is_empty() { "unresolved" } else { "resolved" },
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![
                            ("event_type".into(), json!(event_type)),
                            ("phase".into(), truthy_or(event_listener.args.get("phase"), None)),
                            ("condition".into(), truthy_or(event_listener.args.get("condition"), None)),
                        ]),
                    ));
                    relationships.push(rel(
                        "LISTENS_TO",
                        "Function",
                        &method.symbol_id(),
                        "ApplicationEvent",
                        &event_id,
                        project_id,
                        &method.source,
                        "Spring event listener",
                        if event_type.is_empty() { 0.6 } else { 1.0 },
                        "resolved",
                        FactProps(vec![]),
                    ));
                }

                for (protocol, destination) in template_sends(&method.code) {
                    let dest_id = format!(
                        "spring_destination::{project_id}::{protocol}::{}",
                        stable_hash(&destination)
                    );
                    facts.push(fact(
                        "MessageDestination",
                        &dest_id,
                        &destination,
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        "",
                        FactProps(vec![("protocol".into(), json!(protocol))]),
                    ));
                    relationships.push(rel(
                        "PUBLISHES_TO",
                        "Function",
                        &method.symbol_id(),
                        "MessageDestination",
                        &dest_id,
                        project_id,
                        &method.source,
                        &format!("{protocol} template send"),
                        0.78,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }
                for event_type in published_events(&method.code) {
                    let event_id = format!("spring_event::{project_id}::{}", stable_hash(&event_type));
                    facts.push(fact(
                        "ApplicationEvent",
                        &event_id,
                        &event_type,
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![("event_type".into(), json!(event_type))]),
                    ));
                    relationships.push(rel(
                        "PUBLISHES_EVENT",
                        "Function",
                        &method.symbol_id(),
                        "ApplicationEvent",
                        &event_id,
                        project_id,
                        &method.source,
                        "ApplicationEventPublisher.publishEvent",
                        0.82,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }
            }
        }
    }
    (facts, relationships)
}

fn truthy_or(first: Option<&Value>, second: Option<&Value>) -> Value {
    if let Some(value) = first.filter(|value| crate::pyjson::py_truthy(value)) {
        return value.clone();
    }
    if let Some(value) = second.filter(|value| crate::pyjson::py_truthy(value)) {
        return value.clone();
    }
    json!("")
}

fn listener_destinations(
    annotation: &SourceAnnotation,
    config_index: &std::collections::BTreeMap<String, Vec<String>>,
    resolver: &ValueResolver,
) -> Vec<(String, String, String)> {
    let raw_values = if annotation.short_name().starts_with("Kafka") {
        resolver.list_arg(&annotation.args, &["topics", "topicPattern", "value"])
    } else {
        resolver.list_arg(&annotation.args, &["queues", "bindings", "value"])
    };
    raw_values
        .into_iter()
        .map(|raw| {
            let (resolved, status) = resolver.resolve_placeholders(&raw, config_index);
            (raw, resolved, status)
        })
        .collect()
}

fn template_sends(code: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    let kafka = Regex::new(r#"(KafkaTemplate|kafkaTemplate)\s*\.\s*(send|sendDefault)\s*\(([^)]*)\)"#).unwrap();
    let rabbit = Regex::new(r#"(RabbitTemplate|rabbitTemplate)\s*\.\s*(send|convertAndSend)\s*\(([^)]*)\)"#).unwrap();
    for captures in kafka.captures_iter(code) {
        let args_text = captures.get(3).map(|m| m.as_str()).unwrap_or("");
        let destination = args_text
            .split(',')
            .next()
            .unwrap_or("")
            .trim()
            .trim_matches(|c| c == '"' || c == '\'')
            .to_string();
        let destination = if destination.is_empty() { "<default>" } else { destination.as_str() };
        out.push(("kafka".to_string(), destination.to_string()));
    }
    for captures in rabbit.captures_iter(code) {
        let args_text = captures.get(3).map(|m| m.as_str()).unwrap_or("");
        let destination = args_text
            .split(',')
            .next()
            .unwrap_or("")
            .trim()
            .trim_matches(|c| c == '"' || c == '\'')
            .to_string();
        let destination = if destination.is_empty() { "<unresolved>" } else { destination.as_str() };
        out.push(("rabbit".to_string(), destination.to_string()));
    }
    out
}

fn published_events(code: &str) -> Vec<String> {
    let re = Regex::new(r"publishEvent\s*\(\s*(?:new\s+)?([A-Za-z_][\w.]*)").unwrap();
    re.captures_iter(code)
        .filter_map(|captures| captures.get(1))
        .map(|m| m.as_str().to_string())
        .collect()
}

fn event_type_from_method(method: &crate::spring::source_scanner::SourceMethod) -> String {
    let params = method.params.trim();
    if params.is_empty() {
        return String::new();
    }
    let token = params.split(',').next().unwrap_or("").trim();
    token.split_whitespace().next().unwrap_or(token).to_string()
}
