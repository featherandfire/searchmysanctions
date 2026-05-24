"""
Tests for data.py's L1/L2/L3 fetch layer: _get_entities and _get_entities_batch.

These are the hot paths every entity-returning endpoint depends on. The
flatten function _flat_entity is covered separately; this file tests the
cache-promotion behaviour around it.
"""
import pytest

import data
import cache as l2


@pytest.fixture(autouse=True)
def _clean_l1():
    """Ensure each test starts with an empty L1 cache and module state."""
    data._entity_cache.clear()
    yield
    data._entity_cache.clear()


# ── _get_entities — L1 → L2 → origin fallback ────────────────────────────────

class TestGetEntities:
    def test_l1_hit_skips_l2_and_origin(self, monkeypatch):
        rows = [{"caption": "Alice"}]
        data._entity_cache["us_ofac_sdn"] = rows

        # Tripwires — L2 and origin must never be called on L1 hit
        monkeypatch.setattr(l2, "get",
                            lambda *a, **kw: pytest.fail("L2 must not be touched on L1 hit"))
        monkeypatch.setattr(data, "fetch_index",
                            lambda: pytest.fail("origin must not be touched on L1 hit"))

        assert data._get_entities("us_ofac_sdn") is rows

    def test_l2_hit_promotes_to_l1(self, monkeypatch):
        rows = [{"caption": "Bob"}]
        monkeypatch.setattr(l2, "get",
                            lambda source, ident, **kw: rows if ident == "us_ofac_sdn" else None)
        monkeypatch.setattr(data, "fetch_index",
                            lambda: pytest.fail("origin must not be touched on L2 hit"))

        result = data._get_entities("us_ofac_sdn")
        assert result == rows
        # L1 was warmed
        assert data._entity_cache["us_ofac_sdn"] is rows

    def test_l3_nested_path_writes_to_both_caches(self, monkeypatch):
        # L1 + L2 cold → must fetch from origin via _stream_ndjson
        l2_writes = []
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set",
                            lambda source, ident, data_, **kw: l2_writes.append((source, ident, data_)))

        monkeypatch.setattr(data, "fetch_index", lambda: {"datasets": [
            {"name": "us_ofac_sdn", "resources": [
                {"name": "targets.nested.json", "url": "https://example.test/x.json"},
            ]},
        ]})

        records = [{"id": "1", "caption": "Alice"}]
        captured_url = []
        def fake_stream(url, **kw):
            captured_url.append(url)
            return records
        monkeypatch.setattr(data, "_stream_ndjson", fake_stream)

        result = data._get_entities("us_ofac_sdn")

        assert result is records
        assert captured_url == ["https://example.test/x.json"]
        assert l2_writes == [("entity", "us_ofac_sdn", records)]
        assert data._entity_cache["us_ofac_sdn"] is records

    def test_l3_csv_fallback_when_nested_missing(self, monkeypatch):
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set", lambda *a, **kw: None)
        monkeypatch.setattr(data, "fetch_index", lambda: {"datasets": [
            {"name": "ds_csv", "resources": [
                {"name": "targets.simple.csv", "url": "https://example.test/x.csv"},
            ]},
        ]})

        # Tripwire — nested loader must not be called
        monkeypatch.setattr(data, "_stream_ndjson",
                            lambda *a, **kw: pytest.fail("must not stream when no nested.json"))
        monkeypatch.setattr(data, "_http_get",
                            lambda url, **kw: "id,name\n1,Alice\n2,Bob\n")

        result = data._get_entities("ds_csv")
        assert result == [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
        assert data._entity_cache["ds_csv"] == result

    def test_dataset_not_in_index_returns_empty(self, monkeypatch):
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(data, "fetch_index", lambda: {"datasets": []})
        # Tripwire — must not fetch
        monkeypatch.setattr(data, "_stream_ndjson",
                            lambda *a, **kw: pytest.fail("must not fetch unknown dataset"))

        assert data._get_entities("does_not_exist") == []
        # Empty result is NOT cached (so a retry post-index-refresh can find it)
        assert "does_not_exist" not in data._entity_cache

    def test_dataset_with_no_usable_resource_returns_empty(self, monkeypatch):
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(data, "fetch_index", lambda: {"datasets": [
            {"name": "ds_no_resource", "resources": [
                {"name": "summary.txt", "url": "https://example.test/x.txt"},
            ]},
        ]})

        assert data._get_entities("ds_no_resource") == []
        assert "ds_no_resource" not in data._entity_cache

    def test_prefers_nested_when_both_resources_present(self, monkeypatch):
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set", lambda *a, **kw: None)
        monkeypatch.setattr(data, "fetch_index", lambda: {"datasets": [
            {"name": "ds_both", "resources": [
                {"name": "targets.simple.csv",  "url": "https://example.test/x.csv"},
                {"name": "targets.nested.json", "url": "https://example.test/x.json"},
            ]},
        ]})

        # Tripwire — CSV path must not run
        monkeypatch.setattr(data, "_http_get",
                            lambda *a, **kw: pytest.fail("must prefer nested.json over csv"))
        monkeypatch.setattr(data, "_stream_ndjson",
                            lambda url, **kw: [{"source": "nested"}])

        result = data._get_entities("ds_both")
        assert result == [{"source": "nested"}]


# ── _get_entities_batch — parallel fetch ─────────────────────────────────────

class TestGetEntitiesBatch:
    def test_empty_input_returns_empty_dict(self):
        assert data._get_entities_batch([]) == {}

    def test_all_l1_cached_skips_fetch(self, monkeypatch):
        data._entity_cache["a"] = [{"x": 1}]
        data._entity_cache["b"] = [{"y": 2}]

        # Tripwire — _get_entities must not be invoked on the L1-hot path
        monkeypatch.setattr(data, "_get_entities",
                            lambda *a, **kw: pytest.fail("must not refetch L1-hot datasets"))

        result = data._get_entities_batch(["a", "b"])
        assert result == {"a": [{"x": 1}], "b": [{"y": 2}]}

    def test_mixed_cached_and_uncached(self, monkeypatch):
        data._entity_cache["cached"] = [{"hit": True}]

        fetched_for = []
        def fake_get(ds):
            fetched_for.append(ds)
            return [{"name": ds}]
        monkeypatch.setattr(data, "_get_entities", fake_get)

        result = data._get_entities_batch(["cached", "uncached1", "uncached2"])

        assert result["cached"] == [{"hit": True}]
        assert result["uncached1"] == [{"name": "uncached1"}]
        assert result["uncached2"] == [{"name": "uncached2"}]
        # Only the uncached names were sent to _get_entities
        assert set(fetched_for) == {"uncached1", "uncached2"}

    def test_exception_in_one_fetch_does_not_break_batch(self, monkeypatch):
        def flaky(ds):
            if ds == "broken":
                raise RuntimeError("origin down")
            return [{"name": ds}]
        monkeypatch.setattr(data, "_get_entities", flaky)

        result = data._get_entities_batch(["good", "broken", "also_good"])
        assert result["good"] == [{"name": "good"}]
        assert result["also_good"] == [{"name": "also_good"}]
        # Broken one falls back to [] instead of failing the batch
        assert result["broken"] == []

    def test_worker_count_capped_by_uncached_count(self, monkeypatch):
        """A single uncached dataset shouldn't spin up 8 idle workers."""
        captured = {}
        # Replace ThreadPoolExecutor with one that records max_workers
        from concurrent.futures import ThreadPoolExecutor as RealExecutor

        class RecordingExecutor(RealExecutor):
            def __init__(self, max_workers=None, *a, **kw):
                captured["max_workers"] = max_workers
                super().__init__(max_workers=max_workers, *a, **kw)

        monkeypatch.setattr(data, "ThreadPoolExecutor", RecordingExecutor)
        monkeypatch.setattr(data, "_get_entities", lambda ds: [{"name": ds}])

        data._get_entities_batch(["only_one"], max_workers=8)
        assert captured["max_workers"] == 1

    def test_returns_results_keyed_by_dataset_name(self, monkeypatch):
        """Result dict keys must match input names even when fetches complete
        out of order (as_completed iteration)."""
        import time as _t

        def variable_delay(ds):
            # Reverse-order delay forces out-of-order completion
            order = {"first": 0.03, "second": 0.02, "third": 0.01}
            _t.sleep(order.get(ds, 0))
            return [{"name": ds}]
        monkeypatch.setattr(data, "_get_entities", variable_delay)

        result = data._get_entities_batch(["first", "second", "third"])
        assert result == {
            "first":  [{"name": "first"}],
            "second": [{"name": "second"}],
            "third":  [{"name": "third"}],
        }
