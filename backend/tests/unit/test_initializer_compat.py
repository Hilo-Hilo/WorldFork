from __future__ import annotations


def test_initializer_compat_exports_importable():
    from backend.app.simulation.initializer import (
        InitializerInput,
        default_simulation_config,
        initialize_big_bang,
    )

    init_input = InitializerInput(
        scenario_text="A town debates a zoning change.",
        display_name="Zoning Test",
        tick_duration_minutes=60,
    )

    assert init_input.tick_duration_minutes == 60
    assert callable(initialize_big_bang)
    assert default_simulation_config({})["tick_duration"]


def test_default_simulation_config_normalizes_tick_duration_minutes():
    from backend.app.simulation.initializer import default_simulation_config

    config = default_simulation_config({"tick_duration_minutes": 60})

    assert config["tick_duration"] == "60 minutes"
    assert config["tick_duration_minutes"] == 60


def test_default_simulation_config_preserves_explicit_tick_duration():
    from backend.app.simulation.initializer import default_simulation_config

    config = default_simulation_config(
        {
            "tick_duration": "2 hours",
            "tick_duration_minutes": 60,
        }
    )

    assert config["tick_duration"] == "2 hours"
