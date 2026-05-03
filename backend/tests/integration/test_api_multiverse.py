"""Integration tests for canonical /api/multiverses behavior."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio]


async def test_lineage_visible_ids_excludes_sibling_branches():
    from app.domains.multiverse.routes import _visible_lineage_ids

    root = uuid4()
    child = uuid4()
    sibling = uuid4()
    grandchild = uuid4()
    rows = [
        SimpleNamespace(id=root, parent_multiverse_id=None),
        SimpleNamespace(id=child, parent_multiverse_id=root),
        SimpleNamespace(id=sibling, parent_multiverse_id=root),
        SimpleNamespace(id=grandchild, parent_multiverse_id=child),
    ]

    assert _visible_lineage_ids(rows, child) == {root, child, grandchild}


async def test_simulate_next_tick_requires_idempotency_key(client: AsyncClient):
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


async def test_simulate_next_tick_accepts_header_idempotency_key(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.domains.multiverse import routes as multiverses_api
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
