from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import BACKEND_DIR, Settings
from app.source_of_truth.loader import SourceOfTruthLoader
from app.source_of_truth.validator import REQUIRED_FILES, validate_source_of_truth_dir
from app.storage.artifact_store import ArtifactStore


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def test_default_storage_paths_are_backend_relative(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    settings = Settings(_env_file=None)

    assert settings.artifact_root == (BACKEND_DIR / "../artifacts").resolve()
    assert settings.source_of_truth_dir == (BACKEND_DIR / "../source_of_truth").resolve()


def test_relative_storage_overrides_are_backend_relative():
    settings = Settings(
        _env_file=None,
        artifact_root=Path("var/artifacts"),
        source_of_truth_dir=Path("var/source_of_truth"),
    )

    assert settings.artifact_root == (BACKEND_DIR / "var/artifacts").resolve()
    assert settings.source_of_truth_dir == (BACKEND_DIR / "var/source_of_truth").resolve()


def test_artifact_write_rejects_path_traversal(tmp_path):
    store = ArtifactStore(root=tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.write_text(
            FakeSession(),
            big_bang_id=None,
            relative_path="../escape.txt",
            body="nope",
            kind="test",
        )

    assert not (tmp_path / "escape.txt").exists()


def test_source_of_truth_loader_rejects_path_traversal(tmp_path):
    source_root = tmp_path / "source_of_truth"
    for relative_name in REQUIRED_FILES:
        path = source_root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    (tmp_path / "outside.json").write_text("{}")

    loader = SourceOfTruthLoader(root=source_root)

    with pytest.raises(ValueError):
        loader.load_json("../outside.json")

    with pytest.raises(ValueError):
        loader.load_template("../emotions.json")


@pytest.mark.parametrize(
    "bad_required_file",
    [
        "emotions.json",
        "behavior_axes.json",
        "ideology_axes.json",
        "issue_stance_axes.json",
        "expression_scale.json",
        "event_types.json",
        "social_action_types.json",
        "graph_edge_types.json",
        "sociology_models.json",
        "report_templates/event_summary.md",
    ],
)
def test_source_of_truth_validation_rejects_required_directories(tmp_path, bad_required_file):
    source_root = tmp_path / "source_of_truth"
    for relative_name in REQUIRED_FILES:
        path = source_root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative_name == bad_required_file:
            path.mkdir()
        else:
            path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="required files must be regular files"):
        validate_source_of_truth_dir(source_root)


@pytest.mark.parametrize(
    "bad_required_file",
    [
        "emotions.json",
        "behavior_axes.json",
        "ideology_axes.json",
        "issue_stance_axes.json",
        "expression_scale.json",
        "event_types.json",
        "social_action_types.json",
        "graph_edge_types.json",
        "sociology_models.json",
        "report_templates/event_summary.md",
    ],
)
def test_source_of_truth_validation_rejects_required_symlinks(tmp_path, bad_required_file):
    source_root = tmp_path / "source_of_truth"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    for relative_name in REQUIRED_FILES:
        path = source_root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative_name == bad_required_file:
            path.symlink_to(outside)
        else:
            path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="required files must not be symlinks"):
        validate_source_of_truth_dir(source_root)


def test_artifact_write_preserves_existing_file_and_uses_collision_path(tmp_path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    db = FakeSession()

    first = store.write_text(
        db,
        big_bang_id=None,
        relative_path="reports/out.txt",
        body="first",
        kind="test",
    )
    second = store.write_text(
        db,
        big_bang_id=None,
        relative_path="reports/out.txt",
        body="second",
        kind="test",
    )
    same_as_first = store.write_text(
        db,
        big_bang_id=None,
        relative_path="reports/out.txt",
        body="first",
        kind="test",
    )

    assert Path(first.path).read_text() == "first"
    assert Path(second.path).read_text() == "second"
    assert second.path != first.path
    assert second.meta["requested_relative_path"] == "reports/out.txt"
    assert same_as_first.path == first.path


def test_copy_tree_snapshot_preserves_existing_directory_and_uses_collision_path(tmp_path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    source = tmp_path / "source"
    source.mkdir()
    (source / "item.txt").write_text("first")

    first, _ = store.copy_tree_snapshot(
        FakeSession(),
        big_bang_id=uuid4(),
        source_dir=source,
        relative_dest="snapshots/source",
        kind="snapshot",
    )

    (source / "item.txt").write_text("second")
    second, _ = store.copy_tree_snapshot(
        FakeSession(),
        big_bang_id=uuid4(),
        source_dir=source,
        relative_dest="snapshots/source",
        kind="snapshot",
    )

    assert (Path(first.path) / "item.txt").read_text() == "first"
    assert (Path(second.path) / "item.txt").read_text() == "second"
    assert second.path != first.path
    assert second.meta["requested_relative_path"] == "snapshots/source"


def test_pdf_render_rejects_path_traversal(tmp_path):
    pytest.importorskip("reportlab")
    from app.storage.pdf_store import render_markdown_pdf

    with pytest.raises(ValueError):
        render_markdown_pdf(
            FakeSession(),
            big_bang_id=uuid4(),
            relative_path="../escape.pdf",
            title="Unsafe",
            markdown="body",
        )

    assert not (tmp_path / "escape.pdf").exists()
