import pytest


@pytest.mark.functional
def test_frontend_api_connectivity(page, api_client, base_url):
    api_response = api_client.get("/health")
    page.goto(base_url)
    assert page.locator("body").is_visible()
    assert api_response.status_code != 500
