from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.domains.costs.service import (
    CostEstimateRequest,
    estimate_big_bang_cost,
    summarize_tick_cost,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Can't sort tables for DROP", category=SAWarning)
            models.Base.metadata.drop_all(engine)


def _seed_world(db: Session) -> tuple[models.BigBang, models.Multiverse, models.TickSnapshot]:
    big_bang = models.BigBang(
        name="Cost run",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": 4},
            model_config={},
            branch_policy={"branch_threshold": 0.45},
        )
    )
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        ui_label="M1",
        depth=0,
        status="active",
        state={},
    )
    db.add(multiverse)
    db.flush()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        ui_label="M1:T1",
        status="final",
        provisional_bundle={},
        final_bundle={},
        summary="done",
        created_at=start,
        updated_at=start + timedelta(seconds=60),
    )
    db.add(tick)
    db.flush()
    return big_bang, multiverse, tick


def test_tick_cost_summary_uses_openrouter_actuals_and_estimates_non_openrouter(db, monkeypatch):
    big_bang, _multiverse, tick = _seed_world(db)
    start = tick.created_at
    db.add_all(
        [
            models.LLMCall(
                big_bang_id=big_bang.id,
                provider="openrouter",
                model="deepseek/deepseek-v4-flash",
                purpose="agent_cohort_actor_tick_1",
                status="succeeded",
                created_at=start + timedelta(seconds=10),
                updated_at=start + timedelta(seconds=20),
                meta={
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                        "cost": 0.001,
                        "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
                    }
                },
            ),
            models.LLMCall(
                big_bang_id=big_bang.id,
                provider="openai-codex",
                model="gpt-5.4",
                purpose=f"god_review_{tick.multiverse_id}_tick_1_iter_1",
                status="succeeded",
                created_at=start + timedelta(seconds=30),
                updated_at=start + timedelta(seconds=50),
                meta={
                    "usage": {
                        "prompt_tokens": 200,
                        "completion_tokens": 50,
                        "total_tokens": 250,
                    }
                },
            ),
        ]
    )
    db.flush()
    monkeypatch.setattr(
        "app.domains.costs.service.openrouter_pricing_table",
        lambda: {
            "status": "ok",
            "models": {
                "deepseek/deepseek-v4-flash": {
                    "prompt": 0.000001,
                    "completion": 0.000002,
                    "input_cache_read": 0.00000025,
                    "input_cache_write": 0.000001,
                },
                "openai/gpt-5.4": {"prompt": 0.000001, "completion": 0.000002},
            },
        },
    )

    summary = summarize_tick_cost(db, tick=tick)

    assert summary["currency"] == "USD"
    assert summary["actual"]["openrouter_usd"] == pytest.approx(0.001)
    assert summary["estimated"]["non_openrouter_usd"] == pytest.approx(0.0003)
    assert summary["tokens"]["prompt_tokens"] == 300
    assert summary["tokens"]["completion_tokens"] == 70
    assert summary["tokens"]["cached_tokens"] == 40
    assert summary["by_agent"]["cohort_agent"]["actual_openrouter_usd"] == pytest.approx(0.001)
    assert summary["by_agent"]["god_agent"]["estimated_usd"] == pytest.approx(0.0003)


def test_big_bang_estimate_accounts_for_agent_counts_branching_and_parallel_time(db, monkeypatch):
    big_bang, _multiverse, _tick = _seed_world(db)
    for index in range(4):
        db.add(
            models.Actor(
                big_bang_id=big_bang.id,
                actor_type="cohort",
                name=f"Cohort {index}",
                description=None,
                archetype={"state": {"represented_population": 100}},
                status="active",
            )
        )
    db.add(
        models.Actor(
            big_bang_id=big_bang.id,
            actor_type="hero",
            name="Hero",
            description=None,
            archetype={},
            status="active",
        )
    )
    db.flush()
    monkeypatch.setattr(
        "app.domains.costs.service.openrouter_pricing_table",
        lambda: {
            "status": "ok",
            "models": {
                "deepseek/deepseek-v4-flash": {"prompt": 0.000001, "completion": 0.000002},
                "openai/gpt-5.4": {"prompt": 0.00001, "completion": 0.00002},
            },
        },
    )

    estimate = estimate_big_bang_cost(
        db,
        big_bang=big_bang,
        request=CostEstimateRequest(remaining_ticks=2, max_parallel_cohort_decisions=2),
    )

    assert estimate["scope"] == "post_big_bang"
    assert estimate["assumptions"]["cohort_count"] == 4
    assert estimate["assumptions"]["hero_count"] == 1
    assert estimate["by_agent"]["cohort_agent"]["estimated_calls"] >= 8
    assert estimate["time_estimate"]["parallelism"]["cohort_agent"] == 2
    assert estimate["time_estimate"]["estimated_wall_seconds"] > 0
    assert estimate["estimated"]["including_non_openrouter_usd"] >= estimate["estimated"]["openrouter_only_usd"]
