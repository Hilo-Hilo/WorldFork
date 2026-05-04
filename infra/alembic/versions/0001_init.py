"""Initial schema — all tables.

Revision ID: 0001_init
Revises: (none)
Create Date: 2026-04-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # big_bang_runs
    # -----------------------------------------------------------------------
    op.create_table(
        "big_bang_runs",
        sa.Column("big_bang_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("scenario_text", sa.Text(), nullable=False),
        sa.Column("input_file_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("time_horizon_label", sa.String(), nullable=False),
        sa.Column("tick_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("max_ticks", sa.Integer(), nullable=False),
        sa.Column("max_schedule_horizon_ticks", sa.Integer(), nullable=False),
        sa.Column("source_of_truth_version", sa.String(), nullable=False),
        sa.Column("source_of_truth_snapshot_path", sa.String(), nullable=False),
        sa.Column("provider_snapshot_id", sa.String(), nullable=False),
        sa.Column("root_universe_id", sa.String(), nullable=False),
        sa.Column("run_folder_path", sa.String(), nullable=False),
        sa.Column("safe_edit_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("big_bang_id", name="pk_big_bang_runs"),
    )

    # -----------------------------------------------------------------------
    # universes
    # -----------------------------------------------------------------------
    op.create_table(
        "universes",
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("big_bang_id", sa.String(64), nullable=False),
        sa.Column("parent_universe_id", sa.String(64), nullable=True),
        sa.Column("lineage_path", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("branch_from_tick", sa.Integer(), nullable=True),
        sa.Column("branch_depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("branch_reason", sa.Text(), nullable=False),
        sa.Column("branch_delta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("current_tick", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "latest_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("killed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["big_bang_id"],
            ["big_bang_runs.big_bang_id"],
            name="fk_universes_big_bang_id_big_bang_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_universe_id"],
            ["universes.universe_id"],
            name="fk_universes_parent_universe_id_universes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("universe_id", name="pk_universes"),
    )
    op.create_index(
        "ix_universes_big_bang_parent",
        "universes",
        ["big_bang_id", "parent_universe_id"],
    )
    op.create_index(
        "ix_universes_big_bang_status",
        "universes",
        ["big_bang_id", "status"],
    )
    op.create_index(
        "ix_universes_lineage_path",
        "universes",
        ["lineage_path"],
        postgresql_using="gin",
    )

    # -----------------------------------------------------------------------
    # branch_nodes (one-to-one with universes)
    # -----------------------------------------------------------------------
    op.create_table(
        "branch_nodes",
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("parent_universe_id", sa.String(64), nullable=True),
        sa.Column("child_universe_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("branch_tick", sa.Integer(), nullable=False),
        sa.Column("branch_point_id", sa.String(64), nullable=False),
        sa.Column("branch_trigger", sa.Text(), nullable=False),
        sa.Column(
            "branch_delta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "metrics_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "cost_estimate",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("descendant_count", sa.Integer(), nullable=False),
        sa.Column("lineage_path", postgresql.ARRAY(sa.String()), nullable=False),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.universe_id"],
            name="fk_branch_nodes_universe_id_universes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("universe_id", name="pk_branch_nodes"),
    )
    op.create_index(
        "ix_branch_nodes_parent_universe_id",
        "branch_nodes",
        ["parent_universe_id"],
    )
    op.create_index(
        "ix_branch_nodes_lineage_path",
        "branch_nodes",
        ["lineage_path"],
        postgresql_using="gin",
    )

    # -----------------------------------------------------------------------
    # population_archetypes
    # -----------------------------------------------------------------------
    op.create_table(
        "population_archetypes",
        sa.Column("archetype_id", sa.String(64), nullable=False),
        sa.Column("big_bang_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("population_total", sa.Integer(), nullable=False),
        sa.Column("geography", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("age_band", sa.String(), nullable=True),
        sa.Column("education_profile", sa.String(), nullable=True),
        sa.Column("occupation_or_role", sa.String(), nullable=True),
        sa.Column("socioeconomic_band", sa.String(), nullable=True),
        sa.Column("institution_membership", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("demographic_tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("issue_exposure", sa.Float(), nullable=False),
        sa.Column("material_stake", sa.Float(), nullable=False),
        sa.Column("symbolic_stake", sa.Float(), nullable=False),
        sa.Column("vulnerability_to_policy", sa.Float(), nullable=False),
        sa.Column("ability_to_influence_outcome", sa.Float(), nullable=False),
        sa.Column("ideology_axes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value_priors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("behavior_axes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("baseline_media_diet", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preferred_channels", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("platform_access", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attention_capacity", sa.Float(), nullable=False),
        sa.Column("attention_decay_rate", sa.Float(), nullable=False),
        sa.Column("baseline_trust_priors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("ingroup_affinities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outgroup_distances", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_action_classes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("coordination_capacity", sa.Float(), nullable=False),
        sa.Column("mobilization_capacity", sa.Float(), nullable=False),
        sa.Column("legal_or_status_risk_sensitivity", sa.Float(), nullable=False),
        sa.Column("min_split_population", sa.Integer(), nullable=False),
        sa.Column("min_split_share", sa.Float(), nullable=False),
        sa.Column("max_child_cohorts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["big_bang_id"],
            ["big_bang_runs.big_bang_id"],
            name="fk_population_archetypes_big_bang_id_big_bang_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("archetype_id", name="pk_population_archetypes"),
    )

    # -----------------------------------------------------------------------
    # cohort_states
    # -----------------------------------------------------------------------
    op.create_table(
        "cohort_states",
        sa.Column("cohort_id", sa.String(64), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("archetype_id", sa.String(64), nullable=False),
        sa.Column("parent_cohort_id", sa.String(64), nullable=True),
        sa.Column("child_cohort_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("represented_population", sa.Integer(), nullable=False),
        sa.Column("population_share_of_archetype", sa.Float(), nullable=False),
        sa.Column("issue_stance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expression_level", sa.Float(), nullable=False),
        sa.Column("mobilization_mode", sa.String(32), nullable=False),
        sa.Column("speech_mode", sa.String(32), nullable=False),
        sa.Column("emotions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("behavior_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attention", sa.Float(), nullable=False),
        sa.Column("fatigue", sa.Float(), nullable=False),
        sa.Column("grievance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("perceived_efficacy", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("perceived_majority", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fear_of_isolation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("willingness_to_speak", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("identity_salience", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "visible_trust_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("exposure_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dependency_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("memory_session_id", sa.String(), nullable=True),
        sa.Column("recent_post_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("queued_event_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("previous_action_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("prompt_temperature", sa.Float(), nullable=False),
        sa.Column("representation_mode", sa.String(32), nullable=False),
        sa.Column("allowed_tools", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint(
            "represented_population >= 0",
            name="ck_cohort_states_represented_population_nonneg",
        ),
        sa.CheckConstraint(
            "population_share_of_archetype BETWEEN 0 AND 1",
            name="ck_cohort_states_population_share_range",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.universe_id"],
            name="fk_cohort_states_universe_id_universes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["archetype_id"],
            ["population_archetypes.archetype_id"],
            name="fk_cohort_states_archetype_id_population_archetypes",
            ondelete="RESTRICT",
        ),
        # parent_cohort_id is intentionally cross-tick; the composite PK
        # (cohort_id, tick) on the target table makes a real FK unsupportable.
        # Integrity is enforced at the application layer via split_merge.
        sa.PrimaryKeyConstraint("cohort_id", "tick", name="pk_cohort_states"),
    )
    op.create_index(
        "ix_cohort_states_universe_tick",
        "cohort_states",
        ["universe_id", "tick"],
    )
    op.create_index(
        "ix_cohort_states_archetype_tick",
        "cohort_states",
        ["archetype_id", "tick"],
    )
    op.create_index(
        "ix_cohort_states_universe_active",
        "cohort_states",
        ["universe_id"],
        postgresql_where=sa.text("is_active = true"),
    )

    # -----------------------------------------------------------------------
    # hero_archetypes
    # -----------------------------------------------------------------------
    op.create_table(
        "hero_archetypes",
        sa.Column("hero_id", sa.String(64), nullable=False),
        sa.Column("big_bang_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("location_scope", sa.String(), nullable=False),
        sa.Column("public_reach", sa.Float(), nullable=False),
        sa.Column("institutional_power", sa.Float(), nullable=False),
        sa.Column("financial_power", sa.Float(), nullable=False),
        sa.Column("agenda_control", sa.Float(), nullable=False),
        sa.Column("media_access", sa.Float(), nullable=False),
        sa.Column("ideology_axes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value_priors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trust_priors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("behavioral_axes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("volatility", sa.Float(), nullable=False),
        sa.Column("ego_sensitivity", sa.Float(), nullable=False),
        sa.Column("strategic_discipline", sa.Float(), nullable=False),
        sa.Column("controversy_tolerance", sa.Float(), nullable=False),
        sa.Column("direct_event_power", sa.Float(), nullable=False),
        sa.Column("scheduling_permissions", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("allowed_channels", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["big_bang_id"],
            ["big_bang_runs.big_bang_id"],
            name="fk_hero_archetypes_big_bang_id_big_bang_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("hero_id", name="pk_hero_archetypes"),
    )

    # -----------------------------------------------------------------------
    # hero_states
    # -----------------------------------------------------------------------
    op.create_table(
        "hero_states",
        sa.Column("hero_id", sa.String(64), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("current_emotions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_issue_stances", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attention", sa.Float(), nullable=False),
        sa.Column("fatigue", sa.Float(), nullable=False),
        sa.Column("perceived_pressure", sa.Float(), nullable=False),
        sa.Column("current_strategy", sa.Text(), nullable=False),
        sa.Column("queued_events", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("recent_posts", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("memory_session_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["hero_id"],
            ["hero_archetypes.hero_id"],
            name="fk_hero_states_hero_id_hero_archetypes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.universe_id"],
            name="fk_hero_states_universe_id_universes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("hero_id", "tick", name="pk_hero_states"),
    )
    op.create_index(
        "ix_hero_states_universe_tick",
        "hero_states",
        ["universe_id", "tick"],
    )

    # -----------------------------------------------------------------------
    # events
    # -----------------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("created_tick", sa.Integer(), nullable=False),
        sa.Column("scheduled_tick", sa.Integer(), nullable=False),
        sa.Column("duration_ticks", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by_actor_id", sa.String(64), nullable=False),
        sa.Column("participants", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("target_audience", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("preconditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_effects", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actual_effects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_level", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parent_event_id", sa.String(64), nullable=True),
        sa.Column("source_llm_call_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.universe_id"],
            name="fk_events_universe_id_universes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_events"),
    )
    op.create_index(
        "ix_events_universe_scheduled_status",
        "events",
        ["universe_id", "scheduled_tick", "status"],
    )
    op.create_index(
        "ix_events_universe_status",
        "events",
        ["universe_id", "status"],
    )

    # -----------------------------------------------------------------------
    # social_posts
    # -----------------------------------------------------------------------
    op.create_table(
        "social_posts",
        sa.Column("post_id", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("tick_created", sa.Integer(), nullable=False),
        sa.Column("author_actor_id", sa.String(64), nullable=False),
        sa.Column("author_avatar_id", sa.String(64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("stance_signal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("emotion_signal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("credibility_signal", sa.Float(), nullable=False),
        sa.Column("visibility_scope", sa.String(32), nullable=False),
        sa.Column("reach_score", sa.Float(), nullable=False),
        sa.Column("hot_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reactions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("repost_count", sa.Integer(), nullable=False),
        sa.Column("comment_count", sa.Integer(), nullable=False),
        sa.Column("upvote_power_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("downvote_power_total", sa.Float(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.universe_id"],
            name="fk_social_posts_universe_id_universes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("post_id", name="pk_social_posts"),
    )
    op.create_index(
        "ix_social_posts_universe_tick_desc",
        "social_posts",
        ["universe_id", "tick_created"],
    )
    op.create_index(
        "ix_social_posts_universe_hot_score_desc",
        "social_posts",
        ["universe_id", "hot_score"],
    )

    # -----------------------------------------------------------------------
    # jobs
    # -----------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=True),
        sa.Column("tick", sa.Integer(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("artifact_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        sa.PrimaryKeyConstraint("job_id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_status_priority", "jobs", ["status", "priority"])
    op.create_index("ix_jobs_run_universe_tick", "jobs", ["run_id", "universe_id", "tick"])
    op.create_index("ix_jobs_type_status", "jobs", ["job_type", "status"])

    # -----------------------------------------------------------------------
    # llm_calls
    # -----------------------------------------------------------------------
    op.create_table(
        "llm_calls",
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_used", sa.String(128), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("prompt_packet_path", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("response_path", sa.String(), nullable=False),
        sa.Column("parsed_path", sa.String(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("repaired_once", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.String(64), nullable=True),
        sa.Column("tick", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("call_id", name="pk_llm_calls"),
    )
    op.create_index("ix_llm_calls_run_created_at", "llm_calls", ["run_id", "created_at"])
    op.create_index("ix_llm_calls_job_type_status", "llm_calls", ["job_type", "status"])

    # -----------------------------------------------------------------------
    # settings_provider
    # -----------------------------------------------------------------------
    op.create_table(
        "settings_provider",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("api_key_env", sa.String(128), nullable=False),
        sa.Column("default_model", sa.String(128), nullable=False),
        sa.Column("fallback_model", sa.String(128), nullable=True),
        sa.Column("json_mode_required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tool_calling_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("extra_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider", name="pk_settings_provider"),
    )

    # -----------------------------------------------------------------------
    # settings_model_routing
    # -----------------------------------------------------------------------
    op.create_table(
        "settings_model_routing",
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("preferred_provider", sa.String(64), nullable=False),
        sa.Column("preferred_model", sa.String(128), nullable=False),
        sa.Column("fallback_provider", sa.String(64), nullable=True),
        sa.Column("fallback_model", sa.String(128), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("top_p", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("tokens_per_minute", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_policy", sa.String(32), nullable=False),
        sa.Column("daily_budget_usd", sa.Float(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_type", name="pk_settings_model_routing"),
    )

    # -----------------------------------------------------------------------
    # settings_rate_limit
    # -----------------------------------------------------------------------
    op.create_table(
        "settings_rate_limit",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("rpm_limit", sa.Integer(), nullable=False),
        sa.Column("tpm_limit", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("burst_multiplier", sa.Float(), nullable=False),
        sa.Column("retry_policy", sa.String(32), nullable=False),
        sa.Column("jitter", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("daily_budget_usd", sa.Float(), nullable=True),
        sa.Column("branch_reserved_capacity_pct", sa.Float(), nullable=False),
        sa.Column("healthcheck_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider", name="pk_settings_rate_limit"),
    )

    # -----------------------------------------------------------------------
    # settings_branch_policy
    # -----------------------------------------------------------------------
    op.create_table(
        "settings_branch_policy",
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("max_active_universes", sa.Integer(), nullable=False),
        sa.Column("max_total_branches", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("max_branches_per_tick", sa.Integer(), nullable=False),
        sa.Column("branch_cooldown_ticks", sa.Integer(), nullable=False),
        sa.Column("min_divergence_score", sa.Float(), nullable=False),
        sa.Column("auto_prune_low_value", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("policy_id", name="pk_settings_branch_policy"),
    )

    # -----------------------------------------------------------------------
    # settings_global
    # -----------------------------------------------------------------------
    op.create_table(
        "settings_global",
        sa.Column("setting_id", sa.String(64), nullable=False),
        sa.Column("default_tick_duration_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("default_max_ticks", sa.Integer(), nullable=False, server_default="48"),
        sa.Column(
            "default_max_schedule_horizon_ticks",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column("log_level", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("display_timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("theme", sa.String(16), nullable=False, server_default="system"),
        sa.Column("enable_oasis_adapter", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("branching_defaults", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("setting_id", name="pk_settings_global"),
    )


def downgrade() -> None:
    op.drop_table("settings_global")
    op.drop_table("settings_branch_policy")
    op.drop_table("settings_rate_limit")
    op.drop_table("settings_model_routing")
    op.drop_table("settings_provider")
    op.drop_index("ix_llm_calls_job_type_status", table_name="llm_calls")
    op.drop_index("ix_llm_calls_run_created_at", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_jobs_type_status", table_name="jobs")
    op.drop_index("ix_jobs_run_universe_tick", table_name="jobs")
    op.drop_index("ix_jobs_status_priority", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_social_posts_universe_hot_score_desc", table_name="social_posts")
    op.drop_index("ix_social_posts_universe_tick_desc", table_name="social_posts")
    op.drop_table("social_posts")
    op.drop_index("ix_events_universe_status", table_name="events")
    op.drop_index("ix_events_universe_scheduled_status", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_hero_states_universe_tick", table_name="hero_states")
    op.drop_table("hero_states")
    op.drop_table("hero_archetypes")
    op.drop_index("ix_cohort_states_universe_active", table_name="cohort_states")
    op.drop_index("ix_cohort_states_archetype_tick", table_name="cohort_states")
    op.drop_index("ix_cohort_states_universe_tick", table_name="cohort_states")
    op.drop_table("cohort_states")
    op.drop_table("population_archetypes")
    op.drop_index("ix_branch_nodes_lineage_path", table_name="branch_nodes")
    op.drop_index("ix_branch_nodes_parent_universe_id", table_name="branch_nodes")
    op.drop_table("branch_nodes")
    op.drop_index("ix_universes_lineage_path", table_name="universes")
    op.drop_index("ix_universes_big_bang_status", table_name="universes")
    op.drop_index("ix_universes_big_bang_parent", table_name="universes")
    op.drop_table("universes")
    op.drop_table("big_bang_runs")
