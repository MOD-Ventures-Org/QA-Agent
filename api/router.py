"""Read-only API the dashboard polls for run data."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from storage import runs

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=100), skip: int = Query(0, ge=0), repo: Optional[str] = Query(None)):
    return {"runs": await runs.list_runs(limit=limit, skip=skip, repo=repo)}


@router.get("/repos")
async def list_repos():
    return {"repos": await runs.list_repos()}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await runs.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run
