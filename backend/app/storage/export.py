"""Run-folder export/import helpers.

Provides zip-based export with optional Merkle verification and a top-level
EXPORT_MANIFEST.json inside the archive.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
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
    for path in sorted(p for p in run_folder.rglob("*") if p.is_file()):
        resolved = path.resolve()
        if resolved == dest:
            continue
        if resolved == exports_dir or exports_dir in resolved.parents:
            continue
        files.append(path)
    return files


def _safe_zip_member_path(extract_dir: Path, member_name: str) -> Path:
    """Return a safe extraction target for a zip member.

    Zip names are POSIX-style regardless of host OS. Reject absolute paths,
    parent traversal, and platform path separators before joining with the
    extraction root.
    """
    normalized = posixpath.normpath(member_name)
    if (
        not member_name
        or member_name.startswith("/")
        or os.path.isabs(member_name)
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

    run_manifest_members = [
        item for item in files if Path(item.filename).parts[-1:] == ("manifest.json",)
    ]
    top_level_run_manifest = [
        item for item in run_manifest_members if len(Path(item.filename).parts) == 1
    ]
    if top_level_run_manifest:
        run_manifest_member = top_level_run_manifest[0]
    elif len(run_manifest_members) == 1:
        run_manifest_member = run_manifest_members[0]
    else:
        raise ExportError("Archive does not contain an unambiguous run manifest.json.")

    actual_run_manifest_sha = hashlib.sha256(zf.read(run_manifest_member.filename)).hexdigest()
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
        names = zf.namelist()

        # Validate EXPORT_MANIFEST.json is present
        if "EXPORT_MANIFEST.json" not in names:
            raise ExportError("Zip is missing EXPORT_MANIFEST.json; not a valid WorldFork export.")
        export_manifest = _read_export_manifest(zf)

        for member in zf.infolist():
            if member.filename == "EXPORT_MANIFEST.json" or member.is_dir():
                continue
            if S_ISLNK(member.external_attr >> 16):
                raise ExportError(f"Unsafe zip member type: {member.filename}")
            _safe_zip_member_path(Path("/tmp/worldfork-import-check"), member.filename)

        if verify:
            _verify_export_manifest(zf, export_manifest)

        # Find the run folder name by locating manifest.json at the top level
        run_folder_name: str | None = None
        for name in names:
            parts = Path(name).parts
            if len(parts) == 1 and parts[0] == "manifest.json":
                run_folder_name = zip_path.stem  # use zip stem as fallback
                break
            if len(parts) >= 1 and parts[-1] == "manifest.json":
                # Try reading the manifest to get a meaningful folder name
                try:
                    data = json.loads(zf.read(name))
                    bb_id = data.get("big_bang_id", "")
                    if bb_id:
                        run_folder_name = bb_id
                    else:
                        run_folder_name = zip_path.stem
                except Exception:
                    run_folder_name = zip_path.stem
                break

        if run_folder_name is None:
            run_folder_name = zip_path.stem

        # Read the run manifest from archive to determine canonical folder name
        # Try to use the folder structure that was in the zip
        top_level_dirs: set[str] = set()
        for name in names:
            parts = Path(name).parts
            if parts and parts[0] not in ("EXPORT_MANIFEST.json",):
                top_level_dirs.add(parts[0])

        # If the zip was created with run_folder relative paths, use the zip stem
        # as the run folder name (this matches the export convention)
        extract_dir = dest_runs / run_folder_name
        extract_dir.mkdir(parents=True, exist_ok=True)

        # Extract all files except EXPORT_MANIFEST.json
        for member in zf.infolist():
            if member.filename == "EXPORT_MANIFEST.json" or member.is_dir():
                continue
            target = _safe_zip_member_path(extract_dir, member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member.filename))

    if verify:
        _verify_imported_run(extract_dir)

    return extract_dir


def _verify_imported_run(run_folder: Path) -> None:
    """Verify Merkle roots of all sealed ticks in the extracted run.

    Raises :class:`ExportError` on any mismatch.
    """
    from backend.app.storage.checksums import merkle_root, sha256_file

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
            sot_snapshot = run_folder / str(snapshot_path)
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
            files_found = sorted(
                p for p in tick_dir.rglob("*") if p.is_file() and p.name != "manifest.json"
            )
            file_hashes = [sha256_file(fp) for fp in files_found]
            actual_root = merkle_root(file_hashes)

            if actual_root != expected_root:
                errors.append(
                    f"Merkle mismatch in {tick_dir.relative_to(run_folder)}: "
                    f"expected={expected_root} actual={actual_root}"
                )

    if errors:
        raise ExportError(
            f"Import verification failed ({len(errors)} error(s)): " + "; ".join(errors)
        )
