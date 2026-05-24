"""
L2 persistent cache — Redis-backed, shared across all machines.

Architecture
------------
L1  in-memory dict      per-process, ~µs,  lost on restart  (data._entity_cache)
L2  Redis (this file)   shared, ~ms,        survives restart (this module)
L3  origin              OpenSanctions CDN / Etherscan API     (fallback)

Usage
-----
    import cache as l2

    data = l2.get("entity", "us_ofac_sdn")
    if data is None:
        data = fetch_from_origin(...)
        l2.set("entity", "us_ofac_sdn", data)

TTLs are defined per source-type in the TTL dict below. Redis evicts expired
keys automatically — no purge sweep is needed.
"""

import hashlib
import json
import logging
import os
import zlib

import redis

logger = logging.getLogger(__name__)

# Connection URL. Overridable via env var. Tests inject a fakeredis client
# directly by setting `cache._client`.
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Module-level client — initialised by init() or replaced by tests.
_client: redis.Redis = None

# ── TTLs (seconds) ────────────────────────────────────────────────────────────
TTL = {
    "entity":          86_400,   # 24 h  — OpenSanctions entity records
    "index":            3_600,   #  1 h  — OpenSanctions dataset index
    "sanctions_check":  3_600,   #  1 h  — per-address sanctions result
    "eth_balance":        120,   #  2 m  — wallet ETH balance
    "eth_price":          300,   #  5 m  — ETH/USD spot price
    "eth_txlist":         600,   # 10 m  — transaction list
    "eth_tokentx":        600,   # 10 m  — token transfers
    "census":         604_800,   #  7 d  — Census API responses
    "medicaid_stats":   3_600,   #  1 h  — pre-aggregated medicaid stat results
}
_DEFAULT_TTL = 3_600

# ── Compression ───────────────────────────────────────────────────────────────
# Large payloads are stored zlib-compressed. A b"z:" prefix marks compressed
# entries; smaller payloads are stored as plain JSON bytes.
_ZLIB_MARKER  = b"z:"
_COMPRESS_MIN = 4096


def _pack(data) -> bytes:
    raw = json.dumps(data, default=str).encode()
    if len(raw) >= _COMPRESS_MIN:
        return _ZLIB_MARKER + zlib.compress(raw, level=6)
    return raw


def _unpack(raw: bytes):
    if raw.startswith(_ZLIB_MARKER):
        return json.loads(zlib.decompress(raw[len(_ZLIB_MARKER):]))
    return json.loads(raw)


# ── Key ───────────────────────────────────────────────────────────────────────

def _make_key(source: str, identifier: str, params: dict = None) -> str:
    base = f"{source}:{identifier}"
    if params:
        h = hashlib.md5(
            json.dumps(params, sort_keys=True).encode()
        ).hexdigest()[:10]
        base += f":{h}"
    return base


# ── Connection ────────────────────────────────────────────────────────────────

def init():
    """
    Create the module-level Redis client. Safe to call multiple times; tests
    may inject a fakeredis instance before this is called, in which case init
    is a no-op.
    """
    global _client
    if _client is not None:
        return
    _client = redis.Redis.from_url(_REDIS_URL, decode_responses=False)
    _client.ping()
    logger.info("L2 cache initialised: %s", _REDIS_URL)


# ── Public API ────────────────────────────────────────────────────────────────

def get(source: str, identifier: str, params: dict = None):
    """Return cached value or None if missing / expired."""
    key = _make_key(source, identifier, params)
    raw = _client.get(key)
    if raw is None:
        _client.hincrby("cache:counters", f"{source}:miss", 1)
        return None
    _client.hincrby("cache:counters", f"{source}:hit", 1)
    _client.hincrby("cache:hits", key, 1)
    return _unpack(raw)


def set(source: str, identifier: str, data, params: dict = None, ttl: int = None):
    """
    Store value in L2. Overwrites any existing entry for the same key.

    A ttl of 0 or negative is treated as "expire immediately" — the entry is
    written with a 1ms PEX so a subsequent get() reads None. This preserves
    the historical contract from the SQLite-backed implementation.
    """
    key = _make_key(source, identifier, params)
    ttl = ttl if ttl is not None else TTL.get(source, _DEFAULT_TTL)
    payload = _pack(data)

    if ttl <= 0:
        _client.set(key, payload, px=1)
    else:
        _client.set(key, payload, ex=ttl)
    _client.hincrby("cache:counters", f"{source}:set", 1)


def exists(source: str, identifier: str, params: dict = None) -> bool:
    """Return True if a live (non-expired) entry exists."""
    key = _make_key(source, identifier, params)
    return bool(_client.exists(key))


def invalidate(source: str = None, identifier: str = None):
    """
    Remove entries.
      invalidate()                      — clear entire cache db
      invalidate(source="entity")       — clear all entity rows
      invalidate("entity","us_ofac_sdn")— clear one dataset (all param variants)
    """
    if source and identifier:
        pattern = f"{source}:{identifier}*"
        for k in _client.scan_iter(match=pattern, count=500):
            _client.delete(k)
    elif source:
        pattern = f"{source}:*"
        for k in _client.scan_iter(match=pattern, count=500):
            _client.delete(k)
    else:
        _client.flushdb()


def purge_expired() -> int:
    """
    No-op kept for API compatibility — Redis evicts expired keys natively
    and there's no equivalent to the SQLite "sweep stale rows" operation.
    """
    return 0


def stats() -> list[dict]:
    """
    Per-source rollup: entry count, hit/miss counts, hit rate, TTL.
    Backed by the cache:counters Redis hash that get/set maintain.
    """
    counters_raw = _client.hgetall("cache:counters") or {}
    counters: dict[str, dict[str, int]] = {}
    for k, v in counters_raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        src, event = key.rsplit(":", 1)
        counters.setdefault(src, {"hit": 0, "miss": 0, "set": 0})
        counters[src][event] = int(v)

    # Count live entries per source via SCAN
    source_counts: dict[str, int] = {}
    for key in _client.scan_iter(match="*:*", count=500):
        k = key.decode() if isinstance(key, bytes) else key
        # Skip internal keys (cache:counters, cache:hits)
        if k.startswith("cache:"):
            continue
        src = k.split(":", 1)[0]
        source_counts[src] = source_counts.get(src, 0) + 1

    # Sources with either live entries or recorded activity.
    # Use dict-view union to avoid shadowing by the module-level `set` function.
    all_sources = source_counts.keys() | counters.keys()

    rows = []
    for src in sorted(all_sources):
        c = counters.get(src, {"hit": 0, "miss": 0, "set": 0})
        hits = c["hit"]
        misses = c["miss"]
        total = hits + misses or 1
        rows.append({
            "source":               src,
            "ttl_configured_s":     TTL.get(src, _DEFAULT_TTL),
            "entries":              source_counts.get(src, 0),
            "total_hits_alltime":   hits,
            "hits_24h":             hits,    # Redis doesn't window; same as total
            "misses_24h":           misses,
            "hit_rate_24h_pct":     round(hits / total * 100, 1),
            "avg_age_min":          0,
            "max_age_min":          0,
            "min_ttl_remaining_min": 0,
        })
    return rows
