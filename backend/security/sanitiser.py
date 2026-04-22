"""
Query sanitisation — Layer 2 of the FocalPoint security stack.

Detects and neutralises prompt-injection patterns in user queries before
they reach the prompt builder.  Flagged phrases are stripped (not rejected)
so that the remaining query can still be processed.  If stripping leaves
nothing meaningful, a safe fallback string is returned.

All sanitisation events are logged for monitoring (raw text is NOT logged
to protect journalist privacy).

Design principle: false-positive (over-sanitising) is safer than
false-negative (missing an injection) for a journalist-safety application.
"""

from __future__ import annotations

import re

from loguru import logger
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Detection patterns
# Each tuple: (compiled pattern, reason label for logging)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # --- Persona takeover ---
    (re.compile(r"\byou\s+are\s+now\b", re.I), "persona: 'you are now'"),
    (re.compile(r"\bact\s+as\b", re.I), "persona: 'act as'"),
    (
        re.compile(r"\bpretend\s+(?:to\s+be|you\s+are|you'?re)\b", re.I),
        "persona: 'pretend to be'",
    ),
    (re.compile(r"\broleplay\b", re.I), "persona: 'roleplay'"),

    # --- Instruction override (compound: trigger word near target word) ---
    (
        re.compile(
            r"\b(?:ignore|forget|disregard)\b.{0,60}?"
            r"\b(?:instructions?|rule|prompt|system|guideline)\b",
            re.I | re.S,
        ),
        "override: ignore-instruction",
    ),
    (
        re.compile(
            r"\boverride\b.{0,40}?"
            r"\b(?:instruction|rule|system|safety|guideline|restriction)\b",
            re.I | re.S,
        ),
        "override: override-system",
    ),
    (
        re.compile(
            r"\bbypass\b.{0,40}?"
            r"\b(?:safety|filter|restriction|rule|system|guard)\b",
            re.I | re.S,
        ),
        "bypass: bypass-safety",
    ),
    (
        re.compile(
            r"\bdisable\b.{0,40}?"
            r"\b(?:safety|filter|restriction|rule|system|guard)\b",
            re.I | re.S,
        ),
        "disable: disable-safety",
    ),

    # --- System prompt structure markers ---
    (re.compile(r"\[SYSTEM\b", re.I), "marker: [SYSTEM]"),
    (re.compile(r"<\s*system\b", re.I), "marker: <system>"),
    (re.compile(r"#{2,}\s*system\b", re.I), "marker: ##system"),

    # --- Encoding tricks ---
    (re.compile(r"\\x[0-9a-fA-F]{2}", re.I), "encoding: hex-escape"),
    (re.compile(r"\\u[0-9a-fA-F]{4}", re.I), "encoding: unicode-escape"),
    (re.compile(r"(?:%[0-9a-fA-F]{2}){3,}", re.I), "encoding: url-percent"),

    # --- Obfuscation: 8+ consecutive non-word, non-whitespace characters ---
    (re.compile(r"[^\w\s,.!?'\"()\-]{8,}"), "obfuscation: excessive-special-chars"),
]

_FALLBACK_TEXT = "query could not be processed"

# Matches at least one letter or digit — used alongside an explicit len >= 2 check.
_MEANINGFUL_RE = re.compile(r"[a-zA-Z0-9]")


class SanitisationResult(BaseModel):
    text: str                        # cleaned text, or fallback if empty
    was_modified: bool               # True when any pattern fired
    reasons: list[str] = Field(default_factory=list)  # labels of matched patterns


def sanitise_query(query: str) -> SanitisationResult:
    """
    Strip prompt-injection patterns from *query* and return a
    ``SanitisationResult``.

    The original query is never logged.  Only the count and labels of
    triggered patterns are recorded.
    """
    cleaned = query
    triggered: list[str] = []

    for pattern, reason in _PATTERNS:
        new_text, n = pattern.subn(" ", cleaned)
        if n:
            cleaned = new_text
            triggered.append(reason)

    # Collapse runs of whitespace introduced by substitutions.
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    was_modified = bool(triggered)

    if was_modified:
        logger.warning(
            f"sanitise_query: {len(triggered)} pattern(s) removed — {triggered}"
        )

    if not _MEANINGFUL_RE.search(cleaned) or len(cleaned) < 2:
        logger.warning("sanitise_query: result empty after sanitisation, using fallback")
        return SanitisationResult(
            text=_FALLBACK_TEXT,
            was_modified=True,
            reasons=triggered or ["empty-after-sanitisation"],
        )

    return SanitisationResult(text=cleaned, was_modified=was_modified, reasons=triggered)
