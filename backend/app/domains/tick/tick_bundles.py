from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

TickBundleField = Literal["provisional_bundle", "final_bundle"]

TICK_BUNDLE_REF_KEY = "__worldfork_tick_bundle_ref__"
TICK_BUNDLE_REF_KIND = "inherited_tick_bundle_ref"
TICK_BUNDLE_REF_VERSION = 1


class TickBundleHydrationError(RuntimeError):
    """Raised when a compact inherited tick bundle cannot be hydrated."""


@dataclass
class TickBundleHydrationContext:
    lineage_refs: dict[tuple[str, int], models.TickLineageRef] = field(default_factory=dict)
    source_ticks: dict[str, models.TickSnapshot] = field(default_factory=dict)
    bundles: dict[tuple[str, TickBundleField], dict] = field(default_factory=dict)


def inherited_tick_bundle_ref(
    *,
    source_tick: models.TickSnapshot,
    parent: models.Multiverse,
    child: models.Multiverse,
    bundle_field: TickBundleField,
) -> dict:
    return {
        TICK_BUNDLE_REF_KEY: {
            "kind": TICK_BUNDLE_REF_KIND,
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
    return (
        isinstance(bundle, dict)
        and isinstance(bundle.get(TICK_BUNDLE_REF_KEY), dict)
        and bundle[TICK_BUNDLE_REF_KEY].get("kind") == TICK_BUNDLE_REF_KIND
        and set(bundle).issubset({TICK_BUNDLE_REF_KEY, "inherited_from"})
    )


def hydrate_tick_snapshot_for_read(
    db: Session,
    tick: models.TickSnapshot,
    *,
    context: TickBundleHydrationContext | None = None,
) -> SimpleNamespace:
    context = context or TickBundleHydrationContext()
    values = {column.name: getattr(tick, column.name, None) for column in models.TickSnapshot.__table__.columns}
    values["provisional_bundle"] = hydrate_tick_bundle(
        db,
        tick,
        "provisional_bundle",
        context=context,
    )
    values["final_bundle"] = hydrate_tick_bundle(db, tick, "final_bundle", context=context)
    return SimpleNamespace(**values)


def hydrate_tick_snapshots_for_read(
    db: Session,
    ticks: list[models.TickSnapshot],
) -> list[SimpleNamespace]:
    context = TickBundleHydrationContext()
    return [hydrate_tick_snapshot_for_read(db, tick, context=context) for tick in ticks]


def hydrate_tick_bundle(
    db: Session,
    tick: models.TickSnapshot,
    bundle_field: TickBundleField,
    *,
    context: TickBundleHydrationContext | None = None,
    _seen: set[tuple[str, TickBundleField]] | None = None,
) -> dict:
    context = context or TickBundleHydrationContext()
    cache_key = (str(tick.id), bundle_field)
    if cache_key in context.bundles:
        return deepcopy(context.bundles[cache_key])

    bundle = getattr(tick, bundle_field) or {}
    if not is_inherited_tick_bundle_ref(bundle):
        hydrated = deepcopy(bundle)
        context.bundles[cache_key] = deepcopy(hydrated)
        return hydrated

    marker = bundle[TICK_BUNDLE_REF_KEY]
    marker_version = marker.get("version")
    if marker_version != TICK_BUNDLE_REF_VERSION:
        raise TickBundleHydrationError(
            f"tick {tick.id} {bundle_field} uses unsupported inherited bundle ref version "
            f"{marker_version!r}"
        )
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

    ref_key = (str(tick.multiverse_id), tick.tick_index)
    ref = context.lineage_refs.get(ref_key)
    if ref is None:
        ref = db.scalar(
            select(models.TickLineageRef).where(
                models.TickLineageRef.child_multiverse_id == tick.multiverse_id,
                models.TickLineageRef.inherited_tick_index == tick.tick_index,
            )
        )
        if ref is not None:
            context.lineage_refs[ref_key] = ref
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

    source_tick = context.source_ticks.get(source_tick_snapshot_id)
    if source_tick is None:
        source_tick = db.get(models.TickSnapshot, ref.source_tick_snapshot_id)
        if source_tick is not None:
            context.source_ticks[source_tick_snapshot_id] = source_tick
    if source_tick is None:
        raise TickBundleHydrationError(
            f"missing source tick {source_tick_snapshot_id} for inherited tick {tick.id}"
        )
    if source_tick.multiverse_id != ref.source_multiverse_id:
        raise TickBundleHydrationError(
            f"lineage ref/source multiverse mismatch for inherited tick {tick.id}: "
            f"{ref.source_multiverse_id} != {source_tick.multiverse_id}"
        )
    if source_tick.big_bang_id != tick.big_bang_id:
        raise TickBundleHydrationError(
            f"lineage ref/source big bang mismatch for inherited tick {tick.id}: "
            f"{source_tick.big_bang_id} != {tick.big_bang_id}"
        )

    inherited_from = deepcopy(bundle.get("inherited_from") or _inherited_from_ref(ref, source_tick))
    if not isinstance(inherited_from, dict):
        raise TickBundleHydrationError(f"invalid inherited_from metadata for inherited tick {tick.id}")
    _validate_inherited_from(inherited_from, ref, tick)
    hydrated = hydrate_tick_bundle(db, source_tick, bundle_field, context=context, _seen=seen)
    hydrated["multiverse_id"] = str(tick.multiverse_id)
    hydrated["inherited_from"] = inherited_from
    context.bundles[cache_key] = deepcopy(hydrated)
    return hydrated


def _validate_inherited_from(
    inherited_from: dict,
    ref: models.TickLineageRef,
    tick: models.TickSnapshot,
) -> None:
    expected = {
        "source_multiverse_id": str(ref.source_multiverse_id),
        "source_tick_snapshot_id": str(ref.source_tick_snapshot_id),
    }
    for key, expected_value in expected.items():
        actual = inherited_from.get(key)
        if actual is not None and str(actual) != expected_value:
            raise TickBundleHydrationError(
                f"inherited_from {key} mismatch for inherited tick {tick.id}: "
                f"{actual!r} != {expected_value!r}"
            )


def _inherited_from_ref(ref: models.TickLineageRef, source_tick: models.TickSnapshot) -> dict:
    return {
        "source_multiverse_id": str(ref.source_multiverse_id),
        "source_tick_snapshot_id": str(ref.source_tick_snapshot_id),
        "source_ui_label": source_tick.ui_label,
    }
