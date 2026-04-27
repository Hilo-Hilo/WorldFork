from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api import big_bangs as big_bangs_api
from app.api import multiverses as multiverses_api
from app.api import scenario_bank as scenario_bank_api
from app.api.utils import commit_or_500
from app.db.session import get_db
from app.llm.audit import LLMCallError
from app.main import app


client = TestClient(app)
MISSING_ID = "00000000-0000-0000-0000-000000000000"


class MissingObjectDB:
    def get(self, model, object_id):
        return None

    def scalars(self, statement):
        raise AssertionError("child query ran before parent existence check")

    def scalar(self, statement):
        raise AssertionError("child query ran before parent existence check")


@contextmanager
def missing_object_db():
    app.dependency_overrides[get_db] = lambda: MissingObjectDB()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_child_resource_routes_404_for_missing_parents():
    paths = [
        f"/api/big-bangs/{MISSING_ID}/multiverses",
        f"/api/big-bangs/{MISSING_ID}/actors",
        f"/api/big-bangs/{MISSING_ID}/graphs",
        f"/api/big-bangs/{MISSING_ID}/emotion-observability",
        f"/api/big-bangs/{MISSING_ID}/initialization",
        f"/api/big-bangs/{MISSING_ID}/initialization/scenario-text",
        f"/api/big-bangs/{MISSING_ID}/initialization/corpus",
        f"/api/big-bangs/{MISSING_ID}/initialization/actors",
        f"/api/big-bangs/{MISSING_ID}/initialization/traits",
        f"/api/big-bangs/{MISSING_ID}/initialization/graphs",
        f"/api/big-bangs/{MISSING_ID}/initialization/emotion-baseline",
        f"/api/big-bangs/{MISSING_ID}/initialization/sociology-baseline",
        f"/api/big-bangs/{MISSING_ID}/initialization/audit",
        f"/api/multiverses/{MISSING_ID}/ticks",
        f"/api/multiverses/{MISSING_ID}/graphs",
        f"/api/multiverses/{MISSING_ID}/graphs/trust",
        f"/api/multiverses/{MISSING_ID}/sociology-signals",
        f"/api/multiverses/{MISSING_ID}/emotion-observability",
        f"/api/actors/{MISSING_ID}/events",
        f"/api/actors/{MISSING_ID}/graphs",
        f"/api/actors/{MISSING_ID}/sociology-signals",
        f"/api/actors/{MISSING_ID}/emotion-observability",
        f"/api/ticks/{MISSING_ID}/reasoning-traces",
        f"/api/ticks/{MISSING_ID}/tool-calls",
        f"/api/ticks/{MISSING_ID}/emotion-observability",
        f"/api/ticks/{MISSING_ID}/god-review",
        f"/api/god-reviews/{MISSING_ID}/tool-calls",
    ]

    with missing_object_db():
        for path in paths:
            response = client.get(path)
            assert response.status_code == 404, path


def test_default_body_routes_accept_omitted_body_before_parent_lookup():
    paths = [
        f"/api/big-bangs/{MISSING_ID}/reports/final",
        f"/api/big-bangs/{MISSING_ID}/run-until-complete",
        f"/api/multiverses/{MISSING_ID}/simulate-next-tick",
        f"/api/multiverses/{MISSING_ID}/simulate-ticks",
        f"/api/multiverses/{MISSING_ID}/report",
    ]

    with missing_object_db():
        for path in paths:
            response = client.post(path)
            assert response.status_code == 404, path


def test_report_request_contract_does_not_advertise_unused_regenerate():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    report_request = response.json()["components"]["schemas"]["ReportRequest"]
    assert "regenerate" not in report_request["properties"]


def test_actor_emotion_observability_route_registered_once():
    matches = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/actors/{actor_id}/emotion-observability"
        and "GET" in getattr(route, "methods", set())
    ]

    assert len(matches) == 1


def test_agent_openapi_has_cli_first_routes():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    paths = openapi["paths"]

    assert "/api/agent/status" in paths
    assert "/api/agent/discover" in paths
    assert "/api/agent/runs" in paths
    assert "/api/agent/runs/{run_id}/workspace" in paths
    assert "/api/agent/universes/{multiverse_id}/trace" in paths
    assert "/api/agent/jobs/{job_id}/wait" in paths
    assert "/api/frontend/bootstrap" not in paths


def test_mutation_routes_have_non_empty_response_contracts():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    route_methods = [
        ("/api/big-bangs/{big_bang_id}/reports/final", "post"),
        ("/api/big-bangs/{big_bang_id}/run-until-complete", "post"),
        ("/api/multiverses/{multiverse_id}/report", "post"),
        ("/api/god-reviews/{god_review_id}/regenerate-summary", "post"),
    ]
    for path, method in route_methods:
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema
        assert schema != {}


def test_big_bang_create_maps_llm_unavailable_to_sanitized_503(monkeypatch):
    class RollbackDB:
        rolled_back = False

        def rollback(self):
            self.rolled_back = True

    db = RollbackDB()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        big_bangs_api,
        "create_big_bang",
        lambda _db, _payload: (_ for _ in ()).throw(LLMCallError("LLM unavailable")),
    )
    try:
        response = client.post("/api/big-bangs", json={"name": "No key path", "scenario_text": "x"})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"detail": "LLM unavailable"}
    assert db.rolled_back is True


def test_scenario_bank_create_maps_llm_unavailable_to_sanitized_503(monkeypatch):
    class RollbackDB:
        rolled_back = False

        def rollback(self):
            self.rolled_back = True

    db = RollbackDB()
    monkeypatch.setattr(
        scenario_bank_api,
        "scenario_to_big_bang_payload",
        lambda scenario_id: {"name": "Scenario", "scenario_text": "x"},
    )
    monkeypatch.setattr(
        scenario_bank_api,
        "create_big_bang",
        lambda _db, _payload: (_ for _ in ()).throw(LLMCallError("LLM unavailable")),
    )

    with pytest.raises(HTTPException) as exc:
        scenario_bank_api.create_big_bang_from_scenario("scenario-1", db=db)

    assert exc.value.status_code == 503
    assert exc.value.detail == "LLM unavailable"
    assert db.rolled_back is True


def test_simulate_next_tick_value_error_returns_conflict(monkeypatch):
    class RollbackDB:
        rolled_back = False

        def get(self, model, object_id):
            return SimpleNamespace(id=object_id)

        def rollback(self):
            self.rolled_back = True

    db = RollbackDB()
    monkeypatch.setattr(
        multiverses_api,
        "run_next_tick",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("multiverse has reached max_ticks")),
    )

    with pytest.raises(HTTPException) as exc:
        multiverses_api.simulate(uuid4(), db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail == "multiverse has reached max_ticks"
    assert db.rolled_back is True


def test_final_report_rejects_non_terminal_multiverse(monkeypatch):
    class ScalarResult:
        def all(self):
            return [SimpleNamespace(ui_label="M1", status="active")]

    class FinalReportDB:
        def get(self, model, object_id):
            return SimpleNamespace(id=object_id)

        def scalars(self, statement):
            return ScalarResult()

    monkeypatch.setattr(
        big_bangs_api,
        "generate_final_big_bang_report",
        lambda *args, **kwargs: pytest.fail("final report should not generate"),
    )

    with pytest.raises(HTTPException) as exc:
        big_bangs_api.final_report(uuid4(), db=FinalReportDB())

    assert exc.value.status_code == 409
    assert "final report requires terminal multiverses" in exc.value.detail


def test_commit_or_500_sanitizes_database_errors():
    class IntegrityDB:
        def commit(self):
            raise IntegrityError("statement", {}, Exception("raw duplicate detail"))

        def rollback(self):
            self.rolled_back = True

    class BrokenDB:
        def commit(self):
            raise SQLAlchemyError("raw connection detail")

        def rollback(self):
            self.rolled_back = True

    for db, expected_status, expected_detail in [
        (IntegrityDB(), 409, "database integrity conflict"),
        (BrokenDB(), 500, "database commit failed"),
    ]:
        try:
            commit_or_500(db)
        except Exception as exc:
            assert exc.status_code == expected_status
            assert exc.detail == expected_detail
            assert db.rolled_back is True
