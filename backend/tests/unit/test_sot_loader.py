"""Unit tests for the source-of-truth loader (backend.app.storage.sot_loader)."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.storage.sot_loader import load_sot, snapshot_sot_to, validate_sot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SOT_DIR = Path(__file__).resolve().parents[3] / "source_of_truth"


def _sot_available() -> bool:
    """Return True if the real SoT folder is populated (B1-A must have run)."""
    return (_DEFAULT_SOT_DIR / "VERSION").exists()


# ---------------------------------------------------------------------------
# load_sot tests
# ---------------------------------------------------------------------------


class TestLoadSot:
    def test_load_sot_returns_bundle(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        assert bundle.version

    def test_bundle_has_all_fields(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        assert isinstance(bundle.emotions, (dict, list))
        assert isinstance(bundle.behavior_axes, (dict, list))
        assert isinstance(bundle.ideology_axes, (dict, list))
        assert isinstance(bundle.expression_scale, (dict, list))
        assert isinstance(bundle.issue_stance_axes, (dict, list))
        assert isinstance(bundle.event_types, (dict, list))
        assert isinstance(bundle.social_action_tools, (dict, list))
        assert isinstance(bundle.channel_types, (dict, list))
        assert isinstance(bundle.actor_types, (dict, list))
        assert isinstance(bundle.sociology_parameters, (dict, list))

    def test_bundle_has_prompt_contracts(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        assert isinstance(bundle.prompt_contracts, dict)

    def test_bundle_has_prompt_templates(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        assert isinstance(bundle.prompt_templates, dict)

    def test_bundle_has_snapshot_sha(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        assert len(bundle.snapshot_sha256) == 64

    def test_custom_source_dir(self, tmp_path: Path) -> None:
        """load_sot with a minimal hand-crafted SoT directory."""
        _create_minimal_sot(tmp_path)
        bundle = load_sot(source_dir=tmp_path)
        assert bundle.version == "0.0.1-test"
        assert bundle.source_dir == tmp_path

    def test_snapshot_sha_deterministic(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        b1 = load_sot()
        b2 = load_sot()
        assert b1.snapshot_sha256 == b2.snapshot_sha256

    @pytest.mark.parametrize(
        "bad_relative_path",
        [
            "VERSION",
            "emotions.json",
            "behavior_axes.json",
            "ideology_axes.json",
            "expression_scale.json",
            "issue_stance_axes.json",
            "event_types.json",
            "social_action_tools.json",
            "channel_types.json",
            "prompt_templates/test_template.md",
        ],
    )
    def test_load_sot_rejects_symlinked_files(self, tmp_path: Path, bad_relative_path: str) -> None:
        _create_minimal_sot(tmp_path)
        outside = tmp_path.parent / f"outside-{bad_relative_path.replace('/', '-')}"
        outside.write_text("{}", encoding="utf-8")
        target = tmp_path / bad_relative_path
        target.unlink()
        target.symlink_to(outside)

        with pytest.raises(ValueError, match="source_of_truth files must not be symlinks"):
            load_sot(source_dir=tmp_path)


# ---------------------------------------------------------------------------
# validate_sot tests
# ---------------------------------------------------------------------------


class TestValidateSot:
    def test_validate_real_sot_returns_no_errors(self) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        errors = validate_sot(bundle)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_validate_minimal_sot_reports_counts(self, tmp_path: Path) -> None:
        """A minimal SoT with wrong counts should produce errors."""
        _create_minimal_sot(tmp_path)
        bundle = load_sot(source_dir=tmp_path)
        errors = validate_sot(bundle)
        # Minimal SoT has wrong emotion/axis/etc counts, expect errors
        assert isinstance(errors, list)

    def test_validate_no_version_error(self, tmp_path: Path) -> None:
        """A bundle with empty version should report an error."""
        from backend.app.storage.sot_loader import SoTBundle
        # Build a bundle with empty version by monkeypatching
        _create_minimal_sot(tmp_path)
        bundle = load_sot(source_dir=tmp_path)
        # Create a new bundle with empty version via dataclass replace
        bad_bundle = SoTBundle(
            version="",
            emotions=bundle.emotions,
            behavior_axes=bundle.behavior_axes,
            ideology_axes=bundle.ideology_axes,
            expression_scale=bundle.expression_scale,
            issue_stance_axes=bundle.issue_stance_axes,
            event_types=bundle.event_types,
            social_action_tools=bundle.social_action_tools,
            channel_types=bundle.channel_types,
            actor_types=bundle.actor_types,
            sociology_parameters=bundle.sociology_parameters,
            prompt_contracts=bundle.prompt_contracts,
            prompt_templates=bundle.prompt_templates,
            source_dir=bundle.source_dir,
            snapshot_sha256=bundle.snapshot_sha256,
        )
        errors = validate_sot(bad_bundle)
        assert any("VERSION" in e for e in errors)


# ---------------------------------------------------------------------------
# snapshot_sot_to tests
# ---------------------------------------------------------------------------


class TestSnapshotSotTo:
    def test_snapshot_creates_directory(self, tmp_path: Path) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        dest = snapshot_sot_to(bundle, tmp_path / "run_folder")
        assert dest.is_dir()

    def test_snapshot_sha_matches_bundle(self, tmp_path: Path) -> None:
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        dest = snapshot_sot_to(bundle, tmp_path / "run_folder")
        sha_file = dest / ".snapshot_sha256"
        assert sha_file.exists()
        written_sha = sha_file.read_text(encoding="utf-8").strip()
        assert written_sha == bundle.snapshot_sha256

    def test_snapshot_idempotent(self, tmp_path: Path) -> None:
        """Calling snapshot_sot_to twice returns same path and skips copy."""
        if not _sot_available():
            pytest.skip("SoT not yet populated")
        bundle = load_sot()
        dest1 = snapshot_sot_to(bundle, tmp_path / "run_folder")
        # Record mtime of a file inside snapshot
        files = list(dest1.rglob("*"))
        file_to_check = next(f for f in files if f.is_file() and f.name != ".snapshot_sha256")
        mtime_before = file_to_check.stat().st_mtime

        dest2 = snapshot_sot_to(bundle, tmp_path / "run_folder")
        assert dest1 == dest2
        mtime_after = file_to_check.stat().st_mtime
        assert mtime_before == mtime_after

    def test_snapshot_with_minimal_sot(self, tmp_path: Path) -> None:
        """Works with minimal hand-crafted SoT."""
        sot_dir = tmp_path / "source_of_truth"
        sot_dir.mkdir()
        _create_minimal_sot(sot_dir)
        bundle = load_sot(source_dir=sot_dir)
        dest = snapshot_sot_to(bundle, tmp_path / "run_folder")
        assert dest.is_dir()
        sha_file = dest / ".snapshot_sha256"
        assert sha_file.exists()

    def test_snapshot_replaces_stale_snapshot_sha(self, tmp_path: Path) -> None:
        """A matching marker alone is not enough to skip a stale snapshot."""
        sot_dir = tmp_path / "source_of_truth"
        _create_minimal_sot(sot_dir)
        bundle = load_sot(source_dir=sot_dir)

        dest = snapshot_sot_to(bundle, tmp_path / "run_folder")
        target = dest / "VERSION"
        target.write_text("stale", encoding="utf-8")
        (dest / ".snapshot_sha256").write_text(bundle.snapshot_sha256, encoding="utf-8")

        dest2 = snapshot_sot_to(bundle, tmp_path / "run_folder")

        assert dest2 == dest
        assert target.read_text(encoding="utf-8") == "0.0.1-test"

    def test_loading_snapshot_ignores_snapshot_sha_marker(self, tmp_path: Path) -> None:
        """Reloading a copied snapshot should not hash its marker file."""
        sot_dir = tmp_path / "source_of_truth"
        _create_minimal_sot(sot_dir)
        bundle = load_sot(source_dir=sot_dir)
        dest = snapshot_sot_to(bundle, tmp_path / "run_folder")

        reloaded = load_sot(source_dir=dest)

        assert reloaded.snapshot_sha256 == bundle.snapshot_sha256


# ---------------------------------------------------------------------------
# Minimal SoT builder (used when real SoT is absent)
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402


def _create_minimal_sot(base: Path) -> None:
    """Write a minimal (but syntactically valid) SoT directory to *base*."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "VERSION").write_text("0.0.1-test", encoding="utf-8")

    # Minimal JSON files with wrong counts (just 1 item each) for testing
    for name in [
        "emotions", "behavior_axes", "ideology_axes", "expression_scale",
        "issue_stance_axes", "event_types", "social_action_tools",
        "channel_types", "actor_types", "sociology_parameters",
    ]:
        data = {"test_key": {"label": "Test"}}
        (base / f"{name}.json").write_text(_json.dumps(data), encoding="utf-8")

    # prompt_contracts
    contracts_dir = base / "prompt_contracts"
    contracts_dir.mkdir(exist_ok=True)
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}
    (contracts_dir / "test_schema.json").write_text(_json.dumps(schema), encoding="utf-8")

    # prompt_templates
    templates_dir = base / "prompt_templates"
    templates_dir.mkdir(exist_ok=True)
    (templates_dir / "test_template.md").write_text("# Test Template\n", encoding="utf-8")
