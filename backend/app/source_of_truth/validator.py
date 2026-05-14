from pathlib import Path

REQUIRED_FILES = [
    "emotions.json",
    "behavior_axes.json",
    "ideology_axes.json",
    "issue_stance_axes.json",
    "expression_scale.json",
    "event_types.json",
    "social_action_types.json",
    "graph_edge_types.json",
    "sociology_models.json",
    "sociology_presets.json",
    "cohort_state_schema.json",
    "hero_state_schema.json",
    "actor_schema.json",
    "tool_registry.json",
    "god_agent_policy.json",
    "report_templates/event_summary.md",
    "report_templates/tick_summary.md",
    "report_templates/multiverse_report.md",
    "report_templates/final_big_bang_report.md",
]


def validate_source_of_truth_dir(path: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (path / name).exists()]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"source_of_truth is missing required files: {joined}")
    non_files = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if non_files:
        joined = ", ".join(non_files)
        raise ValueError(f"source_of_truth required files must be regular files: {joined}")
