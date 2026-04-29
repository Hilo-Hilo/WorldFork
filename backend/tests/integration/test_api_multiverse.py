"""Integration tests for /api/multiverse endpoints — §20.3."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import new_id
from backend.app.models.runs import BigBangRunModel
from backend.app.models.universes import UniverseModel

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_run_with_universes(
    session: AsyncSession,
    n_extra_children: int = 0,
) -> tuple[str, str, list[str]]:
    """Seed a run + root universe + optional child universes.

    Returns (big_bang_id, root_universe_id, [child_ids]).
    """
    run_id = new_id("run")
    root_id = new_id("uni")

    run = BigBangRunModel(
        big_bang_id=run_id,
        display_name="Multiverse Test Run",
        scenario_text="Test multiverse scenario",
        input_file_ids=[],
        status="running",
        time_horizon_label="6 months",
        tick_duration_minutes=1440,
        max_ticks=50,
        max_schedule_horizon_ticks=10,
        source_of_truth_version="1.0",
        source_of_truth_snapshot_path="",
        provider_snapshot_id="",
        root_universe_id=root_id,
        run_folder_path="",
        safe_edit_metadata={},
    )
    session.add(run)

    root_uni = UniverseModel(
        universe_id=root_id,
        big_bang_id=run_id,
        parent_universe_id=None,
        lineage_path=[root_id],
        branch_from_tick=0,
        branch_depth=0,
        status="active",
        branch_reason="",
        branch_delta=None,
        current_tick=5,
        latest_metrics={"mobilization_risk": 0.3},
        created_at=datetime.now(timezone.utc),
    )
    session.add(root_uni)

    child_ids: list[str] = []
    for i in range(n_extra_children):
        child_id = new_id("uni")
        child_ids.append(child_id)
        child_uni = UniverseModel(
            universe_id=child_id,
            big_bang_id=run_id,
            parent_universe_id=root_id,
            lineage_path=[root_id, child_id],
            branch_from_tick=i + 1,
            branch_depth=1,
            status="active" if i == 0 else "candidate",
            branch_reason=f"Branch {i}",
            branch_delta=None,
            current_tick=i,
            latest_metrics={"mobilization_risk": 0.1 * i},
            created_at=datetime.now(timezone.utc),
        )
        session.add(child_uni)

    await session.commit()
    return run_id, root_id, child_ids


async def _seed_depth2_lineage(
    session: AsyncSession,
) -> tuple[str, str, str, str]:
    """Seed: run → root → child → grandchild. Returns all four IDs."""
    run_id, root_id, child_ids = await _seed_run_with_universes(session, n_extra_children=1)
    child_id = child_ids[0]

    grandchild_id = new_id("uni")
    grandchild = UniverseModel(
        universe_id=grandchild_id,
        big_bang_id=run_id,
        parent_universe_id=child_id,
        lineage_path=[root_id, child_id, grandchild_id],
        branch_from_tick=2,
        branch_depth=2,
        status="candidate",
        branch_reason="depth-2 branch",
        branch_delta=None,
        current_tick=0,
        latest_metrics={},
        created_at=datetime.now(timezone.utc),
    )
    session.add(grandchild)
    await session.commit()
    return run_id, root_id, child_id, grandchild_id


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/tree
# ---------------------------------------------------------------------------


async def test_tree_root_only(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, _ = await _seed_run_with_universes(db_session)

    resp = await client.get(f"/api/multiverse/{run_id}/tree")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["big_bang_id"] == run_id
    assert data["max_ticks"] == 50
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["universe_id"] == root_id
    assert data["edges"] == []


async def test_tree_with_children(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=2)

    resp = await client.get(f"/api/multiverse/{run_id}/tree")
    assert resp.status_code == 200
    data = resp.json()
    node_ids = {n["universe_id"] for n in data["nodes"]}
    assert root_id in node_ids
    assert all(cid in node_ids for cid in child_ids)

    # Edges should connect root → children.
    edge_targets = {e["target"] for e in data["edges"]}
    assert all(cid in edge_targets for cid in child_ids)


async def test_tree_depth2_lineage(client: AsyncClient, db_session: AsyncSession):
    """Correct tree built with depth-2 lineage: root → child → grandchild."""
    run_id, root_id, child_id, grandchild_id = await _seed_depth2_lineage(db_session)

    resp = await client.get(f"/api/multiverse/{run_id}/tree")
    assert resp.status_code == 200
    data = resp.json()

    node_ids = {n["universe_id"] for n in data["nodes"]}
    assert root_id in node_ids
    assert child_id in node_ids
    assert grandchild_id in node_ids
    assert len(data["edges"]) == 2

    # Verify descendant counts.
    root_node = next(n for n in data["nodes"] if n["universe_id"] == root_id)
    assert root_node["descendant_count"] == 2  # child + grandchild

    child_node = next(n for n in data["nodes"] if n["universe_id"] == child_id)
    assert child_node["descendant_count"] == 1  # grandchild only


async def test_tree_nonexistent_run_404(client: AsyncClient):
    resp = await client.get("/api/multiverse/no_such_run/tree")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/dag
# ---------------------------------------------------------------------------


async def test_dag_happy_path(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=1)

    resp = await client.get(f"/api/multiverse/{run_id}/dag")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["big_bang_id"] == run_id
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2  # root + 1 child


async def test_dag_nonexistent_404(client: AsyncClient):
    resp = await client.get("/api/multiverse/no_such_run/dag")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/metrics
# ---------------------------------------------------------------------------


async def test_metrics_happy_path(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=2)

    resp = await client.get(f"/api/multiverse/{run_id}/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["big_bang_id"] == run_id
    assert data["active_universes"] >= 1
    assert data["total_branches"] == 2  # 2 children
    assert data["max_depth"] == 1
    assert "candidate_branches" in data
    assert "branch_budget_pct" in data


async def test_metrics_nonexistent_404(client: AsyncClient):
    resp = await client.get("/api/multiverse/no_such_run/metrics")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/prune
# ---------------------------------------------------------------------------


async def test_prune_dry_run(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=1)

    resp = await client.post(
        f"/api/multiverse/{run_id}/prune",
        json={"min_value": 0.5, "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dry_run"] is True
    assert isinstance(data["pruned_universe_ids"], list)

    # Verify universes are NOT actually killed (dry_run=True).
    get_resp = await client.get(f"/api/universes/{child_ids[0]}")
    assert get_resp.json()["status"] != "killed"


async def test_prune_wet_run_kills_low_value(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=1)

    # Candidate universe (child_ids[0] is active, need a "candidate" one).
    # Add a frozen universe to test pruning.
    frozen_id = new_id("uni")
    frozen_uni = UniverseModel(
        universe_id=frozen_id,
        big_bang_id=run_id,
        parent_universe_id=root_id,
        lineage_path=[root_id, frozen_id],
        branch_from_tick=1,
        branch_depth=1,
        status="frozen",
        branch_reason="prunable",
        branch_delta=None,
        current_tick=0,
        latest_metrics={},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(frozen_uni)
    await db_session.commit()

    resp = await client.post(
        f"/api/multiverse/{run_id}/prune",
        json={"min_value": 0.9, "dry_run": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is False
    assert frozen_id in data["pruned_universe_ids"]

    # Verify it was killed.
    get_resp = await client.get(f"/api/universes/{frozen_id}")
    assert get_resp.json()["status"] == "killed"


async def test_prune_nonexistent_404(client: AsyncClient):
    resp = await client.post(
        "/api/multiverse/no_such_run/prune",
        json={"min_value": 0.5, "dry_run": True},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/focus-branch
# ---------------------------------------------------------------------------


async def test_focus_branch_returns_subtree(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_id, grandchild_id = await _seed_depth2_lineage(db_session)

    resp = await client.post(
        f"/api/multiverse/{run_id}/focus-branch",
        json={"universe_id": child_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    node_ids = {n["universe_id"] for n in data["nodes"]}

    # Focus on child — should include child and grandchild but not root.
    assert child_id in node_ids
    assert grandchild_id in node_ids
    # Root is not in child's lineage path starting at child — actually root
    # is in child's lineage_path so it may appear; test the grandchild presence.
    assert grandchild_id in node_ids


async def test_focus_branch_nonexistent_universe_404(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, _ = await _seed_run_with_universes(db_session)

    resp = await client.post(
        f"/api/multiverse/{run_id}/focus-branch",
        json={"universe_id": "no_such_universe"},
    )
    assert resp.status_code == 404


async def test_focus_branch_nonexistent_run_404(client: AsyncClient):
    resp = await client.post(
        "/api/multiverse/no_such_run/focus-branch",
        json={"universe_id": "any"},
    )
    assert resp.status_code == 404


async def test_legacy_lineage_visible_ids_excludes_sibling_branches():
    from app.api.multiverses import _visible_lineage_ids

    root = "root"
    child = "child"
    sibling = "sibling"
    grandchild = "grandchild"
    rows = [
        SimpleNamespace(id=root, parent_multiverse_id=None),
        SimpleNamespace(id=child, parent_multiverse_id=root),
        SimpleNamespace(id=sibling, parent_multiverse_id=root),
        SimpleNamespace(id=grandchild, parent_multiverse_id=child),
    ]

    assert _visible_lineage_ids(rows, child) == {root, child, grandchild}


async def test_canonical_simulate_next_tick_requires_idempotency_key(client: AsyncClient):
    from app.db import models
    from app.db.session import get_db
    from backend.app.main import app

    multiverse_id = uuid4()

    class FakeDB:
        def get(self, model, object_id):
            if model is models.Multiverse and object_id == multiverse_id:
                return SimpleNamespace(id=multiverse_id)
            return None

    app.dependency_overrides[get_db] = lambda: FakeDB()
    try:
        resp = await client.post(f"/api/multiverses/{multiverse_id}/simulate-next-tick")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


async def test_canonical_simulate_next_tick_accepts_header_idempotency_key(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import multiverses as multiverses_api
    from app.db import models
    from app.db.session import get_db
    from backend.app.main import app

    now = datetime.now(timezone.utc)
    big_bang_id = uuid4()
    multiverse_id = uuid4()
    captured: dict[str, str] = {}

    class FakeDB:
        def get(self, model, object_id):
            if model is models.Multiverse and object_id == multiverse_id:
                return SimpleNamespace(id=multiverse_id, big_bang_id=big_bang_id)
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

    def fake_run_next_tick(db, *, multiverse, idempotency_key, force=False):
        captured["idempotency_key"] = idempotency_key
        return SimpleNamespace(
            id=uuid4(),
            big_bang_id=big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=0,
            ui_label="A-T0",
            status="final",
            provisional_bundle={},
            final_bundle={},
            summary="ok",
            artifact_id=None,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(multiverses_api, "run_next_tick", fake_run_next_tick)
    app.dependency_overrides[get_db] = lambda: FakeDB()
    try:
        resp = await client.post(
            f"/api/multiverses/{multiverse_id}/simulate-next-tick",
            headers={"Idempotency-Key": "retry-safe-key"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200, resp.text
    assert captured["idempotency_key"] == "retry-safe-key"


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/compare
# ---------------------------------------------------------------------------


async def test_compare_two_universes(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=1)
    child_id = child_ids[0]

    resp = await client.post(
        f"/api/multiverse/{run_id}/compare",
        json={"universe_ids": [root_id, child_id], "aspect": "metrics"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["aspect"] == "metrics"
    assert len(data["comparison"]) == 2
    assert all("universe_id" in c for c in data["comparison"])
    assert all("metrics" in c for c in data["comparison"])


async def test_compare_missing_universe_404(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, _ = await _seed_run_with_universes(db_session)

    resp = await client.post(
        f"/api/multiverse/{run_id}/compare",
        json={"universe_ids": [root_id, "no_such_universe"], "aspect": "metrics"},
    )
    assert resp.status_code == 404


async def test_compare_nonexistent_run_404(client: AsyncClient):
    resp = await client.post(
        "/api/multiverse/no_such_run/compare",
        json={"universe_ids": ["a", "b"], "aspect": "metrics"},
    )
    assert resp.status_code == 404


async def test_compare_status_aspect(client: AsyncClient, db_session: AsyncSession):
    run_id, root_id, child_ids = await _seed_run_with_universes(db_session, n_extra_children=1)
    child_id = child_ids[0]

    resp = await client.post(
        f"/api/multiverse/{run_id}/compare",
        json={"universe_ids": [root_id, child_id], "aspect": "status"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["aspect"] == "status"
    assert all("frozen_at" in c for c in data["comparison"])
