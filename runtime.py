"""Runtime-resolved values that aren't known until the app starts — chiefly the
public base URL used to build dashboard links in Discord notifications.

Precedence: an explicit PUBLIC_BASE_URL setting wins; otherwise the live ngrok
tunnel URL captured at startup; otherwise the local fallback.
"""

from config import settings

LOCAL_FALLBACK = "http://localhost:8000"

# Set by main.py's lifespan when an ngrok tunnel is opened.
_ngrok_url: str = ""


def set_ngrok_url(url: str) -> None:
    global _ngrok_url
    _ngrok_url = (url or "").rstrip("/")


def get_public_base_url() -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    if _ngrok_url:
        return _ngrok_url
    return LOCAL_FALLBACK


def run_link(run_id: str) -> str:
    """The dashboard URL for a single run."""
    return f"{get_public_base_url()}/ui?run={run_id}"
