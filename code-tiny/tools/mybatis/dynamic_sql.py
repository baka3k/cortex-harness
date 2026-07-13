from __future__ import annotations

import re
from typing import Dict, List, Tuple

from tools.mybatis.models import MyBatisDynamicSqlNodeFact, SourceSpan


DYNAMIC_TAGS = {"if", "choose", "when", "otherwise", "foreach", "trim", "where", "set", "bind", "script"}


def ognl_identifiers(text: str) -> Tuple[str, ...]:
    ignored = {"and", "or", "not", "null", "true", "false", "eq", "ne", "lt", "le", "gt", "ge"}
    rows = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.$]*", text or ""):
        head = token.split(".", 1)[0]
        if head not in ignored and head not in rows:
            rows.append(head)
    return tuple(rows)


def dynamic_node(
    *,
    owner_id: str,
    tag: str,
    node_kind: str,
    source: SourceSpan,
    order: int,
    text: str = "",
    attributes: Dict[str, str] | None = None,
    branch_role: str = "",
) -> MyBatisDynamicSqlNodeFact:
    attrs = dict(attributes or {})
    test = attrs.get("test", "")
    stable_id = f"mybatis_dynamic::{owner_id}::{source.start_line}:{source.start_column}:{order}"
    refs = ognl_identifiers(test)
    if tag == "foreach":
        refs = tuple(dict.fromkeys(refs + tuple(filter(None, (attrs.get("collection", ""),)))))
    if tag == "bind":
        refs = tuple(dict.fromkeys(refs + ognl_identifiers(attrs.get("value", ""))))
    return MyBatisDynamicSqlNodeFact(
        stable_id=stable_id,
        owner_id=owner_id,
        tag=tag,
        node_kind=node_kind,
        source=source,
        order=order,
        text=text,
        attributes=attrs,
        test=test,
        branch_role=branch_role,
        referenced_variables=refs,
    )
