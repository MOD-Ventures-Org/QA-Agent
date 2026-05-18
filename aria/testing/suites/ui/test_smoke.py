import pytest


@pytest.mark.ui
def test_homepage_loads(page, base_url):
    page.goto(base_url)
    assert page.title() != ""


@pytest.mark.ui
def test_homepage_has_body(page, base_url):
    page.goto(base_url)
    assert page.locator("body").is_visible()
