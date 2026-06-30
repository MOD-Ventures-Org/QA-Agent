from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import runtime
from config import settings
from utils.logger import get_logger
from webhook.router import router as webhook_router
from webhook.results_router import router as results_router
from api.router import router as api_router

logger = get_logger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"


def _start_ngrok() -> str:
    """Open an ngrok tunnel on port 8000 and return its public URL. Best-effort:
    returns '' if pyngrok/the tunnel is unavailable."""
    try:
        from pyngrok import ngrok, conf
        if settings.ngrok_authtoken:
            conf.get_default().auth_token = settings.ngrok_authtoken
        tunnel = ngrok.connect(8000, "http")
        return tunnel.public_url
    except Exception as e:
        logger.warning(f"ngrok tunnel not started: {e}")
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Resolve the public base URL: explicit PUBLIC_BASE_URL wins; otherwise open an
    # ngrok tunnel when a token is configured.
    if not settings.public_base_url and settings.ngrok_authtoken:
        url = _start_ngrok()
        if url:
            runtime.set_ngrok_url(url)

    base = runtime.get_public_base_url()
    logger.info(
        f"\n{'='*60}\n"
        f"  ARIA is running on http://localhost:8000\n"
        f"  Dashboard:   {base}/ui\n"
        f"  Webhook URL: {base}/webhook/github\n"
        f"{'='*60}"
    )
    yield
    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception:
        pass


app = FastAPI(title="ARIA — Autonomous Regression & Intelligence Agent", lifespan=lifespan)

app.include_router(webhook_router)
app.include_router(results_router)
app.include_router(api_router)

if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.2.0", "agent": "ARIA", "tunnel": runtime.get_public_base_url()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
