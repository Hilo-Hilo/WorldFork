"""Run ledger — atomic, write-once artifact store with Merkle verification.

Every artifact written through :class:`Ledger` is persisted atomically
(tmp → fsync → rename) and optionally made immutable (chmod 0o444).
Tick manifests are Merkle-rooted; the run manifest is updated on every
``seal_tick`` and ``flush`` call.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import orjson

from backend.app.core.clock import now_utc
from backend.app.storage.checksums import merkle_root, sha256_bytes, sha256_file

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Base class for all ledger errors."""


class ImmutabilityError(LedgerError):
    """Raised when attempting to overwrite an immutable artifact."""


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Return a filesystem-safe slug derived from *text*."""
    lowered = text.lower()
    slugged = _NON_ALPHANUM.sub("-", lowered).strip("-")
    return slugged or "bigbang"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class Ledger:
    """A write-once, Merkle-verified artifact ledger for one Big Bang run."""

    # Schema version recorded in run manifests.
    SCHEMA_VERSION = "1"

    def __init__(self, run_root: Path, big_bang_id: str) -> None:
        self._run_root = Path(run_root)
        self._big_bang_id = big_bang_id
        self._run_folder: Path | None = None
        self._manifest: dict[str, Any] = {}
        # In-memory sha cache: rel_path -> {size, sha256, sealed_at}
        self._file_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Class-level constructors
    # ------------------------------------------------------------------

    @classmethod
    def begin_run(
        cls,
        run_root: Path,
        big_bang_id: str,
        *,
        scenario_text: str,
        sot_snapshot_sha: str,
        config_snapshot: dict,
    ) -> Ledger:
        """Create a new run folder and initialise the ledger manifest.

        Writes:
        - ``manifest.json`` (mutable, updated on every ``seal_tick``/``flush``)
        - ``config/config_snapshot.json`` (immutable)
        """
        ledger = cls(run_root, big_bang_id)
        now = now_utc()

        # Build run folder name: BB_{YYYYmmddTHHMMSSZ}_{slug[:32]}
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        slug = _slugify(scenario_text)[:32] or "bigbang"
        folder_name = f"BB_{ts}_{slug}"
        run_folder = Path(run_root) / "runs" / folder_name
        run_folder.mkdir(parents=True, exist_ok=True)
        ledger._run_folder = run_folder

        # Persist config snapshot (immutable)
        config_bytes = orjson.dumps(config_snapshot, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        config_sha = ledger._atomic_write(
            run_folder / "config" / "config_snapshot.json",
            config_bytes,
            immutable=True,
        )

        # Build initial manifest
        scenario_summary = (scenario_text[:120].strip() + "...") if len(scenario_text) > 120 else scenario_text.strip()
        ledger._manifest = {
            "schema_version": cls.SCHEMA_VERSION,
            "big_bang_id": big_bang_id,
            "created_at": now.isoformat(),
            "scenario_summary": scenario_summary,
            "source_of_truth": {
                "snapshot_sha256": sot_snapshot_sha,
                "snapshot_path": "source_of_truth_snapshot",
            },
            "config_sha256": config_sha,
            "universes": {},
        }
        ledger._flush_manifest()
        return ledger

    @classmethod
    def open(cls, run_root: Path, big_bang_id: str) -> Ledger:
        """Open an existing run identified by *big_bang_id*.

        Scans ``run_root/runs/`` for a folder whose manifest matches the
        given ``big_bang_id``.
        """
        runs_dir = Path(run_root) / "runs"
        if not runs_dir.exists():
            raise LedgerError(f"Run root {runs_dir} does not exist")
        for candidate in sorted(runs_dir.iterdir()):
            if not candidate.is_dir():
                continue
            mf_path = candidate / "manifest.json"
            if not mf_path.exists():
                continue
            try:
                mf = json.loads(mf_path.read_bytes())
            except Exception:
                continue
            if mf.get("big_bang_id") == big_bang_id:
                ledger = cls(run_root, big_bang_id)
                ledger._run_folder = candidate
                ledger._manifest = mf
                ledger._hydrate_file_cache_from_tick_manifests()
                return ledger
        raise LedgerError(
            f"No run folder found for big_bang_id={big_bang_id!r} under {runs_dir}"
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_folder(self) -> Path:
        if self._run_folder is None:
            raise LedgerError("Ledger has no run folder; call begin_run() or open() first.")
        return self._run_folder

    # ------------------------------------------------------------------
    # Universe and tick lifecycle
    # ------------------------------------------------------------------

    def begin_universe(
        self,
        universe_id: str,
        *,
        parent: str | None,
        branch_from_tick: int | None,
        branch_delta: dict | None,
    ) -> Path:
        """Create a universe directory and write its manifest.

        Updates the run-level manifest with the new universe entry and
        returns the universe directory path.
        """
        universe_dir = self.run_folder / "universes" / universe_id
        universe_dir.mkdir(parents=True, exist_ok=True)
        (universe_dir / "ticks").mkdir(exist_ok=True)
        (universe_dir / "logs").mkdir(exist_ok=True)

        # Build lineage
        if parent is None:
            lineage_path = [universe_id]
            depth = 0
        else:
            parent_entry = self._manifest["universes"].get(parent, {})
            parent_lineage = parent_entry.get("lineage_path", [parent])
            lineage_path = parent_lineage + [universe_id]
            depth = len(lineage_path) - 1

        # Universe manifest
        now = now_utc()
        universe_manifest: dict[str, Any] = {
            "universe_id": universe_id,
            "parent": parent,
            "depth": depth,
            "lineage_path": lineage_path,
            "branch_from_tick": branch_from_tick,
            "branch_delta": branch_delta,
            "created_at": now.isoformat(),
            "ticks": {},
        }
        um_bytes = orjson.dumps(universe_manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        um_sha = self._atomic_write(
            universe_dir / "manifest.json",
            um_bytes,
            immutable=False,
        )

        # Update run manifest
        self._manifest.setdefault("universes", {})[universe_id] = {
            "manifest_sha256": um_sha,
            "parent": parent,
            "depth": depth,
            "lineage_path": lineage_path,
            "branch_from_tick": branch_from_tick,
            "ticks": {},
        }
        self._flush_manifest()
        return universe_dir

    def begin_tick(self, universe_id: str, tick: int) -> Path:
        """Create a tick directory and write ``clock.json``."""
        tick_dir = self.run_folder / "universes" / universe_id / "ticks" / f"tick_{tick:03d}"
        tick_dir.mkdir(parents=True, exist_ok=True)

        # Sub-directories expected by
        for sub in ("llm_calls", "visible_packets", "events", "social_posts", "sociology", "memory", "god"):
            (tick_dir / sub).mkdir(exist_ok=True)

        now = now_utc()
        clock_data = {
            "tick": tick,
            "started_at": now.isoformat(),
        }
        clock_bytes = orjson.dumps(clock_data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        self._atomic_write(tick_dir / "clock.json", clock_bytes, immutable=False)
        return tick_dir

    # ------------------------------------------------------------------
    # Artifact writers
    # ------------------------------------------------------------------

    def write_artifact(
        self,
        rel_path: str,
        payload: bytes | str | dict,
        *,
        immutable: bool = True,
    ) -> str:
        """Write an artifact to ``run_folder / rel_path`` atomically.

        * If *payload* is a ``dict``, it is serialised with orjson
          (``OPT_INDENT_2 | OPT_SORT_KEYS``).
        * If *payload* is a ``str``, it is encoded as UTF-8.
        * If *immutable* is ``True`` and the target already exists,
          :class:`ImmutabilityError` is raised.

        Returns the hex SHA-256 of the bytes written.
        """
        target = self.run_folder / rel_path

        # Serialise
        if isinstance(payload, dict):
            raw: bytes = orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = payload

        # Immutability guard
        if immutable and target.exists():
            raise ImmutabilityError(
                f"Immutable artifact already exists: {target}"
            )

        sha = self._atomic_write(target, raw, immutable=immutable)

        # Record in in-memory cache
        now_str = now_utc().isoformat()
        self._file_cache[rel_path] = {
            "size": len(raw),
            "sha256": sha,
            "sealed_at": now_str,
        }
        return sha

    def append_jsonl(self, rel_path: str, item: dict) -> None:
        """Append one JSON line to a JSONL file (mutable, never chmod 0o444)."""
        target = self.run_folder / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        line = orjson.dumps(item).decode("utf-8") + "\n"
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)

    # ------------------------------------------------------------------
    # Tick sealing
    # ------------------------------------------------------------------

    def seal_tick(self, universe_id: str, tick: int) -> str:
        """Seal a tick by computing its Merkle root and writing tick manifest.

        Returns the Merkle root hex string.
        """
        tick_dir = self.run_folder / "universes" / universe_id / "ticks" / f"tick_{tick:03d}"

        # Gather all files under tick_dir in sorted path order
        files_found = _tick_files_for_merkle(tick_dir)
        file_records: dict[str, Any] = {}
        file_hashes: list[str] = []
        for fp in files_found:
            rel = str(fp.relative_to(self.run_folder))
            sha = sha256_file(fp)
            file_records[rel] = {
                "sha256": sha,
                "size": fp.stat().st_size,
            }
            file_hashes.append(sha)

        root = merkle_root(file_hashes)

        # Previous tick root
        universe_entry = self._manifest.get("universes", {}).get(universe_id, {})
        prev_tick_root: str | None = None
        if tick > 0:
            prev_key = str(tick - 1)
            prev_entry = universe_entry.get("ticks", {}).get(prev_key)
            if prev_entry:
                prev_tick_root = prev_entry.get("merkle_root")

        now = now_utc()
        tick_manifest: dict[str, Any] = {
            "tick": tick,
            "sealed_at": now.isoformat(),
            "merkle_root": root,
            "prev_tick_root": prev_tick_root,
            "files": file_records,
        }
        tm_bytes = orjson.dumps(tick_manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        self._atomic_write(tick_dir / "manifest.json", tm_bytes, immutable=False)

        # Update run-level manifest
        self._manifest.setdefault("universes", {}).setdefault(universe_id, {}).setdefault("ticks", {})[str(tick)] = {
            "merkle_root": root,
            "sealed_at": now.isoformat(),
            "prev_tick_root": prev_tick_root,
        }
        self._flush_manifest()
        return root

    # ------------------------------------------------------------------
    # Manifest and verification
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Persist the in-memory manifest to disk."""
        self._flush_manifest()

    def manifest(self) -> dict:
        """Return a copy of the in-memory run manifest."""
        return dict(self._manifest)

    def verify(self) -> list[str]:
        """Walk every recorded file, recompute SHA-256, and report mismatches.

        Returns a list of error strings; empty list means everything is clean.
        """
        mismatches: list[str] = []
        for rel_path, record in self._file_cache.items():
            abs_path = self.run_folder / rel_path
            if abs_path.is_symlink():
                mismatches.append(f"Recorded file is a symlink: {rel_path}")
                continue
            if not abs_path.exists():
                mismatches.append(f"Missing: {rel_path}")
                continue
            actual_sha = sha256_file(abs_path)
            expected_sha = record["sha256"]
            if actual_sha != expected_sha:
                mismatches.append(
                    f"SHA mismatch for {rel_path}: expected={expected_sha} actual={actual_sha}"
                )
        return mismatches

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _hydrate_file_cache_from_tick_manifests(self) -> None:
        """Rebuild the file checksum cache from sealed tick manifests on disk."""
        self._file_cache.clear()
        universes_root = self.run_folder / "universes"
        if not universes_root.exists():
            return
        for manifest_path in sorted(universes_root.glob("*/ticks/tick_*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_bytes())
            except Exception:
                continue
            files = manifest.get("files")
            if not isinstance(files, dict):
                continue
            sealed_at = manifest.get("sealed_at")
            for rel_path, record in files.items():
                if not isinstance(rel_path, str) or not isinstance(record, dict):
                    continue
                safe_rel_path = _safe_cached_rel_path(self.run_folder, rel_path)
                self._file_cache[safe_rel_path] = {
                    "size": record.get("size"),
                    "sha256": record.get("sha256"),
                    "sealed_at": sealed_at,
                }

    def _atomic_write(self, target: Path, data: bytes, *, immutable: bool) -> str:
        """Write *data* to *target* atomically; chmod 0o444 if *immutable*."""
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp_suffix = f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        tmp_path = target.with_suffix(target.suffix + tmp_suffix)

        try:
            with open(tmp_path, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
            if immutable:
                os.chmod(target, 0o444)
        except Exception:
            # Clean up tmp on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        return sha256_bytes(data)

    def _flush_manifest(self) -> None:
        """Write the run manifest to disk (mutable file)."""
        if self._run_folder is None:
            return
        mf_path = self._run_folder / "manifest.json"
        mf_bytes = orjson.dumps(self._manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        # Manifest is always mutable (not immutable)
        self._atomic_write(mf_path, mf_bytes, immutable=False)


def _tick_files_for_merkle(tick_dir: Path) -> list[Path]:
    files_found: list[Path] = []
    for path in sorted(tick_dir.rglob("*")):
        if path.name == "manifest.json":
            continue
        if path.is_symlink():
            relative = path.relative_to(tick_dir).as_posix()
            raise LedgerError(f"Tick seal cannot include symlinks: {relative}")
        if path.is_file():
            files_found.append(path)
    return files_found


def _safe_cached_rel_path(run_folder: Path, rel_path: str) -> str:
    if rel_path == "" or "\\" in rel_path:
        raise LedgerError(f"Unsafe tick manifest file path: {rel_path!r}")
    path = Path(rel_path)
    raw_parts = rel_path.split("/")
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise LedgerError(f"Unsafe tick manifest file path: {rel_path!r}")
    resolved_root = run_folder.resolve()
    resolved_path = (run_folder / path).resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise LedgerError(f"Unsafe tick manifest file path: {rel_path!r}")
    return path.as_posix()
