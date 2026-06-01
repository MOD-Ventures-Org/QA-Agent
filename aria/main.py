from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from config import settings
from utils.logger import get_logger
from webhook.router import router as webhook_router

logger = get_logger(__name__)

_ngrok_url: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ngrok_url

    try:
        from pyngrok import ngrok
        ngrok.set_auth_token(settings.ngrok_authtoken)
        tunnel = ngrok.connect(8000, "http")
        _ngrok_url = tunnel.public_url
        logger.info(f"ngrok tunnel established: {_ngrok_url}")
    except Exception as e:
        logger.warning(f"ngrok startup failed: {e} — use manual ngrok or environment tunnel")
        _ngrok_url = None

    logger.info(
        f"\n{'='*60}\n"
        f"  ARIA is running on http://localhost:8000\n"
        f"  Webhook endpoint: {_ngrok_url or 'https://<your-ngrok-url>'}/webhook/github\n"
        f"  Set this URL in GitHub → Settings → Webhooks\n"
        f"  Secret: {settings.github_webhook_secret}\n"
        f"{'='*60}"
    )
    yield

    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception as e:
        logger.warning(f"ngrok cleanup failed: {e}")


app = FastAPI(title="ARIA — Autonomous Regression & Intelligence Agent", lifespan=lifespan)

app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.2.0", "agent": "ARIA", "tunnel": _ngrok_url or "not configured"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
