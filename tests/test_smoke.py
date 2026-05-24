"""
Smoke tests — load the SPA and switch to each view, asserting the page title
updates and the view's container renders. API calls are stubbed in conftest.
"""
import re

import pytest
from playwright.sync_api import Page, expect

VIEWS = [
    ("home", "Home"),
    ("datasets", "Browse Datasets"),
    ("stats", "Statistics"),
    ("cyber", "Cyber & Crypto"),
    ("pep", "Politically Exposed Persons"),
    ("medicaid", "Medicaid Exclusions"),
    ("entity-search", "Entity Search"),
    ("countries", "By Country"),
    ("tags", "Tags"),
]


def test_index_loads(page: Page, base_url: str):
    page.goto(base_url + "/")
    expect(page).to_have_title("International Sanctions Explorer")
    expect(page.locator(".sidebar .logo-title")).to_have_text("SearchMySanctions")
    # Every nav button is rendered
    expect(page.locator(".nav-item")).to_have_count(len(VIEWS))


@pytest.mark.parametrize("view_id,expected_title", VIEWS)
def test_view_switches(page: Page, base_url: str, view_id: str, expected_title: str):
    page.goto(base_url + "/")
    page.locator(f'.nav-item[data-view="{view_id}"]').click()
    expect(page.locator("#page-title")).to_have_text(expected_title)
    expect(page.locator(f'.nav-item[data-view="{view_id}"]')).to_have_class(
        re.compile(r"\bactive\b")
    )


def test_search_box_visible_only_on_datasets(page: Page, base_url: str):
    page.goto(base_url + "/")
    # Default view is datasets — search is visible
    expect(page.locator("#search-wrap")).to_be_visible()
    page.locator('.nav-item[data-view="home"]').click()
    expect(page.locator("#search-wrap")).to_be_hidden()
    page.locator('.nav-item[data-view="datasets"]').click()
    expect(page.locator("#search-wrap")).to_be_visible()
