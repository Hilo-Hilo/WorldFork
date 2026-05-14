from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.source_of_truth.validator import validate_source_of_truth_dir


class SourceOfTruthLoader:
    def __init__(self, root: Path | None = None):
        self.root = (root or get_settings().source_of_truth_dir).resolve()

    def validate(self) -> None:
        validate_source_of_truth_dir(self.root)

    def _resolve_child(self, *parts: str) -> Path:
        for part in parts:
            raw_part = str(part)
            raw_segments = raw_part.split("/")
            if "\\" in raw_part or any(segment in {"", ".", ".."} for segment in raw_segments):
                raise ValueError(f"source_of_truth path must stay under root: {part}")
        relative = Path(*parts)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"source_of_truth path must stay under root: {relative}")
        requested_path = self.root / relative
        if requested_path.is_symlink():
            raise ValueError(f"source_of_truth path must not be a symlink: {relative}")
        path = requested_path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"source_of_truth path escapes root: {relative}")
        return path

    def load_json(self, name: str) -> Any:
        self.validate()
        return json.loads(self._resolve_child(name).read_text())

    def load_template(self, name: str) -> str:
        self.validate()
        return self._resolve_child("report_templates", name).read_text()
