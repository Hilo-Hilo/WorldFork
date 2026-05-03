"""Unit tests for backend.app.storage.export."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from backend.app.storage.export import ExportError, export_run_to_zip, import_run_from_zip
from backend.app.storage.ledger import Ledger
from backend.app.storage.sot_loader import _compute_snapshot_merkle


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

BIG_BANG_ID = "BB_export_test_001"
SCENARIO = "Export test scenario"
CONFIG_SNAPSHOT = {"provider": "openrouter", "model": "openai/gpt-4o"}


def _assert_import_cleanup(dst_root: Path, run_folder_name: str = BIG_BANG_ID) -> None:
    runs_dir = dst_root / "runs"
    assert not (runs_dir / run_folder_name).exists()
    assert list(runs_dir.glob(".*.import-*")) == []


def _make_ledger(tmp_path: Path, big_bang_id: str = BIG_BANG_ID) -> Ledger:
    """Create a minimal ledger with one universe and one sealed tick."""
    sot_source = tmp_path / "sot_source"
    _write_minimal_sot_snapshot(sot_source)
    sot_snapshot_sha = _compute_snapshot_merkle(sot_source)
    ledger = Ledger.begin_run(
        run_root=tmp_path,
        big_bang_id=big_bang_id,
        scenario_text=SCENARIO,
        sot_snapshot_sha=sot_snapshot_sha,
        config_snapshot=CONFIG_SNAPSHOT,
    )
    shutil.copytree(sot_source, ledger.run_folder / "source_of_truth_snapshot")
    ledger.begin_universe("U000", parent=None, branch_from_tick=None, branch_delta=None)
    ledger.begin_tick("U000", 0)
    ledger.write_artifact(
        "universes/U000/ticks/tick_000/universe_state_before.json",
        {"tick": 0, "state": "before"},
        immutable=False,
    )
    ledger.write_artifact(
        "universes/U000/ticks/tick_000/universe_state_after.json",
        {"tick": 0, "state": "after"},
        immutable=False,
    )
    ledger.seal_tick("U000", 0)
    return ledger


def _write_minimal_sot_snapshot(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "VERSION").write_text("0.0.1-test", encoding="utf-8")
    (path / "taxonomy.json").write_text('{"name":"test"}', encoding="utf-8")


def _rewrite_zip_with_member(
    source: Path,
    dest: Path,
    member_name: str,
    replacement: bytes,
    *,
    refresh_export_manifest_hashes: bool = False,
) -> None:
    with zipfile.ZipFile(source, "r") as zf_in, zipfile.ZipFile(
        dest, "w", compression=zipfile.ZIP_DEFLATED
    ) as zf_out:
        members: dict[str, bytes] = {}
        for item in zf_in.infolist():
            data = zf_in.read(item.filename)
            if item.filename == member_name:
                data = replacement
            members[item.filename] = data

        if refresh_export_manifest_hashes:
            manifest = json.loads(members["EXPORT_MANIFEST.json"])
            files = {
                name: data
                for name, data in members.items()
                if name != "EXPORT_MANIFEST.json"
            }
            import hashlib

            manifest["total_bytes"] = sum(len(data) for data in files.values())
            manifest["files_sha256"] = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in sorted(files.items())
            }
            members["EXPORT_MANIFEST.json"] = json.dumps(manifest).encode("utf-8")

        for item in zf_in.infolist():
            zf_out.writestr(item, members[item.filename])


# ---------------------------------------------------------------------------
# Test: export → import roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_roundtrip_preserves_files(self, tmp_path: Path) -> None:
        """Files written to the ledger survive export → import intact."""
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        run_folder = ledger.run_folder

        zip_dest = tmp_path / "export.zip"
        export_run_to_zip(run_folder=run_folder, dest=zip_dest, verify=False)

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        extracted = import_run_from_zip(zip_path=zip_dest, dest_root=dst_root, verify=True)

        assert extracted.is_dir()

        # manifest.json should be present
        assert (extracted / "manifest.json").exists()

        # The sealed tick files should survive
        before_json = extracted / "universes" / "U000" / "ticks" / "tick_000" / "universe_state_before.json"
        assert before_json.exists()
        data = json.loads(before_json.read_bytes())
        assert data["state"] == "before"

    def test_roundtrip_merkle_root_matches(self, tmp_path: Path) -> None:
        """Tick Merkle roots match after roundtrip import."""
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        run_folder = ledger.run_folder

        # Read original Merkle root from the tick manifest
        tick_mf_path = (
            run_folder / "universes" / "U000" / "ticks" / "tick_000" / "manifest.json"
        )
        original_root = json.loads(tick_mf_path.read_bytes())["merkle_root"]

        zip_dest = tmp_path / "rt.zip"
        export_run_to_zip(run_folder=run_folder, dest=zip_dest, verify=False)

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        extracted = import_run_from_zip(zip_path=zip_dest, dest_root=dst_root, verify=True)

        imported_tick_mf = (
            extracted / "universes" / "U000" / "ticks" / "tick_000" / "manifest.json"
        )
        imported_root = json.loads(imported_tick_mf.read_bytes())["merkle_root"]
        assert imported_root == original_root

    def test_export_manifest_present_in_zip(self, tmp_path: Path) -> None:
        """EXPORT_MANIFEST.json is written at the root of the zip."""
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)

        zip_dest = tmp_path / "em.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        with zipfile.ZipFile(zip_dest, "r") as zf:
            names = zf.namelist()
            assert "EXPORT_MANIFEST.json" in names
            em = json.loads(zf.read("EXPORT_MANIFEST.json"))
            assert "exported_at" in em
            assert "file_count" in em
            assert "total_bytes" in em
            assert "run_manifest_sha256" in em
            assert "files_sha256" in em
            assert "exporter_version" in em
            assert em["file_count"] > 0

    def test_export_uses_posix_member_names_and_excludes_exports_dir(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        exports_dir = ledger.run_folder / "exports"
        exports_dir.mkdir()
        stale_zip = exports_dir / "previous.zip"
        stale_zip.write_bytes(b"old archive")

        zip_dest = exports_dir / "current.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        with zipfile.ZipFile(zip_dest, "r") as zf:
            names = zf.namelist()

        assert "exports/previous.zip" not in names
        assert "exports/current.zip" not in names
        assert all("\\" not in name for name in names)
        assert "config/config_snapshot.json" in names

    def test_repeated_export_to_run_folder_does_not_embed_previous_zip(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        zip_dest = ledger.run_folder / "run.zip"

        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)
        first_size = zip_dest.stat().st_size
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        with zipfile.ZipFile(zip_dest, "r") as zf:
            assert "run.zip" not in zf.namelist()
        assert zip_dest.stat().st_size < first_size * 2


# ---------------------------------------------------------------------------
# Test: tampered file detected by import verify
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_tampered_file_raises_export_error(self, tmp_path: Path) -> None:
        """Tampering a file inside the zip causes import verify to raise ExportError."""
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        run_folder = ledger.run_folder

        zip_dest = tmp_path / "tamper.zip"
        export_run_to_zip(run_folder=run_folder, dest=zip_dest, verify=False)

        # Tamper: rewrite the tick state file inside the zip
        tampered_zip = tmp_path / "tampered.zip"
        with zipfile.ZipFile(zip_dest, "r") as zf_in, zipfile.ZipFile(
            tampered_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf_out:
            for item in zf_in.infolist():
                data = zf_in.read(item.filename)
                if "universe_state_before.json" in item.filename:
                    # Overwrite with tampered content
                    data = b'{"tick": 0, "state": "TAMPERED"}'
                zf_out.writestr(item, data)

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="mismatch|verification"):
            import_run_from_zip(zip_path=tampered_zip, dest_root=dst_root, verify=True)

    def test_tampered_export_manifest_raises_export_error(self, tmp_path: Path) -> None:
        """Import verify checks the top-level EXPORT_MANIFEST.json."""
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)

        zip_dest = tmp_path / "manifest.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        tampered_zip = tmp_path / "tampered_manifest.zip"
        with zipfile.ZipFile(zip_dest, "r") as zf_in, zipfile.ZipFile(
            tampered_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf_out:
            for item in zf_in.infolist():
                data = zf_in.read(item.filename)
                if item.filename == "EXPORT_MANIFEST.json":
                    manifest = json.loads(data)
                    manifest["run_manifest_sha256"] = "0" * 64
                    data = json.dumps(manifest).encode("utf-8")
                zf_out.writestr(item, data)

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="EXPORT_MANIFEST.json.*run_manifest_sha256"):
            import_run_from_zip(zip_path=tampered_zip, dest_root=dst_root, verify=True)

    def test_tampered_config_snapshot_raises_export_error(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)

        zip_dest = tmp_path / "config.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        tampered_zip = tmp_path / "tampered_config.zip"
        _rewrite_zip_with_member(
            zip_dest,
            tampered_zip,
            "config/config_snapshot.json",
            b'{"provider":"tampered"}',
            refresh_export_manifest_hashes=True,
        )

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="Config snapshot SHA mismatch"):
            import_run_from_zip(zip_path=tampered_zip, dest_root=dst_root, verify=True)
        _assert_import_cleanup(dst_root)

    def test_tampered_source_of_truth_snapshot_raises_export_error(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)

        zip_dest = tmp_path / "sot.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        tampered_zip = tmp_path / "tampered_sot.zip"
        _rewrite_zip_with_member(
            zip_dest,
            tampered_zip,
            "source_of_truth_snapshot/VERSION",
            b"tampered",
            refresh_export_manifest_hashes=True,
        )

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="Source-of-truth snapshot SHA mismatch"):
            import_run_from_zip(zip_path=tampered_zip, dest_root=dst_root, verify=True)
        _assert_import_cleanup(dst_root)

    def test_tampered_tick_merkle_raises_export_error_and_cleans_import(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)

        zip_dest = tmp_path / "tick.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        tampered_zip = tmp_path / "tampered_tick.zip"
        _rewrite_zip_with_member(
            zip_dest,
            tampered_zip,
            "universes/U000/ticks/tick_000/universe_state_before.json",
            b'{"tick":0,"state":"tampered-after-export"}',
            refresh_export_manifest_hashes=True,
        )

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="Merkle mismatch"):
            import_run_from_zip(zip_path=tampered_zip, dest_root=dst_root, verify=True)
        _assert_import_cleanup(dst_root)


class TestZipSafety:
    def test_import_rejects_zip_path_traversal(self, tmp_path: Path) -> None:
        """Archive members cannot write outside the extraction directory."""
        zip_path = tmp_path / "traversal.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "EXPORT_MANIFEST.json",
                json.dumps(
                    {
                        "exported_at": "2026-04-29T00:00:00+00:00",
                        "file_count": 2,
                        "total_bytes": 4,
                        "run_manifest_sha256": "0" * 64,
                        "exporter_version": "1.0.0",
                    }
                ),
            )
            zf.writestr("manifest.json", "{}")
            zf.writestr("../evil.txt", "bad")

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="Unsafe zip member path"):
            import_run_from_zip(zip_path=zip_path, dest_root=dst_root, verify=False)

        assert not (tmp_path / "evil.txt").exists()

    def test_import_refuses_existing_run_folder_without_overwriting(self, tmp_path: Path) -> None:
        src_root = tmp_path / "src"
        src_root.mkdir()
        ledger = _make_ledger(src_root)
        zip_dest = tmp_path / "existing.zip"
        export_run_to_zip(run_folder=ledger.run_folder, dest=zip_dest, verify=False)

        dst_root = tmp_path / "dst"
        final_dir = dst_root / "runs" / BIG_BANG_ID
        final_dir.mkdir(parents=True)
        sentinel = final_dir / "sentinel.txt"
        sentinel.write_text("keep me", encoding="utf-8")

        with pytest.raises(ExportError, match="Destination run already exists"):
            import_run_from_zip(zip_path=zip_dest, dest_root=dst_root, verify=True)

        assert sentinel.read_text(encoding="utf-8") == "keep me"
        assert list((dst_root / "runs").glob(".*.import-*")) == []

    def test_import_rejects_manifest_big_bang_id_path_traversal(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "unsafe_run_folder.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("EXPORT_MANIFEST.json", "{}")
            zf.writestr("manifest.json", json.dumps({"big_bang_id": "../escape"}))

        dst_root = tmp_path / "dst"
        dst_root.mkdir()
        with pytest.raises(ExportError, match="Unsafe run folder name"):
            import_run_from_zip(zip_path=zip_path, dest_root=dst_root, verify=False)

        assert not (dst_root / "escape").exists()
        assert not (tmp_path / "escape").exists()


# ---------------------------------------------------------------------------
# Test: export missing run folder raises ExportError
# ---------------------------------------------------------------------------


class TestMissingFolder:
    def test_export_missing_folder_raises(self, tmp_path: Path) -> None:
        """Exporting a non-existent run folder raises ExportError."""
        missing = tmp_path / "does_not_exist"
        zip_dest = tmp_path / "out.zip"
        with pytest.raises(ExportError, match="does not exist"):
            export_run_to_zip(run_folder=missing, dest=zip_dest, verify=False)
