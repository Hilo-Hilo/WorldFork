"""Run-folder export/import helpers.

Provides zip-based export with optional Merkle verification and a top-level
EXPORT_MANIFEST.json inside the archive.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import tempfile
import zipfile
from pathlib import Path
from stat import S_ISLNK
from typing import Any

import orjson

from backend.app.core.clock import now_utc

EXPORTER_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ExportError(Exception):
    """Raised on export or import failures (verification mismatch, missing folder, etc.)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute hex SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_run_manifest_sha(run_folder: Path) -> str:
    """Return the SHA-256 of the run-level manifest.json (if present)."""
    manifest_path = run_folder / "manifest.json"
    if not manifest_path.exists():
        return "0" * 64
    return _sha256_file(manifest_path)


def _iter_export_files(run_folder: Path, dest: Path) -> list[Path]:
    """Return run files that should be included in an export archive."""
    run_folder = run_folder.resolve()
    dest = dest.resolve()
    exports_dir = run_folder / "exports"

    files: list[Path] = []
    for path in sorted(run_folder.rglob("*")):
        if path == exports_dir or exports_dir in path.parents:
            continue
        if path.is_symlink():
            relative = path.relative_to(run_folder).as_posix()
            raise ExportError(f"Run export cannot include symlinks: {relative}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved == dest:
            continue
        files.append(path)
    return files


def _safe_zip_member_path(extract_dir: Path, member_name: str) -> Path:
    """Return a safe extraction target for a zip member.

    Zip names are POSIX-style regardless of host OS. Reject absolute paths,
    parent traversal, and platform path separators before joining with the
    extraction root.
    """
    raw_member_name = member_name[:-1] if member_name.endswith("/") else member_name
    raw_parts = raw_member_name.split("/")
    normalized = posixpath.normpath(member_name)
    if (
        not member_name
        or member_name.startswith("/")
        or os.path.isabs(member_name)
        or any(part in ("", ".", "..") for part in raw_parts)
        or normalized in ("", ".")
        or normalized.startswith("../")
        or normalized == ".."
        or "\\" in member_name
    ):
        raise ExportError(f"Unsafe zip member path: {member_name}")

    parts = Path(normalized).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ExportError(f"Unsafe zip member path: {member_name}")

    target = (extract_dir / normalized).resolve()
    extract_root = extract_dir.resolve()
    if target != extract_root and extract_root not in target.parents:
        raise ExportError(f"Unsafe zip member path: {member_name}")
    return target


def _reject_duplicate_zip_members(zf: zipfile.ZipFile) -> None:
    seen: set[str] = set()
    for member in zf.infolist():
        if member.filename in seen:
            raise ExportError(f"Duplicate zip member: {member.filename}")
        seen.add(member.filename)


def _safe_manifest_relative_path(root: Path, value: str, label: str) -> Path:
    try:
        return _safe_zip_member_path(root, value)
    except ExportError as exc:
        raise ExportError(f"Unsafe {label}: {value}") from exc


def _safe_run_folder_name(run_folder_name: str, *, dest_runs: Path) -> str:
    """Validate a run folder name before using it under dest_root/runs."""
    if not isinstance(run_folder_name, str):
        raise ExportError("Run folder name must be a string.")
    normalized = posixpath.normpath(run_folder_name)
    if (
        not run_folder_name
        or run_folder_name.startswith("/")
        or os.path.isabs(run_folder_name)
        or normalized in ("", ".")
        or normalized.startswith("../")
        or normalized == ".."
        or "/" in run_folder_name
        or "\\" in run_folder_name
    ):
        raise ExportError(f"Unsafe run folder name: {run_folder_name}")

    target = (dest_runs / normalized).resolve()
    dest_root = dest_runs.resolve()
    if target == dest_root or dest_root not in target.parents:
        raise ExportError(f"Unsafe run folder name: {run_folder_name}")
    return normalized


def _read_export_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(zf.read("EXPORT_MANIFEST.json"))
    except KeyError as exc:
        raise ExportError("Zip is missing EXPORT_MANIFEST.json; not a valid WorldFork export.") from exc
    except Exception as exc:
        raise ExportError(f"Cannot read EXPORT_MANIFEST.json: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ExportError("EXPORT_MANIFEST.json must contain a JSON object.")
    return manifest


def _read_top_level_run_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(zf.read("manifest.json"))
    except KeyError as exc:
        raise ExportError("Archive is missing top-level manifest.json.") from exc
    except Exception as exc:
        raise ExportError(f"Cannot read top-level manifest.json: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ExportError("Top-level manifest.json must contain a JSON object.")
    return manifest


def _verify_export_manifest(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    """Verify archive contents against the top-level export manifest."""
    files = [
        item
        for item in zf.infolist()
        if not item.is_dir() and item.filename != "EXPORT_MANIFEST.json"
    ]

    expected_file_count = manifest.get("file_count")
    if expected_file_count != len(files):
        raise ExportError(
            f"EXPORT_MANIFEST.json file_count mismatch: "
            f"expected={expected_file_count} actual={len(files)}"
        )

    total_bytes = sum(item.file_size for item in files)
    expected_total_bytes = manifest.get("total_bytes")
    if expected_total_bytes != total_bytes:
        raise ExportError(
            f"EXPORT_MANIFEST.json total_bytes mismatch: "
            f"expected={expected_total_bytes} actual={total_bytes}"
        )

    expected_run_manifest_sha = manifest.get("run_manifest_sha256")
    if not isinstance(expected_run_manifest_sha, str) or len(expected_run_manifest_sha) != 64:
        raise ExportError("EXPORT_MANIFEST.json has invalid run_manifest_sha256.")

    if "manifest.json" not in {item.filename for item in files}:
        raise ExportError("Archive is missing top-level manifest.json.")

    actual_run_manifest_sha = hashlib.sha256(zf.read("manifest.json")).hexdigest()
    if actual_run_manifest_sha != expected_run_manifest_sha:
        raise ExportError(
            f"EXPORT_MANIFEST.json run_manifest_sha256 mismatch: "
            f"expected={expected_run_manifest_sha} actual={actual_run_manifest_sha}"
        )

    expected_file_hashes = manifest.get("files_sha256")
    if expected_file_hashes is not None:
        if not isinstance(expected_file_hashes, dict):
            raise ExportError("EXPORT_MANIFEST.json has invalid files_sha256.")
        actual_file_names = {item.filename for item in files}
        expected_file_names = set(expected_file_hashes)
        if expected_file_names != actual_file_names:
            raise ExportError(
                "EXPORT_MANIFEST.json files_sha256 member mismatch: "
                f"expected={sorted(expected_file_names)} actual={sorted(actual_file_names)}"
            )
        for item in files:
            expected_sha = expected_file_hashes.get(item.filename)
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                raise ExportError(f"EXPORT_MANIFEST.json has invalid SHA for {item.filename}.")
            actual_sha = hashlib.sha256(zf.read(item.filename)).hexdigest()
            if actual_sha != expected_sha:
                raise ExportError(
                    f"EXPORT_MANIFEST.json file SHA mismatch for {item.filename}: "
                    f"expected={expected_sha} actual={actual_sha}"
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_run_to_zip(
    *,
    run_folder: Path,
    dest: Path,
    verify: bool = True,
) -> Path:
    """Zip a run folder into *dest*, optionally verifying ledger integrity first.

    Args:
        run_folder: Path to the run folder (e.g. ``runs/BB_<ts>_<slug>/``).
        dest: Destination .zip path. Parent directories are created if absent.
        verify: If True, calls ``Ledger.open().verify()`` and raises
            :class:`ExportError` if any SHA mismatches are found.

    Returns:
        *dest* on success.

    Raises:
        ExportError: If *run_folder* does not exist, or if verify=True and
            ledger verification fails.
    """
    if not run_folder.exists() or not run_folder.is_dir():
        raise ExportError(f"Run folder does not exist: {run_folder}")

    # Optionally verify ledger integrity before zipping
    if verify:
        try:
            # Import here to avoid circular deps
            from backend.app.storage.ledger import Ledger

            # The run_folder sits inside <run_root>/runs/<folder_name>
            # so run_root is two levels up
            run_root = run_folder.parent.parent
            # Read manifest to get big_bang_id
            manifest_path = run_folder / "manifest.json"
            if not manifest_path.exists():
                raise ExportError(f"No manifest.json found in {run_folder}")
            manifest_data = json.loads(manifest_path.read_bytes())
            big_bang_id = manifest_data.get("big_bang_id")
            if not big_bang_id:
                raise ExportError("manifest.json missing big_bang_id")
            ledger = Ledger.open(run_root, big_bang_id)
            errors = ledger.verify()
            if errors:
                raise ExportError(
                    f"Ledger verification failed before export ({len(errors)} error(s)): "
                    + "; ".join(errors[:5])
                )
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(f"Failed to open/verify ledger: {exc}") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Gather all files. Exclude archive outputs so repeated exports do not
    # include or truncate a previous zip as an input member.
    all_files = _iter_export_files(run_folder, dest)
    total_bytes = sum(p.stat().st_size for p in all_files)
    files_sha256 = {
        file_path.relative_to(run_folder).as_posix(): _sha256_file(file_path)
        for file_path in all_files
    }

    run_manifest_sha256 = _read_run_manifest_sha(run_folder)

    # Build the archive
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=dest.parent,
    )
    os.close(fd)
    temp_dest = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file_path in all_files:
                arcname = file_path.relative_to(run_folder).as_posix()
                zf.write(file_path, arcname)

            # Write top-level EXPORT_MANIFEST.json
            export_manifest: dict[str, Any] = {
                "exported_at": now_utc().isoformat(),
                "file_count": len(all_files),
                "total_bytes": total_bytes,
                "run_manifest_sha256": run_manifest_sha256,
                "files_sha256": files_sha256,
                "exporter_version": EXPORTER_VERSION,
            }
            manifest_bytes = orjson.dumps(export_manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
            zf.writestr("EXPORT_MANIFEST.json", manifest_bytes)
        os.replace(temp_dest, dest)
    finally:
        temp_dest.unlink(missing_ok=True)

    return dest


def import_run_from_zip(
    *,
    zip_path: Path,
    dest_root: Path,
    verify: bool = True,
) -> Path:
    """Extract a run zip into ``dest_root/runs/``.

    Args:
        zip_path: Path to the .zip file produced by :func:`export_run_to_zip`.
        dest_root: Root directory. The run will be extracted under
            ``dest_root/runs/<run_folder_name>/``.
        verify: If True, recomputes Merkle roots for each sealed tick and
            compares them to the stored tick manifests; raises
            :class:`ExportError` on any mismatch.

    Returns:
        The path to the extracted run folder.

    Raises:
        ExportError: On extraction or verification failure.
    """
    if not zip_path.exists():
        raise ExportError(f"Zip file does not exist: {zip_path}")

    dest_runs = dest_root / "runs"
    dest_runs.mkdir(parents=True, exist_ok=True)

    # Peek inside to determine the run folder name from manifest.json
    with zipfile.ZipFile(zip_path, "r") as zf:
        _reject_duplicate_zip_members(zf)
        names = zf.namelist()

        # Validate EXPORT_MANIFEST.json is present
        if "EXPORT_MANIFEST.json" not in names:
            raise ExportError("Zip is missing EXPORT_MANIFEST.json; not a valid WorldFork export.")
        export_manifest = _read_export_manifest(zf)

        for member in zf.infolist():
            if member.filename == "EXPORT_MANIFEST.json":
                continue
            if S_ISLNK(member.external_attr >> 16):
                raise ExportError(f"Unsafe zip member type: {member.filename}")
            _safe_zip_member_path(Path("/tmp/worldfork-import-check"), member.filename)
            if member.is_dir():
                continue

        if verify:
            _verify_export_manifest(zf, export_manifest)

        # Find the run folder name from the top-level run manifest only.
        run_manifest = _read_top_level_run_manifest(zf)
        run_folder_name = run_manifest.get("big_bang_id") or zip_path.stem
        run_folder_name = _safe_run_folder_name(run_folder_name, dest_runs=dest_runs)

        # Extract into a sibling temp directory first. Verification happens before
        # the final rename so failed imports never leave partial or merged run data.
        extract_dir = dest_runs / run_folder_name
        if extract_dir.exists():
            raise ExportError(f"Destination run already exists: {extract_dir.name}")
        temp_extract_dir = Path(
            tempfile.mkdtemp(prefix=f".{run_folder_name}.import-", dir=dest_runs)
        )

        try:
            # Extract all files except EXPORT_MANIFEST.json
            for member in zf.infolist():
                if member.filename == "EXPORT_MANIFEST.json" or member.is_dir():
                    continue
                target = _safe_zip_member_path(temp_extract_dir, member.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member.filename))

            if verify:
                _verify_imported_run(temp_extract_dir)

            temp_extract_dir.rename(extract_dir)
        except Exception:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
            raise

    return extract_dir


def _verify_imported_run(run_folder: Path) -> None:
    """Verify Merkle roots of all sealed ticks in the extracted run.

    Raises :class:`ExportError` on any mismatch.
    """
    from backend.app.storage.checksums import sha256_file
    from backend.app.storage.ledger import (
        LedgerError,
        _tick_files_for_merkle,
        _tick_merkle_root,
    )

    errors: list[str] = []
    run_manifest_path = run_folder / "manifest.json"
    try:
        run_manifest = json.loads(run_manifest_path.read_bytes())
    except Exception as exc:
        raise ExportError(f"Import verification failed: cannot read manifest.json: {exc}") from exc

    expected_config_sha = run_manifest.get("config_sha256")
    config_snapshot = run_folder / "config" / "config_snapshot.json"
    if isinstance(expected_config_sha, str):
        if not config_snapshot.exists():
            errors.append("Missing config/config_snapshot.json")
        else:
            actual_config_sha = sha256_file(config_snapshot)
            if actual_config_sha != expected_config_sha:
                errors.append(
                    f"Config snapshot SHA mismatch: expected={expected_config_sha} actual={actual_config_sha}"
                )

    source_of_truth = run_manifest.get("source_of_truth")
    if isinstance(source_of_truth, dict):
        expected_sot_sha = source_of_truth.get("snapshot_sha256")
        snapshot_path = source_of_truth.get("snapshot_path", "source_of_truth_snapshot")
        if isinstance(expected_sot_sha, str):
            sot_snapshot = _safe_manifest_relative_path(
                run_folder,
                str(snapshot_path),
                "source-of-truth snapshot path",
            )
            if not sot_snapshot.exists():
                errors.append(f"Missing source-of-truth snapshot: {snapshot_path}")
            elif not sot_snapshot.is_dir():
                errors.append(f"Source-of-truth snapshot is not a directory: {snapshot_path}")
            else:
                from backend.app.storage.sot_loader import _compute_snapshot_merkle

                actual_sot_sha = _compute_snapshot_merkle(sot_snapshot)
                if actual_sot_sha != expected_sot_sha:
                    errors.append(
                        f"Source-of-truth snapshot SHA mismatch: "
                        f"expected={expected_sot_sha} actual={actual_sot_sha}"
                    )

    universes_dir = run_folder / "universes"
    if not universes_dir.exists():
        if errors:
            raise ExportError(
                f"Import verification failed ({len(errors)} error(s)): " + "; ".join(errors)
            )
        return

    for universe_dir in sorted(universes_dir.iterdir()):
        if not universe_dir.is_dir():
            continue
        ticks_dir = universe_dir / "ticks"
        if not ticks_dir.exists():
            continue

        for tick_dir in sorted(ticks_dir.iterdir()):
            if not tick_dir.is_dir():
                continue
            tick_manifest_path = tick_dir / "manifest.json"
            if not tick_manifest_path.exists():
                continue

            try:
                tick_manifest = json.loads(tick_manifest_path.read_bytes())
            except Exception as exc:
                errors.append(f"Cannot read tick manifest at {tick_manifest_path}: {exc}")
                continue

            expected_root = tick_manifest.get("merkle_root")
            if not expected_root:
                continue  # Tick not sealed; skip

            # Recompute Merkle root from actual files
            try:
                files_found = _tick_files_for_merkle(tick_dir)
                errors.extend(
                    _verify_imported_tick_manifest_files(
                        run_folder,
                        tick_manifest_path,
                        tick_manifest,
                        files_found,
                    )
                )
                actual_root = _tick_merkle_root(tick_dir, files_found)
            except LedgerError as exc:
                errors.append(
                    f"Cannot compute Merkle root for {tick_dir.relative_to(run_folder)}: {exc}"
                )
                continue

            if actual_root != expected_root:
                errors.append(
                    f"Merkle mismatch in {tick_dir.relative_to(run_folder)}: "
                    f"expected={expected_root} actual={actual_root}"
                )

    if errors:
        raise ExportError(
            f"Import verification failed ({len(errors)} error(s)): " + "; ".join(errors)
        )


def _verify_imported_tick_manifest_files(
    run_folder: Path,
    tick_manifest_path: Path,
    tick_manifest: dict[str, Any],
    files_found: list[Path],
) -> list[str]:
    from backend.app.storage.checksums import sha256_file
    from backend.app.storage.ledger import (
        LedgerError,
        _safe_cached_file_record,
        _safe_cached_rel_path,
    )

    errors: list[str] = []
    manifest_files = tick_manifest.get("files")
    if not isinstance(manifest_files, dict):
        return [f"Invalid tick manifest {tick_manifest_path}: files must be an object"]

    actual_paths = {
        path.relative_to(run_folder).as_posix()
        for path in files_found
        if path != tick_manifest_path
    }
    recorded_paths: set[str] = set()

    for rel_path, record in manifest_files.items():
        if not isinstance(rel_path, str):
            errors.append(f"Invalid tick manifest {tick_manifest_path}: file path must be a string")
            continue
        try:
            safe_rel_path = _safe_cached_rel_path(run_folder, rel_path)
        except LedgerError as exc:
            errors.append(f"Invalid tick manifest {tick_manifest_path}: {exc}")
            continue
        recorded_paths.add(safe_rel_path)

        if not isinstance(record, dict):
            errors.append(f"Invalid tick manifest {tick_manifest_path}: file record must be an object")
            continue
        try:
            safe_record = _safe_cached_file_record(tick_manifest_path, record)
        except LedgerError as exc:
            errors.append(str(exc))
            continue

        target = run_folder / safe_rel_path
        if not target.exists():
            errors.append(f"Invalid tick manifest {tick_manifest_path}: missing recorded file {safe_rel_path}")
            continue
        if target.is_symlink() or not target.is_file():
            errors.append(f"Invalid tick manifest {tick_manifest_path}: recorded file is not regular {safe_rel_path}")
            continue

        actual_sha = sha256_file(target)
        if actual_sha != safe_record["sha256"]:
            errors.append(
                f"Invalid tick manifest {tick_manifest_path}: SHA mismatch for {safe_rel_path}"
            )
        actual_size = target.stat().st_size
        if actual_size != safe_record["size"]:
            errors.append(
                f"Invalid tick manifest {tick_manifest_path}: size mismatch for {safe_rel_path}"
            )

    if recorded_paths != actual_paths:
        errors.append(
            f"Invalid tick manifest {tick_manifest_path}: file list mismatch "
            f"expected={sorted(actual_paths)} actual={sorted(recorded_paths)}"
        )
    return errors
