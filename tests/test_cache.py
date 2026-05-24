"""
Unit tests for cache.py — the L2 Redis cache layer.

Each test runs against a fresh fakeredis instance so they're fully isolated.
No Flask, no browser, no network.
"""
import time

import fakeredis
import pytest

import cache


@pytest.fixture
def db():
    """Fresh fakeredis-backed cache, torn down per test."""
    original = cache._client
    cache._client = fakeredis.FakeRedis(decode_responses=False)
    yield cache
    cache._client = original


# ── pack / unpack ────────────────────────────────────────────────────────────

def test_pack_small_payload_is_plain_json():
    packed = cache._pack({"hello": "world"})
    assert not packed.startswith(cache._ZLIB_MARKER)
    assert cache._unpack(packed) == {"hello": "world"}


def test_pack_large_payload_is_compressed():
    big = {"items": ["x" * 100] * 100}  # well over 4 KB
    packed = cache._pack(big)
    assert packed.startswith(cache._ZLIB_MARKER)
    # Compressed form should be meaningfully smaller than the JSON bytes
    import json as _json
    assert len(packed) < len(_json.dumps(big))
    assert cache._unpack(packed) == big


def test_pack_handles_non_json_native_types():
    """default=str lets us pack types json wouldn't normally serialise."""
    import datetime
    packed = cache._pack({"when": datetime.date(2025, 1, 1)})
    assert cache._unpack(packed) == {"when": "2025-01-01"}


# ── key construction ─────────────────────────────────────────────────────────

def test_make_key_without_params():
    assert cache._make_key("entity", "us_ofac_sdn") == "entity:us_ofac_sdn"


def test_make_key_with_params_appends_hash():
    k = cache._make_key("entity", "us_ofac_sdn", {"limit": 10})
    assert k.startswith("entity:us_ofac_sdn:")
    assert len(k) == len("entity:us_ofac_sdn:") + 10


def test_make_key_param_order_is_stable():
    a = cache._make_key("x", "y", {"a": 1, "b": 2})
    b = cache._make_key("x", "y", {"b": 2, "a": 1})
    assert a == b


def test_make_key_different_params_yield_different_keys():
    a = cache._make_key("x", "y", {"page": 1})
    b = cache._make_key("x", "y", {"page": 2})
    assert a != b


# ── get / set roundtrip ──────────────────────────────────────────────────────

def test_get_miss_returns_none(db):
    assert db.get("entity", "missing") is None


def test_set_then_get_roundtrip(db):
    db.set("entity", "us_ofac_sdn", {"hello": "world"})
    assert db.get("entity", "us_ofac_sdn") == {"hello": "world"}


def test_set_overwrites_existing(db):
    db.set("entity", "x", {"v": 1})
    db.set("entity", "x", {"v": 2})
    assert db.get("entity", "x") == {"v": 2}


def test_set_default_ttl_uses_source_table(db):
    db.set("entity", "x", "data")
    ttl = db._client.ttl("entity:x")
    # Redis returns seconds remaining; allow a small slack for elapsed time
    assert cache.TTL["entity"] - 1 <= ttl <= cache.TTL["entity"]


def test_set_unknown_source_falls_back_to_default_ttl(db):
    db.set("unknown_source", "x", "data")
    ttl = db._client.ttl("unknown_source:x")
    assert cache._DEFAULT_TTL - 1 <= ttl <= cache._DEFAULT_TTL


def test_get_expired_returns_none(db):
    db.set("entity", "x", "data", ttl=0)
    # ttl=0 sets px=1 (1ms) — sleep to let it expire
    time.sleep(0.05)
    assert db.get("entity", "x") is None


def test_get_increments_hit_counter(db):
    db.set("entity", "x", "data")
    db.get("entity", "x")
    db.get("entity", "x")
    db.get("entity", "x")
    hits = int(db._client.hget("cache:counters", "entity:hit"))
    assert hits == 3


# ── exists ───────────────────────────────────────────────────────────────────

def test_exists_true_when_live(db):
    db.set("entity", "x", "data")
    assert db.exists("entity", "x") is True


def test_exists_false_when_missing(db):
    assert db.exists("entity", "nope") is False


def test_exists_false_when_expired(db):
    db.set("entity", "x", "data", ttl=0)
    time.sleep(0.05)
    assert db.exists("entity", "x") is False


# ── invalidate ───────────────────────────────────────────────────────────────

def test_invalidate_specific_entry(db):
    db.set("entity", "a", 1)
    db.set("entity", "b", 2)
    db.invalidate(source="entity", identifier="a")
    assert db.get("entity", "a") is None
    assert db.get("entity", "b") == 2


def test_invalidate_by_source(db):
    db.set("entity", "a", 1)
    db.set("entity", "b", 2)
    db.set("census", "c", 3)
    db.invalidate(source="entity")
    assert db.get("entity", "a") is None
    assert db.get("entity", "b") is None
    assert db.get("census", "c") == 3


def test_invalidate_everything(db):
    db.set("entity", "a", 1)
    db.set("census", "c", 3)
    db.invalidate()
    assert db.get("entity", "a") is None
    assert db.get("census", "c") is None


# ── purge_expired ────────────────────────────────────────────────────────────

def test_purge_expired_is_a_noop(db):
    """Redis evicts expired keys natively — purge_expired returns 0 always."""
    db.set("entity", "fresh", "data", ttl=3600)
    db.set("entity", "stale", "data", ttl=0)
    time.sleep(0.05)
    assert db.purge_expired() == 0


# ── stats ────────────────────────────────────────────────────────────────────

def test_stats_empty_cache(db):
    assert db.stats() == []

def test_stats_reflects_entries_and_hit_rate(db):
    db.set("entity", "a", "data")
    db.get("entity", "a")            # hit
    db.get("entity", "a")            # hit
    db.get("entity", "missing")      # miss

    rows = db.stats()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "entity"
    assert row["entries"] == 1
    assert row["hits_24h"] == 2
    assert row["misses_24h"] == 1
    assert row["hit_rate_24h_pct"] == pytest.approx(66.7, abs=0.1)
    assert row["ttl_configured_s"] == cache.TTL["entity"]


def test_stats_groups_by_source(db):
    db.set("entity", "a", "data")
    db.set("census", "c", "data")
    sources = [r["source"] for r in db.stats()]
    assert sources == sorted(sources)
    assert set(sources) == {"entity", "census"}


def test_stats_includes_sources_with_only_misses(db):
    db.get("entity", "never_set")    # miss only — no entries
    rows = db.stats()
    assert len(rows) == 1
    assert rows[0]["source"] == "entity"
    assert rows[0]["entries"] == 0
    assert rows[0]["misses_24h"] == 1
    assert rows[0]["hits_24h"] == 0


# ── params interact with the rest of the API ─────────────────────────────────

def test_params_create_independent_cache_entries(db):
    db.set("entity", "x", "page1", params={"page": 1})
    db.set("entity", "x", "page2", params={"page": 2})
    assert db.get("entity", "x", params={"page": 1}) == "page1"
    assert db.get("entity", "x", params={"page": 2}) == "page2"
    assert db.get("entity", "x") is None  # no-params variant was never set


def test_invalidate_with_identifier_clears_all_param_variants(db):
    db.set("entity", "x", "a", params={"page": 1})
    db.set("entity", "x", "b", params={"page": 2})
    db.invalidate(source="entity", identifier="x")
    assert db.get("entity", "x", params={"page": 1}) is None
    assert db.get("entity", "x", params={"page": 2}) is None
