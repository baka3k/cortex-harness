from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from tools.spring.annotation_catalog import ASYNC_ANNOTATIONS, EVENT_LISTENER_ANNOTATIONS, MESSAGE_LISTENER_ANNOTATIONS, SCHEDULED_ANNOTATIONS
from tools.spring.extractors.common import fact, first_annotation, rel, stable_hash
from tools.spring.models import SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceUnit
from tools.spring.value_resolver import list_arg, resolve_placeholders


def extract_messaging_facts(
    *,
    units: Sequence[SourceUnit],
    project_id: str,
    project_name: str,
    config_index: Dict[str, List[object]],
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    for unit in units:
        for cls in unit.classes:
            for method in cls.methods:
                listener = first_annotation(method.annotations, MESSAGE_LISTENER_ANNOTATIONS)
                if listener:
                    protocol = "kafka" if listener.short_name.startswith("Kafka") else "rabbit"
                    destinations = _listener_destinations(listener, config_index)
                    endpoint_id = f"spring_message_endpoint::{project_id}::{stable_hash(method.symbol_id + listener.raw)}"
                    facts.append(
                        fact(
                            kind="MessageEndpoint",
                            stable_id=endpoint_id,
                            name=f"{protocol}:{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            protocol=protocol,
                            direction="consumer",
                            destination=[destination for _, destination, _ in destinations],
                            group_id=listener.args.get("groupId") or listener.args.get("group") or "",
                            concurrency=listener.args.get("concurrency") or "",
                        )
                    )
                    relationships.append(rel("HANDLED_BY", "MessageEndpoint", endpoint_id, "Function", method.symbol_id, project_id, method.source, "Message listener handler"))
                    for raw_destination, destination, destination_status in destinations or [("", "", "unresolved")]:
                        dest_id = f"spring_destination::{project_id}::{protocol}::{stable_hash(destination or endpoint_id)}"
                        facts.append(
                            fact(
                                kind="MessageDestination",
                                stable_id=dest_id,
                                name=destination or "<unresolved>",
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                confidence=1.0 if destination_status == "resolved" else 0.55,
                                resolution_status=destination_status if destination else "unresolved",
                                protocol=protocol,
                                raw_value=raw_destination,
                                resolved_value=destination if destination_status == "resolved" else "",
                            )
                        )
                        relationships.append(
                            rel(
                                "CONSUMES_FROM",
                                "MessageEndpoint",
                                endpoint_id,
                                "MessageDestination",
                                dest_id,
                                project_id,
                                method.source,
                                "Listener destination",
                                1.0 if destination_status == "resolved" else 0.55,
                                destination_status if destination else "unresolved",
                            )
                        )

                scheduled = first_annotation(method.annotations, SCHEDULED_ANNOTATIONS)
                if scheduled:
                    task_id = f"spring_scheduled::{project_id}::{stable_hash(method.symbol_id + scheduled.raw)}"
                    facts.append(
                        fact(
                            kind="ScheduledTask",
                            stable_id=task_id,
                            name=f"{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            cron=scheduled.args.get("cron") or "",
                            fixed_delay=scheduled.args.get("fixedDelay") or scheduled.args.get("fixedDelayString") or "",
                            fixed_rate=scheduled.args.get("fixedRate") or scheduled.args.get("fixedRateString") or "",
                            zone=scheduled.args.get("zone") or "",
                            scheduler=scheduled.args.get("scheduler") or "",
                        )
                    )
                    relationships.append(rel("RUNS", "ScheduledTask", task_id, "Function", method.symbol_id, project_id, method.source, "Scheduled method"))

                async_ann = first_annotation(method.annotations, ASYNC_ANNOTATIONS) or first_annotation(cls.annotations, ASYNC_ANNOTATIONS)
                if async_ann:
                    async_id = f"spring_async::{project_id}::{stable_hash(method.symbol_id + async_ann.raw)}"
                    facts.append(
                        fact(
                            kind="AsyncBoundary",
                            stable_id=async_id,
                            name=f"{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            executor=async_ann.args.get("value") or "",
                            target_kind="method",
                        )
                    )
                    relationships.append(rel("EXECUTES_ASYNC", "AsyncBoundary", async_id, "Function", method.symbol_id, project_id, method.source, "@Async boundary"))

                event_listener = first_annotation(method.annotations, EVENT_LISTENER_ANNOTATIONS)
                if event_listener:
                    event_type = _event_type_from_method(method)
                    event_id = f"spring_event::{project_id}::{stable_hash(event_type or method.symbol_id)}"
                    facts.append(
                        fact(
                            kind="ApplicationEvent",
                            stable_id=event_id,
                            name=event_type or "<unresolved>",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            confidence=1.0 if event_type else 0.6,
                            resolution_status="resolved" if event_type else "unresolved",
                            source_symbol_id=method.symbol_id,
                            event_type=event_type,
                            phase=event_listener.args.get("phase") or "",
                            condition=event_listener.args.get("condition") or "",
                        )
                    )
                    relationships.append(rel("LISTENS_TO", "Function", method.symbol_id, "ApplicationEvent", event_id, project_id, method.source, "Spring event listener", 1.0 if event_type else 0.6))

                for send_call in _template_sends(method.code):
                    protocol, destination = send_call
                    dest_id = f"spring_destination::{project_id}::{protocol}::{stable_hash(destination)}"
                    facts.append(
                        fact(
                            kind="MessageDestination",
                            stable_id=dest_id,
                            name=destination,
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            protocol=protocol,
                            raw_value=destination,
                        )
                    )
                    relationships.append(rel("PUBLISHES_TO", "Function", method.symbol_id, "MessageDestination", dest_id, project_id, method.source, f"{protocol} template send", 0.78))
                for event_type in _published_events(method.code):
                    event_id = f"spring_event::{project_id}::{stable_hash(event_type)}"
                    facts.append(
                        fact(
                            kind="ApplicationEvent",
                            stable_id=event_id,
                            name=event_type,
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            event_type=event_type,
                        )
                    )
                    relationships.append(rel("PUBLISHES_EVENT", "Function", method.symbol_id, "ApplicationEvent", event_id, project_id, method.source, "ApplicationEventPublisher.publishEvent", 0.82))
    return facts, relationships


def _listener_destinations(annotation, config_index: Dict[str, List[object]]) -> List[Tuple[str, str, str]]:
    if annotation.short_name.startswith("Kafka"):
        raw_values = list_arg(annotation.args, "topics", "topicPattern", "value")
    else:
        raw_values = list_arg(annotation.args, "queues", "bindings", "value")
    out: List[Tuple[str, str, str]] = []
    for raw in raw_values:
        resolved, status = resolve_placeholders(raw, config_index)
        out.append((raw, resolved, status))
    return out


def _template_sends(code: str):
    out = []
    for match in re.finditer(r"(KafkaTemplate|kafkaTemplate)\s*\.\s*(send|sendDefault)\s*\(([^)]*)\)", code or ""):
        args = match.group(3).split(",")
        destination = args[0].strip().strip('"\'') if args else ""
        out.append(("kafka", destination or "<default>"))
    for match in re.finditer(r"(RabbitTemplate|rabbitTemplate)\s*\.\s*(send|convertAndSend)\s*\(([^)]*)\)", code or ""):
        args = match.group(3).split(",")
        destination = args[0].strip().strip('"\'') if args else ""
        out.append(("rabbit", destination or "<unresolved>"))
    return out


def _published_events(code: str) -> List[str]:
    out: List[str] = []
    for match in re.finditer(r"publishEvent\s*\(\s*(?:new\s+)?([A-Za-z_][\w.]*)", code or ""):
        out.append(match.group(1))
    return out


def _event_type_from_method(method) -> str:
    params = method.params.strip()
    if not params:
        return ""
    token = params.split(",", 1)[0].strip()
    parts = token.split()
    return parts[0] if parts else token
