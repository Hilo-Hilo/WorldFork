from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import models


def update_emotion_observability_graphs(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    tick_snapshot_id=None,
    emotion_self_ratings: list[dict] | None = None,
    event_summaries: list[dict] | None = None,
    god_agent_review: dict | None = None,
) -> dict:
    values = []
    for rating in emotion_self_ratings or []:
        value = float(rating.get("value", 0))
        observation = models.EmotionObservation(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            tick_snapshot_id=tick_snapshot_id,
            actor_id=rating.get("actor_id"),
            tick_index=tick_index,
            emotion=rating.get("emotion", "uncertainty"),
            value=max(0, min(10, value)),
            source="agent_self_report",
            evidence={"raw": {key: str(val) for key, val in rating.items()}},
        )
        db.add(observation)
        values.append({"emotion": observation.emotion, "value": float(observation.value), "source": observation.source})
    graph = {
        "policy": "observability_only_not_prompt_feedback",
        "values": values,
        "event_summary_count": len(event_summaries or []),
        "god_agent_review": god_agent_review or {},
    }
    snapshot = models.EmotionGraphSnapshot(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_snapshot_id=tick_snapshot_id,
        tick_index=tick_index,
        graph=graph,
    )
    db.add(snapshot)
    db.flush()
    return graph
