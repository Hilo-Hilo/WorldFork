from __future__ import annotations

import json
from typing import Any

import click


def unwrap(payload: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        return payload["data"], meta
    return payload, {}


def emit(payload: Any, *, as_json: bool = False) -> None:
    data, _meta = unwrap(payload)
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(data, list):
        if not data:
            click.echo("(no results)")
            return
        if all(isinstance(item, dict) for item in data):
            print_table(data)
            return
    if isinstance(data, dict):
        click.echo(json.dumps(data, indent=2, default=str))
        return
    click.echo(str(data))


def print_table(rows: list[dict[str, Any]]) -> None:
    columns = _columns(rows)
    widths = [len(col) for col in columns]
    for row in rows:
        for index, col in enumerate(columns):
            widths[index] = max(widths[index], len(_cell(row.get(col))))
    click.echo(" | ".join(col.ljust(widths[index]) for index, col in enumerate(columns)))
    click.echo("-+-".join("-" * width for width in widths))
    for row in rows:
        click.echo(" | ".join(_cell(row.get(col)).ljust(widths[index]) for index, col in enumerate(columns)))


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "id",
        "name",
        "status",
        "job_type",
        "big_bang_id",
        "ui_label",
        "tick_index",
        "actor_id",
        "actor_kind",
        "created_at",
    ]
    seen: list[str] = []
    for col in preferred:
        if any(col in row for row in rows):
            seen.append(col)
    for row in rows:
        for col in row:
            if col not in seen:
                seen.append(col)
    return seen


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if len(value) <= 96 else value[:95] + "..."
    if isinstance(value, (int, float, bool)):
        return str(value)
    text = json.dumps(value, default=str, separators=(",", ":"))
    return text if len(text) <= 96 else text[:95] + "..."
