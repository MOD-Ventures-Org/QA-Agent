import pytest


@pytest.mark.functional
def test_api_handles_empty_body(api_client):
    response = api_client.post("/api/data", content=b"")
    assert response.status_code not in (500,)


@pytest.mark.functional
def test_api_handles_large_payload(api_client):
    large_body = b"x" * 10_000
    response = api_client.post("/api/data", content=large_body)
    assert response.status_code not in (500,)
