import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

from config import settings

SCREENSHOT_DIR = Path("/tmp/aria_screenshots")


@pytest.fixture(scope="session")
def base_url() -> str:
    return settings.base_url_frontend


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return settings.base_url_api


@pytest.fixture
def page(request):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.playwright_headless)
        context = browser.new_context()
        pg = context.new_page()
        yield pg
        if request.node.rep_call.failed if hasattr(request.node, "rep_call") else False:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            safe_name = request.node.name.replace("/", "_").replace(":", "_")
            screenshot_path = SCREENSHOT_DIR / f"{ts}_{safe_name}.png"
            try:
                pg.screenshot(path=str(screenshot_path))
            except Exception:
                pass
        context.close()
        browser.close()


@pytest.fixture
def api_client(api_base_url):
    with httpx.Client(base_url=api_base_url, timeout=30) as client:
        yield client


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
