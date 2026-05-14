"""
SocialPost schema.
Import-free of backend.app.models.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_VALID_VISIBILITY_SCOPE = {"public", "followers", "private", "cohort"}


class SocialPost(BaseModel):
    """A public content item in the platform layer."""

    model_config = ConfigDict(extra="forbid")

    post_id: NonEmptyStr
    universe_id: NonEmptyStr
    platform: NonEmptyStr
    tick_created: int = Field(..., ge=0)

    author_actor_id: NonEmptyStr
    author_avatar_id: NonEmptyStr | None = None
    content: NonEmptyStr

    stance_signal: dict[str, float] = Field(default_factory=dict)
    emotion_signal: dict[str, float] = Field(default_factory=dict)
    credibility_signal: float = Field(..., ge=0.0, le=1.0)

    visibility_scope: str
    reach_score: float = Field(..., ge=0.0, le=1.0)
    hot_score: float = Field(default=0.0)

    reactions: dict[str, int] = Field(default_factory=dict)
    repost_count: int = Field(..., ge=0)
    comment_count: int = Field(..., ge=0)
    upvote_power_total: float = Field(default=0.0)
    downvote_power_total: float = Field(default=0.0)

    @field_validator("visibility_scope")
    @classmethod
    def _validate_visibility_scope(cls, v: str) -> str:
        if v not in _VALID_VISIBILITY_SCOPE:
            raise ValueError(
                f"visibility_scope must be one of {sorted(_VALID_VISIBILITY_SCOPE)}, got {v!r}"
            )
        return v
