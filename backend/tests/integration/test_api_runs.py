"""Integration tests for POST/GET/PATCH/POST actions on /api/runs.

Happy paths, idempotency dedup, PATCH forbidden-field 422, and 404 checks.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio]


_CREATE_BODY = {
    "display_name": "Test Big Bang",
    "scenario_text": "Bay Area gig-worker labor dispute, 6-month horizon",
    "time_horizon_label": "6 months",
    "tick_duration_minutes": 1440,
    "max_ticks": 30,
    "max_schedule_horizon_ticks": 5,
    "uploaded_doc_ids": [],
    "provider_snapshot_id": None,
}


@pytest.fixture(autouse=True)
def _stub_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_enqueue(envelope):
        return envelope.job_id

    monkeypatch.setattr("backend.app.api.runs.enqueue", _fake_enqueue)


# ---------------------------------------------------------------------------
# POST /api/runs — create
# ---------------------------------------------------------------------------


async def test_create_run_returns_202(client: AsyncClient):
    resp = await client.post("/api/runs", json=_CREATE_BODY)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "run_id" in data
    assert "root_universe_id" in data
    assert data["status"] == "initializing"
    assert data["enqueued"] is True
    assert data["job_id"]


async def test_create_run_persists_to_db(client: AsyncClient, db_session):
    resp = await client.post("/api/runs", json=_CREATE_BODY)
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # Fetch back via GET.
    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["run_id"] == run_id
    assert detail["scenario_text"] == _CREATE_BODY["scenario_text"]
    assert detail["status"] == "initializing"


# ---------------------------------------------------------------------------
# Idempotency-Key dedup on POST /api/runs
# ---------------------------------------------------------------------------


async def test_idempotency_key_deduplication(client: AsyncClient):
    idem_key = "test-idem-key-abc123"

    # First request — creates the run.
    r1 = await client.post(
        "/api/runs",
        json=_CREATE_BODY,
        headers={"Idempotency-Key": idem_key},
    )
    assert r1.status_code == 202
    run_id_1 = r1.json()["run_id"]

    # Second request with same key — should return same run.
    r2 = await client.post(
        "/api/runs",
        json=_CREATE_BODY,
        headers={"Idempotency-Key": idem_key},
    )
    assert r2.status_code == 202
    run_id_2 = r2.json()["run_id"]

    assert run_id_1 == run_id_2, "Idempotency key should return same run_id"


async def test_different_idempotency_keys_create_different_runs(client: AsyncClient):
    r1 = await client.post(
        "/api/runs", json=_CREATE_BODY, headers={"Idempotency-Key": "key-aaa"}
    )
    r2 = await client.post(
        "/api/runs", json=_CREATE_BODY, headers={"Idempotency-Key": "key-bbb"}
    )
    assert r1.json()["run_id"] != r2.json()["run_id"]


# ---------------------------------------------------------------------------
# GET /api/runs — list
# ---------------------------------------------------------------------------


async def test_list_runs_returns_items(client: AsyncClient):
    # Create at least one run.
    await client.post("/api/runs", json=_CREATE_BODY)

    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    assert data["total"] >= 1


async def test_list_runs_search_by_display_name(client: AsyncClient):
    unique_name = "UniqueName_XYZ_99"
    body = {**_CREATE_BODY, "display_name": unique_name}
    await client.post("/api/runs", json=body)

    resp = await client.get("/api/runs", params={"q": "UniqueName_XYZ"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["display_name"] == unique_name for i in items)


async def test_list_runs_filter_by_status(client: AsyncClient):
    await client.post("/api/runs", json=_CREATE_BODY)
    resp = await client.get("/api/runs", params={"status": "initializing"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(i["status"] == "initializing" for i in data["items"])


async def test_list_runs_pagination(client: AsyncClient):
    # Create multiple runs.
    for i in range(3):
        await client.post("/api/runs", json={**_CREATE_BODY, "display_name": f"Run {i}"})

    r1 = await client.get("/api/runs", params={"limit": 1, "offset": 0})
    r2 = await client.get("/api/runs", params={"limit": 1, "offset": 1})
    assert r1.status_code == 200
    assert r2.status_code == 200
    ids1 = [i["run_id"] for i in r1.json()["items"]]
    ids2 = [i["run_id"] for i in r2.json()["items"]]
    # Different pages should not overlap (unless only 1 run exists).
    if r1.json()["total"] > 1:
        assert ids1 != ids2


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id} — detail
# ---------------------------------------------------------------------------


async def test_get_run_returns_detail(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert "active_universe_count" in data
    assert "total_universe_count" in data
    assert "latest_metrics" in data


async def test_get_run_404_for_nonexistent(client: AsyncClient):
    resp = await client.get("/api/runs/nonexistent_run_id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/runs/{run_id}
# ---------------------------------------------------------------------------


async def test_patch_run_safe_fields(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.patch(
        f"/api/runs/{run_id}",
        json={"display_name": "Updated Name", "description": "My desc", "favorite": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Updated Name"
    assert data["safe_edit_metadata"].get("description") == "My desc"
    assert data["safe_edit_metadata"].get("favorite") is True


async def test_patch_run_rejects_forbidden_fields(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    # Send a field that is not in PatchRunRequest.
    resp = await client.patch(
        f"/api/runs/{run_id}",
        json={"scenario_text": "Hacked scenario"},
    )
    assert resp.status_code == 422, "Expected 422 for forbidden field"


async def test_patch_run_tags(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.patch(f"/api/runs/{run_id}", json={"tags": ["alpha", "beta"]})
    assert resp.status_code == 200
    assert resp.json()["safe_edit_metadata"].get("tags") == ["alpha", "beta"]


async def test_patch_run_404(client: AsyncClient):
    resp = await client.patch("/api/runs/no_such_run", json={"display_name": "X"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/archive
# ---------------------------------------------------------------------------


async def test_archive_run(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/archive")
    assert resp.status_code == 204

    detail = await client.get(f"/api/runs/{run_id}")
    assert detail.json()["safe_edit_metadata"].get("archived") is True


async def test_archive_run_404(client: AsyncClient):
    resp = await client.post("/api/runs/no_such_run/archive")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/duplicate
# ---------------------------------------------------------------------------


async def test_duplicate_run_creates_new_run(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    original_id = create.json()["run_id"]

    dup = await client.post(f"/api/runs/{original_id}/duplicate")
    assert dup.status_code == 201
    data = dup.json()
    assert "run_id" in data
    assert data["run_id"] != original_id
    assert data["status"] == "draft"


async def test_duplicate_run_has_same_scenario(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    original_id = create.json()["run_id"]

    dup = await client.post(f"/api/runs/{original_id}/duplicate")
    new_id = dup.json()["run_id"]

    detail = await client.get(f"/api/runs/{new_id}")
    assert detail.status_code == 200
    assert detail.json()["scenario_text"] == _CREATE_BODY["scenario_text"]
    assert detail.json()["run_folder_path"] is None


async def test_duplicate_run_404(client: AsyncClient):
    resp = await client.post("/api/runs/no_such_run/duplicate")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/export
# ---------------------------------------------------------------------------


async def test_export_run_returns_202_and_job_id(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/export")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"


async def test_export_run_404(client: AsyncClient):
    resp = await client.post("/api/runs/no_such_run/export")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/source-of-truth
# ---------------------------------------------------------------------------


async def test_source_of_truth_returns_bundle_or_503(client: AsyncClient):
    create = await client.post("/api/runs", json=_CREATE_BODY)
    run_id = create.json()["run_id"]

    resp = await client.get(f"/api/runs/{run_id}/source-of-truth")
    # Either 200 (SoT files present) or 503 (not yet populated in test env).
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "version" in data
        assert "emotions" in data
