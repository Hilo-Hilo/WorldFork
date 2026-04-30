from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def multiverse_runtime_config_version(db: Session, multiverse: models.Multiverse) -> int | None:
    state = multiverse.state or {}
    override_version = state.get("runtime_config_version") or (state.get("runtime_overrides") or {}).get(
        "config_version"
    )
    if override_version is not None:
        return int(override_version)

    big_bang = db.get(models.BigBang, multiverse.big_bang_id)
    if big_bang is not None and big_bang.current_config_version:
        return int(big_bang.current_config_version)

    latest = db.scalar(
        select(func.max(models.BigBangConfig.version)).where(models.BigBangConfig.big_bang_id == multiverse.big_bang_id)
    )
    return int(latest) if latest is not None else None


def load_runtime_config(db: Session, multiverse: models.Multiverse) -> models.BigBangConfig | None:
    version = multiverse_runtime_config_version(db, multiverse)
    if version is not None:
        config = db.scalar(
            select(models.BigBangConfig).where(
                models.BigBangConfig.big_bang_id == multiverse.big_bang_id,
                models.BigBangConfig.version == version,
            )
        )
        if config is not None:
            return config
    return db.scalar(
        select(models.BigBangConfig)
        .where(models.BigBangConfig.big_bang_id == multiverse.big_bang_id)
        .order_by(models.BigBangConfig.version.desc())
        .limit(1)
    )


def simulation_config_for_multiverse(db: Session, multiverse: models.Multiverse) -> dict[str, Any]:
    config = load_runtime_config(db, multiverse)
    simulation_config = dict((config.simulation_config or {}) if config else {})
    overrides = (multiverse.state or {}).get("runtime_overrides") or {}
    simulation_config.update(overrides.get("simulation_config") or {})
    if "max_ticks" in overrides:
        simulation_config["max_ticks"] = overrides["max_ticks"]
    return simulation_config


def branch_policy_for_multiverse(db: Session, multiverse: models.Multiverse) -> dict[str, Any]:
    config = load_runtime_config(db, multiverse)
    branch_policy = dict((config.branch_policy or {}) if config else {})
    overrides = (multiverse.state or {}).get("runtime_overrides") or {}
    branch_policy.update(overrides.get("branch_policy") or {})
    return branch_policy
