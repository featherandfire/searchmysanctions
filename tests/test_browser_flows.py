"""
Browser interaction flows — real user journeys through the SPA, with /api/*
responses stubbed per test for deterministic rendering.
"""
import json
import re

import pytest
from playwright.sync_api import Page, expect


def _route_json(page: Page, url_pattern: str, payload):
    """Register a route returning `payload` (dict/list) as JSON for url_pattern."""
    page.route(url_pattern, lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(payload),
    ))


# ── Browse Datasets ──────────────────────────────────────────────────────────

class TestBrowseDatasets:
    SAMPLE = [
        {"name": "us_ofac_sdn", "title": "OFAC SDN",       "type": "source",
         "summary": "US sanctions", "entity_count": 1000, "tags": ["sanctions"],
         "publisher_name": "OFAC", "publisher_country_label": "US", "result": "success"},
        {"name": "eu_sanctions",  "title": "EU Sanctions",  "type": "source",
         "summary": "EU sanctions", "entity_count": 500,  "tags": ["sanctions"],
         "publisher_name": "EU Council", "publisher_country_label": "EU", "result": "success"},
        {"name": "un_sc",         "title": "UN Security Council", "type": "source",
         "summary": "UN list", "entity_count": 200, "tags": ["sanctions"],
         "publisher_name": "UN", "publisher_country_label": "UN", "result": "success"},
    ]

    def test_card_grid_renders_with_full_data(self, page: Page, base_url: str):
        _route_json(page, "**/api/datasets", self.SAMPLE)
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="datasets"]').click()

        expect(page.locator(".dataset-card")).to_have_count(3)
        expect(page.locator(".dataset-card").first.locator(".card-title")).to_have_text("OFAC SDN")
        expect(page.locator(".results-count")).to_contain_text("3 datasets")

    def test_search_filters_results(self, page: Page, base_url: str):
        _route_json(page, "**/api/datasets", self.SAMPLE)
        _route_json(page, "**/api/search**", [self.SAMPLE[0]])
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="datasets"]').click()

        # Wait for the full grid first, then narrow
        expect(page.locator(".dataset-card")).to_have_count(3)
        page.locator("#search-input").fill("OFAC")

        # 220ms debounce + /api/search round-trip; Playwright auto-waits
        expect(page.locator(".dataset-card")).to_have_count(1)
        expect(page.locator(".dataset-card .card-title")).to_have_text("OFAC SDN")

    def test_list_layout_toggle(self, page: Page, base_url: str):
        _route_json(page, "**/api/datasets", self.SAMPLE)
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="datasets"]').click()

        # Grid is the default
        expect(page.locator(".dataset-card")).to_have_count(3)
        expect(page.locator(".list-card")).to_have_count(0)

        page.locator("#list-btn").click()
        expect(page.locator(".list-card")).to_have_count(3)
        expect(page.locator(".dataset-card")).to_have_count(0)
        expect(page.locator("#list-btn")).to_have_class(re.compile(r"\bactive\b"))

    def test_card_click_opens_detail_panel(self, page: Page, base_url: str):
        _route_json(page, "**/api/datasets", self.SAMPLE)
        _route_json(page, "**/api/dataset/us_ofac_sdn", {
            **self.SAMPLE[0],
            "description": "Full description text",
            "resources": [],
            "thing_count": 1100, "target_count": 950,
        })
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="datasets"]').click()

        # Detail overlay starts hidden
        expect(page.locator("#detail-overlay")).to_be_hidden()

        page.locator(".dataset-card").first.click()

        expect(page.locator("#detail-overlay")).to_be_visible()
        expect(page.locator("#detail-title")).to_have_text("OFAC SDN")
        expect(page.locator("#detail-name")).to_have_text("us_ofac_sdn")
        expect(page.locator("#detail-body")).to_contain_text("Full description text")

    def test_detail_panel_close_button(self, page: Page, base_url: str):
        _route_json(page, "**/api/datasets", self.SAMPLE)
        _route_json(page, "**/api/dataset/us_ofac_sdn", {
            **self.SAMPLE[0], "resources": [], "thing_count": 0, "target_count": 0,
        })
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="datasets"]').click()
        page.locator(".dataset-card").first.click()
        expect(page.locator("#detail-overlay")).to_be_visible()

        page.locator(".close-btn").click()
        expect(page.locator("#detail-overlay")).to_be_hidden()


# ── Entity Search ────────────────────────────────────────────────────────────

class TestEntitySearch:
    DATASETS = [
        {"name": "us_ofac_sdn", "title": "OFAC SDN", "entity_count": 1000, "default": True},
        {"name": "eu_sanctions","title": "EU Sanctions", "entity_count": 500, "default": True},
        {"name": "un_sc",       "title": "UN", "entity_count": 200, "default": False},
    ]

    def test_initial_state_shows_prompt(self, page: Page, base_url: str):
        _route_json(page, "**/api/entity-search/datasets", self.DATASETS)
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="entity-search"]').click()

        expect(page.locator("#es-input")).to_be_visible()
        expect(page.locator("#es-results")).to_contain_text("Enter a name, wallet address")
        # Default-selected datasets reflected in the dataset count
        expect(page.locator("#es-ds-count")).to_have_text("2")

    def test_search_renders_result_rows(self, page: Page, base_url: str):
        _route_json(page, "**/api/entity-search/datasets", self.DATASETS)
        _route_json(page, "**/api/entity-search**", {
            "results": [
                {"name": "Alice Smith", "schema": "Person", "_dataset": "us_ofac_sdn",
                 "sanctions": "OFAC SDN", "countries": "RU"},
                {"name": "Bob Inc",     "schema": "Organization", "_dataset": "eu_sanctions",
                 "sanctions": "EU CFSP", "countries": "IR"},
            ],
            "searched": ["us_ofac_sdn", "eu_sanctions"],
            "total": 2,
        })
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="entity-search"]').click()
        page.locator("#es-input").fill("Alice")
        page.locator("#es-input").press("Enter")

        expect(page.locator("#es-results table tbody tr")).to_have_count(2)
        expect(page.locator("#es-results")).to_contain_text("Alice Smith")
        expect(page.locator("#es-results")).to_contain_text("Bob Inc")
        expect(page.locator("#es-results")).to_contain_text('2 records matching')

    def test_search_empty_results(self, page: Page, base_url: str):
        _route_json(page, "**/api/entity-search/datasets", self.DATASETS)
        _route_json(page, "**/api/entity-search**", {
            "results": [], "searched": ["us_ofac_sdn"], "total": 0,
        })
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="entity-search"]').click()
        page.locator("#es-input").fill("zzznoone")
        page.locator("#es-input").press("Enter")

        expect(page.locator("#es-results")).to_contain_text("No records matching")
        expect(page.locator("#es-results")).to_contain_text('"zzznoone"')


# ── Tags ─────────────────────────────────────────────────────────────────────

class TestTagsView:
    def test_tag_pills_rendered_with_counts(self, page: Page, base_url: str):
        _route_json(page, "**/api/tags", [
            ["list.pep",        12],
            ["sector.crypto",    5],
            ["sanctions",        3],
        ])
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="tags"]').click()

        expect(page.locator(".tag-pill")).to_have_count(3)
        expect(page.locator(".tag-pill").first).to_contain_text("list.pep")
        expect(page.locator(".tag-pill").first.locator(".tag-count")).to_have_text("12")
        expect(page.locator(".results-count")).to_contain_text("3 unique tags")


# ── Cyber & Crypto ───────────────────────────────────────────────────────────

class TestCyberView:
    def test_dataset_tab_renders_cards_and_stats(self, page: Page, base_url: str):
        _route_json(page, "**/api/cyber", {
            "datasets": [
                {"name": "us_fbi_lazarus_crypto", "title": "FBI Lazarus Wallets",
                 "summary": "DPRK wallets", "cyber_category": "crypto",
                 "entity_count": 42, "target_count": 42,
                 "publisher_name": "FBI", "publisher_country_label": "US",
                 "result": "success", "tags": ["sector.crypto"]},
                {"name": "ransomwhere", "title": "Ransomwhere",
                 "summary": "Ransom payments", "cyber_category": "ransomware",
                 "entity_count": 99, "target_count": 99,
                 "publisher_name": "Ransomwhere", "publisher_country_label": "—",
                 "result": "success", "tags": []},
            ],
            "total_entities": 141,
            "total_targets": 141,
            "category_counts": {"crypto": 1, "ransomware": 1},
            "sdn_crypto_count": 250,
        })
        page.goto(base_url + "/")
        page.locator('.nav-item[data-view="cyber"]').click()

        # Stat strip: 4 cards (Datasets, Total Entities, Total Targets, SDN Crypto)
        expect(page.locator(".stat-card")).to_have_count(4)
        # SDN crypto stat value
        expect(page.locator(".stat-card").nth(3).locator(".stat-value")).to_have_text("250")
        # Sidebar list: "All Lists" header row + 2 datasets
        expect(page.locator('[data-dsname]')).to_have_count(3)
        expect(page.locator('[data-dsname="us_fbi_lazarus_crypto"]')).to_be_visible()
