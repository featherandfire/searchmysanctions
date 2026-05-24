"""
Tests for routes/entity_search.py — the cross-dataset fuzzy search endpoint
behind the Entity Search view, and the dataset-list endpoint that populates
its sidebar.
"""
import pytest

from routes import entity_search as routes_es


# ── /api/entity-search ───────────────────────────────────────────────────────

class TestEntitySearch:
    def test_empty_query_returns_empty(self, client):
        resp = client.get("/api/entity-search?q=")
        assert resp.status_code == 200
        assert resp.get_json() == {"results": [], "searched": [], "total": 0}

    def test_one_character_query_returns_empty(self, client):
        """Min length is 2 chars — single-char queries would match too much."""
        resp = client.get("/api/entity-search?q=a")
        assert resp.get_json() == {"results": [], "searched": [], "total": 0}

    def test_whitespace_only_query_returns_empty(self, client):
        resp = client.get("/api/entity-search?q=%20%20")
        assert resp.get_json() == {"results": [], "searched": [], "total": 0}

    def test_explicit_datasets_param_overrides_default(self, client, monkeypatch):
        called = []
        def fake_get(ds):
            called.append(ds)
            return [{"caption": "Alice"}]
        monkeypatch.setattr(routes_es, "_get_entities", fake_get)

        client.get("/api/entity-search?q=alice&datasets=ds_a,ds_b")
        assert called == ["ds_a", "ds_b"]

    def test_no_datasets_param_uses_defaults(self, client, monkeypatch):
        # Replace the imported-into-module name so the route picks it up
        monkeypatch.setattr(routes_es, "DEFAULT_SEARCH_DATASETS",
                            ["only_default_1", "only_default_2"])
        called = []
        def fake_get(ds):
            called.append(ds)
            return []
        monkeypatch.setattr(routes_es, "_get_entities", fake_get)

        client.get("/api/entity-search?q=alice")
        assert called == ["only_default_1", "only_default_2"]

    def test_case_insensitive_substring_match(self, client, monkeypatch):
        rows = [
            {"caption": "Alice Smith",   "schema": "Person"},
            {"caption": "Bob Jones",     "schema": "Person"},
            {"caption": "ALICE Holdings","schema": "Organization"},
        ]
        monkeypatch.setattr(routes_es, "_get_entities", lambda ds: rows)

        data = client.get("/api/entity-search?q=alice&datasets=ds_a").get_json()
        captions = [r["caption"] for r in data["results"]]
        assert captions == ["Alice Smith", "ALICE Holdings"]
        assert data["total"] == 2

    def test_matches_against_any_field_value(self, client, monkeypatch):
        rows = [
            {"caption": "Some Person", "country": "Iran", "schema": "Person"},
            {"caption": "Another",     "country": "USA",  "schema": "Person"},
        ]
        monkeypatch.setattr(routes_es, "_get_entities", lambda ds: rows)

        # Match on the `country` field, not caption
        data = client.get("/api/entity-search?q=iran&datasets=ds_a").get_json()
        assert [r["caption"] for r in data["results"]] == ["Some Person"]

    def test_results_augmented_with_dataset_name(self, client, monkeypatch):
        def fake_get(ds):
            return [{"caption": f"hit in {ds}"}] if ds in ("us_ofac_sdn", "eu_sanctions") else []
        monkeypatch.setattr(routes_es, "_get_entities", fake_get)

        data = client.get("/api/entity-search?q=hit&datasets=us_ofac_sdn,eu_sanctions").get_json()
        ds_names = [r["_dataset"] for r in data["results"]]
        assert sorted(ds_names) == ["eu_sanctions", "us_ofac_sdn"]

    def test_searched_lists_only_datasets_that_returned_rows(self, client, monkeypatch):
        def fake_get(ds):
            return [{"caption": "match"}] if ds == "has_data" else []
        monkeypatch.setattr(routes_es, "_get_entities", fake_get)

        data = client.get("/api/entity-search?q=match&datasets=has_data,empty_ds").get_json()
        assert data["searched"] == ["has_data"]

    def test_results_truncated_to_500_but_total_reflects_full_count(self, client, monkeypatch):
        rows = [{"caption": f"match {i}"} for i in range(750)]
        monkeypatch.setattr(routes_es, "_get_entities", lambda ds: rows)

        data = client.get("/api/entity-search?q=match&datasets=big").get_json()
        assert len(data["results"]) == 500
        assert data["total"] == 750

    def test_skips_none_and_empty_field_values(self, client, monkeypatch):
        """Falsy values shouldn't cause str(None).lower() spurious matches."""
        rows = [
            {"caption": "Alice", "extra": None, "blank": ""},
            {"caption": "Bob",   "extra": None},
        ]
        monkeypatch.setattr(routes_es, "_get_entities", lambda ds: rows)

        # "none" would match str(None).lower() if falsy filtering broke
        data = client.get("/api/entity-search?q=none&datasets=ds_a").get_json()
        assert data["results"] == []


# ── /api/entity-search/datasets ──────────────────────────────────────────────

class TestEntitySearchDatasets:
    INDEX = {
        "datasets": [
            {"name": "us_ofac_sdn", "title": "OFAC SDN", "entity_count": 1000,
             "resources": [{"name": "targets.nested.json"}]},
            {"name": "eu_sanctions", "title": "EU", "entity_count": 500,
             "resources": [{"name": "targets.simple.csv"}]},
            {"name": "small_pep", "title": "PEP", "entity_count": 100,
             "resources": [{"name": "targets.nested.json"}]},
            {"name": "no_data", "title": "Stats Only", "entity_count": 0,
             "resources": [{"name": "summary.txt"}]},  # excluded — no usable resource
            {"name": "hidden_ds", "title": "Hidden", "entity_count": 9999,
             "hidden": True, "resources": [{"name": "targets.nested.json"}]},
            {"name": "deprecated_ds", "title": "Old", "entity_count": 9999,
             "deprecated": True, "resources": [{"name": "targets.nested.json"}]},
        ]
    }

    @pytest.fixture(autouse=True)
    def _stub_index(self, monkeypatch):
        monkeypatch.setattr(routes_es, "fetch_index", lambda: self.INDEX)
        monkeypatch.setattr(routes_es, "DEFAULT_SEARCH_DATASETS",
                            ["us_ofac_sdn", "eu_sanctions"])

    def test_excludes_hidden_and_deprecated(self, client):
        rows = client.get("/api/entity-search/datasets").get_json()
        names = [d["name"] for d in rows]
        assert "hidden_ds" not in names
        assert "deprecated_ds" not in names

    def test_excludes_datasets_without_usable_resource(self, client):
        rows = client.get("/api/entity-search/datasets").get_json()
        names = [d["name"] for d in rows]
        assert "no_data" not in names

    def test_sorted_by_entity_count_desc(self, client):
        rows = client.get("/api/entity-search/datasets").get_json()
        names = [d["name"] for d in rows]
        assert names == ["us_ofac_sdn", "eu_sanctions", "small_pep"]

    def test_default_flag_reflects_default_search_datasets(self, client):
        rows = {d["name"]: d for d in client.get("/api/entity-search/datasets").get_json()}
        assert rows["us_ofac_sdn"]["default"] is True
        assert rows["eu_sanctions"]["default"] is True
        assert rows["small_pep"]["default"] is False

    def test_response_shape_minimal(self, client):
        rows = client.get("/api/entity-search/datasets").get_json()
        # Only the four fields the sidebar needs — no leaking publisher data etc.
        for r in rows:
            assert set(r.keys()) == {"name", "title", "entity_count", "default"}
