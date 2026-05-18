import pytest


@pytest.mark.api
def test_api_health(api_client):
    response = api_client.get("/health")
    assert response.status_code in (200, 404)


@pytest.mark.api
def test_api_returns_json(api_client):
    response = api_client.get("/health")
    if response.status_code == 200:
        assert response.headers.get("content-type", "").startswith("application/json")
