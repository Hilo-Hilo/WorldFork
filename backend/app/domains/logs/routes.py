"""Canonical logs API.

Endpoints:
  GET /api/logs/requests
  GET /api/logs/webhooks
  GET /api/logs/errors
  GET /api/logs/audit
  GET /api/logs/traces/{trace_id}
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import false, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.db import models as current_models
from app.db.introspection import table_has_columns
from backend.app.core.db import get_session
from backend.app.models.jobs import JobModel
from backend.app.models.llm_calls import LLMCallModel
from backend.app.schemas.api import (
    AuditLogItem,
    ErrorLogItem,
    JobInfo,
    RequestLogItem,
    TraceResponse,
    WebhookLogItem,
)

router = APIRouter(prefix="/logs", tags=["logs"])

_SESSION = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)

_RAW_TEXT_KEYS = {
    "scenario_text",
    "initializer_prompt",
    "prompt",
    "premise",
    "plain_text",
    "raw_text",
    "source_text",
    "text",
}
_PRIVATE_CONFIG_KEYS = {
    "model_config",
    "llm_model_config",
    "provider_config",
    "openrouter_config",
}
_PRIVATE_KEY_NAMES = {"secret", "api_key", "apikey", "token", "password", "authorization", "bearer"}


def _first_non_null(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _json_text_equals(column: Any, key: str, value: str) -> ColumnElement[bool]:
    return column[key].as_string() == value


def _is_private_key(normalized: str) -> bool:
    compact = normalized.replace("_", "").replace("-", "")
    return (
        normalized in _PRIVATE_KEY_NAMES
        or normalized.endswith(("_secret", "_api_key", "_apikey", "_token", "_password"))
        or normalized.startswith("authorization")
        or compact in _PRIVATE_KEY_NAMES
        or compact.endswith(("secret", "apikey", "token", "password", "secretkey", "privatekey"))
        or compact.startswith("authorization")
    )


def _is_internal_reference_id(normalized: str) -> bool:
    compact = normalized.replace("_", "").replace("-", "")
    return (
        normalized in {"artifact_id", "llm_call_id"}
        or normalized.endswith("_artifact_id")
        or normalized.endswith("_llm_call_id")
        or compact in {"artifactid", "llmcallid"}
        or compact.endswith(("artifactid", "llmcallid"))
    )


def _is_absolute_path_field(normalized: str, value: Any) -> bool:
    return (
        (normalized == "path" or normalized.endswith("_path"))
        and isinstance(value, str)
        and _is_absolute_path_value(value)
    )


def _is_absolute_path_value(value: str) -> bool:
    return (
        value.startswith(("/", "\\\\"))
        or len(value) >= 3
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in {"\\", "/"}
    )


def _sanitize_plain_text_corpus(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_public_job_payload(value)
    for collection_key in ("chunk_artifacts", "chunk_summaries"):
        if isinstance(sanitized.get(collection_key), list):
            sanitized[collection_key] = [
                {key: val for key, val in item.items() if key != "artifact_id"}
                if isinstance(item, dict)
                else item
                for item in sanitized[collection_key]
            ]
    return sanitized


def sanitize_public_job_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_public_job_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        normalized = key_text.lower()
        if _is_internal_reference_id(normalized):
            continue
        if normalized in _PRIVATE_CONFIG_KEYS or _is_private_key(normalized):
            sanitized[key_text] = "[REDACTED]"
            continue
        if normalized == "plain_text_corpus" and isinstance(item, dict):
            sanitized[key_text] = _sanitize_plain_text_corpus(item)
            continue
        if normalized in _RAW_TEXT_KEYS or _is_absolute_path_field(normalized, item):
            sanitized[f"{key_text}_present"] = bool(item)
            if isinstance(item, str):
                sanitized[f"{key_text}_char_count"] = len(item)
            continue
        sanitized[key_text] = sanitize_public_job_payload(item)
    return sanitized


def _llm_call_to_log_item(row: LLMCallModel) -> RequestLogItem:
    return RequestLogItem(
        call_id=row.call_id,
        provider=row.provider,
        model_used=row.model_used,
        job_type=row.job_type,
        run_id=row.run_id,
        universe_id=row.universe_id,
        tick=row.tick,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        cost_usd=float(row.cost_usd) if row.cost_usd is not None else None,
        latency_ms=row.latency_ms,
        status=row.status,
        error=row.error,
        repaired_once=row.repaired_once,
        created_at=row.created_at,
    )


def _job_to_info(row: JobModel) -> JobInfo:
    return JobInfo(
        job_id=row.job_id,
        job_type=row.job_type,
        priority=row.priority,
        run_id=row.run_id,
        universe_id=row.universe_id,
        tick=row.tick,
        attempt_number=row.attempt_number,
        status=row.status,
        idempotency_key=row.idempotency_key,
        payload=sanitize_public_job_payload(dict(row.payload or {})),
        enqueued_at=row.enqueued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        result_summary=dict(row.result_summary) if row.result_summary else None,
        artifact_path=row.artifact_path,
        created_at=row.created_at,
    )


def _current_llm_to_log_item(row: current_models.LLMCall) -> RequestLogItem:
    meta = dict(row.meta or {})
    return RequestLogItem(
        call_id=str(row.id),
        provider=row.provider,
        model_used=row.model,
        job_type=row.purpose,
        run_id=str(row.big_bang_id) if row.big_bang_id else "",
        universe_id=meta.get("universe_id"),
        tick=_first_non_null(meta, "tick", "tick_index"),
        prompt_tokens=int(meta.get("prompt_tokens") or 0),
        completion_tokens=int(meta.get("completion_tokens") or 0),
        total_tokens=int(meta.get("total_tokens") or 0),
        cost_usd=meta.get("cost_usd"),
        latency_ms=int(meta.get("latency_ms") or 0),
        status=row.status,
        error=meta.get("error"),
        repaired_once=bool(meta.get("repaired_once", False)),
        created_at=row.created_at,
    )


def _current_job_to_info(row: current_models.Job) -> JobInfo:
    payload = dict(row.payload or {})
    return JobInfo(
        job_id=str(row.id),
        job_type=row.job_type,
        priority=row.queue_name,
        run_id=str(row.big_bang_id) if row.big_bang_id else "",
        universe_id=_first_non_null(payload, "universe_id", "multiverse_id"),
        tick=_first_non_null(payload, "tick", "tick_index"),
        attempt_number=row.attempt_number,
        status=row.status,
        idempotency_key=row.idempotency_key,
        payload=sanitize_public_job_payload(payload),
        enqueued_at=row.queued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        result_summary=dict(row.result or {}) if row.result else None,
        artifact_path=None,
        created_at=row.created_at,
    )


async def _uses_current_llm_table(session: AsyncSession) -> bool:
    return (
        await table_has_columns(session, "llm_calls", ["id", "model", "purpose"])
        and not await table_has_columns(session, "llm_calls", ["call_id"])
    )


async def _uses_current_jobs_table(session: AsyncSession) -> bool:
    return (
        await table_has_columns(session, "jobs", ["id", "queue_name", "big_bang_id"])
        and not await table_has_columns(session, "jobs", ["job_id"])
    )


# ---------------------------------------------------------------------------
# Request logs
# ---------------------------------------------------------------------------


@router.get(
    "/requests",
    response_model=list[RequestLogItem],
    summary="List LLM request logs",
)
async def get_request_logs(
    session: _SESSION,
    provider: str | None = Query(default=None),
    status: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    universe_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[RequestLogItem]:
    if await _uses_current_llm_table(session):
        stmt = select(current_models.LLMCall)
        if provider:
            stmt = stmt.where(current_models.LLMCall.provider == provider)
        if status:
            stmt = stmt.where(current_models.LLMCall.status == status)
        if run_id:
            try:
                from uuid import UUID

                stmt = stmt.where(current_models.LLMCall.big_bang_id == UUID(run_id))
            except ValueError:
                return []
        if universe_id:
            stmt = stmt.where(_json_text_equals(current_models.LLMCall.meta, "universe_id", universe_id))
        stmt = stmt.order_by(current_models.LLMCall.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        return [_current_llm_to_log_item(r) for r in result.scalars().all()]

    if not await table_has_columns(session, "llm_calls", ["call_id"]):
        return []

    stmt = select(LLMCallModel)
    if provider:
        stmt = stmt.where(LLMCallModel.provider == provider)
    if status:
        stmt = stmt.where(LLMCallModel.status == status)
    if run_id:
        stmt = stmt.where(LLMCallModel.run_id == run_id)
    if universe_id:
        stmt = stmt.where(LLMCallModel.universe_id == universe_id)
    stmt = stmt.order_by(LLMCallModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [_llm_call_to_log_item(r) for r in rows]


# ---------------------------------------------------------------------------
# Webhook logs
# ---------------------------------------------------------------------------


@router.get(
    "/webhooks",
    response_model=list[WebhookLogItem],
    summary="List webhook delivery logs",
)
async def get_webhook_logs(
    session: _SESSION,
    run_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[WebhookLogItem]:
    if not await table_has_columns(session, "webhook_events", ["id", "run_id", "event_type"]):
        return []
    try:
        from backend.app.models.webhooks import WebhookEventModel

        stmt = select(WebhookEventModel)
        if run_id:
            stmt = stmt.where(WebhookEventModel.run_id == run_id)
        if status:
            stmt = stmt.where(WebhookEventModel.status == status)
        stmt = stmt.order_by(WebhookEventModel.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            WebhookLogItem(
                id=r.id,
                run_id=r.run_id,
                event_type=r.event_type,
                target_url=r.target_url,
                status=r.status,
                attempts=r.attempts,
                last_delivered_at=r.last_delivered_at,
                error=r.error,
                created_at=r.created_at,
            )
            for r in rows
        ]
    except Exception as exc:
        logger.debug("webhook_events unavailable while reading logs: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Error logs
# ---------------------------------------------------------------------------


@router.get(
    "/errors",
    response_model=list[ErrorLogItem],
    summary="List error logs from jobs and LLM calls",
)
async def get_error_logs(
    session: _SESSION,
    run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ErrorLogItem]:
    items: list[ErrorLogItem] = []
    per_source_limit = offset + limit

    # Failed jobs
    if await _uses_current_jobs_table(session):
        current_job_stmt = select(current_models.Job).where(current_models.Job.status == "failed")
        if run_id:
            try:
                from uuid import UUID

                current_job_stmt = current_job_stmt.where(current_models.Job.big_bang_id == UUID(run_id))
            except ValueError:
                current_job_stmt = current_job_stmt.where(false())
        current_job_stmt = current_job_stmt.order_by(current_models.Job.created_at.desc()).limit(per_source_limit)
        current_job_result = await session.execute(current_job_stmt)
        for r in current_job_result.scalars().all():
            items.append(
                ErrorLogItem(
                    source="job",
                    id=str(r.id),
                    job_type=r.job_type,
                    provider=None,
                    run_id=str(r.big_bang_id) if r.big_bang_id else None,
                    status=r.status,
                    error=r.error,
                    created_at=r.created_at,
                )
            )
    elif await table_has_columns(session, "jobs", ["job_id"]):
        job_stmt = select(JobModel).where(JobModel.status == "failed")
        if run_id:
            job_stmt = job_stmt.where(JobModel.run_id == run_id)
        job_stmt = job_stmt.order_by(JobModel.created_at.desc()).limit(per_source_limit)
        job_result = await session.execute(job_stmt)
        for r in job_result.scalars().all():
            items.append(ErrorLogItem(
                source="job",
                id=r.job_id,
                job_type=r.job_type,
                provider=None,
                run_id=r.run_id,
                status=r.status,
                error=r.error,
                created_at=r.created_at,
            ))

    # LLM calls with errors
    if await _uses_current_llm_table(session):
        current_llm_stmt = select(current_models.LLMCall).where(
            current_models.LLMCall.status.in_(["failed", "error"])
        )
        if run_id:
            try:
                from uuid import UUID

                current_llm_stmt = current_llm_stmt.where(current_models.LLMCall.big_bang_id == UUID(run_id))
            except ValueError:
                current_llm_stmt = current_llm_stmt.where(false())
        current_llm_stmt = current_llm_stmt.order_by(current_models.LLMCall.created_at.desc()).limit(per_source_limit)
        current_llm_result = await session.execute(current_llm_stmt)
        for r in current_llm_result.scalars().all():
            meta = dict(r.meta or {})
            items.append(
                ErrorLogItem(
                    source="llm_call",
                    id=str(r.id),
                    job_type=r.purpose,
                    provider=r.provider,
                    run_id=str(r.big_bang_id) if r.big_bang_id else None,
                    status=r.status,
                    error=meta.get("error"),
                    created_at=r.created_at,
                )
            )
    elif await table_has_columns(session, "llm_calls", ["call_id"]):
        llm_stmt = select(LLMCallModel).where(LLMCallModel.error.isnot(None))
        if run_id:
            llm_stmt = llm_stmt.where(LLMCallModel.run_id == run_id)
        llm_stmt = llm_stmt.order_by(LLMCallModel.created_at.desc()).limit(per_source_limit)
        llm_result = await session.execute(llm_stmt)
        for r in llm_result.scalars().all():
            items.append(ErrorLogItem(
                source="llm_call",
                id=r.call_id,
                job_type=r.job_type,
                provider=r.provider,
                run_id=r.run_id,
                status=r.status,
                error=r.error,
                created_at=r.created_at,
            ))

    # Sort combined by created_at desc and paginate
    items.sort(key=lambda x: (x.created_at or datetime.min), reverse=True)
    return items[offset : offset + limit]


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


@router.get(
    "/audit",
    response_model=list[AuditLogItem],
    summary="List audit-style lifecycle log entries",
)
async def get_audit_logs(
    session: _SESSION,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogItem]:
    if await _uses_current_jobs_table(session):
        stmt = select(current_models.Job).order_by(current_models.Job.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            AuditLogItem(
                id=str(row.id),
                actor="worker",
                action=f"job.{row.status}",
                resource=f"job:{row.id}",
                timestamp=row.created_at
                or row.queued_at
                or row.started_at
                or row.finished_at
                or datetime.now(UTC),
                details={
                    "job_type": row.job_type,
                    "run_id": str(row.big_bang_id) if row.big_bang_id else None,
                    "attempt_number": row.attempt_number,
                    "queue_name": row.queue_name,
                    "error": row.error,
                },
            )
            for row in rows
        ]

    if not await table_has_columns(session, "jobs", ["job_id"]):
        return []

    stmt = select(JobModel).order_by(JobModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        AuditLogItem(
            id=row.job_id,
            actor="worker",
            action=f"job.{row.status}",
            resource=f"job:{row.job_id}",
            timestamp=row.created_at
            or row.enqueued_at
            or row.started_at
            or row.finished_at
            or datetime.now(UTC),
            details={
                "job_type": row.job_type,
                "run_id": row.run_id,
                "universe_id": row.universe_id,
                "tick": row.tick,
                "attempt_number": row.attempt_number,
                "priority": row.priority,
                "error": row.error,
            },
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


@router.get(
    "/traces/{trace_id}",
    response_model=TraceResponse,
    summary="Get full trace by run, universe, LLM call, or job identifier",
)
async def get_trace(trace_id: str, session: _SESSION) -> TraceResponse:
    if await _uses_current_llm_table(session) or await _uses_current_jobs_table(session):
        llm_rows: list[Any] = []
        if await _uses_current_llm_table(session):
            llm_stmt = select(current_models.LLMCall)
            clauses = []
            try:
                from uuid import UUID

                parsed = UUID(trace_id)
                clauses.extend(
                    [
                        current_models.LLMCall.id == parsed,
                        current_models.LLMCall.big_bang_id == parsed,
                    ]
                )
            except ValueError:
                pass
            llm_stmt = llm_stmt.where(
                or_(
                    *clauses,
                    _json_text_equals(current_models.LLMCall.meta, "universe_id", trace_id),
                )
            )
            llm_result = await session.execute(llm_stmt)
            llm_rows = list(llm_result.scalars().all())

        job_rows: list[Any] = []
        if await _uses_current_jobs_table(session):
            job_stmt = select(current_models.Job)
            clauses = []
            try:
                from uuid import UUID

                parsed = UUID(trace_id)
                clauses.extend(
                    [
                        current_models.Job.id == parsed,
                        current_models.Job.big_bang_id == parsed,
                    ]
                )
            except ValueError:
                pass
            job_stmt = job_stmt.where(
                or_(
                    *clauses,
                    _json_text_equals(current_models.Job.payload, "universe_id", trace_id),
                    _json_text_equals(current_models.Job.payload, "multiverse_id", trace_id),
                )
            )
            job_result = await session.execute(job_stmt)
            job_rows = list(job_result.scalars().all())

        return TraceResponse(
            trace_id=trace_id,
            llm_calls=[_current_llm_to_log_item(r) for r in llm_rows],
            jobs=[_current_job_to_info(r) for r in job_rows],
        )

    if not await table_has_columns(session, "llm_calls", ["call_id"]):
        return TraceResponse(trace_id=trace_id, llm_calls=[], jobs=[])

    llm_stmt = select(LLMCallModel).where(
        or_(
            LLMCallModel.call_id == trace_id,
            LLMCallModel.run_id == trace_id,
            LLMCallModel.universe_id == trace_id,
        )
    )
    llm_result = await session.execute(llm_stmt)
    llm_rows = list(llm_result.scalars().all())

    job_stmt = select(JobModel).where(
        or_(
            JobModel.job_id == trace_id,
            JobModel.run_id == trace_id,
            JobModel.universe_id == trace_id,
        )
    )
    job_result = await session.execute(job_stmt)
    job_rows = list(job_result.scalars().all())

    return TraceResponse(
        trace_id=trace_id,
        llm_calls=[_llm_call_to_log_item(r) for r in llm_rows],
        jobs=[_job_to_info(r) for r in job_rows],
    )
