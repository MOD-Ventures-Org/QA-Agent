import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from utils.logger import get_logger
from webhook.router import router as webhook_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ngrok_authtoken:
        try:
            from pyngrok import ngrok, conf
            conf.get_default().auth_token = settings.ngrok_authtoken
            tunnel = ngrok.connect(8000)
            public_url = tunnel.public_url
            logger.info(f"ngrok tunnel active: {public_url}")
            logger.info(
                f"\n{'='*60}\n"
                f"  Paste this URL into your GitHub webhook settings:\n"
                f"  {public_url}/webhook/github\n"
                f"{'='*60}"
            )
            app.state.ngrok_tunnel = tunnel
        except Exception as e:
            logger.warning(f"ngrok startup failed: {e}")
    yield
    if hasattr(app.state, "ngrok_tunnel"):
        from pyngrok import ngrok
        ngrok.disconnect(app.state.ngrok_tunnel.public_url)


app = FastAPI(title="ARIA — Autonomous Regression & Intelligence Agent", lifespan=lifespan)

app.include_router(webhook_router)


@app.get("/health")
async def health():
    tunnel_url = ""
    if hasattr(app.state, "ngrok_tunnel"):
        tunnel_url = app.state.ngrok_tunnel.public_url
    return {"status": "ok", "tunnel": tunnel_url}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
