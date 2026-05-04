"""
SocialPostModel — mirrors SocialPost.
Table: social_posts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.schemas.posts import SocialPost


class SocialPostModel(Base):
    __tablename__ = "social_posts"

    post_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    universe_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    tick_created: Mapped[int] = mapped_column(Integer, nullable=False)

    author_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_avatar_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    stance_signal: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    emotion_signal: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    credibility_signal: Mapped[float] = mapped_column(Float, nullable=False)

    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reach_score: Mapped[float] = mapped_column(Float, nullable=False)
    hot_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    reactions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    upvote_power_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    downvote_power_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_social_posts_universe_tick_desc", "universe_id", "tick_created"),
        Index("ix_social_posts_universe_hot_score_desc", "universe_id", "hot_score"),
    )

    def to_schema(self) -> SocialPost:
        from backend.app.schemas.posts import SocialPost

        return SocialPost(
            post_id=self.post_id,
            universe_id=self.universe_id,
            platform=self.platform,
            tick_created=self.tick_created,
            author_actor_id=self.author_actor_id,
            author_avatar_id=self.author_avatar_id,
            content=self.content,
            stance_signal=dict(self.stance_signal or {}),
            emotion_signal=dict(self.emotion_signal or {}),
            credibility_signal=self.credibility_signal,
            visibility_scope=self.visibility_scope,
            reach_score=self.reach_score,
            hot_score=self.hot_score,
            reactions=dict(self.reactions or {}),
            repost_count=self.repost_count,
            comment_count=self.comment_count,
            upvote_power_total=self.upvote_power_total,
            downvote_power_total=self.downvote_power_total,
        )

    @classmethod
    def from_schema(cls, s: SocialPost) -> SocialPostModel:
        return cls(
            post_id=s.post_id,
            universe_id=s.universe_id,
            platform=s.platform,
            tick_created=s.tick_created,
            author_actor_id=s.author_actor_id,
            author_avatar_id=s.author_avatar_id,
            content=s.content,
            stance_signal=dict(s.stance_signal),
            emotion_signal=dict(s.emotion_signal),
            credibility_signal=s.credibility_signal,
            visibility_scope=s.visibility_scope,
            reach_score=s.reach_score,
            hot_score=s.hot_score,
            reactions=dict(s.reactions),
            repost_count=s.repost_count,
            comment_count=s.comment_count,
            upvote_power_total=s.upvote_power_total,
            downvote_power_total=s.downvote_power_total,
        )
