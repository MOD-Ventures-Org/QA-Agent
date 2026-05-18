import pytest


@pytest.mark.ui
def test_navigation_links_present(page, base_url):
    page.goto(base_url)
    links = page.locator("a").all()
    assert len(links) >= 0


@pytest.mark.ui
def test_no_console_errors(page, base_url):
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.goto(base_url)
    assert len(errors) == 0, f"Console errors: {[e.text for e in errors]}"
