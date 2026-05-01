from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.artifacts import has_secure_debug_artifact_gate
from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/big-bangs/{big_bang_id}/initialization", tags=["initialization"])


@router.get("")
def initialization(
    big_bang_id: UUID,
    debug: bool = False,
    x_worldfork_debug_token: str | None = Header(default=None, alias="X-WorldFork-Debug-Token"),
    db: Session = Depends(get_db),
):
    big_bang = require(db, models.BigBang, big_bang_id)
    include_debug = has_secure_debug_artifact_gate(debug, x_worldfork_debug_token)
    return {
        "big_bang_id": big_bang.id,
        "scenario_text_present": bool(big_bang.scenario_input.get("scenario_text")),
        "plain_text_corpus": _public_corpus(big_bang.scenario_input.get("plain_text_corpus", {}), include_debug=include_debug),
        "initializer_output": _public_initializer_output(big_bang.scenario_input.get("initializer_output", {}), include_debug=include_debug),
    }


@router.get("/scenario-text")
def scenario_text(
    big_bang_id: UUID,
    debug: bool = False,
    x_worldfork_debug_token: str | None = Header(default=None, alias="X-WorldFork-Debug-Token"),
    db: Session = Depends(get_db),
):
    big_bang = require(db, models.BigBang, big_bang_id)
    if not has_secure_debug_artifact_gate(debug, x_worldfork_debug_token):
        raise HTTPException(status_code=403, detail="scenario text requires secure debug gate")
    return {
        "big_bang_id": big_bang.id,
        "scenario_text": big_bang.scenario_input.get("scenario_text", ""),
    }


@router.get("/corpus")
def corpus(
    big_bang_id: UUID,
    debug: bool = False,
    x_worldfork_debug_token: str | None = Header(default=None, alias="X-WorldFork-Debug-Token"),
    db: Session = Depends(get_db),
):
    big_bang = require(db, models.BigBang, big_bang_id)
    return _public_corpus(
        big_bang.scenario_input.get("plain_text_corpus", {}),
        include_debug=has_secure_debug_artifact_gate(debug, x_worldfork_debug_token),
    )


@router.get("/actors")
def actors(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(select(models.Actor).where(models.Actor.big_bang_id == big_bang_id)).all()


@router.get("/traits")
def traits(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    actors = db.scalars(select(models.Actor).where(models.Actor.big_bang_id == big_bang_id)).all()
    return [
        {
            "actor_id": actor.id,
            "name": actor.name,
            "actor_type": actor.actor_type,
            "trait_vector": (actor.archetype or {}).get("trait_vector"),
        }
        for actor in actors
    ]


@router.get("/graphs")
def graphs(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    edges = db.scalars(select(models.GraphEdge).where(models.GraphEdge.big_bang_id == big_bang_id)).all()
    snapshots = db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.big_bang_id == big_bang_id)).all()
    return {"edges": edges, "snapshots": snapshots}


@router.get("/emotion-baseline")
def emotion_baseline(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    observations = db.scalars(
        select(models.EmotionObservation).where(
            models.EmotionObservation.big_bang_id == big_bang_id,
            models.EmotionObservation.tick_index == 0,
        )
    ).all()
    snapshots = db.scalars(
        select(models.EmotionGraphSnapshot).where(
            models.EmotionGraphSnapshot.big_bang_id == big_bang_id,
            models.EmotionGraphSnapshot.tick_index == 0,
        )
    ).all()
    return {"observations": observations, "snapshots": snapshots}


@router.get("/sociology-baseline")
def sociology_baseline(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    signals = db.scalars(
        select(models.SociologySignal).where(
            models.SociologySignal.big_bang_id == big_bang_id,
            models.SociologySignal.tick_index == 0,
        )
    ).all()
    influences = db.scalars(
        select(models.SociologyPromptInfluence).where(
            models.SociologyPromptInfluence.big_bang_id == big_bang_id,
            models.SociologyPromptInfluence.tick_index == 0,
        )
    ).all()
    return {"signals": signals, "prompt_influences": influences}


def audit_llm_call(call: models.LLMCall, *, include_debug: bool = False) -> dict:
    meta = dict(call.meta or {})
    if not include_debug:
        meta.pop("raw_request_artifact_id", None)
        meta.pop("raw_response_artifact_id", None)
    return {
        "id": call.id,
        "big_bang_id": call.big_bang_id,
        "provider": call.provider,
        "model": call.model,
        "purpose": call.purpose,
        "status": call.status,
        "request_artifact_id": call.request_artifact_id,
        "response_artifact_id": call.response_artifact_id,
        "meta": meta,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
    }


def audit_artifact(artifact: models.Artifact, *, include_debug: bool = False) -> dict:
    payload = {
        "id": artifact.id,
        "big_bang_id": artifact.big_bang_id,
        "kind": artifact.kind,
        "content_type": artifact.content_type,
        "content_hash": artifact.content_hash,
        "size_bytes": artifact.size_bytes,
        "debug_only": artifact.debug_only,
        "meta": artifact.meta,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }
    if include_debug:
        payload["path"] = artifact.path
    return payload


@router.get("/audit")
def audit(
    big_bang_id: UUID,
    debug: bool = False,
    x_worldfork_debug_token: str | None = Header(default=None, alias="X-WorldFork-Debug-Token"),
    db: Session = Depends(get_db),
):
    require(db, models.BigBang, big_bang_id)
    include_debug = has_secure_debug_artifact_gate(debug, x_worldfork_debug_token)
    calls = db.scalars(
        select(models.LLMCall)
        .where(
            models.LLMCall.big_bang_id == big_bang_id,
            models.LLMCall.purpose.like("initializer%"),
        )
        .order_by(models.LLMCall.created_at)
    ).all()
    artifact_query = select(models.Artifact).where(models.Artifact.big_bang_id == big_bang_id)
    if not include_debug:
        artifact_query = artifact_query.where(models.Artifact.debug_only.is_(False))
    artifacts = db.scalars(artifact_query.order_by(models.Artifact.created_at)).all()
    return {
        "llm_calls": [audit_llm_call(call, include_debug=include_debug) for call in calls],
        "artifacts": [audit_artifact(artifact, include_debug=include_debug) for artifact in artifacts],
    }


def _public_initializer_output(value: dict, *, include_debug: bool) -> dict:
    if include_debug or not isinstance(value, dict):
        return value
    sanitized = dict(value)
    sanitized.pop("plain_text_corpus", None)
    return sanitized


def _public_corpus(value: dict, *, include_debug: bool) -> dict:
    if include_debug or not isinstance(value, dict):
        return value
    sanitized = dict(value)
    sanitized.pop("raw_text_artifact_id", None)
    sanitized["chunk_artifacts"] = _strip_artifact_ids(sanitized.get("chunk_artifacts", []))
    sanitized["chunk_summaries"] = _strip_artifact_ids(sanitized.get("chunk_summaries", []))
    sanitized["simulation_brief"] = _public_simulation_brief(sanitized.get("simulation_brief", {}))
    sanitized.pop("simulation_brief_artifact_id", None)
    return sanitized


def _public_simulation_brief(value) -> dict:
    if not isinstance(value, dict):
        return {}
    sanitized = dict(value)
    if "text" in sanitized:
        sanitized["text"] = "[debug gated]"
    sanitized.pop("raw_text_artifact_id", None)
    sanitized["chunk_summaries"] = _strip_artifact_ids(sanitized.get("chunk_summaries", []))
    return sanitized


def _strip_artifact_ids(value):
    if not isinstance(value, list):
        return []
    stripped = []
    for item in value:
        if isinstance(item, dict):
            clean = dict(item)
            clean.pop("artifact_id", None)
            stripped.append(clean)
    return stripped
