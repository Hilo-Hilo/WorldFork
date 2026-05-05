from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domains.jobs import routes as jobs_api
from app.api.schemas import JobCreate
from app.db import models
from app.domains.jobs import executor as jobs_executor
from app.domains.jobs.queues import JOB_TYPES, default_idempotency_key, queue_name_for_job
from app.domains.jobs.executor import (
    JOB_LEASE_SECONDS,
    JobNotRunnableError,
    _endpoint_path_mass_resolution,
    claim_job_for_execution,
    execute_job,
    job_should_enqueue_for_retry,
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


def test_advertised_job_types_are_executable_and_payload_validated():
    assert "render_pdf_report" not in JOB_TYPES

    with pytest.raises(ValueError, match="unknown job_type"):
        validate_job_payload("render_pdf_report", {})

    with pytest.raises(ValueError, match="multiverse_id is required"):
        validate_job_payload("run_multiverse_tick", {})

    with pytest.raises(ValueError, match="big_bang_id is required"):
        validate_job_payload("run_big_bang_until_complete", {})

    validate_job_payload(
        "run_big_bang_until_complete",
        {"max_total_ticks": 16, "stop_when_endpoint_ledger_resolved": True},
        big_bang_id=uuid4(),
    )
    with pytest.raises(ValueError, match="stop_when_endpoint_ledger_resolved must be a boolean"):
        validate_job_payload(
            "run_big_bang_until_complete",
            {"max_total_ticks": 16, "stop_when_endpoint_ledger_resolved": "true"},
            big_bang_id=uuid4(),
        )


def test_endpoint_path_mass_resolution_treats_ticks_as_caps():
    resolved = _endpoint_path_mass_resolution(
        [
            {
                "endpoint_key": "yes",
                "path_mass": 0.62,
                "status_path_masses": {"realized": 0.62},
            },
            {
                "endpoint_key": "no",
                "path_mass": 0.38,
                "status_path_masses": {"realized": 0.38},
            },
        ]
    )
    unresolved = _endpoint_path_mass_resolution(
        [
            {
                "endpoint_key": "yes",
                "status": "realized",
                "path_mass": 0.7,
                "status_path_masses": {"realized": 0.7},
            },
            {
                "endpoint_key": "endpoint_insufficient_ticks",
                "status": "insufficient_ticks",
                "path_mass": 0.3,
                "status_path_masses": {"insufficient_ticks": 0.3},
            },
        ]
    )

    assert resolved["resolved"] is True
    assert resolved["unresolved_mass"] == 0.0
    assert resolved["insufficient_ticks_mass"] == 0.0
    assert unresolved["resolved"] is False
    assert unresolved["insufficient_ticks_mass"] == 0.3


def test_advertised_job_queues_are_runtime_celery_queues():
    from backend.app.workers.celery_app import celery_app

    configured_queues = {queue.name for queue in celery_app.conf.task_queues}

    assert {queue_name_for_job(job_type) for job_type in JOB_TYPES}.issubset(configured_queues)


def test_job_worker_imports_without_undeclared_queue_dependency():
    import importlib

    workers = importlib.import_module("app.domains.jobs.workers")

    assert workers.run_job.name == "worldfork.execute_job"


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


def test_audited_long_running_jobs_skip_nested_transaction(monkeypatch):
    job = SimpleNamespace(
        id=uuid4(),
        job_type="generate_multiverse_report",
        status="queued",
        big_bang_id=uuid4(),
        payload={"multiverse_id": str(uuid4())},
        result=None,
        error=None,
    )

    class NoNestedDb(_ExecutionDb):
        def begin_nested(self):
            pytest.fail("audited long-running jobs must not run inside a nested transaction")

    monkeypatch.setattr(jobs_executor, "_execute_job", lambda _db, _job: {"report_version_id": str(uuid4())})
    monkeypatch.setattr(jobs_executor, "_job_interrupt_status", lambda _db, _job: None)

    returned = jobs_executor.execute_job(NoNestedDb(rowcount=1), job)

    assert returned.status == "succeeded"
    assert returned.error is None


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
        jobs_api.run_job(job.id, BackgroundTasks(), inline=True, db=db)

    assert exc.value.status_code == 500
    assert exc.value.detail == "job execution failed"
    assert db.commits == 1


def test_run_job_dispatches_background_when_requested(monkeypatch):
    job = SimpleNamespace(id=uuid4(), status="queued", error=None)
    db = _RunJobDb(job)
    scheduled = {}

    def fail_if_inline(*args, **kwargs):
        raise AssertionError("run_job should not execute inline when inline is false")

    def fake_schedule(background_tasks, job_id):
        scheduled["job_id"] = job_id

    monkeypatch.setattr(jobs_api, "execute_job", fail_if_inline)
    monkeypatch.setattr(jobs_api, "schedule_local_fallback", fake_schedule)

    returned = jobs_api.run_job(job.id, BackgroundTasks(), inline=False, db=db)

    assert returned is job
    assert scheduled == {"job_id": job.id}


def test_final_report_job_rejects_active_multiverse_before_generating(monkeypatch):
    from app.domains.report import engine as report_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        big_bang = models.BigBang(name="Active final report", scenario_input={}, status="running")
        db.add(big_bang)
        db.flush()
        db.add(
            models.Multiverse(
                big_bang_id=big_bang.id,
                parent_multiverse_id=None,
                fork_tick_index=None,
                ui_label="M1",
                depth=0,
                status="active",
                branch_reason="Root",
                state={},
                report_status="not_ready",
            )
        )
        job = models.Job(
            job_type="generate_final_big_bang_report",
            status="queued",
            big_bang_id=big_bang.id,
            payload={},
            result={},
            idempotency_key=f"final-report:{uuid4()}",
        )
        db.add(job)
        db.commit()

        monkeypatch.setattr(
            report_engine,
            "generate_final_big_bang_report",
            lambda *args, **kwargs: pytest.fail("final report should not generate"),
        )

        execute_job(db, job)

        assert job.status == "failed"
        assert "final report requires terminal multiverses" in job.error
    finally:
        db.close()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


def test_run_until_complete_job_rejects_archived_big_bang():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        big_bang = models.BigBang(name="Archived control", scenario_input={}, status="archived")
        db.add(big_bang)
        db.flush()
        job = models.Job(
            job_type="run_big_bang_until_complete",
            status="queued",
            big_bang_id=big_bang.id,
            payload={},
            result={},
            idempotency_key=f"run-complete:{uuid4()}",
        )
        db.add(job)
        db.commit()

        execute_job(db, job)

        assert job.status == "failed"
        assert "archived" in job.error
        assert big_bang.status == "archived"
    finally:
        db.close()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


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
            lease_expires_at=now - timedelta(seconds=5),
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
            lease_expires_at=now + timedelta(seconds=JOB_LEASE_SECONDS),
            updated_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
        )
        legacy_expired = models.Job(
            job_type="run_big_bang_until_complete",
            status="running",
            big_bang_id=uuid4(),
            payload={},
            result={},
            error="legacy worker exited",
            idempotency_key="legacy-expired",
            lease_expires_at=None,
            updated_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
        )
        stale_call = models.LLMCall(
            big_bang_id=expired.big_bang_id,
            provider="openrouter",
            model="stale-model",
            purpose="expired job call",
            status="running",
            meta={},
            created_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
            updated_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
        )
        fresh_call = models.LLMCall(
            big_bang_id=fresh.big_bang_id,
            provider="openrouter",
            model="fresh-model",
            purpose="fresh job call",
            status="running",
            meta={},
            created_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
            updated_at=now - timedelta(seconds=JOB_LEASE_SECONDS + 5),
        )
        db.add_all([expired, fresh, legacy_expired, stale_call, fresh_call])
        db.commit()

        assert claim_job_for_execution(db, expired, now=now) is True
        assert expired.status == "running"
        assert expired.error is None
        db.refresh(stale_call)
        assert stale_call.status == "failed"
        assert "expired job lease reclaimed" in stale_call.meta["stale_reclaim_reason"]
        assert claim_job_for_execution(db, fresh, now=now) is False
        db.refresh(fresh_call)
        assert fresh_call.status == "running"
        assert claim_job_for_execution(db, legacy_expired, now=now) is True
    finally:
        db.close()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


def test_running_job_retry_respects_live_lease_even_with_stale_updated_at():
    now = datetime.now(timezone.utc)
    live = SimpleNamespace(
        status="running",
        lease_expires_at=now + timedelta(minutes=10),
        updated_at=now - timedelta(hours=1),
    )
    expired = SimpleNamespace(
        status="running",
        lease_expires_at=now - timedelta(seconds=1),
        updated_at=now,
    )

    assert job_should_enqueue_for_retry(live, now=now) is False
    assert job_should_enqueue_for_retry(expired, now=now) is True


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
