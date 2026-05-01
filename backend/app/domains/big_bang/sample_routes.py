from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import BigBangCreate
from app.api.utils import commit_or_500
from app.db.session import get_db
from app.domains.big_bang.initializer import create_big_bang
from app.domains.big_bang.run_orchestrator import run_big_bang_until_complete

router = APIRouter(prefix="/sample-runs", tags=["sample-runs"])


@router.post("")
def create_sample_run(db: Session = Depends(get_db)):
    payload = BigBangCreate(
        name="Civic Shock Sample",
        description="A compact backend-only sample run.",
        scenario_text=(
            "A mid-sized city announces an emergency downtown private-car ban after a bridge failure. "
            "Commuters are split between safety compliance, anger at lost mobility, and workaround-seeking. "
            "Small businesses fear immediate revenue collapse and blame city officials for unclear communication. "
            "City Operations argues the restriction is necessary to prevent gridlock and secondary accidents. "
            "Local media amplifies rumors that enforcement will be unequal across neighborhoods. "
            "Organizer Mira tries to build mutual-aid rides while avoiding escalation. "
            "Track trust, reputation, secrecy, dependency, influence, coalition formation, conflict, "
            "emotion-observability vectors, and sociology signals over the first day."
        ),
        scenario_input={
            "premise": "A city announces a sudden transport restriction and communities react online.",
            "setting": "near-future civic network",
        },
        simulation_config={"tick_duration": "6 hours", "max_ticks": 3},
        branch_policy={"max_branch_depth": 2, "max_active_multiverses": 6, "max_branches_per_tick": 1, "branch_score_threshold": 0.7},
        actors=[
            {"name": "Commuter Coalition", "actor_type": "cohort", "goals": ["mobility", "fairness"]},
            {"name": "City Operations Desk", "actor_type": "institution", "goals": ["stability", "compliance"]},
            {"name": "Local Organizer Mira", "actor_type": "hero", "goals": ["mutual aid", "visibility"]},
        ],
        cohorts=[{"name": "commuters"}, {"name": "small businesses"}],
        heroes=[{"name": "Mira"}],
    )
    big_bang = create_big_bang(db, payload)
    result = run_big_bang_until_complete(db, big_bang=big_bang, max_total_ticks=6)
    commit_or_500(db)
    return {"big_bang_id": big_bang.id, **result}
