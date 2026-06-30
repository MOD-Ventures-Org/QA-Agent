"""Standard conftest.py ARIA commits alongside the generated tests.

The generated tests import fixtures (`base_url`, `api_base_url`, `api_client`,
`page`) — but an arbitrary target repo won't define them. ARIA pushes this file
to ``testing/suites/generated/conftest.py`` so pytest discovers the fixtures
automatically for everything in that directory.

Design notes:
  * No top-level Playwright import — API-only repos don't install it, so importing
    it here would crash collection. The ``page`` fixture is provided by the
    ``pytest-playwright`` plugin (installed by the workflow only when UI tests
    exist); this file does not redefine it.
  * URLs come from the workflow's env (BASE_URL_FRONTEND / BASE_URL_API), with
    localhost fallbacks for local runs.
"""

CONFTEST_PATH = "testing/suites/generated/conftest.py"

CONFTEST_CONTENT = r'''# Managed by ARIA. Provides the fixtures the generated tests rely on.
import os

import pytest

try:
    import httpx
except ImportError:  # API tests will skip if httpx isn't available
    httpx = None


@pytest.fixture
def base_url():
    """Frontend base URL for Playwright/UI tests."""
    return os.environ.get("BASE_URL_FRONTEND", "http://localhost:3000")


@pytest.fixture
def api_base_url():
    """API base URL for HTTP/API tests."""
    return os.environ.get("BASE_URL_API", "http://localhost:8080")


@pytest.fixture
def api_client(api_base_url):
    """An httpx.Client pointed at the API base URL."""
    if httpx is None:
        pytest.skip("httpx is not installed in this environment")
    with httpx.Client(base_url=api_base_url, timeout=30) as client:
        yield client
'''
