"""
Sociology parameter loading.

Converts the SoT bundle's `sociology_parameters` dict into the typed
`SociologyParams` Pydantic model. Also exports a `DEFAULT_PARAMS` constant
for unit tests that don't want to load the SoT.
"""
from __future__ import annotations

from typing import Any

from backend.app.schemas.sociology import SociologyParams
from backend.app.storage.sot_loader import SoTBundle


def load_sociology_params(sot: SoTBundle) -> SociologyParams:
    """Convert the SoT bundle's sociology_parameters dict into SociologyParams."""
    raw: dict[str, Any] = dict(sot.sociology_parameters or {})
    # Strip metadata-only top-level keys.
    raw.pop("version", None)
    raw.pop("note", None)
    return SociologyParams.model_validate(raw)


DEFAULT_PARAMS: SociologyParams = SociologyParams()


__all__ = ["load_sociology_params", "DEFAULT_PARAMS"]
