"""Static protobuf package/service/RPC extraction."""

from __future__ import annotations

import re
from typing import List

from ..models import (
    ConfidenceLevel,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    EndpointFact,
    EndpointProtocol,
    ParseDepth,
    SourceEvidence,
    stable_fact_id,
    stable_module_id,
    safe_summary,
)
from .common import line_number, module_path_for_file


_PACKAGE_RE = re.compile(r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;")
_SERVICE_START_RE = re.compile(
    r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{"
)
_RPC_HEADER_RE = re.compile(
    r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(stream\s+)?([.\w]+)\s*\)"
    r"\s*returns\s*\(\s*(stream\s+)?([.\w]+)\s*\)",
)
_HTTP_RE = re.compile(
    r"\b(get|put|post|delete|patch)\s*:\s*\"([^\"]+)\"",
    re.IGNORECASE,
)


def _service_blocks(text: str):
    for match in _SERVICE_START_RE.finditer(text):
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        yield match.group(1), text[match.end() : index - 1], match.end()


def _rpc_options(body: str, start: int) -> str:
    index = start
    while index < len(body) and body[index].isspace():
        index += 1
    if index >= len(body) or body[index] != "{":
        return ""
    depth = 1
    end = index + 1
    while end < len(body) and depth:
        if body[end] == "{":
            depth += 1
        elif body[end] == "}":
            depth -= 1
        end += 1
    return body[index + 1 : end - 1]


def parse_protobuf(*, project_id: str, path: str, text: str) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    package_match = _PACKAGE_RE.search(text)
    package = package_match.group(1) if package_match else ""
    services = []
    endpoints: List[EndpointFact] = []
    for service, body, body_offset in _service_blocks(text):
        services.append(service)
        for rpc_match in _RPC_HEADER_RE.finditer(body):
            name, client_stream, request_type, server_stream, response_type = rpc_match.groups()
            http_match = _HTTP_RE.search(_rpc_options(body, rpc_match.end()))
            method = http_match.group(1).upper() if http_match else ""
            route = http_match.group(2) if http_match else ""
            qualified = ".".join(value for value in (package, service, name) if value)
            endpoints.append(
                EndpointFact(
                    id=stable_fact_id(project_id, "grpc-endpoint", path, qualified),
                    project_id=project_id,
                    module_id=stable_module_id(project_id, module_path),
                    protocol=EndpointProtocol.GRPC,
                    name=name,
                    path=route,
                    method=method,
                    framework="protobuf",
                    service=".".join(value for value in (package, service) if value),
                    request_type=request_type,
                    response_type=response_type,
                    client_streaming=bool(client_stream),
                    server_streaming=bool(server_stream),
                    file_path=path,
                    start_line=line_number(text, body_offset + rpc_match.start()),
                    evidence=(
                        SourceEvidence(
                            path, line_number(text, body_offset + rpc_match.start())
                        ),
                    ),
                    original_kind="GrpcEndpoint",
                )
            )
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.PROTOBUF,
        role=DescriptorRole.INTERFACE,
        parser="protobuf",
        parse_depth=ParseDepth.SEMANTIC,
        summary=safe_summary(f"Protobuf package {package or '(default)'} with {len(endpoints)} RPCs"),
        properties={
            "package": package,
            "services": sorted(set(services)),
            "endpoint_ids": [item.id for item in endpoints],
        },
        confidence=ConfidenceLevel.HIGH,
        evidence=(SourceEvidence(path),),
    )
    return DescriptorParseOutput(
        descriptor=descriptor,
        endpoints=tuple(sorted(endpoints, key=lambda item: item.id)),
    )
