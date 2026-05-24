"""
Tests for the persistence endpoints in routes/cyber.py:
  /api/notes              GET / POST / PUT / DELETE
  /api/address-history    GET / POST / DELETE

Backed by a temp SQLite database (see conftest.py); the `_clean_db_tables`
autouse fixture truncates both tables before every test for isolation.
"""
import pytest


# ── /api/notes ───────────────────────────────────────────────────────────────

class TestNotes:
    def test_get_returns_empty_list_when_no_notes(self, client):
        resp = client.get("/api/notes")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_post_creates_note_and_returns_201(self, client):
        resp = client.post("/api/notes", json={
            "title": "Sanctioned wallet seen",
            "body":  "0xabc was flagged today",
            "tags":  ["ofac", "btc"],
        })
        assert resp.status_code == 201
        note = resp.get_json()
        assert note["title"] == "Sanctioned wallet seen"
        assert note["body"]  == "0xabc was flagged today"
        assert note["tags"]  == ["ofac", "btc"]
        assert isinstance(note["id"], int)
        assert "UTC" in note["created_at"]

        # Persisted — visible to subsequent GET
        listed = client.get("/api/notes").get_json()
        assert len(listed) == 1
        assert listed[0]["title"] == "Sanctioned wallet seen"

    def test_post_strips_whitespace_and_filters_empty_tags(self, client):
        resp = client.post("/api/notes", json={
            "title": "   spaced title   ",
            "body":  "  body  ",
            "tags":  ["good", "", "  ", "bad "],
        })
        note = resp.get_json()
        assert note["title"] == "spaced title"
        assert note["body"]  == "body"
        assert note["tags"]  == ["good", "bad"]

    def test_post_handles_missing_fields(self, client):
        resp = client.post("/api/notes", json={})
        assert resp.status_code == 201
        note = resp.get_json()
        assert note["title"] == ""
        assert note["body"]  == ""
        assert note["tags"]  == []

    def test_post_inserts_at_top(self, client):
        client.post("/api/notes", json={"title": "first"})
        client.post("/api/notes", json={"title": "second"})
        notes = client.get("/api/notes").get_json()
        assert [n["title"] for n in notes] == ["second", "first"]

    def test_post_assigns_unique_ids_to_rapid_consecutive_creates(self, client):
        """SQL sequences guarantee uniqueness — no millisecond-precision race."""
        ids = {client.post("/api/notes", json={"title": str(i)}).get_json()["id"]
               for i in range(10)}
        assert len(ids) == 10

    def test_delete_removes_by_id(self, client):
        a = client.post("/api/notes", json={"title": "keep"}).get_json()
        b = client.post("/api/notes", json={"title": "drop"}).get_json()
        assert a["id"] != b["id"]

        resp = client.delete(f"/api/notes/{b['id']}")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

        remaining = client.get("/api/notes").get_json()
        assert [n["title"] for n in remaining] == ["keep"]

    def test_delete_unknown_id_is_a_noop(self, client):
        client.post("/api/notes", json={"title": "stays"})
        resp = client.delete("/api/notes/99999")
        assert resp.status_code == 200
        assert len(client.get("/api/notes").get_json()) == 1

    def test_put_updates_fields_and_sets_updated_at(self, client):
        note = client.post("/api/notes", json={
            "title": "old", "body": "old body", "tags": ["a"],
        }).get_json()

        resp = client.put(f"/api/notes/{note['id']}", json={
            "title": "new", "tags": ["b", "c"],
        })
        assert resp.status_code == 200

        updated = client.get("/api/notes").get_json()[0]
        assert updated["title"] == "new"
        assert updated["body"]  == "old body"  # untouched
        assert updated["tags"]  == ["b", "c"]
        assert "updated_at" in updated

    def test_put_unknown_id_is_a_noop(self, client):
        client.post("/api/notes", json={"title": "untouched"})
        resp = client.put("/api/notes/99999", json={"title": "x"})
        assert resp.status_code == 200
        assert client.get("/api/notes").get_json()[0]["title"] == "untouched"


# ── /api/address-history ─────────────────────────────────────────────────────

class TestAddressHistory:
    def test_get_returns_empty_list_when_no_history(self, client):
        resp = client.get("/api/address-history")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_post_rejects_empty_address(self, client):
        resp = client.post("/api/address-history", json={"address": "   "})
        assert resp.status_code == 400
        assert resp.get_json() == {"ok": False, "error": "address required"}
        assert client.get("/api/address-history").get_json() == []

    def test_post_logs_lowercased_address_at_top(self, client):
        resp = client.post("/api/address-history", json={
            "address": "0xABC", "sanctioned": True,
            "sanction_lists": ["OFAC SDN"], "label": "Bad Actor",
            "mode": "balance",
        })
        assert resp.status_code == 201
        entry = resp.get_json()
        assert entry["address"] == "0xabc"
        assert entry["sanctioned"] is True
        assert entry["sanction_lists"] == ["OFAC SDN"]
        assert entry["label"] == "Bad Actor"
        assert entry["mode"] == "balance"
        assert "UTC" in entry["searched_at"]

    def test_post_dedupes_by_address_moving_to_top(self, client):
        client.post("/api/address-history", json={"address": "0xaaa", "label": "first"})
        client.post("/api/address-history", json={"address": "0xbbb"})
        client.post("/api/address-history", json={"address": "0xAAA", "label": "second"})

        history = client.get("/api/address-history").get_json()
        addresses = [h["address"] for h in history]
        assert addresses == ["0xaaa", "0xbbb"]
        assert history[0]["label"] == "second"

    def test_post_defaults_optional_fields(self, client):
        resp = client.post("/api/address-history", json={"address": "0xabc"})
        entry = resp.get_json()
        assert entry["sanctioned"] is False
        assert entry["sanction_lists"] == []
        assert entry["label"] == ""
        assert entry["mode"] == "balance"
        assert entry["referred_from"] == ""

    def test_delete_single_address(self, client):
        client.post("/api/address-history", json={"address": "0xaaa"})
        client.post("/api/address-history", json={"address": "0xbbb"})

        resp = client.delete("/api/address-history/0xAAA")
        assert resp.status_code == 200

        remaining = [h["address"] for h in client.get("/api/address-history").get_json()]
        assert remaining == ["0xbbb"]

    def test_delete_unknown_address_is_a_noop(self, client):
        client.post("/api/address-history", json={"address": "0xaaa"})
        resp = client.delete("/api/address-history/0xnope")
        assert resp.status_code == 200
        assert len(client.get("/api/address-history").get_json()) == 1

    def test_delete_all_clears_history(self, client):
        client.post("/api/address-history", json={"address": "0xaaa"})
        client.post("/api/address-history", json={"address": "0xbbb"})

        resp = client.delete("/api/address-history")
        assert resp.status_code == 200
        assert client.get("/api/address-history").get_json() == []
