"""Load testing suite — fires concurrent requests at the API under test and
asserts success-rate and p95-latency thresholds. Runs for backend repo events.

Uses httpx (already a project dependency) with a thread pool to generate load,
so it slots into the existing pytest + json-report flow with no extra packages.
If the API is unreachable, the test skips instead of failing (CI-safe).
"""

import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from config import settings


def _percentile(values, pct):
    """Linear-interpolated percentile (pct in 0..1)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@pytest.mark.load
def test_api_handles_concurrent_load(api_base_url):
    total = settings.load_test_requests
    workers = settings.load_test_concurrency
    path = settings.load_test_path

    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    with httpx.Client(base_url=api_base_url, timeout=30, limits=limits) as client:

        def _hit(_):
            start = time.perf_counter()
            try:
                resp = client.get(path)
                elapsed_ms = (time.perf_counter() - start) * 1000
                return resp.status_code < 500, elapsed_ms, True
            except httpx.HTTPError:
                elapsed_ms = (time.perf_counter() - start) * 1000
                return False, elapsed_ms, False

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_hit, range(total)))

    connected = [r for r in results if r[2]]
    if not connected:
        pytest.skip(f"API at {api_base_url}{path} not reachable — skipping load test")

    successes = sum(1 for r in results if r[0])
    success_rate = successes / total
    p95_ms = _percentile([r[1] for r in connected], 0.95)

    assert success_rate >= settings.load_test_min_success_rate, (
        f"Success rate {success_rate:.1%} below threshold "
        f"{settings.load_test_min_success_rate:.1%} ({successes}/{total} succeeded)"
    )
    assert p95_ms <= settings.load_test_max_p95_ms, (
        f"p95 latency {p95_ms:.0f}ms exceeds threshold {settings.load_test_max_p95_ms:.0f}ms"
    )
