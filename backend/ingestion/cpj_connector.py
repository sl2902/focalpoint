"""
CPJ (Committee to Protect Journalists) CSV loader.

Reads backend/data/cpj_incidents.csv into memory at startup and indexes
incidents by country for O(1) lookup during severity scoring.

No Redis caching — the CSV is a static versioned asset loaded once.
No async — all operations are synchronous in-memory lookups.

Source: https://cpj.org/data-api/ → "Download this database"
Format: CSV with 8 columns; see _COLUMN_MAP for name normalisation.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Union

import pandas as pd
from loguru import logger
from pydantic import BaseModel

DEFAULT_CSV_PATH = Path(__file__).parent.parent / "data" / "cpj_incidents.csv"

# Maps raw CSV headers → snake_case field names used in CpjIncident.
_COLUMN_MAP: dict[str, str] = {
    "Name": "name",
    "Status": "status",
    "Date": "date",
    "Country": "country",
    "Journalist or Media Worker": "journalist_or_media_worker",
    "Motive": "motive",
    "Type of Death": "type_of_death",
    "cpj.org URL": "cpj_url",
}


class CpjIncident(BaseModel):
    name: str
    status: str
    date: str                           # raw string, e.g. "April 30, 2018"
    country: str
    journalist_or_media_worker: str = ""  # 3 nulls in source CSV
    motive: str                         # "Confirmed" | "Unconfirmed"
    type_of_death: str = ""             # 324 nulls in source CSV
    cpj_url: str
    year: int                           # extracted from date at load time


class CountryStats(BaseModel):
    country: str
    total_incidents: int
    incidents_per_year: float   # total / (latest_year - earliest_year + 1)
    earliest_year: int          # 0 when country has no incidents
    latest_year: int            # 0 when country has no incidents


class CPJConnector:
    """
    In-memory CPJ incident store, indexed by country.

    Instantiate once at backend startup and pass the singleton wherever
    severity scoring needs historical journalist-safety data.

    The constructor accepts either a file path (default: the committed CSV)
    or any file-like object — making it straightforward to test with an
    io.StringIO fixture instead of the real file.
    """

    def __init__(self, source: Union[str, Path, IO] = DEFAULT_CSV_PATH) -> None:
        df = pd.read_csv(source)
        self._incidents, self._by_country = self._parse(df)
        logger.info(
            f"CPJ: loaded {len(self._incidents)} incidents"
            f" across {len(self._by_country)} countries"
        )

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(
        df: pd.DataFrame,
    ) -> tuple[list[CpjIncident], dict[str, list[CpjIncident]]]:
        df = df.rename(columns=_COLUMN_MAP)
        df["journalist_or_media_worker"] = df["journalist_or_media_worker"].fillna("")
        df["type_of_death"] = df["type_of_death"].fillna("")
        # Extract 4-digit year from date strings like "April 30, 2018"
        df["year"] = df["date"].str.extract(r"(\d{4})")[0].astype(int)

        incidents: list[CpjIncident] = [
            CpjIncident(**row) for row in df.to_dict(orient="records")
        ]

        by_country: dict[str, list[CpjIncident]] = {}
        for incident in incidents:
            by_country.setdefault(incident.country, []).append(incident)

        return incidents, by_country

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def total_incidents(self) -> int:
        """Total number of incidents loaded from the CSV."""
        return len(self._incidents)

    def list_countries(self) -> list[str]:
        """Alphabetically sorted list of countries that have incidents."""
        return sorted(self._by_country.keys())

    def get_incidents(self, country: str) -> list[CpjIncident]:
        """Return all incidents for *country*, or an empty list if none."""
        return self._by_country.get(country, [])

    def get_country_stats(self, country: str) -> CountryStats:
        """
        Return aggregated incident statistics for *country*.

        incidents_per_year is calculated over the span from the earliest
        to the latest recorded incident year (inclusive), so a country
        with 3 incidents in 2020, 2021, and 2022 yields rate = 1.0,
        while 2 incidents both in 2021 yields rate = 2.0.

        Returns a zeroed CountryStats if the country has no incidents.
        """
        incidents = self._by_country.get(country, [])
        total = len(incidents)
        if total == 0:
            return CountryStats(
                country=country,
                total_incidents=0,
                incidents_per_year=0.0,
                earliest_year=0,
                latest_year=0,
            )

        years = [i.year for i in incidents]
        earliest = min(years)
        latest = max(years)
        span = max(latest - earliest + 1, 1)

        return CountryStats(
            country=country,
            total_incidents=total,
            incidents_per_year=round(total / span, 2),
            earliest_year=earliest,
            latest_year=latest,
        )
