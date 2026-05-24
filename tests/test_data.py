"""
Unit tests for data.py — focused on _flat_entity, the function that flattens
every OpenSanctions record into the row shape consumed by every downstream
view. Bugs here silently corrupt every entity-bearing endpoint.
"""
from data import (
    _flat_entity,
    _first,
    _join,
    fmt_date,
    serialize_dataset,
    visible_datasets,
)


# ── _first / _join helpers ───────────────────────────────────────────────────

class TestFirst:
    def test_returns_first_element(self):
        assert _first(["a", "b", "c"]) == "a"

    def test_returns_none_for_empty_list(self):
        assert _first([]) is None

    def test_returns_none_for_none(self):
        assert _first(None) is None


class TestJoin:
    def test_joins_with_semicolon(self):
        assert _join(["a", "b", "c"]) == "a; b; c"

    def test_filters_falsy(self):
        assert _join(["a", "", None, "b"]) == "a; b"

    def test_returns_none_for_empty(self):
        assert _join([]) is None
        assert _join(None) is None

    def test_stringifies_non_strings(self):
        assert _join([1, 2, 3]) == "1; 2; 3"


# ── fmt_date ─────────────────────────────────────────────────────────────────

class TestFmtDate:
    def test_iso_with_zulu(self):
        assert fmt_date("2025-01-15T10:30:00Z") == "2025-01-15"

    def test_iso_with_offset(self):
        assert fmt_date("2025-01-15T10:30:00+00:00") == "2025-01-15"

    def test_falls_back_to_truncated_string_on_parse_failure(self):
        assert fmt_date("2025-01-15 garbage") == "2025-01-15"

    def test_returns_none_for_empty(self):
        assert fmt_date("") is None
        assert fmt_date(None) is None


# ── visible_datasets ─────────────────────────────────────────────────────────

class TestVisibleDatasets:
    def test_filters_hidden_and_deprecated(self):
        datasets = [
            {"name": "a"},
            {"name": "b", "hidden": True},
            {"name": "c", "deprecated": True},
            {"name": "d", "hidden": False, "deprecated": False},
        ]
        names = [d["name"] for d in visible_datasets(datasets)]
        assert names == ["a", "d"]


# ── serialize_dataset ────────────────────────────────────────────────────────

class TestSerializeDataset:
    def test_extracts_top_level_fields(self):
        ds = {
            "name": "us_ofac_sdn", "title": "OFAC SDN", "type": "source",
            "summary": "summary", "description": "desc",
            "tags": ["sanctions"], "entity_count": 1000, "target_count": 950,
        }
        out = serialize_dataset(ds)
        assert out["name"] == "us_ofac_sdn"
        assert out["title"] == "OFAC SDN"
        assert out["entity_count"] == 1000
        assert out["tags"] == ["sanctions"]

    def test_flattens_publisher(self):
        ds = {"name": "x", "publisher": {
            "name": "OFAC", "country": "us",
            "country_label": "United States", "official": True,
        }}
        out = serialize_dataset(ds)
        assert out["publisher_name"] == "OFAC"
        assert out["publisher_country"] == "us"
        assert out["publisher_country_label"] == "United States"
        assert out["publisher_official"] is True

    def test_handles_missing_publisher(self):
        out = serialize_dataset({"name": "x"})
        assert out["publisher_name"] is None
        assert out["publisher_country"] is None

    def test_defaults_counts_to_zero(self):
        out = serialize_dataset({"name": "x"})
        assert out["entity_count"] == 0
        assert out["target_count"] == 0
        assert out["thing_count"] == 0

    def test_flattens_resource_list(self):
        out = serialize_dataset({"name": "x", "resources": [
            {"name": "targets.nested.json", "url": "https://x/a.json", "size": 1024,
             "title": "Nested", "mime_type_label": "JSON", "extra": "ignored"},
        ]})
        assert len(out["resources"]) == 1
        r = out["resources"][0]
        assert r["name"] == "targets.nested.json"
        assert r["size"] == 1024
        assert "extra" not in r  # only whitelisted resource fields kept

    def test_extracts_frequency_from_coverage(self):
        out = serialize_dataset({"name": "x", "coverage": {"frequency": "daily"}})
        assert out["frequency"] == "daily"


# ── _flat_entity — the big one ───────────────────────────────────────────────

class TestFlatEntityCore:
    def test_top_level_fields(self):
        row = _flat_entity({
            "id": "abc", "caption": "Alice", "schema": "Person",
            "datasets": ["us_ofac_sdn", "eu_sanctions"],
            "first_seen":  "2020-01-15T12:00:00Z",
            "last_seen":   "2024-03-20T08:00:00Z",
            "last_change": "2024-06-01T00:00:00Z",
            "properties": {},
        })
        assert row["id"] == "abc"
        assert row["caption"] == "Alice"
        assert row["schema"] == "Person"
        assert row["datasets"] == "us_ofac_sdn; eu_sanctions"
        # Dates truncated to 10 chars
        assert row["first_seen"] == "2020-01-15"
        assert row["last_seen"] == "2024-03-20"
        assert row["last_change"] == "2024-06-01"

    def test_empty_and_none_fields_are_stripped(self):
        row = _flat_entity({
            "id": "abc",
            "caption": None,
            "schema": "",
            "first_seen": "",
            "properties": {"name": [], "alias": [""], "title": [None]},
        })
        # None / empty values dropped from output
        assert "caption" not in row
        assert "schema" not in row
        assert "first_seen" not in row
        # Empty lists produce no key
        assert "name" not in row

    def test_scalar_property_lists_joined(self):
        row = _flat_entity({"id": "x", "properties": {
            "alias": ["Bob", "Robert", "Bobby"],
        }})
        assert row["alias"] == "Bob; Robert; Bobby"

    def test_non_list_scalar_property_passes_through(self):
        row = _flat_entity({"id": "x", "properties": {"weight": 42}})
        assert row["weight"] == 42

    def test_dict_property_is_dropped(self):
        # Dict values that aren't in _NESTED_KEYS get silently dropped — they
        # would otherwise crash JSON serialisation downstream
        row = _flat_entity({"id": "x", "properties": {
            "nested": {"foo": "bar"},
        }})
        assert "nested" not in row

    def test_entity_list_uses_captions(self):
        # When a property is a list of sub-entities (dicts), use their captions
        row = _flat_entity({"id": "x", "properties": {
            "associates": [
                {"caption": "Carol", "properties": {"name": ["Carol Smith"]}},
                {"caption": "Dave"},
            ],
        }})
        assert row["associates"] == "Carol; Dave"

    def test_entity_list_falls_back_to_first_name(self):
        # Sub-entity has no caption — fall back to the first `name` property
        row = _flat_entity({"id": "x", "properties": {
            "associates": [{"properties": {"name": ["Eve Adams", "E. Adams"]}}],
        }})
        assert row["associates"] == "Eve Adams"


class TestFlatEntitySanctions:
    def test_aggregates_across_multiple_sanctions(self):
        row = _flat_entity({"id": "x", "properties": {"sanctions": [
            {"properties": {
                "authority": ["OFAC"], "program": ["SDGT"],
                "reason":    ["terrorism"],
            }},
            {"properties": {
                "authority": ["EU"],   "program": ["CFSP"],
                "reason":    ["sanctions evasion"],
            }},
        ]}})
        assert row["sanction_authority"] == "OFAC; EU"
        assert row["sanction_program"]   == "SDGT; CFSP"
        assert row["sanction_reason"]    == "terrorism; sanctions evasion"

    def test_deduplicates_repeated_values(self):
        row = _flat_entity({"id": "x", "properties": {"sanctions": [
            {"properties": {"authority": ["OFAC", "OFAC"]}},
            {"properties": {"authority": ["OFAC"]}},
        ]}})
        assert row["sanction_authority"] == "OFAC"

    def test_no_key_when_no_values(self):
        # If a sanction field is never populated, no `sanction_<field>` key
        row = _flat_entity({"id": "x", "properties": {"sanctions": [
            {"properties": {"authority": ["OFAC"]}},
        ]}})
        assert "sanction_authority" in row
        assert "sanction_program" not in row
        assert "sanction_reason" not in row


class TestFlatEntityHolder:
    def test_holder_caption_extracted(self):
        row = _flat_entity({"id": "x", "properties": {"holder": [
            {"caption": "Bad Actor Ltd", "properties": {"alias": ["BA Ltd"]}},
        ]}})
        assert row["holder"] == "Bad Actor Ltd"
        assert row["holder_alias"] == "BA Ltd"

    def test_holder_falls_back_to_first_name(self):
        row = _flat_entity({"id": "x", "properties": {"holder": [
            {"properties": {"name": ["Mallory"]}},
        ]}})
        assert row["holder"] == "Mallory"

    def test_only_first_holder_used(self):
        row = _flat_entity({"id": "x", "properties": {"holder": [
            {"caption": "Holder One"},
            {"caption": "Holder Two"},  # ignored
        ]}})
        assert row["holder"] == "Holder One"


class TestFlatEntityAddressEntities:
    def test_full_address_joined(self):
        row = _flat_entity({"id": "x", "properties": {"addressEntity": [
            {"properties": {"full": ["123 Main St, Fresno, CA 93701"]}},
            {"properties": {"full": ["456 Oak Ave, Davis, CA"]}},
        ]}})
        assert row["address_full"] == "123 Main St, Fresno, CA 93701; 456 Oak Ave, Davis, CA"

    def test_falls_back_to_caption(self):
        row = _flat_entity({"id": "x", "properties": {"addressEntity": [
            {"caption": "123 Main St, Fresno"},
        ]}})
        assert row["address_full"] == "123 Main St, Fresno"

    def test_combines_existing_address_field(self):
        # If `address` was already set from a scalar property, prepend it
        row = _flat_entity({"id": "x", "properties": {
            "address": ["PO Box 99"],
            "addressEntity": [{"properties": {"full": ["123 Main St"]}}],
        }})
        # `address` becomes "PO Box 99" (scalar), then prefix in address_full
        assert row["address"] == "PO Box 99"
        assert row["address_full"] == "PO Box 99; 123 Main St"


class TestFlatEntityNestedKeysSkipped:
    """_NESTED_KEYS get special handling — they should NOT appear in the
    output dict via the generic property loop. Ensures bare sub-entity lists
    don't leak into the flat row as concatenated captions."""

    def test_nested_keys_dont_leak_through_generic_loop(self):
        row = _flat_entity({"id": "x", "properties": {
            "sanctions": [{"caption": "Sanction One"}],
            "holder":    [{"caption": "Holder One"}],
            "addressEntity": [{"caption": "Address One"}],
            "ownershipAsset": [{"caption": "Asset"}],
            "directorshipOrganization": [{"caption": "Org"}],
            "unknownLinks": [{"caption": "Link"}],
            "asset": [{"caption": "Asset2"}],
        }})
        # None of the raw nested-key names appear as keys (only their special
        # treatments do, like `holder` and `address_full`)
        for k in ("sanctions", "ownershipAsset", "directorshipOrganization",
                  "unknownLinks", "asset"):
            assert k not in row, f"{k!r} should be filtered out"
        # `holder` IS allowed (it's set by the holder-specific block)
        assert row.get("holder") == "Holder One"
