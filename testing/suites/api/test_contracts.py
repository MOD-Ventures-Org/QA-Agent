import pytest


@pytest.mark.api
def test_health_response_schema(api_client):
    response = api_client.get("/health")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, dict)


@pytest.mark.api
def test_api_no_500_errors(api_client):
    response = api_client.get("/health")
    assert response.status_code != 500
