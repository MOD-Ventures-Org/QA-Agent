from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from utils.logger import get_logger
from webhook.router import router as webhook_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        f"\n{'='*60}\n"
        f"  ARIA is running on http://localhost:8000\n"
        f"  Run ngrok in a separate terminal:\n"
        f"  ngrok http 8000\n"
        f"  Then paste the https URL into your GitHub webhook settings.\n"
        f"{'='*60}"
    )
    yield


app = FastAPI(title="ARIA — Autonomous Regression & Intelligence Agent", lifespan=lifespan)

app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
