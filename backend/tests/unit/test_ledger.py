"""Unit tests for the run ledger (backend.app.storage.ledger)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.app.storage.ledger import (
    ImmutabilityError,
    Ledger,
    LedgerError,
    _tick_files_for_merkle,
    _tick_merkle_root,
)
from backend.app.storage.checksums import sha256_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BIG_BANG_ID = "BB_test_001"
SCENARIO = "Metro Region gig-worker labor dispute"

CONFIG_SNAPSHOT = {"provider": "openrouter", "model": "openai/gpt-4o"}


@pytest.fixture()
def ledger(tmp_path: Path) -> Ledger:
    """A freshly created ledger in a temp directory."""
    return Ledger.begin_run(
        run_root=tmp_path,
        big_bang_id=BIG_BANG_ID,
        scenario_text=SCENARIO,
        sot_snapshot_sha="a" * 64,
        config_snapshot=CONFIG_SNAPSHOT,
    )


# ---------------------------------------------------------------------------
# begin_run tests
# ---------------------------------------------------------------------------


class TestBeginRun:
    def test_creates_run_folder(self, tmp_path: Path) -> None:
        ld = Ledger.begin_run(
            run_root=tmp_path,
            big_bang_id=BIG_BANG_ID,
            scenario_text=SCENARIO,
            sot_snapshot_sha="b" * 64,
            config_snapshot={},
        )
        assert ld.run_folder.is_dir()

    def test_run_folder_name_contains_bb_prefix(self, tmp_path: Path) -> None:
        ld = Ledger.begin_run(
            run_root=tmp_path,
            big_bang_id=BIG_BANG_ID,
            scenario_text=SCENARIO,
            sot_snapshot_sha="c" * 64,
            config_snapshot={},
        )
        assert ld.run_folder.name.startswith("BB_")

    def test_manifest_json_created(self, ledger: Ledger) -> None:
        mf_path = ledger.run_folder / "manifest.json"
        assert mf_path.exists()
        data = json.loads(mf_path.read_bytes())
        assert data["big_bang_id"] == BIG_BANG_ID
        assert data["schema_version"] == "1"

    def test_config_snapshot_written(self, ledger: Ledger) -> None:
        config_path = ledger.run_folder / "config" / "config_snapshot.json"
        assert config_path.exists()

    def test_config_snapshot_is_immutable(self, ledger: Ledger) -> None:
        config_path = ledger.run_folder / "config" / "config_snapshot.json"
        mode = oct(config_path.stat().st_mode)[-3:]
        assert mode == "444"


# ---------------------------------------------------------------------------
# write_artifact tests
# ---------------------------------------------------------------------------


class TestWriteArtifact:
    def test_happy_path_dict(self, ledger: Ledger) -> None:
        sha = ledger.write_artifact("universes/U000/test.json", {"hello": "world"})
        assert len(sha) == 64
        dest = ledger.run_folder / "universes/U000/test.json"
        assert dest.exists()

    def test_happy_path_bytes(self, ledger: Ledger) -> None:
        sha = ledger.write_artifact("universes/U000/raw.bin", b"\x00\x01\x02")
        assert len(sha) == 64

    def test_happy_path_str(self, ledger: Ledger) -> None:
        sha = ledger.write_artifact("universes/U000/text.md", "# Hello")
        assert len(sha) == 64

    def test_immutable_file_has_444_mode(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/locked.json", {"x": 1}, immutable=True)
        target = ledger.run_folder / "universes/U000/locked.json"
        mode = oct(target.stat().st_mode)[-3:]
        assert mode == "444"

    def test_immutability_error_on_overwrite(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/locked.json", {"x": 1}, immutable=True)
        with pytest.raises(ImmutabilityError):
            ledger.write_artifact("universes/U000/locked.json", {"x": 2}, immutable=True)

    def test_no_tmp_files_left_on_success(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/clean.json", {"ok": True})
        parent = ledger.run_folder / "universes/U000"
        tmp_files = list(parent.glob("*.tmp.*"))
        assert tmp_files == []

    def test_mutable_overwrite_allowed(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/mutable.json", {"v": 1}, immutable=False)
        ledger.write_artifact("universes/U000/mutable.json", {"v": 2}, immutable=False)
        target = ledger.run_folder / "universes/U000/mutable.json"
        data = json.loads(target.read_bytes())
        assert data["v"] == 2

    @pytest.mark.parametrize(
        "rel_path",
        [
            "../outside.json",
            "../../outside.json",
            "universes/U000/../outside.json",
            "universes/U000/ticks/tick_000/../../outside.json",
            "/absolute/outside.json",
            "\\windows\\outside.json",
            "",
            ".",
            "..",
            "universes/U000/ticks/tick_000/./state.json",
        ],
    )
    def test_write_artifact_rejects_unsafe_paths(self, ledger: Ledger, rel_path: str, tmp_path: Path) -> None:
        unsafe_path = (tmp_path / "outside.json").as_posix() if rel_path.startswith("/absolute/") else rel_path

        with pytest.raises(LedgerError, match="Unsafe ledger artifact path"):
            ledger.write_artifact(unsafe_path, {"blocked": True}, immutable=False)


# ---------------------------------------------------------------------------
# seal_tick / Merkle root tests
# ---------------------------------------------------------------------------


class TestSealTick:
    def _setup_universe(self, ledger: Ledger, universe_id: str = "U000") -> None:
        ledger.begin_universe(
            universe_id,
            parent=None,
            branch_from_tick=None,
            branch_delta=None,
        )

    def test_seal_tick_returns_hex_root(self, ledger: Ledger) -> None:
        self._setup_universe(ledger)
        ledger.begin_tick("U000", 0)
        ledger.write_artifact("universes/U000/ticks/tick_000/universe_state_before.json", {"tick": 0})
        root = ledger.seal_tick("U000", 0)
        assert len(root) == 64
        assert all(c in "0123456789abcdef" for c in root)

    def test_seal_tick_writes_manifest(self, ledger: Ledger) -> None:
        self._setup_universe(ledger)
        ledger.begin_tick("U000", 0)
        ledger.write_artifact("universes/U000/ticks/tick_000/state.json", {"v": 1})
        ledger.seal_tick("U000", 0)
        tick_mf = ledger.run_folder / "universes/U000/ticks/tick_000/manifest.json"
        assert tick_mf.exists()
        data = json.loads(tick_mf.read_bytes())
        assert "merkle_root" in data
        assert "files" in data

    def test_seal_tick_stable_merkle_root(self, ledger: Ledger) -> None:
        """Same files → same Merkle root on two independent computations."""
        self._setup_universe(ledger)
        ledger.begin_tick("U000", 0)
        ledger.write_artifact("universes/U000/ticks/tick_000/a.json", {"x": 1})
        ledger.write_artifact("universes/U000/ticks/tick_000/b.json", {"y": 2})
        root1 = ledger.seal_tick("U000", 0)

        # Recompute manually
        tick_dir = ledger.run_folder / "universes/U000/ticks/tick_000"
        files = _tick_files_for_merkle(tick_dir)
        root2 = _tick_merkle_root(tick_dir, files)
        assert root1 == root2

    @pytest.mark.parametrize(
        ("left_path", "right_path"),
        [
            ("a.json", "b.json"),
            ("root-a.json", "root-b.json"),
            ("nested/a.json", "nested/b.json"),
            ("nested/a.json", "other/a.json"),
            ("state/a.json", "state/b.json"),
            ("events/a.jsonl", "events/b.jsonl"),
            ("memory/a.json", "memory/b.json"),
            ("god/a.json", "god/b.json"),
            ("llm_calls/a.json", "llm_calls/b.json"),
            ("visible_packets/a.json", "visible_packets/b.json"),
        ],
    )
    def test_seal_tick_merkle_root_includes_relative_file_paths(
        self,
        ledger: Ledger,
        left_path: str,
        right_path: str,
    ) -> None:
        self._setup_universe(ledger, "U000")
        self._setup_universe(ledger, "U001")
        ledger.begin_tick("U000", 0)
        ledger.begin_tick("U001", 0)
        clock = {"tick": 0, "started_at": "fixed"}
        ledger.write_artifact("universes/U000/ticks/tick_000/clock.json", clock, immutable=False)
        ledger.write_artifact("universes/U001/ticks/tick_000/clock.json", clock, immutable=False)
        ledger.write_artifact(f"universes/U000/ticks/tick_000/{left_path}", "same-content", immutable=False)
        ledger.write_artifact(f"universes/U001/ticks/tick_000/{right_path}", "same-content", immutable=False)

        left_root = ledger.seal_tick("U000", 0)
        right_root = ledger.seal_tick("U001", 0)

        assert left_root != right_root

    def test_seal_tick_updates_run_manifest(self, ledger: Ledger) -> None:
        self._setup_universe(ledger)
        ledger.begin_tick("U000", 0)
        ledger.write_artifact("universes/U000/ticks/tick_000/s.json", {})
        ledger.seal_tick("U000", 0)
        mf = ledger.manifest()
        assert "0" in mf["universes"]["U000"]["ticks"]

    @pytest.mark.parametrize(
        "relative_path",
        [
            "linked-root.json",
            "state/linked-state.json",
            "events/linked-events.jsonl",
            "logs/linked-log.jsonl",
            "artifacts/linked-artifact.json",
            "actors/linked-actor.json",
            "cohorts/linked-cohort.json",
            "tools/linked-tool.json",
            "reports/linked-report.md",
            "nested/deep/linked-deep.json",
        ],
    )
    def test_seal_tick_rejects_symlinked_tick_files(self, ledger: Ledger, tmp_path: Path, relative_path: str) -> None:
        self._setup_universe(ledger)
        ledger.begin_tick("U000", 0)
        outside = tmp_path / "outside.json"
        outside.write_text('{"outside": true}', encoding="utf-8")
        link = ledger.run_folder / "universes" / "U000" / "ticks" / "tick_000" / relative_path
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)

        with pytest.raises(LedgerError, match="Tick seal cannot include symlinks"):
            ledger.seal_tick("U000", 0)


# ---------------------------------------------------------------------------
# verify() tests
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_returns_empty_on_clean_ledger(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/art.json", {"clean": True})
        errors = ledger.verify()
        assert errors == []

    def test_verify_reports_tampered_file(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/tamper.json", {"original": True}, immutable=False)
        target = ledger.run_folder / "universes/U000/tamper.json"
        # Make it writable and tamper
        os.chmod(target, 0o644)
        target.write_bytes(b'{"tampered": true}')
        errors = ledger.verify()
        assert any("tamper.json" in e for e in errors)

    def test_verify_reports_missing_file(self, ledger: Ledger) -> None:
        ledger.write_artifact("universes/U000/gone.json", {"here": True}, immutable=False)
        target = ledger.run_folder / "universes/U000/gone.json"
        os.chmod(target, 0o644)
        target.unlink()
        errors = ledger.verify()
        assert any("gone.json" in e for e in errors)

    @pytest.mark.parametrize(
        "rel_path",
        [
            "universes/U000/root.json",
            "universes/U000/state/state.json",
            "universes/U000/events/events.jsonl",
            "universes/U000/logs/log.jsonl",
            "universes/U000/artifacts/artifact.json",
            "universes/U000/actors/actor.json",
            "universes/U000/cohorts/cohort.json",
            "universes/U000/tools/tool.json",
            "universes/U000/reports/report.md",
            "universes/U000/nested/deep/item.json",
        ],
    )
    def test_verify_reports_recorded_file_replaced_by_symlink(
        self,
        ledger: Ledger,
        tmp_path: Path,
        rel_path: str,
    ) -> None:
        body = '{"same": true}'
        ledger.write_artifact(rel_path, body, immutable=False)
        target = ledger.run_folder / rel_path
        outside = tmp_path / "outside.json"
        outside.write_text(body, encoding="utf-8")
        target.unlink()
        target.symlink_to(outside)

        errors = ledger.verify()

        assert any(rel_path in error and "symlink" in error for error in errors)


# ---------------------------------------------------------------------------
# open() tests
# ---------------------------------------------------------------------------


class TestOpen:
    def _sealed_tick_manifest_path(self, ledger: Ledger) -> Path:
        ledger.begin_universe("U000", parent=None, branch_from_tick=None, branch_delta=None)
        ledger.begin_tick("U000", 0)
        ledger.write_artifact("universes/U000/ticks/tick_000/state.json", {"ok": True}, immutable=False)
        ledger.seal_tick("U000", 0)
        return ledger.run_folder / "universes" / "U000" / "ticks" / "tick_000" / "manifest.json"

    def _write_tick_manifest_file_entry(self, ledger: Ledger, rel_path: str, outside: Path) -> None:
        manifest_path = self._sealed_tick_manifest_path(ledger)
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"][rel_path] = {
            "sha256": sha256_file(outside),
            "size": outside.stat().st_size,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_open_finds_existing_run(self, tmp_path: Path) -> None:
        ld1 = Ledger.begin_run(
            run_root=tmp_path,
            big_bang_id="BB_open_test",
            scenario_text="test scenario",
            sot_snapshot_sha="0" * 64,
            config_snapshot={},
        )
        ld2 = Ledger.open(tmp_path, "BB_open_test")
        assert ld2.run_folder == ld1.run_folder

    def test_open_raises_on_missing(self, tmp_path: Path) -> None:
        from backend.app.storage.ledger import LedgerError
        with pytest.raises(LedgerError):
            Ledger.open(tmp_path, "BB_nonexistent")

    @pytest.mark.parametrize(
        "unsafe_rel_path",
        [
            "../outside.json",
            "../../outside.json",
            "universes/U000/../outside.json",
            "universes/U000/ticks/tick_000/../../outside.json",
            "/absolute/outside.json",
            "\\windows\\outside.json",
            "",
            ".",
            "..",
            "universes/U000/ticks/tick_000/./state.json",
        ],
    )
    def test_open_rejects_unsafe_tick_manifest_file_paths(self, tmp_path: Path, unsafe_rel_path: str) -> None:
        outside = tmp_path / "outside.json"
        outside.write_text('{"outside": true}', encoding="utf-8")
        ledger = Ledger.begin_run(
            run_root=tmp_path,
            big_bang_id="BB_unsafe_manifest",
            scenario_text="unsafe manifest",
            sot_snapshot_sha="0" * 64,
            config_snapshot={},
        )
        rel_path = outside.as_posix() if unsafe_rel_path.startswith("/absolute/") else unsafe_rel_path
        self._write_tick_manifest_file_entry(ledger, rel_path, outside)

        with pytest.raises(LedgerError, match="Unsafe tick manifest file path"):
            Ledger.open(tmp_path, "BB_unsafe_manifest")

    @pytest.mark.parametrize(
        "corruption",
        [
            "invalid_json",
            "root_list",
            "missing_files",
            "files_null",
            "files_list",
            "file_record_null",
            "missing_sha256",
            "sha256_not_string",
            "sha256_short",
            "missing_size",
        ],
    )
    def test_open_rejects_corrupt_tick_manifest_file_records(self, tmp_path: Path, corruption: str) -> None:
        ledger = Ledger.begin_run(
            run_root=tmp_path,
            big_bang_id="BB_corrupt_manifest",
            scenario_text="corrupt manifest",
            sot_snapshot_sha="0" * 64,
            config_snapshot={},
        )
        manifest_path = self._sealed_tick_manifest_path(ledger)
        manifest = json.loads(manifest_path.read_bytes())
        rel_path = "universes/U000/ticks/tick_000/state.json"

        if corruption == "invalid_json":
            manifest_path.write_text("{", encoding="utf-8")
        elif corruption == "root_list":
            manifest_path.write_text(json.dumps([]), encoding="utf-8")
        else:
            if corruption == "missing_files":
                manifest.pop("files")
            elif corruption == "files_null":
                manifest["files"] = None
            elif corruption == "files_list":
                manifest["files"] = []
            elif corruption == "file_record_null":
                manifest["files"][rel_path] = None
            elif corruption == "missing_sha256":
                manifest["files"][rel_path].pop("sha256")
            elif corruption == "sha256_not_string":
                manifest["files"][rel_path]["sha256"] = 123
            elif corruption == "sha256_short":
                manifest["files"][rel_path]["sha256"] = "abc"
            elif corruption == "missing_size":
                manifest["files"][rel_path].pop("size")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(LedgerError, match="Invalid tick manifest"):
            Ledger.open(tmp_path, "BB_corrupt_manifest")
