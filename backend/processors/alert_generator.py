"""
Alert generator — orchestrates ingestion outputs into validated AlertOutput.

This module is the single entry point for Phase 3 alert production.
It wires together:
  1. prompt_builder   — constructs the grounded Gemma 4 prompt
  2. gemma_client     — calls the model and validates the response
  3. output_validator — already applied inside gemma_client.generate_alert

Callers (FastAPI routes, the scheduler) pass validated Pydantic models from
the ingestion layer and receive a typed AlertOutput — never raw model text.

Maximum severity rule
---------------------
When a SeverityResult from score_severity is supplied, the final severity is
max(gemma_severity, scorer_severity) using SEVERITY_ORDER:

  - Gemma higher  → keep Gemma severity; append an elevation note to the summary.
  - Scorer higher → override severity with scorer value; summary unchanged.
  - Equal         → no changes.

INSUFFICIENT_DATA has order -1, so any real severity level wins over it.
"""

from __future__ import annotations

from typing import Final

from backend.alerts.severity_scorer import SeverityResult
from backend.ingestion.cpj_connector import CountryStats
from backend.ingestion.gdelt_connector import GdeltArticle
from backend.ingestion.gdeltcloud_connector import GdeltCloudEvent
from backend.processors.gemma_client import GemmaClient
from backend.processors.prompt_builder import build_prompt
from backend.security.output_validator import AlertOutput
from backend.security.sanitiser import sanitise_query

# Severity ordering for the maximum severity rule.
# INSUFFICIENT_DATA is -1 so any real score always wins over a model failure.
SEVERITY_ORDER: Final[dict[str, int]] = {
    "INSUFFICIENT_DATA": -1,
    "GREEN": 0,
    "AMBER": 1,
    "RED": 2,
    "CRITICAL": 3,
}

# Note appended to the summary when Gemma's severity exceeds the scorer's.
_ELEVATION_NOTE: Final[str] = (
    " [Note: contextual factors elevated severity above the data-driven baseline.]"
)


def _apply_max_severity(alert: AlertOutput, severity_result: SeverityResult) -> AlertOutput:
    """Return alert with severity = max(gemma_severity, scorer_severity).

    Special case: if the scorer returned INSUFFICIENT_DATA the data environment is
    genuinely empty — override Gemma's output regardless, preventing the model from
    hallucinating a real severity when there is nothing to assess.

    Normal cases:
      Gemma higher  → keep Gemma severity, append _ELEVATION_NOTE to summary.
      Scorer higher → set severity to scorer value, leave summary unchanged.
      Equal         → return alert unchanged.

    Note: INSUFFICIENT_DATA in GEMMA's output has order -1, so the scorer always
    wins when Gemma fails — this is distinct from the scorer's own INSUFFICIENT_DATA
    veto handled above.
    """
    # Scorer veto — no data available to assess.
    if severity_result.level.value == "INSUFFICIENT_DATA":
        return alert.model_copy(update={"severity": "INSUFFICIENT_DATA"})

    gemma_order = SEVERITY_ORDER.get(alert.severity, -1)
    scorer_order = SEVERITY_ORDER.get(severity_result.level.value, -1)

    if gemma_order > scorer_order:
        # Gemma's assessment is more alarming — honour it and explain why.
        max_body = 1000 - len(_ELEVATION_NOTE)
        new_summary = alert.summary[:max_body] + _ELEVATION_NOTE
        return alert.model_copy(update={"summary": new_summary})

    if scorer_order > gemma_order:
        # Deterministic scorer outranks the model — use scorer severity.
        return alert.model_copy(update={"severity": severity_result.level.value})

    # Equal — no change.
    return alert


class AlertGenerator:
    """
    Produces validated AlertOutput models from multi-source conflict data.

    Instantiate once at backend startup with a shared GemmaClient, then
    call generate() for each region that needs an alert.
    """

    def __init__(self, gemma_client: GemmaClient) -> None:
        self._gemma = gemma_client

    def generate(
        self,
        conflict_events: list[GdeltCloudEvent],
        gdelt_articles: list[GdeltArticle],
        gdelt_aggregate_tone: float,
        cpj_stats: CountryStats,
        rsf_score: float,
        region: str,
        journalist_query: str = "",
        severity_result: SeverityResult | None = None,
    ) -> AlertOutput:
        """
        Generate a validated alert for *region* from multi-source inputs.

        Web search is enabled automatically when GDELT Doc API provides no usable
        articles (empty list). When web search is active and the deterministic scorer
        returns INSUFFICIENT_DATA, the scorer's veto is suppressed — Gemma may have
        found live sources via web search that the scorer had no visibility into.

        Args:
            conflict_events:      Validated GdeltCloudEvent list for the target region.
            gdelt_articles:       Validated GDELT articles for the target query.
            gdelt_aggregate_tone: Mean tone from the GDELT timelinetone endpoint.
            cpj_stats:            Historical CPJ journalist-safety stats for the country.
            rsf_score:            RSF Press Freedom Index score (0–100).
            region:               Human-readable region label (e.g. "northern Gaza").
            journalist_query:     Optional free-text query from a journalist.
                                  Sanitised before inclusion in the prompt.
            severity_result:      Optional SeverityResult from score_severity.
                                  When provided the maximum severity rule is applied:
                                  final severity = max(gemma, scorer).

        Returns:
            Validated AlertOutput. Always returns — never raises.
        """
        use_web_search = len(gdelt_articles) == 0

        if journalist_query:
            sanitised = sanitise_query(journalist_query).text
        else:
            sanitised = "Provide a current safety assessment for the region."

        prompt = build_prompt(
            conflict_events=conflict_events,
            gdelt_articles=gdelt_articles,
            gdelt_aggregate_tone=gdelt_aggregate_tone,
            cpj_stats=cpj_stats,
            rsf_score=rsf_score,
            region=region,
            sanitised_query=sanitised,
            use_web_search=use_web_search,
        )

        alert = self._gemma.generate_alert(prompt, region, use_web_search=use_web_search)

        if severity_result is not None:
            # When web search was active, suppress the scorer's INSUFFICIENT_DATA veto.
            # Gemma may have found live sources the deterministic scorer could not see.
            if not (use_web_search and severity_result.level.value == "INSUFFICIENT_DATA"):
                alert = _apply_max_severity(alert, severity_result)

        return alert
