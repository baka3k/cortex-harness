from __future__ import annotations

from typing import Dict, List

from tools.struts.models import WebFilterConfig, WebXmlData
from tools.struts.xml_utils import child_text, children, parse_xml, text


def parse_web_xml_file(root: str, file_path: str) -> WebXmlData:
    document, source, diagnostics = parse_xml(root, file_path, "struts.web_xml")
    if document is None:
        return WebXmlData(diagnostics=diagnostics)

    declarations: Dict[str, tuple[str, Dict[str, str]]] = {}
    for element in children(document, "filter"):
        name = child_text(element, "filter-name")
        class_name = child_text(element, "filter-class")
        init_params: Dict[str, str] = {}
        for item in children(element, "init-param"):
            param_name = child_text(item, "param-name")
            if param_name:
                init_params[param_name] = child_text(item, "param-value")
        if name:
            declarations[name] = (class_name, init_params)

    mappings: Dict[str, List[str]] = {}
    for element in children(document, "filter-mapping"):
        name = child_text(element, "filter-name")
        if not name:
            continue
        mappings.setdefault(name, []).extend(
            value for value in (text(item) for item in children(element, "url-pattern")) if value
        )

    filters = tuple(
        WebFilterConfig(
            name=name,
            class_name=class_name,
            url_patterns=tuple(dict.fromkeys(mappings.get(name, ()))),
            init_params=init_params,
            source=source,
        )
        for name, (class_name, init_params) in sorted(declarations.items())
    )
    return WebXmlData(filters=filters, diagnostics=diagnostics)


__all__ = ["parse_web_xml_file"]
