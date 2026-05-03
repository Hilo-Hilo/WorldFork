from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import jobs as jobs_api
from app.api.schemas import JobCreate
from app.db import models
from app.jobs.queues import JOB_TYPES, celery_queue_for_job_queue, default_idempotency_key
from app.jobs.tasks import (
    JOB_LEASE_SECONDS,
    JobNotRunnableError,
    claim_job_for_execution,
    execute_job,
    validate_job_payload,
)


def test_default_idempotency_key_uses_canonical_json():
    big_bang_id = uuid4()

    first = default_idempotency_key(
        "run_big_bang_until_complete",
        big_bang_id,
        {"z": [3, 2, 1], "a": {"b": 1, "c": 2}},
    )
    second = default_idempotency_key(
        "run_big_bang_until_complete",
        big_bang_id,
        {"a": {"c": 2, "b": 1}, "z": [3, 2, 1]},
    )

    assert first == second
    assert len(first) < 180


def test_canonical_job_queues_map_to_running_celery_queues():
    assert celery_queue_for_job_queue("big_bang_control") == "p0"
    assert celery_queue_for_job_queue("multiverse_ticks") == "p0"
    assert celery_queue_for_job_queue("reports") == "p2"
    assert celery_queue_for_job_queue("default") == "p1"


def test_advertised_job_types_are_executable_and_payload_validated():
    assert "render_pdf_report" not in JOB_TYPES

    with pytest.raises(ValueError, match="unknown job_type"):
        validate_job_payload("render_pdf_report", {})

    with pytest.raises(ValueError, match="multiverse_id is required"):
        validate_job_payload("run_multiverse_tick", {})

    with pytest.raises(ValueError, match="big_bang_id is required"):
        validate_job_payload("run_big_bang_until_complete", {})


def test_execute_job_claims_before_validating_and_marks_bad_payload_failed():
    job = SimpleNamespace(
        id=uuid4(),
        job_type="run_multiverse_tick",
        status="queued",
        big_bang_id=None,
        payload={},
        result=None,
        error=None,
    )
    db = _ExecutionDb(rowcount=1)

    returned = execute_job(db, job)

    assert returned is job
    assert job.status == "failed"
    assert job.result == {}
    assert "multiverse_id is required" in job.error
    assert db.flushes >= 1


def test_execute_job_refuses_non_queued_rerun_or_concurrent_claim():
    job = SimpleNamespace(
        id=uuid4(),
        job_type="run_big_bang_until_complete",
        status="running",
        big_bang_id=uuid4(),
        payload={},
        result=None,
        error=None,
    )
    db = _ExecutionDb(rowcount=0)

    with pytest.raises(JobNotRunnableError, match="only queued or expired running jobs can run"):
        execute_job(db, job)

    assert job.status == "running"


def test_create_job_enqueues_new_job(monkeypatch):
    sent = []
    monkeypatch.setattr(jobs_api, "enqueue_job", lambda job_id: sent.append(str(job_id)))
    db = _CreateDb()

    job = jobs_api.create_job_record(
        JobCreate(job_type="run_big_bang_until_complete", big_bang_id=uuid4()),
        db=db,
    )

    assert job.status == "queued"
    assert sent == [str(job.id)]
    assert db.commits == 1


def test_create_job_returns_existing_job_on_duplicate_key_race(monkeypatch):
    existing = SimpleNamespace(id=uuid4(), idempotency_key="same-key", status="succeeded")
    monkeypatch.setattr(
        jobs_api,
        "enqueue_job",
        lambda job_id: pytest.fail(f"duplicate job should not enqueue: {job_id}"),
    )
    db = _DuplicateRaceDb(existing)

    result = jobs_api.create_job_record(
        JobCreate(
            job_type="run_big_bang_until_complete",
            big_bang_id=uuid4(),
            idempotency_key="same-key",
        ),
        db=db,
    )

    assert result is existing
    assert db.rollbacks == 1


def test_create_job_enqueue_failure_leaves_job_queued_without_fallback(monkeypatch):
    def fail_enqueue(job_id):
        raise RuntimeError("redis://internal-host:6379 refused connection")

    monkeypatch.setattr(jobs_api, "enqueue_job", fail_enqueue)
    db = _CreateDb()
    background_tasks = _BackgroundTasks()

    result = jobs_api.create_job_record(
        JobCreate(job_type="run_big_bang_until_complete", big_bang_id=uuid4()),
        db=db,
        background_tasks=background_tasks,
    )

    assert result is db.added
    assert db.added.status == "queued"
    assert db.added.error == "enqueue failed; job remains queued; fix the queue or run it explicitly"
    assert "internal-host" not in db.added.error
    assert background_tasks.tasks == []


def test_create_job_idempotent_retry_reenqueues_existing_queued_job(monkeypatch):
    existing = SimpleNamespace(
        id=uuid4(),
        idempotency_key="same-key",
        status="queued",
        error="enqueue failed; retry with the same idempotency key",
    )
    sent = []
    monkeypatch.setattr(jobs_api, "enqueue_job", lambda job_id: sent.append(str(job_id)))
    db = _ExistingDb(existing)

    result = jobs_api.create_job_record(
        JobCreate(
            job_type="run_big_bang_until_complete",
            big_bang_id=uuid4(),
            idempotency_key="same-key",
        ),
        db=db,
    )

    assert result is existing
    assert sent == [str(existing.id)]
    assert existing.error is None
    assert db.commits == 1


def test_create_job_commit_errors_are_sanitized(monkeypatch):
    monkeypatch.setattr(
        jobs_api,
        "enqueue_job",
        lambda job_id: pytest.fail(f"job should not enqueue after commit failure: {job_id}"),
    )
    db = _CommitFailureDb()

    with pytest.raises(HTTPException) as exc:
        jobs_api.create_job_record(
            JobCreate(job_type="run_big_bang_until_complete", big_bang_id=uuid4()),
            db=db,
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "could not create job"
    assert "database secret" not in exc.value.detail
    assert db.rollbacks == 1


def test_run_job_returns_error_when_execution_marks_failed(monkeypatch):
    job = SimpleNamespace(id=uuid4(), status="queued", error=None)
    db = _RunJobDb(job)

    def fail_execution(_db, job, *, commit_running=False):
        job.status = "failed"
        job.error = "executor failed"
        return job

    monkeypatch.setattr(jobs_api, "execute_job", fail_execution)

    with pytest.raises(HTTPException) as exc:
        jobs_api.run_job(job.id, db=db)

    assert exc.value.status_code == 500
    assert exc.value.detail == "job execution failed"
    assert db.commits == 1


def test_claim_job_for_execution_reclaims_expired_running_job():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        now = datetime.now(timezone.utc)
        expired = models.Job(
            job_type="run_big_bang_until_complete",
            status="running",
            big_bang_id=uuid4(),
            payload={},
            result={},
            error="worker exited",
            idempotency_key="expired",
            updated_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
        )
        fresh = models.Job(
            job_type="run_big_bang_until_complete",
            status="running",
            big_bang_id=uuid4(),
            payload={},
            result={},
            error="still leased",
            idempotency_key="fresh",
            updated_at=now,
        )
        db.add_all([expired, fresh])
        db.commit()

        assert claim_job_for_execution(db, expired, now=now) is True
        assert expired.status == "running"
        assert expired.error is None
        assert claim_job_for_execution(db, fresh, now=now) is False
    finally:
        db.close()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


class _ExecutionDb:
    def __init__(self, *, rowcount: int):
        self.rowcount = rowcount
        self.flushes = 0

    def execute(self, statement):
        return SimpleNamespace(rowcount=self.rowcount)

    def flush(self):
        self.flushes += 1

    def refresh(self, job):
        return None

    def commit(self):
        return None

    def begin_nested(self):
        return _NoopTransaction()


class _CreateDb:
    def __init__(self):
        self.added = None
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement):
        return None

    def add(self, job):
        self.added = job

    def commit(self):
        self.commits += 1
        if self.added is not None and self.added.id is None:
            self.added.id = uuid4()

    def rollback(self):
        self.rollbacks += 1


class _DuplicateRaceDb(_CreateDb):
    def __init__(self, existing):
        super().__init__()
        self.existing = existing
        self.scalar_calls = 0

    def scalar(self, statement):
        self.scalar_calls += 1
        return None if self.scalar_calls == 1 else self.existing

    def commit(self):
        self.commits += 1
        raise IntegrityError("insert job", {}, Exception("duplicate idempotency key"))


class _ExistingDb(_CreateDb):
    def __init__(self, existing):
        super().__init__()
        self.existing = existing

    def scalar(self, statement):
        return self.existing


class _CommitFailureDb(_CreateDb):
    def commit(self):
        self.commits += 1
        raise RuntimeError("database secret: password=not-for-clients")


class _RunJobDb:
    def __init__(self, job):
        self.job = job
        self.commits = 0

    def get(self, model, object_id):
        return self.job

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None


class _BackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class _NoopTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False
