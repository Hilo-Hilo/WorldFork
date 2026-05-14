from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models


class ArtifactStore:
    def __init__(self, root: Path | None = None):
        settings = get_settings()
        self.root = (root or settings.artifact_root).resolve()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError(f"artifact root is not a directory: {self.root}")

    def big_bang_root(self, big_bang_id: UUID | str) -> Path:
        return self.root / f"big_bang_{big_bang_id}"

    def artifact_path(self, relative_path: str) -> Path:
        relative = self._safe_relative_path(relative_path)
        parent = self._ensure_safe_parent(relative.parent)
        path = parent / relative.name
        if not path.parent.resolve().is_relative_to(self.root):
            raise ValueError(f"artifact path escapes root: {relative_path}")
        return path

    def write_json(
        self,
        db: Session,
        *,
        big_bang_id: UUID | None,
        relative_path: str,
        payload: Any,
        kind: str,
        debug_only: bool = False,
    ) -> models.Artifact:
        body = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return self.write_text(
            db,
            big_bang_id=big_bang_id,
            relative_path=relative_path,
            body=body,
            kind=kind,
            content_type="application/json",
            debug_only=debug_only,
        )

    def write_text(
        self,
        db: Session,
        *,
        big_bang_id: UUID | None,
        relative_path: str,
        body: str,
        kind: str,
        content_type: str = "text/plain",
        debug_only: bool = False,
    ) -> models.Artifact:
        encoded = body.encode("utf-8")
        return self.write_bytes(
            db,
            big_bang_id=big_bang_id,
            relative_path=relative_path,
            body=encoded,
            kind=kind,
            content_type=content_type,
            debug_only=debug_only,
        )

    def write_bytes(
        self,
        db: Session,
        *,
        big_bang_id: UUID | None,
        relative_path: str,
        body: bytes,
        kind: str,
        content_type: str,
        debug_only: bool = False,
    ) -> models.Artifact:
        path, stored_relative_path, created_file = self._write_file_bytes(relative_path, body)
        meta = {"relative_path": stored_relative_path}
        if stored_relative_path != relative_path:
            meta["requested_relative_path"] = relative_path
        artifact = models.Artifact(
            big_bang_id=big_bang_id,
            kind=kind,
            path=str(path),
            content_type=content_type,
            content_hash=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            debug_only=debug_only,
            meta=meta,
        )
        db.add(artifact)
        try:
            db.flush()
        except Exception:
            if created_file:
                _cleanup_artifact_path(path)
            raise
        return artifact

    def copy_tree_snapshot(
        self,
        db: Session,
        *,
        big_bang_id: UUID,
        source_dir: Path,
        relative_dest: str,
        kind: str,
    ) -> tuple[models.Artifact, str]:
        dest, stored_relative_dest, content_hash, created_tree = self._copy_tree_snapshot(
            source_dir=source_dir,
            relative_dest=relative_dest,
        )
        meta = {"relative_path": stored_relative_dest}
        if stored_relative_dest != relative_dest:
            meta["requested_relative_path"] = relative_dest
        artifact = models.Artifact(
            big_bang_id=big_bang_id,
            kind=kind,
            path=str(dest),
            content_type="inode/directory",
            content_hash=content_hash,
            size_bytes=None,
            debug_only=False,
            meta=meta,
        )
        db.add(artifact)
        try:
            db.flush()
        except Exception:
            if created_tree:
                _cleanup_artifact_path(dest)
            raise
        return artifact, content_hash

    def _write_file_bytes(self, relative_path: str, body: bytes) -> tuple[Path, str, bool]:
        relative = self._safe_relative_path(relative_path)
        digest = hashlib.sha256(body).hexdigest()
        for candidate in self._candidate_relative_paths(relative, digest):
            target = self.artifact_path(candidate.as_posix())
            if target.exists() or target.is_symlink():
                if self._existing_file_matches(target, body):
                    return target, candidate.as_posix(), False
                continue
            try:
                self._link_completed_temp_file(target, body)
                return target, candidate.as_posix(), True
            except FileExistsError:
                if self._existing_file_matches(target, body):
                    return target, candidate.as_posix(), False
                continue
        raise RuntimeError(f"could not allocate artifact path for {relative_path}")

    def _copy_tree_snapshot(
        self,
        *,
        source_dir: Path,
        relative_dest: str,
    ) -> tuple[Path, str, str, bool]:
        source_dir = Path(source_dir)
        if source_dir.is_symlink():
            raise ValueError(f"source snapshot path must not be a symlink: {source_dir}")
        source_dir = source_dir.resolve()
        if not source_dir.is_dir():
            raise ValueError(f"source snapshot path is not a directory: {source_dir}")

        content_hash = hash_directory(source_dir)
        relative = self._safe_relative_path(relative_dest)
        for candidate in self._candidate_relative_paths(relative, content_hash):
            target = self.artifact_path(candidate.as_posix())
            if target.exists() or target.is_symlink():
                if self._existing_directory_hash(target) == content_hash:
                    return target, candidate.as_posix(), content_hash, False
                continue
            try:
                self._rename_completed_temp_tree(source_dir, target, content_hash)
                return target, candidate.as_posix(), content_hash, True
            except FileExistsError:
                if self._existing_directory_hash(target) == content_hash:
                    return target, candidate.as_posix(), content_hash, False
                continue
        raise RuntimeError(f"could not allocate artifact directory for {relative_dest}")

    def _safe_relative_path(self, relative_path: str | Path) -> Path:
        raw_path = relative_path.as_posix() if isinstance(relative_path, Path) else str(relative_path)
        raw_parts = raw_path.split("/")
        if "\\" in raw_path or any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError(f"artifact path must stay under root: {relative_path}")
        relative = Path(raw_path)
        if relative.is_absolute() or not relative.parts:
            raise ValueError(f"artifact path must stay under root: {relative_path}")
        return relative

    def _ensure_safe_parent(self, relative_parent: Path) -> Path:
        self.ensure_root()
        current = self.root
        for part in relative_parent.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError(f"artifact directory escapes root: {relative_parent}")
            current = current / part
            try:
                current.mkdir()
            except FileExistsError:
                pass
            if current.is_symlink() or not current.is_dir():
                raise ValueError(f"artifact directory is unsafe: {current}")
            if not current.resolve().is_relative_to(self.root):
                raise ValueError(f"artifact directory escapes root: {current}")
        return current

    def _candidate_relative_paths(self, relative: Path, digest: str):
        yield relative
        short_hash = digest[:12]
        for index in range(1, 1000):
            suffix = f".{short_hash}" if index == 1 else f".{short_hash}.{index}"
            yield relative.with_name(f"{relative.stem}{suffix}{relative.suffix}")

    def _existing_file_matches(self, path: Path, body: bytes) -> bool:
        if path.is_symlink():
            raise ValueError(f"artifact path is a symlink: {path}")
        if not path.exists():
            return False
        if not path.is_file():
            return False
        return path.read_bytes() == body

    def _existing_directory_hash(self, path: Path) -> str | None:
        if path.is_symlink():
            raise ValueError(f"artifact path is a symlink: {path}")
        if not path.exists() or not path.is_dir():
            return None
        return hash_directory(path)

    def _link_completed_temp_file(self, target: Path, body: bytes) -> None:
        if not target.parent.resolve().is_relative_to(self.root):
            raise ValueError(f"artifact path escapes root: {target}")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(body)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.link(temp_path, target)
            _fsync_directory(target.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def _rename_completed_temp_tree(
        self,
        source_dir: Path,
        target: Path,
        expected_hash: str,
    ) -> None:
        if not target.parent.resolve().is_relative_to(self.root):
            raise ValueError(f"artifact path escapes root: {target}")
        temp_parent = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        )
        temp_tree = temp_parent / "tree"
        try:
            shutil.copytree(source_dir, temp_tree, symlinks=False)
            copied_hash = hash_directory(temp_tree)
            if copied_hash != expected_hash:
                raise ValueError("source snapshot changed while copying")
            if target.exists() or target.is_symlink():
                raise FileExistsError(str(target))
            try:
                temp_tree.rename(target)
            except FileExistsError:
                raise
            except OSError as exc:
                if target.exists():
                    raise FileExistsError(str(target)) from exc
                raise
            _fsync_directory(target.parent)
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)


def hash_directory(path: Path) -> str:
    digest = hashlib.sha256()
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"cannot hash artifact tree with symlink: {candidate}")
        relative_path = candidate.relative_to(path).as_posix().encode("utf-8")
        if candidate.is_dir():
            digest.update(b"D")
            digest.update(len(relative_path).to_bytes(8, "big"))
            digest.update(relative_path)
            continue
        if not candidate.is_file():
            continue
        content = candidate.read_bytes()
        digest.update(b"F")
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cleanup_artifact_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
    except OSError:
        pass
