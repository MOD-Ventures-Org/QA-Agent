import pytest


@pytest.mark.a11y
def test_homepage_accessibility(page, base_url):
    try:
        from axe_playwright_python import Axe
        page.goto(base_url)
        axe = Axe()
        results = axe.run(page)
        critical_violations = [
            v for v in results.get("violations", [])
            if v.get("impact") in ("critical", "serious")
        ]
        violation_report = "\n".join(
            f"Rule: {v['id']} | Impact: {v['impact']} | "
            f"Selector: {v['nodes'][0]['target'] if v.get('nodes') else 'N/A'}"
            for v in critical_violations
        )
        assert not critical_violations, f"Critical/serious a11y violations found:\n{violation_report}"
    except ImportError:
        pytest.skip("playwright-axe not installed")
