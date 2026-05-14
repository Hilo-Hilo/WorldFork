"""Source-of-truth loader.

Reads ``source_of_truth/`` from disk into an immutable ``SoTBundle`` dataclass.
All validation and I/O happens inside function calls — nothing executes at
import time so that other batches that haven't yet populated the SoT folder
don't break on import.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.storage.checksums import merkle_root, sha256_file

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

_DEFAULT_SOT_DIR: Path | None = None


def _default_source_dir() -> Path:
    """Return the default source_of_truth directory (next to the repo root)."""
    return Path(__file__).resolve().parents[3] / "source_of_truth"


@dataclass(frozen=True)
class SoTBundle:
    version: str
    emotions: dict[str, Any]
    behavior_axes: dict[str, Any]
    ideology_axes: dict[str, Any]
    expression_scale: dict[str, Any]
    issue_stance_axes: dict[str, Any]
    event_types: dict[str, Any]
    social_action_tools: dict[str, Any]
    channel_types: dict[str, Any]
    actor_types: dict[str, Any]
    sociology_parameters: dict[str, Any]
    prompt_contracts: dict[str, dict]   # name -> JSONSchema
    prompt_templates: dict[str, str]    # name -> raw template text
    source_dir: Path
    snapshot_sha256: str                # Merkle root over all files


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_JSON_FILES = [
    "emotions",
    "behavior_axes",
    "ideology_axes",
    "expression_scale",
    "issue_stance_axes",
    "event_types",
    "social_action_tools",
    "channel_types",
    "actor_types",
    "sociology_parameters",
]


def load_sot(source_dir: Path | None = None) -> SoTBundle:
    """Load the source-of-truth folder into an immutable :class:`SoTBundle`.

    Parameters
    ----------
    source_dir:
        Path to the ``source_of_truth/`` directory.  Defaults to the
        canonical location three levels above the package root.
    """
    if source_dir is None:
        source_dir = _default_source_dir()

    source_dir = Path(source_dir).resolve()
    _reject_sot_symlinks(source_dir)

    # VERSION
    version_path = source_dir / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip()

    # Top-level JSON files
    loaded_json: dict[str, Any] = {}
    for name in _JSON_FILES:
        path = source_dir / f"{name}.json"
        loaded_json[name] = json.loads(path.read_bytes())

    # prompt_contracts/*.json
    contracts_dir = source_dir / "prompt_contracts"
    prompt_contracts: dict[str, dict] = {}
    if contracts_dir.is_dir():
        for p in sorted(contracts_dir.glob("*.json")):
            prompt_contracts[p.stem] = json.loads(p.read_bytes())

    # prompt_templates/*.md
    templates_dir = source_dir / "prompt_templates"
    prompt_templates: dict[str, str] = {}
    if templates_dir.is_dir():
        for p in sorted(templates_dir.glob("*.md")):
            prompt_templates[p.stem] = p.read_text(encoding="utf-8")

    # Merkle root over all files in sorted path order
    snapshot_sha = _compute_sot_merkle(source_dir)

    return SoTBundle(
        version=version,
        emotions=loaded_json["emotions"],
        behavior_axes=loaded_json["behavior_axes"],
        ideology_axes=loaded_json["ideology_axes"],
        expression_scale=loaded_json["expression_scale"],
        issue_stance_axes=loaded_json["issue_stance_axes"],
        event_types=loaded_json["event_types"],
        social_action_tools=loaded_json["social_action_tools"],
        channel_types=loaded_json["channel_types"],
        actor_types=loaded_json["actor_types"],
        sociology_parameters=loaded_json["sociology_parameters"],
        prompt_contracts=prompt_contracts,
        prompt_templates=prompt_templates,
        source_dir=source_dir,
        snapshot_sha256=snapshot_sha,
    )


def _collect_sot_files(source_dir: Path, *, exclude_snapshot_marker: bool = False) -> list[Path]:
    """Return all files under *source_dir* in deterministic sorted order."""
    _reject_sot_symlinks(source_dir)
    return sorted(
        p for p in source_dir.rglob("*")
        if p.is_file() and not (exclude_snapshot_marker and p.name == ".snapshot_sha256")
    )


def _reject_sot_symlinks(source_dir: Path) -> None:
    symlinks = sorted(p for p in source_dir.rglob("*") if p.is_symlink())
    if symlinks:
        relative = ", ".join(p.relative_to(source_dir).as_posix() for p in symlinks)
        raise ValueError(f"source_of_truth files must not be symlinks: {relative}")


def _compute_sot_merkle(source_dir: Path) -> str:
    files = _collect_sot_files(source_dir, exclude_snapshot_marker=True)
    hashes = [sha256_file(f) for f in files]
    return merkle_root(hashes)


def _compute_snapshot_merkle(snapshot_dir: Path) -> str:
    files = _collect_sot_files(snapshot_dir, exclude_snapshot_marker=True)
    hashes = [sha256_file(f) for f in files]
    return merkle_root(hashes)


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------

def snapshot_sot_to(bundle: SoTBundle, dest_dir: Path) -> Path:
    """Copy the entire SoT directory into *dest_dir*, preserving structure.

    Returns the destination path (``dest_dir / "source_of_truth_snapshot"``).
    Idempotent: if the snapshot already exists with a matching Merkle root the
    copy is skipped.
    """
    dest_dir = Path(dest_dir).resolve()
    dest = dest_dir / "source_of_truth_snapshot"

    # Idempotency: skip if sha matches
    sha_file = dest / ".snapshot_sha256"
    if dest.exists() and sha_file.exists():
        existing_sha = sha_file.read_text(encoding="utf-8").strip()
        if existing_sha == bundle.snapshot_sha256 and _compute_snapshot_merkle(dest) == bundle.snapshot_sha256:
            return dest

    # Copy tree
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(bundle.source_dir, dest)

    # Recompute to verify integrity after copy
    recomputed_sha = _compute_snapshot_merkle(dest)
    sha_file.write_text(recomputed_sha, encoding="utf-8")

    return dest


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_sot(bundle: SoTBundle) -> list[str]:
    """Return a list of validation errors; empty list means OK."""
    errors: list[str] = []

    # VERSION present (already ensured by load_sot raising on missing file,
    # but we check it's non-empty here)
    if not bundle.version:
        errors.append("VERSION is empty")

    # Each JSON file must have parsed (load_sot guarantees this; check dicts
    # are non-None)
    for name in _JSON_FILES:
        value = getattr(bundle, name)
        if not isinstance(value, (dict, list)):
            errors.append(f"{name}.json did not parse to dict or list")

    def _items_of(obj, *candidate_keys):
        """Pull a list of items from a wrapped SoT file."""
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for k in candidate_keys:
                v = obj.get(k)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    return list(v.values())
        return []

    # Taxonomy files must be populated enough to support validation/runtime use.
    emotions_list = _items_of(bundle.emotions, "items", "emotions")
    if len(emotions_list) < 5:
        errors.append(
            f"emotions must have at least 5 items, found {len(emotions_list)}"
        )

    baxes_list = _items_of(bundle.behavior_axes, "items", "axes", "behavior_axes")
    if len(baxes_list) < 4:
        errors.append(
            f"behavior_axes must have at least 4 items, found {len(baxes_list)}"
        )

    iaxes_list = _items_of(bundle.ideology_axes, "axes", "items", "ideology_axes")
    iaxes_count = len(iaxes_list)
    if iaxes_count < 4:
        errors.append(
            f"ideology_axes must have at least 4 axes, found {iaxes_count}"
        )

    escale_list = _items_of(bundle.expression_scale, "bands", "items", "expression_scale", "scale")
    escale_count = len(escale_list)
    if escale_count < 3:
        errors.append(
            f"expression_scale must have at least 3 bands, found {escale_count}"
        )

    # prompt_contracts: each must be a valid JSONSchema Draft 2020-12
    try:
        from jsonschema import Draft202012Validator, SchemaError  # type: ignore[import]
        for name, schema in bundle.prompt_contracts.items():
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                errors.append(f"prompt_contract '{name}' is not valid JSONSchema: {exc.message}")
    except ImportError:
        errors.append("jsonschema package not installed; cannot validate prompt_contracts")

    return errors
