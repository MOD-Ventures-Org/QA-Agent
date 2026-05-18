import pytest


@pytest.mark.api
def test_protected_endpoint_requires_auth(api_client):
    response = api_client.get("/api/protected")
    assert response.status_code in (401, 403, 404)


@pytest.mark.api
def test_invalid_token_rejected(api_client):
    response = api_client.get("/api/protected", headers={"Authorization": "Bearer invalid_token"})
    assert response.status_code in (401, 403, 404)
