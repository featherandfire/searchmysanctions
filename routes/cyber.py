"""
Blueprint: cyber_bp
Routes: /api/cyber
Helpers: is_cyber_dataset, cyber_category
Constants: CYBER_TITLE_KEYWORDS, CYBER_DESC_KEYWORDS, CYBER_TAGS, CYBER_NAME_ALLOWLIST
"""

from flask import Blueprint, jsonify, request
from data import fetch_index, visible_datasets, serialize_dataset, _get_entities, _get_entities_batch, _entity_cache
import cache as l2
import db
from settings import ETHERSCAN_API_KEY

cyber_bp = Blueprint("cyber_bp", __name__)

# Keywords matched only against name + title + summary (not full description)
CYBER_TITLE_KEYWORDS = [
    "crypto", "cryptocurrency", "wallet", "bitcoin", "blockchain",
    "ransomware", "cyber", "hacking", "malware", "lazarus",
    "darknet", "digital currency", "virtual currency", "nft", "defi",
]
# Keywords that can also match the description when title/summary don't hit
CYBER_DESC_KEYWORDS = [
    "cryptocurrency wallet", "bitcoin address", "ransomware address",
    "cyber actor", "cyber attack", "crypto wallet",
]
CYBER_TAGS = {"sector.crypto"}
# Explicit name allowlist catches well-known datasets that might slip through
CYBER_NAME_ALLOWLIST = {
    "us_fbi_lazarus_crypto", "il_mod_crypto", "ransomwhere",
    "us_cyber_sanctions", "ofac_cyber",
}


def is_cyber_dataset(ds):
    if ds.get("name") in CYBER_NAME_ALLOWLIST:
        return True
    if CYBER_TAGS.intersection(set(ds.get("tags", []))):
        return True
    # Tight match: name + title + summary only
    short_text = " ".join([
        ds.get("name", ""),
        ds.get("title", ""),
        ds.get("summary", ""),
    ]).lower()
    if any(kw in short_text for kw in CYBER_TITLE_KEYWORDS):
        return True
    # Loose match: full description, but only for very specific phrases
    desc = (ds.get("description", "") or "").lower()
    if any(kw in desc for kw in CYBER_DESC_KEYWORDS):
        return True
    return False


def cyber_category(ds):
    """Return a human-readable threat category label."""
    short_text = " ".join([
        ds.get("name", ""),
        ds.get("title", ""),
        ds.get("summary", ""),
    ]).lower()
    tags = set(ds.get("tags", []))

    if "sector.crypto" in tags or any(k in short_text for k in ["wallet", "bitcoin", "blockchain", "digital currency", "virtual currency", "nft", "defi", "crypto"]):
        if any(k in short_text for k in ["ransomware", "ransom"]):
            return "ransomware"
        return "crypto"
    if any(k in short_text for k in ["ransomware", "ransom"]):
        return "ransomware"
    if any(k in short_text for k in ["lazarus", "dprk", "north korea"]):
        return "state-sponsored"
    if any(k in short_text for k in ["hacking", "hack", "malware", "cyber", "intrusion"]):
        return "cyber"
    if any(k in short_text for k in ["darknet", "dark web"]):
        return "darknet"
    return "other"


@cyber_bp.route("/api/cyber")
def api_cyber():
    index = fetch_index()
    datasets = visible_datasets(index["datasets"])
    results = [ds for ds in datasets if is_cyber_dataset(ds)]
    serialized = []
    for ds in results:
        s = serialize_dataset(ds)
        s["cyber_category"] = cyber_category(ds)
        serialized.append(s)
    # Sort: crypto first, then by entity count desc
    order = ["crypto", "ransomware", "state-sponsored", "cyber", "darknet", "other"]
    serialized.sort(key=lambda d: (order.index(d["cyber_category"]), -d.get("entity_count", 0)))

    total_entities = sum(d.get("entity_count", 0) for d in serialized)
    total_targets = sum(d.get("target_count", 0) for d in serialized)
    category_counts = {}
    for d in serialized:
        c = d["cyber_category"]
        category_counts[c] = category_counts.get(c, 0) + 1

    # Count OFAC SDN crypto wallet records — use L1/L2 cache so this is fast
    sdn_rows = _get_entities("us_ofac_sdn")
    sdn_crypto_count = sum(1 for r in sdn_rows if _is_crypto_entity(r))

    return jsonify({
        "datasets": serialized,
        "total_entities": total_entities,
        "total_targets": total_targets,
        "category_counts": category_counts,
        "sdn_crypto_count": sdn_crypto_count,
    })


# Datasets to scan for crypto wallet entities — cyber-specific lists + major
# sanctions lists that frequently include crypto addresses as linked records.
CRYPTO_SCAN_DATASETS = [
    "us_ofac_sdn", "us_ofac_cons", "un_sc_sanctions", "eu_sanctions_map",
    "gb_hmt_sanctions", "gb_fcdo_sanctions", "ch_seco_sanctions",
    "us_fbi_lazarus_crypto", "il_mod_crypto", "ransomwhere",
    "ua_nsdc_sanctions", "ca_dfatd_sema_sanctions",
]


def _is_crypto_entity(row):
    """Return True if the flattened entity record is a crypto wallet / address."""
    return (
        row.get("schema") == "CryptoWallet"
        or bool(row.get("publicKey"))
        or bool(row.get("currency"))
    )


@cyber_bp.route("/api/crypto-wallets")
def api_crypto_wallets():
    """
    Scan entity records across major sanctions datasets and return all
    CryptoWallet entries (schema=CryptoWallet or has publicKey/currency).
    Results are cached in _entity_cache via _get_entities.
    """
    extra = request.args.get("datasets", "")
    ds_names = list(dict.fromkeys(
        CRYPTO_SCAN_DATASETS + [d.strip() for d in extra.split(",") if d.strip()]
    ))

    results = []
    searched = []
    for name in ds_names:
        rows = _get_entities(name)
        if rows:
            searched.append(name)
        for row in rows:
            if _is_crypto_entity(row):
                results.append(dict(row, _dataset=name))

    # Sort: schema first (CryptoWallet on top), then by dataset
    results.sort(key=lambda r: (r.get("schema") != "CryptoWallet", r.get("_dataset", "")))

    return jsonify({"results": results, "searched": searched, "total": len(results)})


_COUNTRY_NAMES = {
    "af":"Afghanistan","al":"Albania","dz":"Algeria","ao":"Angola","am":"Armenia",
    "au":"Australia","at":"Austria","az":"Azerbaijan","bh":"Bahrain","by":"Belarus",
    "be":"Belgium","bz":"Belize","bo":"Bolivia","ba":"Bosnia","br":"Brazil",
    "bg":"Bulgaria","kh":"Cambodia","ca":"Canada","cf":"Cent. Africa","cn":"China",
    "co":"Colombia","cd":"Congo (DRC)","cu":"Cuba","cz":"Czechia","dk":"Denmark",
    "eg":"Egypt","et":"Ethiopia","fi":"Finland","fr":"France","de":"Germany",
    "gh":"Ghana","gr":"Greece","gt":"Guatemala","gy":"Guyana","hk":"Hong Kong",
    "hu":"Hungary","in":"India","id":"Indonesia","ir":"Iran","iq":"Iraq",
    "il":"Israel","it":"Italy","jm":"Jamaica","jp":"Japan","jo":"Jordan",
    "kz":"Kazakhstan","ke":"Kenya","kp":"North Korea","kr":"South Korea",
    "kw":"Kuwait","kg":"Kyrgyzstan","la":"Laos","lb":"Lebanon","ly":"Libya",
    "lt":"Lithuania","lu":"Luxembourg","my":"Malaysia","ml":"Mali","mt":"Malta",
    "mx":"Mexico","md":"Moldova","ma":"Morocco","mm":"Myanmar","na":"Namibia",
    "np":"Nepal","nl":"Netherlands","nz":"New Zealand","ni":"Nicaragua",
    "ng":"Nigeria","no":"Norway","pk":"Pakistan","ps":"Palestine","pa":"Panama",
    "pe":"Peru","ph":"Philippines","pl":"Poland","pt":"Portugal","qa":"Qatar",
    "ro":"Romania","ru":"Russia","sa":"Saudi Arabia","rs":"Serbia",
    "sl":"Sierra Leone","sg":"Singapore","so":"Somalia","za":"South Africa",
    "ss":"South Sudan","es":"Spain","lk":"Sri Lanka","sd":"Sudan","se":"Sweden",
    "ch":"Switzerland","sy":"Syria","tw":"Taiwan","tj":"Tajikistan","tz":"Tanzania",
    "th":"Thailand","tn":"Tunisia","tr":"Turkey","tm":"Turkmenistan","ug":"Uganda",
    "ua":"Ukraine","ae":"UAE","gb":"United Kingdom","us":"United States",
    "uz":"Uzbekistan","ve":"Venezuela","vn":"Vietnam","ye":"Yemen","zw":"Zimbabwe",
    "kn":"Saint Kitts & Nevis","ee":"Estonia","lv":"Latvia","ie":"Ireland",
    "vc":"Saint Vincent","dm":"Dominica","ag":"Antigua","lc":"Saint Lucia",
    "ky":"Cayman Islands","bm":"Bermuda","vg":"British Virgin Islands",
    "pa":"Panama","li":"Liechtenstein","mc":"Monaco","sm":"San Marino",
    "cv":"Cape Verde","mz":"Mozambique","rw":"Rwanda","ci":"Ivory Coast",
    "cm":"Cameroon","sn":"Senegal","tz":"Tanzania","zm":"Zambia",
    "ht":"Haiti","cr":"Costa Rica","ec":"Ecuador","uy":"Uruguay",
    "py":"Paraguay","bo":"Bolivia","sr":"Suriname","tt":"Trinidad & Tobago",
    "bb":"Barbados","bs":"Bahamas","jm":"Jamaica",
}


def _entity_country(row):
    """Best-effort country code from a flattened entity record."""
    for field in ("nationality", "countries", "jurisdiction", "registrationCountry", "country"):
        val = row.get(field, "")
        if val:
            return val.split(";")[0].strip().lower()
    return ""


@cyber_bp.route("/api/stats/crypto-by-country")
def api_crypto_by_country():
    """
    Count sanctioned entities WITH cryptocurrency addresses, grouped by country.
    Strategy:
      1. For each dataset, index all Person/Org entities by name → country.
      2. For each CryptoWallet, find the holder entity's country via that index.
      3. Also count any non-wallet entity that directly carries publicKey/currency fields.
    Only returns countries that could be resolved (skips unattributed records).
    """
    cached = l2.get("stats", "crypto-by-country")
    if cached is not None:
        return jsonify(cached)

    batch = _get_entities_batch(CRYPTO_SCAN_DATASETS)

    country_counts = {}

    for ds_name in CRYPTO_SCAN_DATASETS:
        rows = batch.get(ds_name, [])

        # Index name → country for every Person/Org in this dataset
        name_to_country = {}
        for row in rows:
            if row.get("schema") not in ("Person", "Organization", "Company", "LegalEntity"):
                continue
            name = (row.get("caption") or "").strip().lower()
            country = _entity_country(row)
            if name and country:
                name_to_country[name] = country

        for row in rows:
            if not _is_crypto_entity(row):
                continue

            # Prefer the entity's own country fields; fall back to holder lookup
            country = _entity_country(row)
            if not country:
                holder = (row.get("holder") or "").strip().lower()
                country = name_to_country.get(holder, "")

            if country:  # skip unresolvable records — they'd swamp the chart as "Unknown"
                country_counts[country] = country_counts.get(country, 0) + 1

    ranked = sorted(country_counts.items(), key=lambda x: -x[1])[:20]
    result = [{"label": _COUNTRY_NAMES.get(c, c.upper()), "value": n} for c, n in ranked]
    l2.set("stats", "crypto-by-country", result)
    return jsonify(result)


@cyber_bp.route("/api/stats/sdn-crypto-country")
def api_sdn_crypto_country():
    """
    Return CryptoWallet counts from OFAC SDN grouped by the holder's nationality.
    Cross-references wallet holder names against Person/Org entities in the same dataset.
    """
    cached = l2.get("stats", "sdn-crypto-country")
    if cached is not None:
        return jsonify(cached)

    rows = _get_entities("us_ofac_sdn")

    # Build caption → countries mapping from Person / Org entities
    name_to_country = {}
    for row in rows:
        if row.get("schema") in ("Person", "Organization", "Company", "LegalEntity"):
            name = (row.get("caption") or "").strip().lower()
            country = row.get("countries") or row.get("nationality") or ""
            if name and country:
                # take first country if semicolon-separated
                name_to_country[name] = country.split(";")[0].strip()

    country_counts = {}
    for row in rows:
        if not _is_crypto_entity(row):
            continue
        holder = (row.get("holder") or "").strip().lower()
        country = name_to_country.get(holder) or row.get("countries") or "Unknown"
        country_counts[country] = country_counts.get(country, 0) + 1

    ranked = sorted(country_counts.items(), key=lambda x: -x[1])[:20]
    result = [{"label": _COUNTRY_NAMES.get(c.lower(), c), "value": n} for c, n in ranked]
    l2.set("stats", "sdn-crypto-country", result)
    return jsonify(result)


@cyber_bp.route("/api/cyber-records")
def api_cyber_records():
    """Return all entity records from every cyber/crypto dataset."""
    index = fetch_index()
    datasets = visible_datasets(index["datasets"])
    cyber_ds_names = [ds["name"] for ds in datasets if is_cyber_dataset(ds)]

    results = []
    searched = []
    for name in cyber_ds_names:
        rows = _get_entities(name)
        if rows:
            searched.append(name)
        for row in rows:
            results.append(dict(row, _dataset=name))

    return jsonify({"results": results, "searched": searched, "total": len(results)})


def _cached_datasets():
    """
    Return only datasets that are already in L1 or L2 — never trigger a
    cold network fetch during a sanctions check request.
    """
    ready = []
    for ds_name in CRYPTO_SCAN_DATASETS:
        if ds_name in _entity_cache:
            ready.append((ds_name, _entity_cache[ds_name]))
            continue
        rows = l2.get("entity", ds_name)
        if rows is not None:
            _entity_cache[ds_name] = rows   # warm L1 while we're here
            ready.append((ds_name, rows))
    return ready


@cyber_bp.route("/api/sanctions-check")
def api_sanctions_check():
    """
    Check whether a crypto address appears on any sanctions list.
    Only scans datasets already in L1/L2 — never blocks on a cold fetch.
    Result cached in L2 for 1 hour.
    """
    address = (request.args.get("address") or "").strip().lower()
    if not address:
        return jsonify({"matches": [], "sanctioned": False, "checked_datasets": 0})

    cached = l2.get("sanctions_check", address)
    if cached is not None:
        return jsonify(cached)

    matches = []
    ready = _cached_datasets()
    for ds_name, rows in ready:
        for row in rows:
            pub = (row.get("publicKey") or "").strip().lower()
            cap = (row.get("caption")   or "").strip().lower()
            if address in (pub, cap):
                matches.append({
                    "dataset":            ds_name,
                    "caption":            row.get("caption", ""),
                    "holder":             row.get("holder", ""),
                    "currency":           row.get("currency", ""),
                    "schema":             row.get("schema", ""),
                    "sanction_program":   row.get("sanction_program", ""),
                    "sanction_authority": row.get("sanction_authority", ""),
                    "sanction_reason":    row.get("sanction_reason", ""),
                    "first_seen":         row.get("first_seen", ""),
                })

    result = {
        "matches": matches,
        "sanctioned": len(matches) > 0,
        "checked_datasets": len(ready),
        "total_datasets": len(CRYPTO_SCAN_DATASETS),
    }
    # Only cache if we scanned all datasets — partial results shouldn't persist
    if len(ready) == len(CRYPTO_SCAN_DATASETS):
        l2.set("sanctions_check", address, result)
    return jsonify(result)


@cyber_bp.route("/api/sanctions-check-batch", methods=["POST"])
def api_sanctions_check_batch():
    """
    Check a list of crypto addresses against all cached sanctions lists.
    Only scans datasets already in L1/L2 — never blocks on a cold fetch.
    Body: {"addresses": ["0x...", ...]}
    Returns: {"hits": {"0x...": {"datasets": [...], "holder": "..."}}}
    """
    body      = request.get_json(force=True) or {}
    addresses = {a.strip().lower() for a in (body.get("addresses") or []) if a}
    if not addresses:
        return jsonify({"hits": {}, "checked_datasets": 0})

    hits  = {}
    ready = _cached_datasets()
    for ds_name, rows in ready:
        for row in rows:
            pub = (row.get("publicKey") or "").strip().lower()
            cap = (row.get("caption")   or "").strip().lower()
            matched = addresses.intersection({pub, cap} - {""})
            for addr in matched:
                if addr not in hits:
                    hits[addr] = {"datasets": [], "holder": row.get("holder", ""), "currency": row.get("currency", "")}
                if ds_name not in hits[addr]["datasets"]:
                    hits[addr]["datasets"].append(ds_name)

    return jsonify({"hits": hits, "checked_datasets": len(ready), "total_datasets": len(CRYPTO_SCAN_DATASETS)})


@cyber_bp.route("/api/notes", methods=["GET"])
def api_notes_get():
    return jsonify(db.list_notes())


@cyber_bp.route("/api/notes", methods=["POST"])
def api_notes_post():
    body = request.get_json(force=True) or {}
    note = db.create_note(
        title=(body.get("title") or "").strip(),
        body=(body.get("body")  or "").strip(),
        tags=[t.strip() for t in (body.get("tags") or []) if t.strip()],
    )
    return jsonify(note), 201


@cyber_bp.route("/api/notes/<int:note_id>", methods=["DELETE"])
def api_notes_delete(note_id):
    db.delete_note(note_id)
    return jsonify({"ok": True})


@cyber_bp.route("/api/notes/<int:note_id>", methods=["PUT"])
def api_notes_put(note_id):
    body = request.get_json(force=True) or {}
    fields = {}
    if "title" in body: fields["title"] = (body["title"] or "").strip()
    if "body"  in body: fields["body"]  = (body["body"]  or "").strip()
    if "tags"  in body: fields["tags"]  = [t.strip() for t in (body["tags"] or []) if t.strip()]
    db.update_note(note_id, fields)
    return jsonify({"ok": True})


@cyber_bp.route("/api/address-history", methods=["GET"])
def api_address_history_get():
    return jsonify(db.list_address_history())


@cyber_bp.route("/api/address-history", methods=["POST"])
def api_address_history_post():
    """
    Log a searched address. Deduplicates by address — re-posting an address
    refreshes its `searched_at` and overwrites sanctions/label fields.
    Body: {
      "address": "0x...",
      "sanctioned": bool,
      "sanction_lists": ["OFAC SDN", ...],
      "label": "optional display name",
      "referred_from": "0x... (optional parent address)",
      "mode": "balance|txlist|tokentx"
    }
    """
    body    = request.get_json(force=True) or {}
    address = (body.get("address") or "").strip().lower()
    if not address:
        return jsonify({"ok": False, "error": "address required"}), 400

    entry = db.upsert_address_history(
        address=address,
        sanctioned=bool(body.get("sanctioned", False)),
        sanction_lists=body.get("sanction_lists") or [],
        label=(body.get("label") or "").strip(),
        referred_from=(body.get("referred_from") or "").strip().lower(),
        mode=body.get("mode") or "balance",
    )
    return jsonify(entry), 201


@cyber_bp.route("/api/address-history/<path:address>", methods=["DELETE"])
def api_address_history_delete(address):
    db.delete_address(address.strip().lower())
    return jsonify({"ok": True})


@cyber_bp.route("/api/address-history", methods=["DELETE"])
def api_address_history_clear():
    db.clear_address_history()
    return jsonify({"ok": True})
