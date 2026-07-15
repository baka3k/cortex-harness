from __future__ import annotations

from pathlib import PurePath
from typing import List

from tools.struts.models import ValidationData, ValidationRule
from tools.struts.xml_utils import child_text, children, params, parse_xml


def _target_from_name(file_path: str) -> tuple[str, str]:
    stem = PurePath(file_path).name
    prefix = stem[: -len("-validation.xml")] if stem.endswith("-validation.xml") else PurePath(file_path).stem
    if "-" not in prefix:
        return prefix, ""
    target, method = prefix.rsplit("-", 1)
    return target, method


def parse_validation_xml_file(root: str, file_path: str) -> ValidationData:
    document, source, diagnostics = parse_xml(root, file_path, "struts.validation")
    if document is None:
        return ValidationData(diagnostics=diagnostics)

    target, method = _target_from_name(source.file_path)
    rules: List[ValidationRule] = []
    for element in document:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "field":
            field_name = (element.get("name") or "").strip()
            validators = children(element, "field-validator")
        elif tag == "validator":
            field_name = ""
            validators = (element,)
        else:
            continue
        for validator in validators:
            message_element = next(children(validator, "message"), None)
            rules.append(
                ValidationRule(
                    target=target,
                    method=method,
                    validator_type=(validator.get("type") or "").strip(),
                    field_name=field_name,
                    message=child_text(validator, "message"),
                    message_key=(message_element.get("key") or "").strip() if message_element is not None else "",
                    params=params(validator),
                    source=source,
                )
            )
    return ValidationData(rules=tuple(rules), diagnostics=diagnostics)


__all__ = ["parse_validation_xml_file"]
