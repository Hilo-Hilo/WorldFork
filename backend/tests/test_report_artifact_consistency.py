from __future__ import annotations

import warnings
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.jobs.tasks import execute_job
from app.simulation import report_engine
from app.storage.artifact_store import ArtifactStore, hash_directory


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


def test_failed_report_job_rolls_back_completed_report_state(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Report consistency",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    multiverse = models.Multiverse(
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
    db.add(multiverse)
    db.flush()
    report = models.Report(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        report_type="multiverse",
        status="draft",
        current_version=0,
    )
    job = models.Job(
        job_type="generate_multiverse_report",
        status="queued",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(multiverse.id)},
        result={},
        idempotency_key=f"report:{uuid4()}",
    )
    db.add_all([report, job])
    db.commit()

    monkeypatch.setattr(
        report_engine,
        "ArtifactStore",
        lambda: ArtifactStore(root=tmp_path / "artifacts"),
    )

    def fail_markdown(*args, **kwargs):
        raise RuntimeError("markdown writer failed")

    monkeypatch.setattr(report_engine, "_write_markdown_artifact", fail_markdown)

    execute_job(db, job)
    db.commit()
    db.expire_all()

    persisted_report = db.scalar(select(models.Report).where(models.Report.id == report.id))
    persisted_multiverse = db.get(models.Multiverse, multiverse.id)

    assert job.status == "failed"
    assert "markdown writer failed" in job.error
    assert persisted_report.status == "draft"
    assert persisted_report.current_version == 0
    assert persisted_multiverse.report_status == "not_ready"
    assert db.scalars(select(models.ReportVersion)).all() == []
    assert not list((tmp_path / "artifacts").rglob("*.md"))


def test_final_report_inventory_uses_committed_status_and_version(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Final inventory",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        status="completed",
        branch_reason="Root",
        state={},
        report_status="completed",
    )
    db.add(multiverse)
    db.flush()

    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(
        report_engine,
        "ArtifactStore",
        lambda: ArtifactStore(root=artifact_root),
    )

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    markdown = db.get(models.Artifact, report_version.markdown_artifact_id)

    body = Path(markdown.path).read_text(encoding="utf-8")
    assert "- final_big_bang: completed v1" in body
    assert "- final_big_bang: draft v0" not in body


def test_report_version_stores_structured_content_and_renders_pdf_on_demand(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Structured reports",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": 2},
            model_config={},
            branch_policy={},
        )
    )
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        version=2,
        depth=0,
        status="completed",
        branch_reason="Root",
        state={"runtime_config_version": 1},
        report_status="ready",
    )
    db.add(multiverse)
    db.flush()
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(report_engine, "ArtifactStore", lambda: ArtifactStore(root=artifact_root))

    def fake_pdf(db, *, big_bang_id, relative_path, title, markdown):
        return ArtifactStore(root=artifact_root).write_bytes(
            db,
            big_bang_id=big_bang_id,
            relative_path=relative_path,
            body=b"%PDF-1.4\n",
            kind="report_pdf",
            content_type="application/pdf",
        )

    monkeypatch.setattr(report_engine, "render_markdown_pdf", fake_pdf)

    report_version = report_engine.generate_multiverse_report(db, multiverse=multiverse)

    assert report_version.source_multiverse_version == 2
    assert report_version.source_big_bang_config_version == 1
    assert report_version.model
    assert report_version.content["source"]["multiverse_version"] == 2
    assert report_version.generation_metadata["storage"]["canonical"] == "report_versions.content"
    assert report_version.pdf_artifact_id is None

    pdf_artifact = report_engine.render_report_version_artifact(
        db,
        report_version=report_version,
        output_format="pdf",
    )

    assert pdf_artifact.content_type == "application/pdf"
    assert report_version.pdf_artifact_id == pdf_artifact.id


def test_artifact_file_is_removed_when_db_flush_fails(tmp_path: Path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    db = _FailingFlushSession()

    with pytest.raises(RuntimeError, match="flush failed"):
        store.write_text(
            db,
            big_bang_id=None,
            relative_path="reports/report.md",
            body="partial",
            kind="report_markdown",
        )

    assert not (tmp_path / "artifacts/reports/report.md").exists()


def test_artifact_cleanup_does_not_remove_reused_existing_file(tmp_path: Path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    existing = store.write_text(
        _SuccessfulFlushSession(),
        big_bang_id=None,
        relative_path="reports/report.md",
        body="already committed",
        kind="report_markdown",
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        store.write_text(
            _FailingFlushSession(),
            big_bang_id=None,
            relative_path="reports/report.md",
            body="already committed",
            kind="report_markdown",
        )

    assert Path(existing.path).read_text() == "already committed"


def test_hash_directory_frames_paths_and_contents(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "ab").write_text("c", encoding="utf-8")
    (second / "a").write_text("bc", encoding="utf-8")

    assert hash_directory(first) != hash_directory(second)


class _FailingFlushSession:
    def add(self, obj):
        return None

    def flush(self):
        raise RuntimeError("flush failed")


class _SuccessfulFlushSession:
    def add(self, obj):
        return None

    def flush(self):
        return None
