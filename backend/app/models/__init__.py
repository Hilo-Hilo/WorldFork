"""
backend.app.models — public re-exports for all SQLAlchemy ORM models.

Import from this module to access Base + every model class.
Dependency direction: models → schemas (never schemas → models).
"""
from __future__ import annotations

from backend.app.models.base import Base, TimestampMixin
from backend.app.models.branches import BranchNodeModel
from backend.app.models.cohorts import CohortStateModel, PopulationArchetypeModel
from backend.app.models.events import EventModel
from backend.app.models.heroes import HeroArchetypeModel, HeroStateModel
from backend.app.models.jobs import JobModel
from backend.app.models.llm_calls import LLMCallModel
from backend.app.models.posts import SocialPostModel
from backend.app.models.results import RunResultModel
from backend.app.models.runs import BigBangRunModel
from backend.app.models.settings import (
    BranchPolicySettingModel,
    GlobalSettingModel,
    ModelRoutingEntryModel,
    ProviderSettingModel,
    RateLimitSettingModel,
)
from backend.app.models.universes import UniverseModel

__all__ = [
    # Base
    "Base",
    "TimestampMixin",
    # Runs
    "BigBangRunModel",
    "RunResultModel",
    # Universes
    "UniverseModel",
    # Branches
    "BranchNodeModel",
    # Cohorts / archetypes
    "PopulationArchetypeModel",
    "CohortStateModel",
    # Heroes
    "HeroArchetypeModel",
    "HeroStateModel",
    # Events
    "EventModel",
    # Posts
    "SocialPostModel",
    # Jobs
    "JobModel",
    # LLM calls
    "LLMCallModel",
    # Settings
    "ProviderSettingModel",
    "ModelRoutingEntryModel",
    "RateLimitSettingModel",
    "BranchPolicySettingModel",
    "GlobalSettingModel",
]
