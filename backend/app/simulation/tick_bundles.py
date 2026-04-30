from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

TickBundleField = Literal["provisional_bundle", "final_bundle"]

TICK_BUNDLE_REF_KEY = "__worldfork_tick_bundle_ref__"
TICK_BUNDLE_REF_VERSION = 1


class TickBundleHydrationError(RuntimeError):
    """Raised when a compact inherited tick bundle cannot be hydrated."""


def inherited_tick_bundle_ref(
    *,
    source_tick: models.TickSnapshot,
    parent: models.Multiverse,
    child: models.Multiverse,
    bundle_field: TickBundleField,
) -> dict:
    return {
        TICK_BUNDLE_REF_KEY: {
            "version": TICK_BUNDLE_REF_VERSION,
            "bundle_field": bundle_field,
            "source_tick_snapshot_id": str(source_tick.id),
        },
        "inherited_from": {
            "source_multiverse_id": str(parent.id),
            "source_tick_snapshot_id": str(source_tick.id),
            "source_ui_label": source_tick.ui_label,
        },
    }


def is_inherited_tick_bundle_ref(bundle: object) -> bool:
    return isinstance(bundle, dict) and isinstance(bundle.get(TICK_BUNDLE_REF_KEY), dict)


def hydrate_tick_snapshot_for_read(db: Session, tick: models.TickSnapshot) -> SimpleNamespace:
    values = {column.name: getattr(tick, column.name) for column in models.TickSnapshot.__table__.columns}
    values["provisional_bundle"] = hydrate_tick_bundle(db, tick, "provisional_bundle")
    values["final_bundle"] = hydrate_tick_bundle(db, tick, "final_bundle")
    return SimpleNamespace(**values)


def hydrate_tick_bundle(
    db: Session,
    tick: models.TickSnapshot,
    bundle_field: TickBundleField,
    *,
    _seen: set[tuple[str, TickBundleField]] | None = None,
) -> dict:
    bundle = getattr(tick, bundle_field) or {}
    if not is_inherited_tick_bundle_ref(bundle):
        return deepcopy(bundle)

    marker = bundle[TICK_BUNDLE_REF_KEY]
    marker_field = marker.get("bundle_field")
    if marker_field != bundle_field:
        raise TickBundleHydrationError(
            f"tick {tick.id} {bundle_field} points to {marker_field!r}"
        )

    seen = set(_seen or set())
    visit_key = (str(tick.id), bundle_field)
    if visit_key in seen:
        raise TickBundleHydrationError(f"cycle while hydrating tick {tick.id} {bundle_field}")
    seen.add(visit_key)

    ref = db.scalar(
        select(models.TickLineageRef).where(
            models.TickLineageRef.child_multiverse_id == tick.multiverse_id,
            models.TickLineageRef.inherited_tick_index == tick.tick_index,
        )
    )
    if ref is None:
        raise TickBundleHydrationError(
            f"missing lineage ref for inherited tick {tick.id} at index {tick.tick_index}"
        )

    source_tick_snapshot_id = str(ref.source_tick_snapshot_id)
    marker_source_id = marker.get("source_tick_snapshot_id")
    if marker_source_id is not None and str(marker_source_id) != source_tick_snapshot_id:
        raise TickBundleHydrationError(
            f"lineage ref/source mismatch for inherited tick {tick.id}: "
            f"{marker_source_id!r} != {source_tick_snapshot_id!r}"
        )

    source_tick = db.get(models.TickSnapshot, ref.source_tick_snapshot_id)
    if source_tick is None:
        raise TickBundleHydrationError(
            f"missing source tick {source_tick_snapshot_id} for inherited tick {tick.id}"
        )

    hydrated = hydrate_tick_bundle(db, source_tick, bundle_field, _seen=seen)
    hydrated["multiverse_id"] = str(tick.multiverse_id)
    hydrated["inherited_from"] = deepcopy(bundle.get("inherited_from") or _inherited_from_ref(ref, source_tick))
    return hydrated


def _inherited_from_ref(ref: models.TickLineageRef, source_tick: models.TickSnapshot) -> dict:
    return {
        "source_multiverse_id": str(ref.source_multiverse_id),
        "source_tick_snapshot_id": str(ref.source_tick_snapshot_id),
        "source_ui_label": source_tick.ui_label,
    }
