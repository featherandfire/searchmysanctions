"""
Data layer: index fetching, entity loading, helpers.
"""

import csv
import io
import json
import logging
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import cache as l2

logger = logging.getLogger(__name__)

INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"

_cache = {}        # legacy in-process index cache — retained as a same-process
                   # micro-cache to avoid hammering Redis on a per-request basis
_entity_cache = {} # dataset_name -> list of flat dicts  (L1)

DEFAULT_SEARCH_DATASETS = [
    "us_ofac_sdn", "us_ofac_cons", "un_sc_sanctions", "eu_sanctions_map",
    "gb_hmt_sanctions", "gb_fcdo_sanctions", "ca_dfatd_sema_sanctions",
    "au_dfat_sanctions", "ch_seco_sanctions", "jp_mof_sanctions",
    "ua_nsdc_sanctions", "il_mod_crypto", "us_fbi_lazarus_crypto", "ransomwhere",
]

# Defined once at module level — avoids allocating a new set for every entity parsed
_NESTED_KEYS = frozenset({
    "sanctions", "holder", "addressEntity", "ownershipAsset",
    "directorshipOrganization", "unknownLinks", "asset",
})


def fetch_index():
    """
    Return the OpenSanctions dataset index.

    Lookup order:
      1. In-process micro-cache (avoids a Redis round-trip per request)
      2. Redis L2 (TTL = cache.TTL["index"], shared across machines)
      3. Origin fetch, then warm both layers
    """
    # 1. Same-process: serves the index for the lifetime of one warmed L1 entry
    cached = _cache.get("index")
    if cached is not None and time.monotonic() - _cache.get("index_ts", 0) < l2.TTL["index"]:
        return cached

    # 2. Shared L2 (Redis)
    shared = l2.get("index", "opensanctions")
    if shared is not None:
        _cache["index"] = shared
        _cache["index_ts"] = time.monotonic()
        return shared

    # 3. Origin
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(INDEX_URL, timeout=30, context=ctx) as resp:
            data = json.load(resp)
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(INDEX_URL, timeout=30, context=ctx) as resp:
            data = json.load(resp)

    l2.set("index", "opensanctions", data)
    _cache["index"] = data
    _cache["index_ts"] = time.monotonic()
    return data


def fmt_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def visible_datasets(datasets):
    return [d for d in datasets if not d.get("hidden") and not d.get("deprecated")]


def serialize_dataset(ds):
    pub = ds.get("publisher") or {}
    return {
        "name": ds.get("name"),
        "title": ds.get("title"),
        "type": ds.get("type"),
        "summary": ds.get("summary"),
        "description": ds.get("description"),
        "url": ds.get("url"),
        "tags": ds.get("tags", []),
        "collections": ds.get("collections", []),
        "entity_count": ds.get("entity_count", 0),
        "target_count": ds.get("target_count", 0),
        "thing_count": ds.get("thing_count", 0),
        "result": ds.get("result"),
        "updated_at": fmt_date(ds.get("updated_at")),
        "last_change": fmt_date(ds.get("last_change")),
        "frequency": ds.get("coverage", {}).get("frequency"),
        "publisher_name": pub.get("name"),
        "publisher_country": pub.get("country"),
        "publisher_country_label": pub.get("country_label"),
        "publisher_official": pub.get("official"),
        "resources": [
            {
                "name": r.get("name"),
                "url": r.get("url"),
                "size": r.get("size", 0),
                "title": r.get("title"),
                "mime_type_label": r.get("mime_type_label"),
            }
            for r in ds.get("resources", [])
        ],
    }


def _http_get(url, timeout=120):
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8")


def _first(lst):
    return lst[0] if lst else None


def _join(lst):
    return "; ".join(str(x) for x in lst if x) if lst else None


def _flat_entity(record):
    """Flatten a targets.nested.json record into a complete, display-ready dict."""
    props = record.get("properties", {})
    row = {
        "id":          record.get("id"),
        "caption":     record.get("caption"),
        "schema":      record.get("schema"),
        "datasets":    _join(record.get("datasets", [])),
        "first_seen":  (record.get("first_seen")  or "")[:10] or None,
        "last_seen":   (record.get("last_seen")   or "")[:10] or None,
        "last_change": (record.get("last_change") or "")[:10] or None,
    }

    for k, v in props.items():
        if k in _NESTED_KEYS:
            continue
        if isinstance(v, list):
            scalars = [x for x in v if not isinstance(x, dict)]
            if scalars:
                row[k] = _join(scalars)
            entities = [x for x in v if isinstance(x, dict)]
            if entities:
                row[k] = _join([e.get("caption") or _first(
                    e.get("properties", {}).get("name", [])
                ) for e in entities])
        elif not isinstance(v, dict):
            row[k] = v

    # Sanction sub-entities
    sanctions = props.get("sanctions", [])
    s_fields = {
        "authority": [], "authorityId": [], "program": [], "programId": [],
        "country": [], "startDate": [], "endDate": [], "modifiedAt": [],
        "sourceUrl": [], "summary": [], "status": [], "duration": [],
        "listingDate": [], "reason": [],
    }
    for s in sanctions:
        sp = s.get("properties", {}) if isinstance(s, dict) else {}
        for field in s_fields:
            s_fields[field].extend(sp.get(field, []))

    for field, vals in s_fields.items():
        unique = list(dict.fromkeys(v for v in vals if v))
        if unique:
            row[f"sanction_{field}"] = _join(unique)

    # Holder / wallet owner
    holders = props.get("holder", [])
    if holders:
        h = holders[0] if isinstance(holders[0], dict) else {}
        hp = h.get("properties", {})
        row["holder"] = h.get("caption") or _first(hp.get("name", []))
        aliases = hp.get("alias", [])
        if aliases:
            row["holder_alias"] = _join(aliases)

    # Address entities
    addr_entities = props.get("addressEntity", [])
    if addr_entities:
        parts = []
        for ae in addr_entities:
            ap = ae.get("properties", {}) if isinstance(ae, dict) else {}
            full = _first(ap.get("full", [])) or ae.get("caption", "")
            if full:
                parts.append(full)
        if parts:
            existing = row.get("address", "")
            combined = "; ".join(filter(None, [existing] + parts))
            if combined:
                row["address_full"] = combined

    return {k: v for k, v in row.items() if v is not None and v != ""}


def _stream_ndjson(url, timeout=120):
    """Stream and parse NDJSON line by line to avoid loading the full file into RAM."""
    ctx = ssl.create_default_context()
    records = []

    def _parse_stream(resp):
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                records.append(_flat_entity(json.loads(line)))
            except Exception:
                continue

    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            _parse_stream(resp)
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        records.clear()
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            _parse_stream(resp)

    return records


def _get_entities(ds_name):
    # L1 hit
    if ds_name in _entity_cache:
        return _entity_cache[ds_name]

    # L2 hit — warm L1 from disk, skip network fetch
    cached = l2.get("entity", ds_name)
    if cached is not None:
        logger.debug("L2 hit: entity/%s (%d records)", ds_name, len(cached))
        _entity_cache[ds_name] = cached
        return cached

    # L3 — fetch from origin
    logger.info("L2 miss: fetching entity/%s from origin", ds_name)
    index = fetch_index()
    ds = next((d for d in index["datasets"] if d["name"] == ds_name), None)
    if not ds:
        return []

    nested = next((r for r in ds.get("resources", []) if r["name"] == "targets.nested.json"), None)
    csv_r  = next((r for r in ds.get("resources", []) if r["name"] == "targets.simple.csv"), None)

    if nested:
        records = _stream_ndjson(nested["url"])
    elif csv_r:
        raw = _http_get(csv_r["url"])
        records = list(csv.DictReader(io.StringIO(raw)))
    else:
        return []

    # Populate both cache layers
    l2.set("entity", ds_name, records)
    _entity_cache[ds_name] = records
    logger.info("L2 set: entity/%s (%d records)", ds_name, len(records))
    return records


def _get_entities_batch(ds_names, max_workers=8):
    """Load multiple datasets in parallel, returning {name: rows} dict."""
    results = {}
    uncached = [n for n in ds_names if n not in _entity_cache]

    # Return cached datasets immediately
    for n in ds_names:
        if n in _entity_cache:
            results[n] = _entity_cache[n]

    if not uncached:
        return results

    workers = min(max_workers, len(uncached))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_get_entities, n): n for n in uncached}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception:
                results[name] = []

    return results
