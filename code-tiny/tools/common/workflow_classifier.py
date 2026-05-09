"""
workflow_classifier.py
──────────────────────
Classify raw FlowDef objects into business-named WorkflowMatch objects.

Two-stage pipeline:
  1. Heuristic keyword rule lookup (fast, zero-cost, always runs).
  2. Optional LLM enrichment via OpenAI-compatible API for flows that
     the heuristic couldn't confidently name (confidence < HEURISTIC_THRESHOLD).

Environment variables (all optional)
─────────────────────────────────────
  OPENAI_API_KEY            Enable LLM enrichment
  OPENAI_BASE_URL           Override base URL (LiteLLM proxy, Azure, etc.)
  LLM_WORKFLOW_MODEL        Model for workflow naming (default: gpt-4o-mini)
  ENABLE_WORKFLOW_LLM       Set to "1" / "true" to force-enable (needed when
                            OPENAI_API_KEY is not in env, e.g. proxy auth)

Public API
──────────
  WorkflowMatch – dataclass representing a named/classified workflow
  WorkflowNameClassifier – async classifier
    classify_batch(flows, function_lookup) -> List[WorkflowMatch]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Heuristic configuration ────────────────────────────────────────────────

# Threshold below which LLM enrichment is attempted (if available)
HEURISTIC_THRESHOLD = 0.6

_DEFAULT_LLM_MODEL = "gpt-4o-mini"

# (regex_pattern, domain, name_template)
# The first match wins.  {name} is replaced with the titlecased entrypoint name.
_HEURISTIC_RULES: List[Tuple[re.Pattern, str, str]] = [
    # Auth / identity
    (re.compile(r"\b(login|logout|signin|sign_in|sign_out|authenticate|auth)\b", re.I), "auth", "Login Flow"),
    (re.compile(r"\b(register|signup|sign_up|create_account|onboard)\b", re.I), "auth", "Registration Flow"),
    (re.compile(r"\b(password|reset_pass|forgot_pass|change_pass)\b", re.I), "auth", "Password Reset Flow"),
    (re.compile(r"\b(token|refresh_token|revoke_token|access_token)\b", re.I), "auth", "Token Flow"),
    # Payment / billing
    (re.compile(r"\b(payment|pay|checkout|purchase|buy|charge|billing|invoice)\b", re.I), "payment", "Payment Flow"),
    (re.compile(r"\b(refund|reimburse|chargeback|reverse_payment)\b", re.I), "payment", "Refund Flow"),
    (re.compile(r"\b(subscription|subscribe|unsubscribe|recurring)\b", re.I), "payment", "Subscription Flow"),
    # Loyalty / rewards
    (re.compile(r"\b(reward|loyalty|point|redeem|earn_point|spend_point)\b", re.I), "loyalty", "Reward Flow"),
    (re.compile(r"\b(tier|rank|badge|level_up|vip)\b", re.I), "loyalty", "Loyalty Tier Flow"),
    # Order / fulfillment
    (re.compile(r"\b(order|place_order|checkout_order|fulfill|fulfillment)\b", re.I), "order", "Order Flow"),
    (re.compile(r"\b(ship|deliver|dispatch|track_order|delivery)\b", re.I), "order", "Delivery Flow"),
    (re.compile(r"\b(cart|basket|add_to_cart|update_cart|remove_from_cart)\b", re.I), "order", "Cart Flow"),
    # Notification
    (re.compile(r"\b(notify|notification|send_email|send_sms|push_notification|alert)\b", re.I), "notification", "Notification Flow"),
    # User profile / account
    (re.compile(r"\b(profile|update_profile|edit_profile|user_setting)\b", re.I), "profile", "Profile Update Flow"),
    (re.compile(r"\b(delete_account|deactivate|suspend_account)\b", re.I), "profile", "Account Deletion Flow"),
    # Search / discovery
    (re.compile(r"\b(search|find|lookup|discover|browse|list_item)\b", re.I), "search", "Search Flow"),
    # Upload / file
    (re.compile(r"\b(upload|import_file|ingest|import_data|parse_file)\b", re.I), "data", "Data Import Flow"),
    (re.compile(r"\b(export|download|generate_report|report)\b", re.I), "data", "Export Flow"),
    # Onboarding / wizard
    (re.compile(r"\b(onboard|wizard|setup|configure|setup_account)\b", re.I), "onboarding", "Onboarding Flow"),
    # Admin
    (re.compile(r"\b(admin|moderate|review|approve|reject|verify)\b", re.I), "admin", "Admin Review Flow"),
    # Data sync / job
    (re.compile(r"\b(sync|reconcile|batch_process|cron|scheduled|job)\b", re.I), "background", "Background Job Flow"),
    # API / webhook
    (re.compile(r"\b(webhook|callback|event_handler|handle_event)\b", re.I), "integration", "Event Handler Flow"),
]


def _stable_id(step_ids: List[str]) -> str:
    """Deterministic SHA-256 based workflow ID from sorted step list."""
    raw = "|".join(sorted(step_ids))
    return "wf::" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _signal_text(flow_name: str, step_ids: List[str], function_lookup: Dict[str, Any]) -> str:
    """Build a combined signal string for heuristic + LLM matching."""
    parts = [flow_name]
    for sid in step_ids[:12]:  # cap to avoid huge prompts
        func = function_lookup.get(sid)
        if func:
            parts.append(func.get("name", ""))
    return " ".join(p for p in parts if p)


def _heuristic_classify(signal: str) -> Tuple[str, str, float]:
    """Return (workflow_name, domain, confidence) via keyword rules."""
    for pattern, domain, name in _HEURISTIC_RULES:
        if pattern.search(signal):
            return name, domain, 0.75
    # Generic fallback based on the entrypoint name itself
    base = signal.split()[0]  # first token = entrypoint function name
    human = re.sub(r"[_\-]+", " ", base).title()
    return f"{human} Flow", "unknown", 0.3


# ── Dataclass ─────────────────────────────────────────────────────────────


@dataclass
class WorkflowMatch:
    workflow_id: str
    workflow_name: str
    domain: str
    description: str
    confidence: float
    entrypoint_id: str
    step_ids: List[str]
    language: str
    project: str
    kind: str = ""  # http_handler | celery_task | event_handler | cli_command | ...

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "domain": self.domain,
            "description": self.description,
            "confidence": self.confidence,
            "entrypoint_id": self.entrypoint_id,
            "step_ids": self.step_ids,
            "language": self.language,
            "project": self.project,
            "kind": self.kind,
        }


# ── LLM helper ────────────────────────────────────────────────────────────


def _llm_available() -> bool:
    if os.environ.get("ENABLE_WORKFLOW_LLM", "").lower() in ("1", "true", "yes"):
        return True
    return bool(os.environ.get("OPENAI_API_KEY"))


def _call_llm_classify(
    signals: List[str],
    model: str,
    api_key: str,
    base_url: str,
) -> List[Optional[Dict[str, Any]]]:
    """
    Ask the LLM to name each flow.
    Returns a list of dicts {name, domain, description, confidence} or None.
    """
    try:
        import openai  # type: ignore
    except ImportError:
        return [None] * len(signals)

    client = openai.OpenAI(api_key=api_key or "none", base_url=base_url or None)

    system = (
        "You are a senior software architect. "
        "Given a list of function names that form an execution flow in a codebase, "
        "return a JSON object with these keys:\n"
        "  name: business-readable flow name (e.g. 'Login Flow', 'Payment Flow')\n"
        "  domain: one of: auth, payment, loyalty, order, notification, profile, "
        "search, data, onboarding, admin, background, integration, unknown\n"
        "  description: one sentence (max 25 words) describing the flow\n"
        "  confidence: float 0.0–1.0\n"
        "Respond with only valid JSON. No markdown, no explanation."
    )

    results: List[Optional[Dict[str, Any]]] = []
    for signal in signals:
        prompt = f"Flow functions: {signal[:400]}"
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=120,
            )
            raw = response.choices[0].message.content or ""
            parsed = json.loads(raw.strip())
            if isinstance(parsed, dict) and "name" in parsed:
                results.append(parsed)
            else:
                results.append(None)
        except Exception:
            results.append(None)
    return results


# ── Main classifier ────────────────────────────────────────────────────────


class WorkflowNameClassifier:
    """
    Classify FlowDef objects into WorkflowMatch objects.

    Usage::

        classifier = WorkflowNameClassifier(project_id="my-project", language="python")
        matches = await classifier.classify_batch(flows, function_lookup)
    """

    def __init__(
        self,
        project_id: str,
        language: str,
        enable_llm: bool = True,
        llm_model: Optional[str] = None,
    ) -> None:
        self.project_id = project_id
        self.language = language
        self.enable_llm = enable_llm
        self.llm_model = llm_model or os.environ.get("LLM_WORKFLOW_MODEL", _DEFAULT_LLM_MODEL)

    async def classify_batch(
        self,
        flows: list,  # List[FlowDef]
        function_lookup: Dict[str, Any],
    ) -> List[WorkflowMatch]:
        if not flows:
            return []

        # Build signal strings and heuristic results
        results: List[WorkflowMatch] = []
        llm_candidates: List[Tuple[int, str]] = []  # (index, signal)

        for idx, flow in enumerate(flows):
            signal = _signal_text(flow.name, flow.step_ids, function_lookup)
            wf_name, domain, confidence = _heuristic_classify(signal)
            step_ids = list(flow.step_ids)
            wf_id = _stable_id(step_ids)
            description = f"Execution flow starting from {flow.name} ({flow.kind})"
            match = WorkflowMatch(
                workflow_id=wf_id,
                workflow_name=wf_name,
                domain=domain,
                description=description,
                confidence=confidence,
                entrypoint_id=flow.entrypoint_id,
                step_ids=step_ids,
                language=self.language,
                project=self.project_id,
                kind=flow.kind,
            )
            results.append(match)

            # Queue low-confidence flows for LLM enrichment
            if confidence < HEURISTIC_THRESHOLD:
                llm_candidates.append((idx, signal))

        # Optional LLM enrichment pass
        if self.enable_llm and llm_candidates and _llm_available():
            api_key = os.environ.get("OPENAI_API_KEY", "")
            base_url = os.environ.get("OPENAI_BASE_URL", "")
            signals = [sig for _, sig in llm_candidates]
            llm_outputs = _call_llm_classify(signals, self.llm_model, api_key, base_url)
            for (idx, _), llm_out in zip(llm_candidates, llm_outputs):
                if llm_out is None:
                    continue
                match = results[idx]
                match.workflow_name = llm_out.get("name", match.workflow_name)
                match.domain = llm_out.get("domain", match.domain)
                match.description = llm_out.get("description", match.description)
                match.confidence = float(llm_out.get("confidence", match.confidence))

        return results

    async def classify_navigators(
        self,
        navigators: List[Any],  # List[NavigatorDef] or List[Dict]
        screen_lookup: Dict[str, Any],
    ) -> List[WorkflowMatch]:
        """Classify React Navigation navigator declarations as workflow entrypoints.

        Each navigator (Tab / Stack / Drawer) is treated as a natural UI flow
        entrypoint — e.g. a bottom-tab navigator typically represents a distinct
        user journey per tab; a stack represents a feature flow.

        Args:
            navigators: List of NavigatorDef dataclasses or equivalent dicts.
            screen_lookup: Mapping of ``screen_symbol_id → function_dict`` used
                           to enrich signal text (same shape as the ``function_lookup``
                           accepted by ``classify_batch``).

        Returns:
            List[WorkflowMatch] — one entry per navigator.
        """
        if not navigators:
            return []

        results: List[WorkflowMatch] = []
        llm_candidates: List[Tuple[int, str]] = []

        for idx, nav in enumerate(navigators):
            # Accept both dataclass instances and dicts
            if hasattr(nav, "__dataclass_fields__"):
                nav_dict: Dict[str, Any] = {
                    "symbol_id": nav.symbol_id,
                    "var_name": nav.var_name,
                    "nav_type": nav.nav_type,
                    "routes": list(nav.routes),
                }
            else:
                nav_dict = nav  # type: ignore[assignment]

            var_name: str = nav_dict.get("var_name", "Navigator")
            nav_type: str = nav_dict.get("nav_type", "unknown")
            routes: List[Any] = nav_dict.get("routes") or []
            symbol_id: str = nav_dict.get("symbol_id", f"nav::{var_name}")

            # Route names become the step signal text
            step_ids: List[str] = []
            for route in routes:
                if isinstance(route, (list, tuple)) and len(route) >= 2:
                    _rname, _comp = route[0], route[1]
                    # Resolve component → screen symbol_id if available
                    screen_entry = screen_lookup.get(_comp)
                    if screen_entry and isinstance(screen_entry, dict):
                        step_ids.append(screen_entry.get("symbol_id", _comp))
                    else:
                        step_ids.append(_comp)
                elif isinstance(route, str):
                    step_ids.append(route)

            # Signal text: var_name + nav_type + route names
            signal_parts = [var_name, nav_type] + [
                (r[0] if isinstance(r, (list, tuple)) else r) for r in routes
            ]
            signal = " ".join(str(p) for p in signal_parts if p)

            wf_name, domain, confidence = _heuristic_classify(signal)
            wf_id = _stable_id([symbol_id] + step_ids) if step_ids else _stable_id([symbol_id])
            description = (
                f"UI flow navigated via {nav_type} navigator '{var_name}' "
                f"({len(routes)} routes)"
            )
            match = WorkflowMatch(
                workflow_id=wf_id,
                workflow_name=wf_name,
                domain=domain,
                description=description,
                confidence=confidence,
                entrypoint_id=symbol_id,
                step_ids=step_ids,
                language=self.language,
                project=self.project_id,
                kind="navigator",
            )
            results.append(match)

            if confidence < HEURISTIC_THRESHOLD:
                llm_candidates.append((idx, signal))

        # Optional LLM enrichment pass
        if self.enable_llm and llm_candidates and _llm_available():
            api_key = os.environ.get("OPENAI_API_KEY", "")
            base_url = os.environ.get("OPENAI_BASE_URL", "")
            signals = [sig for _, sig in llm_candidates]
            llm_outputs = _call_llm_classify(signals, self.llm_model, api_key, base_url)
            for (idx, _), llm_out in zip(llm_candidates, llm_outputs):
                if llm_out is None:
                    continue
                match = results[idx]
                match.workflow_name = llm_out.get("name", match.workflow_name)
                match.domain = llm_out.get("domain", match.domain)
                match.description = llm_out.get("description", match.description)
                match.confidence = float(llm_out.get("confidence", match.confidence))

        return results
