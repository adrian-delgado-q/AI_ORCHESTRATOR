"""DiagnosticUtility — Stage 4.

Converts raw tool output (findings) into structured fix-it instructions
that ``dev_node`` can act on in the next repair loop.

Only called on *failing* ``ToolEvidence`` entries — never on passing ones.
"""
from __future__ import annotations

import logging

from src.core.llm import BaseLLM
from src.state.schema import ToolEvidence

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior software engineer reviewing automated quality-gate output.
Your task is to analyse the raw tool findings and produce CONCISE, ACTIONABLE
fix-it instructions for a code-generation agent that will rewrite the code.

Rules:
- Be specific: reference line numbers, symbol names, or patterns when present.
- Do NOT reproduce the full stack trace — summarise the root cause.
- Max 200 words.
- Output plain text only (no markdown headers).
"""


class DiagnosticUtility:
    """Wraps an LLM to diagnose tool failures."""

    def __init__(self, llm: BaseLLM) -> None:
        self._llm = llm

    def diagnose(self, evidence: ToolEvidence) -> str:
        """Return a plain-text fix-it instruction for a failing *evidence* entry.

        If *evidence.passed* is True the caller should not invoke this
        method, but we guard defensively and return an empty string.
        """
        if evidence.passed:
            logger.debug("[Diagnostic] %s passed — no diagnosis needed.", evidence.tool_name)
            return ""

        user_content = (
            f"Tool: {evidence.tool_name}\n\n"
            f"Raw findings:\n{evidence.findings}\n\n"
            "Provide fix-it instructions for the code-generation agent."
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        diagnosis = self._llm.chat(messages).strip()
        logger.info("[Diagnostic] %s → %d char diagnosis.", evidence.tool_name, len(diagnosis))
        return diagnosis
