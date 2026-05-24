"""
Tests for routes/datasets.py — pure helpers + the warm-cache contracts that
prevent Cloudflare 524 timeouts when the L1/L2 entity cache is cold.
"""
import pytest

import cache as l2
from routes import datasets as routes_datasets
from routes.datasets import (
    _city_from_address,
    _zip_from_address,
    _normalize_sector,
    _warm_medicaid_names,
    _medicaid_entities,
)


# ── _city_from_address ───────────────────────────────────────────────────────

class TestCityFromAddress:
    def test_returns_none_for_empty(self):
        assert _city_from_address("") is None
        assert _city_from_address(None) is None

    def test_trailing_state_with_zip(self):
        assert _city_from_address("123 Main St, Fresno, CA 93701") == "Fresno"

    def test_trailing_state_without_zip(self):
        assert _city_from_address("123 Main St, Fresno, CA") == "Fresno"

    def test_comma_separated_no_state(self):
        assert _city_from_address("123 Main St, Fresno, 93701") == "Fresno"

    def test_inline_state_mid_string(self):
        # State appears mid-string with trailing non-zip text → branch 3 fires
        assert _city_from_address("Fresno CA Suite 200") == "Fresno"

    def test_manual_patch_lookup(self):
        assert _city_from_address("621 E. Cypress Ave.") == "Glendora"

    def test_rejects_pure_digit_city(self):
        assert _city_from_address("123, 12345, CA") is None

    def test_rejects_full_state_name_as_city(self):
        # A street segment ending in a state name shouldn't be parsed as the city
        assert _city_from_address("California, CA") is None


# ── _zip_from_address ────────────────────────────────────────────────────────

class TestZipFromAddress:
    def test_returns_none_for_empty(self):
        assert _zip_from_address("") is None
        assert _zip_from_address(None) is None

    def test_five_digit_zip(self):
        assert _zip_from_address("123 Main St, Fresno, CA 93701") == "93701"

    def test_zip_plus_four(self):
        assert _zip_from_address("123 Main St 93701-1234") == "93701"

    def test_no_zip_returns_none(self):
        assert _zip_from_address("123 Main St, Fresno, CA") is None


# ── _normalize_sector ────────────────────────────────────────────────────────

class TestNormalizeSector:
    def test_known_alias(self):
        assert _normalize_sector("RN") == "Registered Nurse"

    def test_known_alias_case_insensitive(self):
        assert _normalize_sector("  rn ") == "Registered Nurse"

    def test_unknown_passes_through_stripped(self):
        assert _normalize_sector("  Surgeon  ") == "Surgeon"


# ── _warm_medicaid_names ─────────────────────────────────────────────────────

class TestWarmMedicaidNames:
    """
    The contract: only return dataset names that are already in L1 or L2 cache.
    A miss here would trigger a cold network fetch in the request handler and
    cause Cloudflare 524 timeouts.
    """

    def _fake_index(self, names):
        return {
            "datasets": [
                {
                    "name": n,
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.nested.json"}],
                }
                for n in names
            ]
        }

    def test_returns_empty_when_nothing_warm(self, monkeypatch):
        monkeypatch.setattr(routes_datasets, "fetch_index",
                            lambda: self._fake_index(["us_ca_med", "us_ny_med"]))
        monkeypatch.setattr(routes_datasets, "_entity_cache", {})
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)

        assert _warm_medicaid_names() == []

    def test_returns_only_warm_l1_entries(self, monkeypatch):
        monkeypatch.setattr(routes_datasets, "fetch_index",
                            lambda: self._fake_index(["us_ca_med", "us_ny_med"]))
        monkeypatch.setattr(routes_datasets, "_entity_cache", {"us_ca_med": []})
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)

        assert _warm_medicaid_names() == ["us_ca_med"]

    def test_returns_l2_entries_when_l1_cold(self, monkeypatch):
        monkeypatch.setattr(routes_datasets, "fetch_index",
                            lambda: self._fake_index(["us_ca_med", "us_ny_med"]))
        monkeypatch.setattr(routes_datasets, "_entity_cache", {})

        def fake_exists(source, ident, **kw):
            return ident == "us_ny_med"
        monkeypatch.setattr(l2, "exists", fake_exists)

        assert _warm_medicaid_names() == ["us_ny_med"]

    def test_skips_datasets_without_targets_resource(self, monkeypatch):
        index = {
            "datasets": [
                {
                    "name": "us_xx_med",
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "summary.txt"}],  # wrong resource
                },
                {
                    "name": "us_yy_med",
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.simple.csv"}],
                },
            ]
        }
        monkeypatch.setattr(routes_datasets, "fetch_index", lambda: index)
        monkeypatch.setattr(routes_datasets, "_entity_cache", {"us_xx_med": [], "us_yy_med": []})
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)

        assert _warm_medicaid_names() == ["us_yy_med"]

    def test_skips_hidden_and_deprecated(self, monkeypatch):
        index = {
            "datasets": [
                {
                    "name": "hidden_ds",
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.nested.json"}],
                    "hidden": True,
                },
                {
                    "name": "deprecated_ds",
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.nested.json"}],
                    "deprecated": True,
                },
                {
                    "name": "live_ds",
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.nested.json"}],
                },
            ]
        }
        monkeypatch.setattr(routes_datasets, "fetch_index", lambda: index)
        monkeypatch.setattr(routes_datasets, "_entity_cache",
                            {"hidden_ds": [], "deprecated_ds": [], "live_ds": []})
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)

        assert _warm_medicaid_names() == ["live_ds"]


# ── _medicaid_entities — the function the user highlighted ───────────────────

class TestMedicaidEntities:
    """
    Contract: never trigger a cold fetch. Use only L1/L2-warm datasets.
    Returns [] when nothing is warm rather than blocking on origin.
    """

    def _stub_index(self, monkeypatch, names):
        monkeypatch.setattr(routes_datasets, "fetch_index", lambda: {
            "datasets": [
                {
                    "name": n,
                    "tags": ["sector.usmed.debarment"],
                    "resources": [{"name": "targets.nested.json"}],
                }
                for n in names
            ]
        })

    def test_returns_empty_when_nothing_warm(self, client, monkeypatch):
        self._stub_index(monkeypatch, ["us_ca_med", "us_ny_med"])
        monkeypatch.setattr(routes_datasets, "_entity_cache", {})
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)
        monkeypatch.setattr(routes_datasets, "_get_entities",
                            lambda *a, **kw: pytest.fail("must not fetch when cold"))

        # _medicaid_entities is called inside a request handler — exercise via /api/stats/medicaid-by-schema
        resp = client.get("/api/stats/medicaid-by-schema?datasets=__never_set__")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_explicit_datasets_param_filters_to_warm_only(self, client, monkeypatch):
        # User asks for two datasets; only one is warm → only that one is loaded
        self._stub_index(monkeypatch, ["us_ca_med", "us_ny_med"])
        monkeypatch.setattr(routes_datasets, "_entity_cache",
                            {"us_ca_med": [{"schema": "Person"}]})

        def fake_exists(source, ident, **kw):
            return ident == "us_ca_med"
        monkeypatch.setattr(l2, "exists", fake_exists)

        called_with = []
        def fake_single(ds):
            called_with.append(ds)
            return [{"schema": "Person"}]
        monkeypatch.setattr(routes_datasets, "_get_entities", fake_single)

        resp = client.get("/api/stats/medicaid-by-schema?datasets=us_ca_med,us_ny_med")
        assert resp.status_code == 200
        # Only the warm one was actually fetched
        assert called_with == ["us_ca_med"]
        assert resp.get_json() == [{"label": "Person", "value": 1}]

    def test_falls_back_to_default_when_index_has_no_warm_datasets(self, client, monkeypatch):
        """
        Third branch: no ?datasets= param, _warm_medicaid_names() returns [],
        but the default dataset itself is warm in L1 — use it rather than
        returning [].
        """
        self._stub_index(monkeypatch, [])  # empty index → no warm names
        monkeypatch.setattr(
            routes_datasets, "_entity_cache",
            {"us_ca_med_exclusions": [{"schema": "Person"}, {"schema": "Organization"}]},
        )
        monkeypatch.setattr(l2, "exists", lambda *a, **kw: False)

        called_with = []
        def fake_single(ds):
            called_with.append(ds)
            return [{"schema": "Person"}, {"schema": "Organization"}]
        monkeypatch.setattr(routes_datasets, "_get_entities", fake_single)

        resp = client.get("/api/stats/medicaid-by-schema")
        assert resp.status_code == 200
        assert called_with == ["us_ca_med_exclusions"]
        # Counter insertion order preserved when values tie (stable sort)
        assert resp.get_json() == [
            {"label": "Person",       "value": 1},
            {"label": "Organization", "value": 1},
        ]


# ── /api/datasets ────────────────────────────────────────────────────────────

class TestDatasetsRoutes:
    SAMPLE_INDEX = {
        "datasets": [
            {
                "name": "a", "title": "A", "type": "source",
                "summary": "alpha", "tags": ["t1"],
                "entity_count": 100, "publisher": {"country": "us", "name": "Pub A"},
            },
            {
                "name": "b", "title": "B", "type": "collection",
                "summary": "beta", "tags": ["t1", "t2"],
                "entity_count": 50, "publisher": {"country": "gb", "name": "Pub B"},
            },
            {
                "name": "hidden_one", "title": "Hidden", "type": "source",
                "hidden": True, "entity_count": 999,
            },
        ]
    }

    @pytest.fixture(autouse=True)
    def _stub_index(self, monkeypatch):
        monkeypatch.setattr(routes_datasets, "fetch_index",
                            lambda: self.SAMPLE_INDEX)

    def test_list_datasets_excludes_hidden(self, client):
        resp = client.get("/api/datasets")
        assert resp.status_code == 200
        names = [d["name"] for d in resp.get_json()]
        assert names == ["a", "b"]
        assert "hidden_one" not in names

    def test_dataset_detail_found(self, client):
        resp = client.get("/api/dataset/a")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "a"

    def test_dataset_detail_404(self, client):
        resp = client.get("/api/dataset/nope")
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Not found"}

    def test_search_no_query_returns_all_visible(self, client):
        resp = client.get("/api/search")
        names = sorted(d["name"] for d in resp.get_json())
        assert names == ["a", "b"]

    def test_search_by_tag(self, client):
        resp = client.get("/api/search?q=t2&field=tag")
        names = [d["name"] for d in resp.get_json()]
        assert names == ["b"]

    def test_search_by_country(self, client):
        resp = client.get("/api/search?q=us&field=country")
        names = [d["name"] for d in resp.get_json()]
        assert names == ["a"]

    def test_search_by_type(self, client):
        resp = client.get("/api/search?q=collection&field=type")
        names = [d["name"] for d in resp.get_json()]
        assert names == ["b"]

    def test_search_full_text(self, client):
        resp = client.get("/api/search?q=alpha")
        names = [d["name"] for d in resp.get_json()]
        assert names == ["a"]

    def test_tags_sorted_by_count_desc(self, client):
        resp = client.get("/api/tags")
        # t1 appears in both visible datasets, t2 only in one
        assert resp.get_json() == [["t1", 2], ["t2", 1]]


# ── /api/stats — aggregate stats for Visual Statistics view ──────────────────

class TestStatsRoute:
    """
    Sample index covers every branch:
      - sources vs collections
      - success vs non-success result
      - publishers with / without country
      - hidden datasets (must be excluded)
      - empty tag lists
    """
    SAMPLE_INDEX = {
        "run_time": "2025-01-15T10:00:00Z",
        "datasets": [
            {"name": "a", "title": "A", "type": "source", "result": "success",
             "entity_count": 1000, "target_count": 800, "tags": ["sanctions"],
             "publisher": {"country": "us"}},
            {"name": "b", "title": "B", "type": "source", "result": "success",
             "entity_count": 500,  "target_count": 400, "tags": ["sanctions", "pep"],
             "publisher": {"country": "us"}},
            {"name": "c", "title": "C", "type": "source", "result": "warning",
             "entity_count": 200,  "target_count": 100, "tags": ["pep"],
             "publisher": {"country": "gb"}},
            {"name": "d", "title": "D", "type": "collection", "result": "success",
             "entity_count": 1700, "target_count": 1300, "tags": ["all"],
             "publisher": {"country": "us"}},  # collection publisher NOT counted
            {"name": "e", "title": "E", "type": "source", "result": None,
             "entity_count": 50,   "target_count": 50, "tags": [],
             "publisher": {}},  # no country → excluded from country counts
            {"name": "hidden", "title": "H", "type": "source", "result": "success",
             "entity_count": 9999, "target_count": 9999, "hidden": True,
             "tags": ["sanctions"], "publisher": {"country": "fr"}},
        ],
    }

    @pytest.fixture(autouse=True)
    def _stub_index(self, monkeypatch):
        monkeypatch.setattr(routes_datasets, "fetch_index", lambda: self.SAMPLE_INDEX)

    def test_totals_exclude_hidden(self, client):
        s = client.get("/api/stats").get_json()
        # 6 datasets in index, 1 hidden → 5 visible
        assert s["total"] == 5
        # entity/target sums exclude the hidden 9999s
        assert s["total_entities"] == 1000 + 500 + 200 + 1700 + 50
        assert s["total_targets"] == 800 + 400 + 100 + 1300 + 50

    def test_source_vs_collection_split(self, client):
        s = client.get("/api/stats").get_json()
        assert s["sources"] == 4         # a, b, c, e
        assert s["collections"] == 1     # d

    def test_errors_classified_correctly(self, client):
        """`result` of None / "" / "success" → not an error; anything else → error."""
        s = client.get("/api/stats").get_json()
        # Only c (result="warning") qualifies. e (None) and d/a/b (success) don't.
        assert s["errors"] == 1

    def test_country_counts_only_include_source_publishers(self, client):
        s = client.get("/api/stats").get_json()
        # Sources with country: a(us), b(us), c(gb). e has empty publisher.
        # d is a collection — its `us` does NOT contribute.
        # Hidden's `fr` is excluded.
        assert dict(s["top_countries"]) == {"us": 2, "gb": 1}
        assert s["countries"] == 2

    def test_top_datasets_sorted_by_entity_count_desc(self, client):
        s = client.get("/api/stats").get_json()
        names = [d["name"] for d in s["top_datasets"]]
        assert names == ["d", "a", "b", "c", "e"]

    def test_top_datasets_shape(self, client):
        s = client.get("/api/stats").get_json()
        first = s["top_datasets"][0]
        assert set(first.keys()) == {"name", "title", "entity_count"}
        assert first == {"name": "d", "title": "D", "entity_count": 1700}

    def test_top_tags_counts_across_all_visible(self, client):
        s = client.get("/api/stats").get_json()
        # Visible: a[sanctions], b[sanctions,pep], c[pep], d[all], e[]
        # → sanctions=2, pep=2, all=1
        assert dict(s["top_tags"]) == {"sanctions": 2, "pep": 2, "all": 1}

    def test_runtime_echoed_from_index(self, client):
        s = client.get("/api/stats").get_json()
        assert s["run_time"] == "2025-01-15T10:00:00Z"


class TestStatsCaps:
    """Top-N truncation: top_datasets ≤ 15, top_countries ≤ 20, top_tags ≤ 30."""

    @pytest.fixture(autouse=True)
    def _stub_large_index(self, monkeypatch):
        # 25 source datasets, each with a distinct country and 2 distinct tags →
        # 25 candidates for top_datasets, 25 for top_countries, 50 for top_tags
        datasets = [
            {"name": f"ds_{i:02d}", "title": f"DS {i}", "type": "source",
             "result": "success",
             "entity_count": (25 - i) * 100,  # descending entity counts
             "target_count": (25 - i) * 50,
             "tags": [f"tag_a_{i}", f"tag_b_{i}"],
             "publisher": {"country": f"c{i:02d}"}}
            for i in range(25)
        ]
        monkeypatch.setattr(routes_datasets, "fetch_index",
                            lambda: {"datasets": datasets, "run_time": ""})

    def test_top_datasets_capped_at_15(self, client):
        s = client.get("/api/stats").get_json()
        assert len(s["top_datasets"]) == 15
        # First item has the highest entity_count (ds_00 → 2500)
        assert s["top_datasets"][0]["name"] == "ds_00"

    def test_top_countries_capped_at_20(self, client):
        s = client.get("/api/stats").get_json()
        assert len(s["top_countries"]) == 20

    def test_top_tags_capped_at_30(self, client):
        s = client.get("/api/stats").get_json()
        # 50 distinct tags exist → response truncated to 30
        assert len(s["top_tags"]) == 30
