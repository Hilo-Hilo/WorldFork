"""Integration tests for backend.app.branching.lineage.

Covers:
* tree-build correctness (nodes, edges, depth_index, ticks),
* :class:`LineageCache` set/get/invalidate roundtrip,
* :func:`get_descendants` recursion,
* :func:`get_lineage` direct lookup,
* :func:`prune_low_value` candidate selection + dry-run guard.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Reuse shadow tables from the branch_engine integration tests.
from backend.tests.integration.test_branch_engine import (
    _BranchNodeShadow,
    _ShadowBase,
    _UniverseShadow,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_ShadowBase.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    import backend.app.models.branches as bm
    import backend.app.models.universes as um

    monkeypatch.setattr(um, "UniverseModel", _UniverseShadow)
    monkeypatch.setattr(bm, "BranchNodeModel", _BranchNodeShadow)
    yield


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Builders for a small tree:
#   root (depth 0)
#   ├── child1 (depth 1, branch_tick 3)
#   │     └── grand (depth 2, branch_tick 5)
#   └── child2 (depth 1, branch_tick 4)
# ---------------------------------------------------------------------------


async def _seed_tree(session: AsyncSession, big_bang_id: str = "bb-tree-1") -> dict:
    root = _UniverseShadow(
        universe_id="U_root_tree001",
        big_bang_id=big_bang_id,
        parent_universe_id=None,
        lineage_path=["U_root_tree001"],
        branch_from_tick=0,
        branch_depth=0,
        status="active",
        branch_reason="",
        branch_delta=None,
        current_tick=10,
        latest_metrics={"divergence": 1.0},
        child_universe_ids=["U_chld1_tree", "U_chld2_tree"],
        created_at=_now(),
    )
    c1 = _UniverseShadow(
        universe_id="U_chld1_tree",
        big_bang_id=big_bang_id,
        parent_universe_id="U_root_tree001",
        lineage_path=["U_root_tree001", "U_chld1_tree"],
        branch_from_tick=3,
        branch_depth=1,
        status="active",
        branch_reason="reason-1",
        branch_delta={"type": "parameter_shift"},
        current_tick=8,
        latest_metrics={"divergence": 0.7},
        child_universe_ids=["U_grand_tree1"],
        created_at=_now(),
    )
    c2 = _UniverseShadow(
        universe_id="U_chld2_tree",
        big_bang_id=big_bang_id,
        parent_universe_id="U_root_tree001",
        lineage_path=["U_root_tree001", "U_chld2_tree"],
        branch_from_tick=4,
        branch_depth=1,
        status="active",
        branch_reason="reason-2",
        branch_delta=None,
        current_tick=7,
        latest_metrics={"divergence": 0.05},  # low — pruning candidate
        child_universe_ids=[],
        created_at=_now(),
    )
    g = _UniverseShadow(
        universe_id="U_grand_tree1",
        big_bang_id=big_bang_id,
        parent_universe_id="U_chld1_tree",
        lineage_path=["U_root_tree001", "U_chld1_tree", "U_grand_tree1"],
        branch_from_tick=5,
        branch_depth=2,
        status="candidate",
        branch_reason="reason-3",
        branch_delta=None,
        current_tick=6,
        latest_metrics={"divergence": 0.4},
        child_universe_ids=[],
        created_at=_now(),
    )

    bn_root = _BranchNodeShadow(
        universe_id=root.universe_id,
        parent_universe_id=None,
        child_universe_ids=[c1.universe_id, c2.universe_id],
        depth=0, branch_tick=0,
        branch_point_id="root@t0",
        branch_trigger="big_bang",
        branch_delta={},
        status="active",
        metrics_summary={"divergence": 1.0},
        cost_estimate={},
        descendant_count=3,
        lineage_path=root.lineage_path,
    )
    bn_c1 = _BranchNodeShadow(
        universe_id=c1.universe_id,
        parent_universe_id=root.universe_id,
        child_universe_ids=[g.universe_id],
        depth=1, branch_tick=3,
        branch_point_id=f"{root.universe_id}@t3",
        branch_trigger="reason-1",
        branch_delta={},
        status="active",
        metrics_summary={"divergence": 0.7},
        cost_estimate={},
        descendant_count=1,
        lineage_path=c1.lineage_path,
    )
    bn_c2 = _BranchNodeShadow(
        universe_id=c2.universe_id,
        parent_universe_id=root.universe_id,
        child_universe_ids=[],
        depth=1, branch_tick=4,
        branch_point_id=f"{root.universe_id}@t4",
        branch_trigger="reason-2",
        branch_delta={},
        status="active",
        metrics_summary={"divergence": 0.05},
        cost_estimate={},
        descendant_count=0,
        lineage_path=c2.lineage_path,
    )
    bn_g = _BranchNodeShadow(
        universe_id=g.universe_id,
        parent_universe_id=c1.universe_id,
        child_universe_ids=[],
        depth=2, branch_tick=5,
        branch_point_id=f"{c1.universe_id}@t5",
        branch_trigger="reason-3",
        branch_delta={},
        status="candidate",
        metrics_summary={"divergence": 0.4},
        cost_estimate={},
        descendant_count=0,
        lineage_path=g.lineage_path,
    )
    session.add_all([root, c1, c2, g, bn_root, bn_c1, bn_c2, bn_g])
    await session.flush()
    return {
        "big_bang_id": big_bang_id,
        "root": root.universe_id,
        "c1": c1.universe_id,
        "c2": c2.universe_id,
        "grand": g.universe_id,
    }


# ---------------------------------------------------------------------------
# build_tree
# ---------------------------------------------------------------------------


async def test_build_tree_returns_correct_structure(db_session):
    info = await _seed_tree(db_session)

    from backend.app.branching.lineage import build_tree

    tree = await build_tree(db_session, info["big_bang_id"])
    assert tree["big_bang_id"] == info["big_bang_id"]
    assert tree["root_id"] == info["root"]
    assert tree["node_count"] == 4
    assert tree["edge_count"] == 3
    # 3 unique branch_ticks: 0, 3, 4, 5
    assert sorted(tree["ticks"]) == [0, 3, 4, 5]
    # depth_index keys are stringified ints
    assert sorted(tree["depth_index"].keys()) == ["0", "1", "2"]
    assert info["root"] in tree["depth_index"]["0"]
    assert sorted(tree["depth_index"]["1"]) == sorted([info["c1"], info["c2"]])
    assert info["grand"] in tree["depth_index"]["2"]

    # Edges
    edge_pairs = {(e["source"], e["target"]) for e in tree["edges"]}
    assert (info["root"], info["c1"]) in edge_pairs
    assert (info["root"], info["c2"]) in edge_pairs
    assert (info["c1"], info["grand"]) in edge_pairs


# ---------------------------------------------------------------------------
# LineageCache
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async stand-in for redis.asyncio.Redis used by LineageCache."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


async def test_lineage_cache_set_get_invalidate_roundtrip():
    from backend.app.branching.lineage import LineageCache

    redis = _FakeRedis()
    cache = LineageCache(redis)

    # Miss
    assert await cache.get_tree("bb-x") is None

    # Set
    payload = {"big_bang_id": "bb-x", "node_count": 2}
    await cache.set_tree("bb-x", payload, ttl=60)
    # Internal store
    assert "wf:lineage:bb-x" in redis.store
    assert json.loads(redis.store["wf:lineage:bb-x"]) == payload

    # Hit
    cached = await cache.get_tree("bb-x")
    assert cached == payload

    # Invalidate
    await cache.invalidate("bb-x")
    assert await cache.get_tree("bb-x") is None


# ---------------------------------------------------------------------------
# get_descendants / get_lineage
# ---------------------------------------------------------------------------


async def test_get_descendants_returns_subtree(db_session):
    info = await _seed_tree(db_session)

    from backend.app.branching.lineage import get_descendants

    # Root → root + 3 descendants
    desc_root = await get_descendants(db_session, info["root"])
    assert sorted(desc_root) == sorted(
        [info["root"], info["c1"], info["c2"], info["grand"]]
    )

    # c1 → c1 + grand
    desc_c1 = await get_descendants(db_session, info["c1"])
    assert sorted(desc_c1) == sorted([info["c1"], info["grand"]])

    # c2 → leaf
    desc_c2 = await get_descendants(db_session, info["c2"])
    assert desc_c2 == [info["c2"]]

    # Unknown universe
    assert await get_descendants(db_session, "missing") == []


async def test_get_lineage_uses_lineage_path_array(db_session):
    info = await _seed_tree(db_session)

    from backend.app.branching.lineage import get_lineage

    assert await get_lineage(db_session, info["grand"]) == [
        info["root"], info["c1"], info["grand"],
    ]
    assert await get_lineage(db_session, info["root"]) == [info["root"]]
    assert await get_lineage(db_session, "missing") == []


# ---------------------------------------------------------------------------
# prune_low_value
# ---------------------------------------------------------------------------


async def test_prune_low_value_dry_run_does_not_mutate(db_session):
    info = await _seed_tree(db_session)

    from backend.app.branching.prune import prune_low_value

    out = await prune_low_value(
        db_session,
        info["big_bang_id"],
        min_value=0.1,
        dry_run=True,
    )
    assert out["dry_run"] is True
    assert out["killed"] == 0
    cand_ids = [c["universe_id"] for c in out["candidates"]]
    # Only c2 has divergence < 0.1.  The grandchild is candidate-status (not
    # active), so it's excluded by the auto-prune filter.
    assert cand_ids == [info["c2"]]

    # Status unchanged in dry-run
    c2_row = await db_session.get(_UniverseShadow, info["c2"])
    assert c2_row.status == "active"


async def test_prune_low_value_kills_active_low_divergence(db_session):
    info = await _seed_tree(db_session)

    from backend.app.branching.prune import prune_low_value

    out = await prune_low_value(
        db_session,
        info["big_bang_id"],
        min_value=0.1,
        dry_run=False,
    )
    assert out["killed"] == 1
    c2_row = await db_session.get(_UniverseShadow, info["c2"])
    assert c2_row.status == "killed"
    assert c2_row.killed_at is not None
    bn_row = await db_session.get(_BranchNodeShadow, info["c2"])
    assert bn_row.status == "killed"

    # Other rows untouched.
    root_row = await db_session.get(_UniverseShadow, info["root"])
    assert root_row.status == "active"
