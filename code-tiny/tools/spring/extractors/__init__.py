from __future__ import annotations

from tools.spring.extractors.core import extract_core_facts
from tools.spring.extractors.crosscutting import extract_crosscutting_facts
from tools.spring.extractors.messaging import extract_messaging_facts
from tools.spring.extractors.persistence import extract_persistence_facts
from tools.spring.extractors.security import extract_security_facts

__all__ = [
    "extract_core_facts",
    "extract_crosscutting_facts",
    "extract_messaging_facts",
    "extract_persistence_facts",
    "extract_security_facts",
]
