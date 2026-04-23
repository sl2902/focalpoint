"""
Alert generator — orchestrates ingestion outputs into validated AlertOutput.

This module is the single entry point for Phase 3 alert production.
It wires together:
  1. prompt_builder   — constructs the grounded Gemma 4 prompt
  2. gemma_client     — calls the model and validates the response
  3. output_validator — already applied inside gemma_client.generate_alert

Callers (FastAPI routes, the scheduler) pass validated Pydantic models from
the ingestion layer and receive a typed AlertOutput — never raw model text.
"""

from __future__ import annotations

from backend.ingestion.acled_connector import AcledEvent
from backend.ingestion.cpj_connector import CountryStats
from backend.ingestion.gdelt_connector import GdeltArticle
from backend.processors.gemma_client import GemmaClient
from backend.processors.prompt_builder import build_prompt
from backend.security.output_validator import AlertOutput
from backend.security.sanitiser import sanitise_query


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
        acled_events: list[AcledEvent],
        gdelt_articles: list[GdeltArticle],
        gdelt_aggregate_tone: float,
        cpj_stats: CountryStats,
        rsf_score: float,
        region: str,
        journalist_query: str = "",
    ) -> AlertOutput:
        """
        Generate a validated alert for *region* from multi-source inputs.

        Args:
            acled_events:         Validated ACLED events for the target region.
            gdelt_articles:       Validated GDELT articles for the target query.
            gdelt_aggregate_tone: Mean tone from the GDELT timelinetone endpoint.
            cpj_stats:            Historical CPJ journalist-safety stats for the country.
            rsf_score:            RSF Press Freedom Index score (0–100).
            region:               Human-readable region label (e.g. "northern Gaza").
            journalist_query:     Optional free-text query from a journalist.
                                  Sanitised before inclusion in the prompt.

        Returns:
            Validated AlertOutput. Always returns — never raises.
        """
        # Sanitise any journalist query before it reaches the prompt.
        if journalist_query:
            sanitised = sanitise_query(journalist_query).text
        else:
            sanitised = "Provide a current safety assessment for the region."

        prompt = build_prompt(
            acled_events=acled_events,
            gdelt_articles=gdelt_articles,
            gdelt_aggregate_tone=gdelt_aggregate_tone,
            cpj_stats=cpj_stats,
            rsf_score=rsf_score,
            region=region,
            sanitised_query=sanitised,
        )

        return self._gemma.generate_alert(prompt, region)
