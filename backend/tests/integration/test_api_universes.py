"""Integration tests for /api/universes endpoints — §20.2."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import new_id
from backend.app.models.runs import BigBangRunModel
from backend.app.models.universes import UniverseModel

pytestmark = [pytest.mark.asyncio]

_BRANCH_DELTA = {
    "type": "parameter_shift",
    "target": "attention_decay_rate",
    "delta": {"value": 0.1},
}


# ---------------------------------------------------------------------------
# Fixtures — seed a run + root universe
# ---------------------------------------------------------------------------


async def _seed_run(session: AsyncSession) -> tuple[str, str]:
    """Insert a BigBangRunModel and root UniverseModel; return (run_id, universe_id)."""
    run_id = new_id("run")
    uni_id = new_id("uni")

    run = BigBangRunModel(
        big_bang_id=run_id,
        display_name="Test Run",
        scenario_text="Test scenario",
        input_file_ids=[],
        status="running",
        time_horizon_label="3 months",
        tick_duration_minutes=1440,
        max_ticks=30,
        max_schedule_horizon_ticks=5,
        source_of_truth_version="1.0",
        source_of_truth_snapshot_path="",
        provider_snapshot_id="",
        root_universe_id=uni_id,
        run_folder_path="",
        safe_edit_metadata={},
    )
    session.add(run)

    uni = UniverseModel(
        universe_id=uni_id,
        big_bang_id=run_id,
        parent_universe_id=None,
        lineage_path=[uni_id],
        branch_from_tick=0,
        branch_depth=0,
        status="active",
        branch_reason="",
        branch_delta=None,
        current_tick=3,
        latest_metrics={"dominant_emotion": "hope", "mobilization_risk": 0.4},
        created_at=datetime.now(timezone.utc),
    )
    session.add(uni)
    await session.commit()
    return run_id, uni_id


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}
# ---------------------------------------------------------------------------


async def test_get_universe_happy_path(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.get(f"/api/universes/{uni_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["universe_id"] == uni_id
    assert data["big_bang_id"] == run_id
    assert data["status"] == "active"
    assert data["current_tick"] == 3
    assert "latest_metrics" in data
    assert "active_cohort_count" in data
    assert "child_universe_ids" in data


async def test_get_universe_404(client: AsyncClient):
    resp = await client.get("/api/universes/nonexistent_uni")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/pause
# ---------------------------------------------------------------------------


async def test_pause_active_universe(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(f"/api/universes/{uni_id}/pause")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "frozen"

    # Verify persisted.
    detail = await client.get(f"/api/universes/{uni_id}")
    assert detail.json()["status"] == "frozen"


async def test_pause_already_frozen_raises_409(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)
    await client.post(f"/api/universes/{uni_id}/pause")

    resp = await client.post(f"/api/universes/{uni_id}/pause")
    assert resp.status_code == 409


async def test_pause_nonexistent_404(client: AsyncClient):
    resp = await client.post("/api/universes/no_such/pause")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/resume
# ---------------------------------------------------------------------------


async def test_resume_frozen_universe(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)
    await client.post(f"/api/universes/{uni_id}/pause")

    resp = await client.post(f"/api/universes/{uni_id}/resume")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "active"
    assert "next_tick" in data


async def test_resume_active_raises_409(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(f"/api/universes/{uni_id}/resume")
    assert resp.status_code == 409


async def test_resume_nonexistent_404(client: AsyncClient):
    resp = await client.post("/api/universes/no_such/resume")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/step
# ---------------------------------------------------------------------------


async def test_step_universe_returns_job_id(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(f"/api/universes/{uni_id}/step", json={})
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["universe_id"] == uni_id
    assert data["tick"] == 4  # current_tick(3) + 1


async def test_step_with_explicit_tick(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(f"/api/universes/{uni_id}/step", json={"tick": 10})
    assert resp.status_code == 202
    assert resp.json()["tick"] == 10


async def test_step_nonexistent_404(client: AsyncClient):
    resp = await client.post("/api/universes/no_such/step", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/branch-preview
# ---------------------------------------------------------------------------


async def test_branch_preview_placeholder(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(
        f"/api/universes/{uni_id}/branch-preview",
        json={"delta": _BRANCH_DELTA, "reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approved"] is True
    assert "note" in data


async def test_branch_preview_nonexistent_404(client: AsyncClient):
    resp = await client.post(
        "/api/universes/no_such/branch-preview",
        json={"delta": _BRANCH_DELTA, "reason": ""},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/branch
# ---------------------------------------------------------------------------


async def test_branch_returns_candidate_id(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.post(
        f"/api/universes/{uni_id}/branch",
        json={"delta": _BRANCH_DELTA, "reason": "test branch"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "candidate_universe_id" in data
    assert "job_id" in data
    assert data["candidate_universe_id"].startswith(("uni_", "U_"))


async def test_branch_nonexistent_404(client: AsyncClient):
    resp = await client.post(
        "/api/universes/no_such/branch",
        json={"delta": _BRANCH_DELTA, "reason": ""},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/ticks/{tick}
# ---------------------------------------------------------------------------


async def test_tick_artifact_no_run_folder(client: AsyncClient, db_session: AsyncSession):
    """With no run_folder_path, returns empty artifact for tick <= current_tick."""
    run_id, uni_id = await _seed_run(db_session)

    # tick 3 <= current_tick(3): should return empty artifact.
    resp = await client.get(f"/api/universes/{uni_id}/ticks/3")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["universe_id"] == uni_id
    assert data["tick"] == 3
    assert isinstance(data["parsed_decisions"], list)
    assert isinstance(data["social_posts"], list)


async def test_tick_artifact_future_tick_404(client: AsyncClient, db_session: AsyncSession):
    """Tick beyond current_tick with no run folder → 404."""
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.get(f"/api/universes/{uni_id}/ticks/999")
    assert resp.status_code == 404


async def test_tick_artifact_nonexistent_universe(client: AsyncClient):
    resp = await client.get("/api/universes/no_such/ticks/1")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/lineage
# ---------------------------------------------------------------------------


async def test_lineage_root_universe(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.get(f"/api/universes/{uni_id}/lineage")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["lineage_path"] == [uni_id]
    assert data["parent"] is None
    assert data["depth"] == 0


async def test_lineage_child_universe(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id = await _seed_run(db_session)
    child_id = new_id("uni")

    child = UniverseModel(
        universe_id=child_id,
        big_bang_id=run_id,
        parent_universe_id=root_id,
        lineage_path=[root_id, child_id],
        branch_from_tick=2,
        branch_depth=1,
        status="candidate",
        branch_reason="test branch",
        branch_delta=None,
        current_tick=0,
        latest_metrics={},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(child)
    await db_session.commit()

    resp = await client.get(f"/api/universes/{child_id}/lineage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lineage_path"] == [root_id, child_id]
    assert data["parent"] == root_id
    assert data["depth"] == 1
    assert data["branch_from_tick"] == 2


async def test_lineage_nonexistent_404(client: AsyncClient):
    resp = await client.get("/api/universes/no_such/lineage")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/descendants
# ---------------------------------------------------------------------------


async def test_descendants_root_no_children(client: AsyncClient, db_session: AsyncSession):
    run_id, uni_id = await _seed_run(db_session)

    resp = await client.get(f"/api/universes/{uni_id}/descendants")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["universe_id"] == uni_id
    assert data["children"] == []


async def test_descendants_depth_2_lineage(client: AsyncClient, db_session: AsyncSession):
    """Depth-2 lineage: root → child → grandchild."""
    run_id, root_id = await _seed_run(db_session)

    child_id = new_id("uni")
    grandchild_id = new_id("uni")

    child = UniverseModel(
        universe_id=child_id,
        big_bang_id=run_id,
        parent_universe_id=root_id,
        lineage_path=[root_id, child_id],
        branch_from_tick=1,
        branch_depth=1,
        status="active",
        branch_reason="branch 1",
        branch_delta=None,
        current_tick=2,
        latest_metrics={},
        created_at=datetime.now(timezone.utc),
    )
    grandchild = UniverseModel(
        universe_id=grandchild_id,
        big_bang_id=run_id,
        parent_universe_id=child_id,
        lineage_path=[root_id, child_id, grandchild_id],
        branch_from_tick=2,
        branch_depth=2,
        status="candidate",
        branch_reason="branch 2",
        branch_delta=None,
        current_tick=0,
        latest_metrics={},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(child)
    db_session.add(grandchild)
    await db_session.commit()

    resp = await client.get(f"/api/universes/{root_id}/descendants")
    assert resp.status_code == 200
    data = resp.json()
    assert data["universe_id"] == root_id
    assert len(data["children"]) == 1

    child_node = data["children"][0]
    assert child_node["universe_id"] == child_id
    assert len(child_node["children"]) == 1

    grandchild_node = child_node["children"][0]
    assert grandchild_node["universe_id"] == grandchild_id
    assert grandchild_node["children"] == []


async def test_descendants_nonexistent_404(client: AsyncClient):
    resp = await client.get("/api/universes/no_such/descendants")
    assert resp.status_code == 404


async def test_agent_trace_state_reads_final_bundle_sociology_result():
    from app.api.agent import _actor_rows_from_state, _state_at_tick

    tick = SimpleNamespace(
        final_bundle={
            "sociology_result": {
                "cohort_state_updates": [{"cohort_id": "coh-1", "name": "Cohort One"}],
                "hero_state_updates": [{"hero_id": "hero-1", "name": "Hero One"}],
            }
        },
        provisional_bundle={},
    )

    state = _state_at_tick(tick)
    actors = _actor_rows_from_state(state)
    assert {actor["actor_id"] for actor in actors} == {"coh-1", "hero-1"}
