"""The standing security suite: every finding to date has a regression test.

``fixtures/security_findings.json`` is the ledger. These tests check it covers
every row of every review's summary table and that each named test exists,
and they add the regression tests the 2026-08-07 review left as prose: the
rebinding ``Host``, the upload ceiling, the negative list limit, the audit
jobs, the advised ``next`` major, the UI headers and the bandit rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import Harness

from repcut.api.schemas import MAX_UPLOAD_BYTES

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path(__file__).resolve().parent / "fixtures" / "security_findings.json"


def _ledger() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(LEDGER.read_text(encoding="utf-8"))
    return data


def _summary_rows(review: Path) -> list[str]:
    """The finding numbers in a review's summary table (``| 1 | ... |`` rows)."""
    text = review.read_text(encoding="utf-8")
    summary = text.split("## Summary", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\|\s*(\d+)\s*\|", summary, flags=re.MULTILINE)


def _exists(node: str) -> bool:
    file, _, name = node.partition("::")
    path = REPO_ROOT / file
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if file.endswith(".py"):
        return re.search(rf"^(async )?def {re.escape(name)}\(", text, re.MULTILINE) is not None
    return f'it("{name}"' in text or f"it('{name}'" in text


def test_every_finding_of_every_review_is_in_the_ledger() -> None:
    ledger = _ledger()
    ids = {f["id"] for f in ledger["findings"]}
    for review in ledger["reviews"]:
        path = REPO_ROOT / review
        date = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        assert date is not None
        rows = _summary_rows(path)
        assert rows, f"{review}: no summary rows found"
        missing = [n for n in rows if f"{date.group(1)}#{n}" not in ids]
        assert not missing, f"{review}: findings {missing} have no regression test in the ledger"


def test_every_review_on_disk_is_in_the_ledger() -> None:
    reviews = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "docs" / "reports").glob("security-review-*.md")
    )
    assert reviews == sorted(_ledger()["reviews"])


def test_every_named_regression_test_exists() -> None:
    absent = [
        node for finding in _ledger()["findings"] for node in finding["tests"] if not _exists(node)
    ]
    assert not absent, f"ledger names tests that do not exist: {absent}"
    assert all(f["tests"] for f in _ledger()["findings"])


async def test_a_rebinding_host_is_refused_before_any_route(api: Harness) -> None:
    """Finding 1: an attacker's hostname re-pointed at 127.0.0.1 is same-origin
    with their page; ``Host`` is the only field still naming them."""
    for host in ("evil.example", "localhost.evil.example", "127.0.0.1.nip.io"):
        refused = await api.client.post("/projects", json={"name": "x"}, headers={"Host": host})
        assert refused.status_code == 400, host
    allowed = await api.client.get("/health", headers={"Host": "localhost:8000"})
    assert allowed.status_code == 200


async def test_a_transfer_larger_than_the_ceiling_is_refused(api: Harness) -> None:
    """Finding 4: a declared size is a write budget; unbounded it fills the disk."""
    project = (await api.client.post("/projects", json={"name": "p"})).json()["id"]
    body = {"display_name": "huge.mp4", "size_bytes": MAX_UPLOAD_BYTES + 1}
    refused = await api.client.post(f"/projects/{project}/uploads", json=body)
    assert refused.status_code == 422
    body["size_bytes"] = -1
    assert (await api.client.post(f"/projects/{project}/uploads", json=body)).status_code == 422


@pytest.mark.parametrize("limit", ["-1", "0", "100000"])
async def test_a_negative_job_limit_is_refused(api: Harness, limit: str) -> None:
    """Finding 7: ``LIMIT -1`` is no limit to SQLite; the bound lives in the route."""
    response = await api.client.get("/jobs", params={"limit": limit})
    assert response.status_code == 422


def test_the_dependency_audits_gate_the_build() -> None:
    """Findings 5 and 10: both audits run in CI, strictly, and nothing softens them."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pip-audit --strict" in ci
    assert "npm audit --omit=dev --audit-level=high" in ci
    assert "continue-on-error" not in ci
    assert "|| true" not in ci.split("dependencies:", 1)[1].split("\n  guardrails:", 1)[0]


def test_next_is_past_the_advised_major() -> None:
    """Finding 5: next@14.2.35 carried six high advisories; the pin left 14."""
    package = json.loads((REPO_ROOT / "ui" / "package.json").read_text(encoding="utf-8"))
    major = int(str(package["dependencies"]["next"]).lstrip("^~").split(".")[0])
    assert major >= 16


def test_the_ui_ships_its_security_headers() -> None:
    """Finding 6: the headers are part of the build, not a deployment concern."""
    config = (REPO_ROOT / "ui" / "next.config.mjs").read_text(encoding="utf-8")
    for needle in (
        "frame-ancestors 'none'",
        "X-Content-Type-Options",
        "nosniff",
        "Referrer-Policy",
        "Content-Security-Policy",
    ):
        assert needle in config, needle


def test_the_bandit_rules_cover_engine_and_scripts() -> None:
    """Finding 10: ruff's S rules on both scopes ``make lint`` checks."""
    for config in ("pyproject.toml", "engine/pyproject.toml"):
        text = (REPO_ROOT / config).read_text(encoding="utf-8")
        select = text.split("select = [", 1)[1].split("]", 1)[0]
        assert re.search(r'"S"', select), config
