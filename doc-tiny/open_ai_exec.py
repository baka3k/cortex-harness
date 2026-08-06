"""Legacy OpenAI relationship-extraction helper.

This module does not own graph persistence. The former unused direct Neo4j and
``neo4j-graphrag`` imports were removed so importing the helper cannot bypass
the provider-neutral :mod:`graph_store` runtime.
"""

from __future__ import annotations

from typing import List

from openai import OpenAI
from pydantic import BaseModel


class GraphRelationship(BaseModel):
    node: str
    target_node: str
    relationship: str


class GraphComponents(BaseModel):
    graph: List[GraphRelationship]


def openai_llm_parser(prompt: str) -> GraphComponents:
    client = OpenAI()
    completion = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """You are a precise graph relationship extractor. Extract all
relationships from the text and return a JSON object with a graph array. Each
item must contain node, target_node, and relationship string fields.""",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return GraphComponents.model_validate_json(completion.choices[0].message.content)


__all__ = ["GraphComponents", "GraphRelationship", "openai_llm_parser"]
