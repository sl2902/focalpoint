"""
Independent tests for the CPJ CSV connector.

All tests use a small synthetic CSV via io.StringIO — no real file I/O,
no dependency on backend/data/cpj_incidents.csv.

Synthetic data:
  Palestine  3 incidents — years 2020, 2021, 2022  → rate = 3/3 = 1.0
  Syria      2 incidents — both in 2021            → rate = 2/1 = 2.0
  Iraq       1 incident  — year 2019               → rate = 1/1 = 1.0
  (Eve Black has null journalist; Dan White has null type_of_death)
"""

from io import StringIO

import pytest

from backend.ingestion.cpj_connector import (
    CPJConnector,
    CountryStats,
    CpjIncident,
)

# ---------------------------------------------------------------------------
# Synthetic CSV fixture
# ---------------------------------------------------------------------------

_CSV = """\
Name,Status,Date,Country,Journalist or Media Worker,Motive,Type of Death,cpj.org URL
Alice Smith,Killed,January 15 2020,Palestine,Al Jazeera,Confirmed,Murder,https://cpj.org/data/people/alice-smith/
Bob Jones,Killed,March 20 2021,Palestine,Reuters,Confirmed,Murder,https://cpj.org/data/people/bob-jones/
Carol Davis,Killed,June 1 2022,Palestine,BBC,Confirmed,Dangerous Assignment,https://cpj.org/data/people/carol-davis/
Dan White,Killed,February 10 2021,Syria,Freelance,Unconfirmed,,https://cpj.org/data/people/dan-white/
Eve Black,Killed,August 5 2021,Syria,,Confirmed,Crossfire,https://cpj.org/data/people/eve-black/
Frank Gray,Killed,November 3 2019,Iraq,AP,Confirmed,Murder,https://cpj.org/data/people/frank-gray/
"""


@pytest.fixture
def connector() -> CPJConnector:
    return CPJConnector(source=StringIO(_CSV))


# ---------------------------------------------------------------------------
# CpjIncident model — no I/O
# ---------------------------------------------------------------------------


class TestCpjIncident:
    def test_valid_full_incident(self) -> None:
        inc = CpjIncident(
            name="Alice Smith",
            status="Killed",
            date="January 15 2020",
            country="Palestine",
            journalist_or_media_worker="Al Jazeera",
            motive="Confirmed",
            type_of_death="Murder",
            cpj_url="https://cpj.org/data/people/alice-smith/",
            year=2020,
        )
        assert inc.name == "Alice Smith"
        assert inc.year == 2020
        assert inc.country == "Palestine"

    def test_optional_journalist_defaults_to_empty(self) -> None:
        inc = CpjIncident(
            name="Eve Black",
            status="Killed",
            date="August 5 2021",
            country="Syria",
            motive="Confirmed",
            type_of_death="Crossfire",
            cpj_url="https://cpj.org/data/people/eve-black/",
            year=2021,
        )
        assert inc.journalist_or_media_worker == ""

    def test_optional_type_of_death_defaults_to_empty(self) -> None:
        inc = CpjIncident(
            name="Dan White",
            status="Killed",
            date="February 10 2021",
            country="Syria",
            journalist_or_media_worker="Freelance",
            motive="Unconfirmed",
            cpj_url="https://cpj.org/data/people/dan-white/",
            year=2021,
        )
        assert inc.type_of_death == ""


class TestCountryStats:
    def test_valid_stats(self) -> None:
        stats = CountryStats(
            country="Palestine",
            total_incidents=3,
            incidents_per_year=1.0,
            earliest_year=2020,
            latest_year=2022,
        )
        assert stats.total_incidents == 3
        assert stats.incidents_per_year == 1.0

    def test_zero_stats(self) -> None:
        stats = CountryStats(
            country="Germany",
            total_incidents=0,
            incidents_per_year=0.0,
            earliest_year=0,
            latest_year=0,
        )
        assert stats.total_incidents == 0


# ---------------------------------------------------------------------------
# CPJConnector — loading and indexing
# ---------------------------------------------------------------------------


class TestLoading:
    def test_loads_all_rows(self, connector: CPJConnector) -> None:
        assert connector.total_incidents == 6

    def test_indexes_palestine(self, connector: CPJConnector) -> None:
        assert len(connector.get_incidents("Palestine")) == 3

    def test_indexes_syria(self, connector: CPJConnector) -> None:
        assert len(connector.get_incidents("Syria")) == 2

    def test_indexes_iraq(self, connector: CPJConnector) -> None:
        assert len(connector.get_incidents("Iraq")) == 1

    def test_unknown_country_returns_empty_list(self, connector: CPJConnector) -> None:
        assert connector.get_incidents("Germany") == []

    def test_list_countries_is_sorted(self, connector: CPJConnector) -> None:
        countries = connector.list_countries()
        assert countries == sorted(countries)

    def test_list_countries_contains_all_three(self, connector: CPJConnector) -> None:
        assert set(connector.list_countries()) == {"Iraq", "Palestine", "Syria"}

    def test_null_type_of_death_becomes_empty_string(
        self, connector: CPJConnector
    ) -> None:
        dan = next(
            i for i in connector.get_incidents("Syria") if i.name == "Dan White"
        )
        assert dan.type_of_death == ""

    def test_null_journalist_becomes_empty_string(
        self, connector: CPJConnector
    ) -> None:
        eve = next(
            i for i in connector.get_incidents("Syria") if i.name == "Eve Black"
        )
        assert eve.journalist_or_media_worker == ""

    def test_year_extracted_from_date(self, connector: CPJConnector) -> None:
        alice = next(
            i for i in connector.get_incidents("Palestine") if i.name == "Alice Smith"
        )
        assert alice.year == 2020

    def test_incidents_are_cpjincident_instances(
        self, connector: CPJConnector
    ) -> None:
        incidents = connector.get_incidents("Palestine")
        assert all(isinstance(i, CpjIncident) for i in incidents)


# ---------------------------------------------------------------------------
# CPJConnector — get_country_stats
# ---------------------------------------------------------------------------


class TestCountryStatsMethod:
    def test_palestine_multi_year_rate(self, connector: CPJConnector) -> None:
        # 3 incidents over 2020, 2021, 2022 → span=3, rate=3/3=1.0
        stats = connector.get_country_stats("Palestine")
        assert stats.total_incidents == 3
        assert stats.earliest_year == 2020
        assert stats.latest_year == 2022
        assert stats.incidents_per_year == 1.0

    def test_syria_single_year_rate(self, connector: CPJConnector) -> None:
        # 2 incidents both in 2021 → span=1, rate=2/1=2.0
        stats = connector.get_country_stats("Syria")
        assert stats.total_incidents == 2
        assert stats.earliest_year == 2021
        assert stats.latest_year == 2021
        assert stats.incidents_per_year == 2.0

    def test_iraq_single_incident_rate(self, connector: CPJConnector) -> None:
        # 1 incident in 2019 → span=1, rate=1/1=1.0
        stats = connector.get_country_stats("Iraq")
        assert stats.total_incidents == 1
        assert stats.earliest_year == 2019
        assert stats.latest_year == 2019
        assert stats.incidents_per_year == 1.0

    def test_unknown_country_returns_zero_stats(
        self, connector: CPJConnector
    ) -> None:
        stats = connector.get_country_stats("Germany")
        assert stats.country == "Germany"
        assert stats.total_incidents == 0
        assert stats.incidents_per_year == 0.0
        assert stats.earliest_year == 0
        assert stats.latest_year == 0

    def test_country_name_preserved_in_stats(self, connector: CPJConnector) -> None:
        stats = connector.get_country_stats("Palestine")
        assert stats.country == "Palestine"

    def test_incidents_per_year_is_rounded(self) -> None:
        # 2 incidents over a 3-year span → 2/3 = 0.666... → rounded to 0.67
        csv = StringIO(
            "Name,Status,Date,Country,Journalist or Media Worker,"
            "Motive,Type of Death,cpj.org URL\n"
            "A,Killed,January 1 2019,X,,Confirmed,,https://cpj.org/\n"
            "B,Killed,January 1 2021,X,,Confirmed,,https://cpj.org/\n"
        )
        c = CPJConnector(source=csv)
        stats = c.get_country_stats("X")
        assert stats.incidents_per_year == 0.67  # round(2/3, 2)
