import pytest


@pytest.mark.ui
def test_page_responsive(page, base_url):
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base_url)
    assert page.locator("body").is_visible()


@pytest.mark.ui
def test_page_loads_within_timeout(page, base_url):
    page.goto(base_url, timeout=10000)
    assert page.locator("body").is_visible()
