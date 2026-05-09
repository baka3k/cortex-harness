"""
llm_summary.py
──────────────
Opt-in LLM-based summary generation for Python functions.

Activated via ``--enable-llm-summary`` flag in python_analyzer.py.
Only called for functions whose ``doc_confidence < 0.4`` (not documented
by comments/docstrings and not resolved by signal-based inference).

Environment variables
─────────────────────
  OPENAI_API_KEY        Use OpenAI API directly
  OPENAI_BASE_URL       Override base URL (LiteLLM proxy, Azure, etc.)
  LLM_SUMMARY_MODEL     Model name (default: gpt-4o-mini)
  LLM_SUMMARY_BATCH     Batch size for parallel requests (default: 5)

Public API
──────────
  generate_summaries(functions, verbose=False) → None
    Mutates ``summary`` field of qualifying function dicts in-place.
"""

from __future__ import annotations

import os
import json
import time
from typing import Any, Dict, List, Optional

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_BATCH = 5
_SYSTEM_PROMPT = (
    "You are a senior engineer writing concise function documentation. "
    "Given a Python function signature and body, write a single sentence "
    "summary (max 20 words) describing what the function does. "
    "Respond with only the summary text, no punctuation at end."
)


def _build_prompt(func: Dict[str, Any]) -> str:
    sig = func.get("note") or func.get("code") or func.get("name") or "unknown"
    # Limit to 600 chars to keep token cost low
    return sig[:600]


def _call_openai(
    prompts: List[str],
    model: str,
    api_key: str,
    base_url: str,
) -> List[Optional[str]]:
    """Batch-call OpenAI-compatible API. Returns list of summaries (None on error)."""
    try:
        import openai  # type: ignore
    except ImportError:
        return [None] * len(prompts)

    client = openai.OpenAI(api_key=api_key, base_url=base_url or None)
    results: List[Optional[str]] = []
    for prompt in prompts:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=60,
                temperature=0.2,
            )
            results.append(response.choices[0].message.content.strip())
        except Exception:
            results.append(None)
    return results


def generate_summaries(
    functions: List[Dict[str, Any]],
    verbose: bool = False,
) -> None:
    """
    Generate LLM summaries for functions with low doc_confidence.

    Mutates ``summary`` (and ``note``) fields in-place on qualifying dicts.
    Silently skips if OpenAI credentials are not available.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        if verbose:
            print("[llm] OPENAI_API_KEY not set; skipping LLM summary generation")
        return

    model = os.environ.get("LLM_SUMMARY_MODEL", _DEFAULT_MODEL)
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    batch_size = int(os.environ.get("LLM_SUMMARY_BATCH", str(_DEFAULT_BATCH)))

    total = len(functions)
    if verbose:
        print(f"[llm] Generating summaries for {total} functions using {model}...")

    for i in range(0, total, batch_size):
        chunk = functions[i : i + batch_size]
        prompts = [_build_prompt(f) for f in chunk]
        summaries = _call_openai(prompts, model, api_key, base_url)
        for func, summary in zip(chunk, summaries):
            if summary:
                func["summary"] = summary
                # Re-build note preview (signature + new summary + partial code)
                existing_note = func.get("note", "")
                sig_line = existing_note.split("\n\n")[0] if existing_note else ""
                func["note"] = f"{sig_line}\n\nSummary (LLM):\n{summary}".strip()

    if verbose:
        print("[llm] Summary generation complete")
