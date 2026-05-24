"""
Tests for routes/cyber.py — pure helpers + the cached-only sanctions-check
contract that prevents cold network fetches during wallet screening.
"""
import pytest

import cache as l2
from routes import cyber as routes_cyber
from routes.cyber import (
    is_cyber_dataset,
    cyber_category,
    _is_crypto_entity,
    _entity_country,
    _cached_datasets,
)


# ── is_cyber_dataset ─────────────────────────────────────────────────────────

class TestIsCyberDataset:
    def test_name_allowlist_match(self):
        assert is_cyber_dataset({"name": "us_fbi_lazarus_crypto"}) is True

    def test_tag_match(self):
        assert is_cyber_dataset({"name": "x", "tags": ["sector.crypto"]}) is True

    def test_title_keyword_match(self):
        assert is_cyber_dataset({"name": "x", "title": "Cryptocurrency Sanctions"}) is True

    def test_summary_keyword_match(self):
        assert is_cyber_dataset({"name": "x", "summary": "Tracking ransomware groups"}) is True

    def test_description_only_keyword_match(self):
        # short_text wouldn't hit; only description does
        assert is_cyber_dataset({
            "name": "x", "title": "Some List",
            "description": "Includes a cryptocurrency wallet column",
        }) is True

    def test_non_cyber_dataset(self):
        assert is_cyber_dataset({
            "name": "us_ofac_sdn", "title": "OFAC SDN",
            "summary": "Sanctioned persons", "tags": ["sanctions"],
        }) is False


# ── cyber_category ───────────────────────────────────────────────────────────

class TestCyberCategory:
    def test_crypto_via_tag(self):
        assert cyber_category({"name": "x", "tags": ["sector.crypto"]}) == "crypto"

    def test_ransomware_beats_crypto(self):
        # ransomware keyword in a crypto dataset → ransomware wins
        assert cyber_category({
            "name": "ransomware_wallets", "tags": ["sector.crypto"],
        }) == "ransomware"

    def test_state_sponsored_lazarus(self):
        assert cyber_category({"name": "fbi_lazarus"}) == "state-sponsored"

    def test_cyber_generic(self):
        assert cyber_category({"name": "x", "title": "Malware Attribution"}) == "cyber"

    def test_darknet(self):
        assert cyber_category({"name": "x", "title": "Darknet Market Vendors"}) == "darknet"

    def test_other_fallback(self):
        assert cyber_category({"name": "x", "title": "Unrelated"}) == "other"


# ── _is_crypto_entity ────────────────────────────────────────────────────────

class TestIsCryptoEntity:
    def test_cryptowallet_schema(self):
        assert _is_crypto_entity({"schema": "CryptoWallet"}) is True

    def test_public_key_field(self):
        assert _is_crypto_entity({"schema": "Person", "publicKey": "0xabc"}) is True

    def test_currency_field(self):
        assert _is_crypto_entity({"schema": "Person", "currency": "BTC"}) is True

    def test_plain_person(self):
        assert _is_crypto_entity({"schema": "Person", "caption": "Alice"}) is False


# ── _entity_country ──────────────────────────────────────────────────────────

class TestEntityCountry:
    def test_picks_first_priority_field(self):
        row = {"nationality": "us", "countries": "ru"}
        assert _entity_country(row) == "us"

    def test_falls_through_to_country(self):
        assert _entity_country({"country": "GB"}) == "gb"

    def test_splits_semicolon_separated(self):
        assert _entity_country({"countries": "ru; us; gb"}) == "ru"

    def test_empty_when_no_fields(self):
        assert _entity_country({"schema": "Person"}) == ""


# ── _cached_datasets ─────────────────────────────────────────────────────────

class TestCachedDatasets:
    """Only return datasets already in L1 or L2 — never trigger origin fetch."""

    def test_returns_empty_when_all_cold(self, monkeypatch):
        monkeypatch.setattr(routes_cyber, "_entity_cache", {})
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        assert _cached_datasets() == []

    def test_returns_l1_warm_dataset(self, monkeypatch):
        rows = [{"schema": "CryptoWallet"}]
        monkeypatch.setattr(routes_cyber, "_entity_cache", {"us_ofac_sdn": rows})
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        ready = _cached_datasets()
        assert ("us_ofac_sdn", rows) in ready

    def test_promotes_l2_to_l1(self, monkeypatch):
        local_cache = {}
        rows = [{"schema": "CryptoWallet"}]
        monkeypatch.setattr(routes_cyber, "_entity_cache", local_cache)

        def fake_l2_get(source, ident, **kw):
            return rows if ident == "us_ofac_sdn" else None
        monkeypatch.setattr(l2, "get", fake_l2_get)

        ready = _cached_datasets()
        assert ("us_ofac_sdn", rows) in ready
        # L2 row was promoted into L1
        assert local_cache["us_ofac_sdn"] is rows


# ── /api/sanctions-check ─────────────────────────────────────────────────────

class TestSanctionsCheck:
    def test_empty_address_returns_clean_response(self, client):
        resp = client.get("/api/sanctions-check?address=")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "matches": [], "sanctioned": False, "checked_datasets": 0,
        }

    def test_no_match_when_address_not_in_lists(self, client, monkeypatch):
        rows = [{"publicKey": "0xaaa", "caption": "Alice", "schema": "CryptoWallet"}]
        monkeypatch.setattr(routes_cyber, "_cached_datasets",
                            lambda: [("us_ofac_sdn", rows)])
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set", lambda *a, **kw: None)

        resp = client.get("/api/sanctions-check?address=0xnotonlist")
        data = resp.get_json()
        assert data["sanctioned"] is False
        assert data["matches"] == []
        assert data["checked_datasets"] == 1

    def test_match_on_public_key(self, client, monkeypatch):
        rows = [{
            "publicKey": "0xabc", "caption": "Sanctioned Wallet",
            "holder": "Bad Actor", "currency": "BTC", "schema": "CryptoWallet",
        }]
        monkeypatch.setattr(routes_cyber, "_cached_datasets",
                            lambda: [("us_ofac_sdn", rows)])
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set", lambda *a, **kw: None)

        resp = client.get("/api/sanctions-check?address=0xabc")
        data = resp.get_json()
        assert data["sanctioned"] is True
        assert len(data["matches"]) == 1
        assert data["matches"][0]["dataset"] == "us_ofac_sdn"
        assert data["matches"][0]["holder"] == "Bad Actor"

    def test_address_lookup_is_case_insensitive(self, client, monkeypatch):
        rows = [{"publicKey": "0xabc", "schema": "CryptoWallet"}]
        monkeypatch.setattr(routes_cyber, "_cached_datasets",
                            lambda: [("us_ofac_sdn", rows)])
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)
        monkeypatch.setattr(l2, "set", lambda *a, **kw: None)

        resp = client.get("/api/sanctions-check?address=0xABC")
        assert resp.get_json()["sanctioned"] is True

    def test_partial_scan_is_not_cached(self, client, monkeypatch):
        """If not every CRYPTO_SCAN_DATASETS dataset was warm, result must not
        be cached — otherwise a partial 'safe' result would persist for 1h."""
        rows = [{"publicKey": "0xabc", "schema": "CryptoWallet"}]
        # Only 1 of len(CRYPTO_SCAN_DATASETS) datasets ready → partial
        monkeypatch.setattr(routes_cyber, "_cached_datasets",
                            lambda: [("us_ofac_sdn", rows)])
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)

        set_calls = []
        monkeypatch.setattr(l2, "set", lambda *a, **kw: set_calls.append((a, kw)))

        client.get("/api/sanctions-check?address=0xanything")
        assert set_calls == []

    def test_full_scan_is_cached(self, client, monkeypatch):
        rows = [{"publicKey": "0xabc", "schema": "CryptoWallet"}]
        # Every dataset in CRYPTO_SCAN_DATASETS appears warm
        all_warm = [(ds, rows) for ds in routes_cyber.CRYPTO_SCAN_DATASETS]
        monkeypatch.setattr(routes_cyber, "_cached_datasets", lambda: all_warm)
        monkeypatch.setattr(l2, "get", lambda *a, **kw: None)

        set_calls = []
        monkeypatch.setattr(l2, "set", lambda *a, **kw: set_calls.append((a, kw)))

        client.get("/api/sanctions-check?address=0xanything")
        assert len(set_calls) == 1
        # Cached under source="sanctions_check", identifier=address
        assert set_calls[0][0][:2] == ("sanctions_check", "0xanything")


# ── /api/sanctions-check-batch ───────────────────────────────────────────────

class TestSanctionsCheckBatch:
    def test_empty_body_returns_no_hits(self, client):
        resp = client.post("/api/sanctions-check-batch", json={})
        assert resp.get_json() == {"hits": {}, "checked_datasets": 0}

    def test_collects_multi_address_hits(self, client, monkeypatch):
        rows = [
            {"publicKey": "0xaaa", "holder": "Alice", "currency": "BTC"},
            {"publicKey": "0xbbb", "holder": "Bob",   "currency": "ETH"},
            {"publicKey": "0xccc", "holder": "Carol", "currency": "BTC"},
        ]
        monkeypatch.setattr(routes_cyber, "_cached_datasets",
                            lambda: [("us_ofac_sdn", rows)])

        resp = client.post(
            "/api/sanctions-check-batch",
            json={"addresses": ["0xAAA", "0xbbb", "0xnope"]},
        )
        data = resp.get_json()
        assert set(data["hits"].keys()) == {"0xaaa", "0xbbb"}
        assert data["hits"]["0xaaa"]["holder"] == "Alice"
        assert data["hits"]["0xbbb"]["datasets"] == ["us_ofac_sdn"]
