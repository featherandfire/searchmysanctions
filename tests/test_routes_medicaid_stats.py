"""
Tests for the medicaid stats aggregation endpoints in routes/datasets.py.

These sit on top of `_medicaid_entities` (the warm-cache contract tested
separately in test_routes_datasets.py) — these tests mock that function and
focus on the aggregation math, year/sector filtering, and L2 cache behaviour.
"""
import pytest

import cache as l2
from routes import datasets as routes_datasets


# ── /api/stats/medicaid-by-year ──────────────────────────────────────────────

class TestMedicaidByYear:
    @pytest.fixture(autouse=True)
    def _disable_cache(self, monkeypatch):
        """Force a cache miss so every test exercises the aggregation path."""
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        self.cache_writes = []
        monkeypatch.setattr(
            l2, "set",
            lambda *a, **kw: self.cache_writes.append(a),
        )

    def test_groups_by_year_of_first_seen(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"first_seen": "2020-01-15"},
            {"first_seen": "2020-06-30"},
            {"first_seen": "2021-03-10"},
        ])

        data = client.get("/api/stats/medicaid-by-year").get_json()
        assert data == [
            {"label": "2020", "value": 2},
            {"label": "2021", "value": 1},
        ]

    def test_sorted_by_year_ascending(self, client, monkeypatch):
        """Returned list is sorted by year (label), not by count."""
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"first_seen": "2022-01-01"},
            {"first_seen": "2020-01-01"},
            {"first_seen": "2020-01-01"},  # extra to break value-sort
            {"first_seen": "2021-01-01"},
        ])
        labels = [r["label"] for r in client.get("/api/stats/medicaid-by-year").get_json()]
        assert labels == ["2020", "2021", "2022"]

    def test_filters_out_non_digit_years(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"first_seen": "abcd-garbage"},
            {"first_seen": ""},
            {"first_seen": None},
            {"first_seen": "2020-01-01"},
        ])
        data = client.get("/api/stats/medicaid-by-year").get_json()
        assert data == [{"label": "2020", "value": 1}]

    def test_filters_out_years_outside_2000_to_2100(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"first_seen": "1999-12-31"},  # below range
            {"first_seen": "2101-01-01"},  # above range
            {"first_seen": "2000-01-01"},  # boundary OK
            {"first_seen": "2100-01-01"},  # boundary OK
        ])
        labels = [r["label"] for r in client.get("/api/stats/medicaid-by-year").get_json()]
        assert labels == ["2000", "2100"]

    def test_result_written_to_l2_under_per_dataset_key(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities",
                            lambda: [{"first_seen": "2024-01-01"}])

        client.get("/api/stats/medicaid-by-year?datasets=us_ny_med,us_ca_med")
        # l2.set(source, key, value)
        assert len(self.cache_writes) == 1
        source, key = self.cache_writes[0][:2]
        assert source == "medicaid_stats"
        assert key == "by-year:us_ny_med,us_ca_med"

    def test_returns_cached_result_when_present(self, client, monkeypatch):
        cached = [{"label": "2024", "value": 999}]
        monkeypatch.setattr(l2, "get", lambda *a, **kw: cached)
        # Tripwire — _medicaid_entities must not be touched on cache hit
        monkeypatch.setattr(routes_datasets, "_medicaid_entities",
                            lambda: pytest.fail("must not aggregate when cached"))

        data = client.get("/api/stats/medicaid-by-year").get_json()
        assert data == cached


# ── /api/stats/medicaid-by-sector ────────────────────────────────────────────

class TestMedicaidBySector:
    """Exercises the shared `_medicaid_counts` helper + sector-specific
    extractor: split comma-separated sector strings, normalise aliases."""

    @pytest.fixture(autouse=True)
    def _disable_cache(self, monkeypatch):
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        self.cache_writes = []
        monkeypatch.setattr(
            l2, "set",
            lambda *a, **kw: self.cache_writes.append(a),
        )

    def test_counts_by_sector(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "Registered Nurse"},
            {"sector": "Registered Nurse"},
            {"sector": "Dentist"},
        ])
        data = client.get("/api/stats/medicaid-by-sector").get_json()
        assert data == [
            {"label": "Registered Nurse", "value": 2},
            {"label": "Dentist", "value": 1},
        ]

    def test_normalises_sector_aliases(self, client, monkeypatch):
        # "RN" and "rn" both alias to "Registered Nurse"
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN"},
            {"sector": "rn"},
            {"sector": "Registered Nurse"},
        ])
        data = client.get("/api/stats/medicaid-by-sector").get_json()
        assert data == [{"label": "Registered Nurse", "value": 3}]

    def test_splits_comma_separated_sectors(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN, Dentist"},  # one row, two sectors
        ])
        data = sorted(client.get("/api/stats/medicaid-by-sector").get_json(),
                      key=lambda r: r["label"])
        assert data == [
            {"label": "Dentist", "value": 1},
            {"label": "Registered Nurse", "value": 1},
        ]

    def test_falls_back_to_position_then_title(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"position": "Cardiologist"},       # falls through from sector
            {"title": "Pharmacist"},            # falls through from position
            {"sector": "RN"},                   # picked first
        ])
        labels = {r["label"] for r in client.get("/api/stats/medicaid-by-sector").get_json()}
        assert labels == {"Registered Nurse", "Cardiologist", "Pharmacist"}

    def test_empty_result_is_not_cached(self, client, monkeypatch):
        """`_medicaid_counts` skips caching empty results so a cold-warmup
        retry can refill them later."""
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [])
        client.get("/api/stats/medicaid-by-sector")
        assert self.cache_writes == []

    def test_non_empty_result_is_cached(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities",
                            lambda: [{"sector": "RN"}])
        client.get("/api/stats/medicaid-by-sector")
        assert len(self.cache_writes) == 1
        source, key = self.cache_writes[0][:2]
        assert source == "medicaid_stats"
        assert key == "by-sector:us_ca_med_exclusions"  # default ds_param


# ── /api/stats/medicaid-top-sector-cities ────────────────────────────────────

class TestMedicaidTopSectorCities:
    """City breakdown for the top (or a named) sector, with all-sector city
    totals included as a denominator for per-city % calculations."""

    def test_empty_when_no_sectors_present(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"address": "123 Main St, Fresno, CA"},  # no sector field
        ])
        data = client.get("/api/stats/medicaid-top-sector-cities").get_json()
        assert data == {"sector": None, "data": []}

    def test_picks_most_common_sector_when_no_param(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN", "address": "1 Main St, Fresno, CA"},
            {"sector": "RN", "address": "2 Main St, Davis,  CA"},
            {"sector": "Dentist", "address": "3 Main St, Fresno, CA"},
        ])
        data = client.get("/api/stats/medicaid-top-sector-cities").get_json()
        assert data["sector"] == "Registered Nurse"

    def test_respects_sector_param_when_present(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN", "address": "1 Main St, Fresno, CA"},
            {"sector": "RN", "address": "2 Main St, Fresno, CA"},
            {"sector": "Dentist", "address": "3 Main St, Davis, CA"},
        ])
        data = client.get(
            "/api/stats/medicaid-top-sector-cities?sector=Dentist"
        ).get_json()
        assert data["sector"] == "Dentist"
        assert [d["label"] for d in data["data"]] == ["Davis"]

    def test_unknown_sector_param_falls_back_to_top(self, client, monkeypatch):
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN", "address": "1 Main St, Fresno, CA"},
        ])
        data = client.get(
            "/api/stats/medicaid-top-sector-cities?sector=NotASector"
        ).get_json()
        assert data["sector"] == "Registered Nurse"

    def test_city_total_reflects_all_sector_records(self, client, monkeypatch):
        """`city_total` should count every record in that city across ALL
        sectors, used as denominator for "X% of Fresno are RNs" calculation."""
        monkeypatch.setattr(routes_datasets, "_medicaid_entities", lambda: [
            {"sector": "RN",      "address": "1 Main St, Fresno, CA"},
            {"sector": "Dentist", "address": "2 Main St, Fresno, CA"},
            {"sector": "RN",      "address": "3 Main St, Davis, CA"},
        ])
        data = client.get(
            "/api/stats/medicaid-top-sector-cities?sector=Registered Nurse"
        ).get_json()
        by_city = {d["label"]: d for d in data["data"]}
        # Fresno has 2 records total (1 RN + 1 Dentist), 1 of which is RN
        assert by_city["Fresno"]["value"] == 1
        assert by_city["Fresno"]["city_total"] == 2
        # Davis has 1 record, an RN
        assert by_city["Davis"]["value"] == 1
        assert by_city["Davis"]["city_total"] == 1
