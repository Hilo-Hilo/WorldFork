from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.utils import require
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
DEBUG_ARTIFACT_TOKEN_ENV = "WORLDFORK_DEBUG_ARTIFACT_TOKEN"


def has_secure_debug_artifact_gate(debug: bool, token: str | None) -> bool:
    expected = os.environ.get(DEBUG_ARTIFACT_TOKEN_ENV)
    return bool(debug and expected and token and secrets.compare_digest(token, expected))


def require_secure_debug_artifact_gate(debug: bool, token: str | None) -> None:
    if has_secure_debug_artifact_gate(debug, token):
        return
    raise HTTPException(status_code=403, detail="debug artifact requires secure debug gate")


def _artifact_file_path(path_value: str) -> Path:
    artifact_root = get_settings().artifact_root.resolve()
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = artifact_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact file not found") from exc
    if candidate.is_symlink() or not resolved.is_relative_to(artifact_root):
        raise HTTPException(status_code=404, detail="artifact file not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    return resolved


@router.get("/{artifact_id}")
def get_artifact(
    artifact_id: UUID,
    debug: bool = False,
    x_worldfork_debug_token: str | None = Header(default=None, alias="X-WorldFork-Debug-Token"),
    db: Session = Depends(get_db),
):
    artifact = require(db, models.Artifact, artifact_id)
    if artifact.debug_only:
        require_secure_debug_artifact_gate(debug, x_worldfork_debug_token)
    path = _artifact_file_path(artifact.path)
    return FileResponse(path, media_type=artifact.content_type, filename=path.name)
