from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.llm.audit import LLMCallError
from app.llm.provider import LLMProviderUnavailable

logger = logging.getLogger(__name__)


def require(db: Session, model, object_id):
    obj = db.get(model, object_id)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return obj


def commit_or_500(db: Session):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="database integrity conflict") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="database commit failed") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="commit failed") from exc


def raise_llm_unavailable(exc: Exception):
    if isinstance(exc, LLMCallError | LLMProviderUnavailable):
        logger.exception("LLM provider unavailable", exc_info=exc)
        raise HTTPException(status_code=503, detail="LLM unavailable") from exc
    message = str(exc).lower()
    if "openrouter" in message or "llm" in message or "provider" in message:
        logger.exception("LLM call failed and is being surfaced as 503", exc_info=exc)
        raise HTTPException(status_code=503, detail="LLM unavailable") from exc
    raise exc


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
