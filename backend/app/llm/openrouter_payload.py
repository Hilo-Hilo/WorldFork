from __future__ import annotations

import re
from typing import Any


def build_openrouter_response_format(
    *,
    json_schema: dict[str, Any] | None = None,
    response_format: dict[str, Any] | str | None = None,
    name: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Return an OpenRouter-compatible response_format payload.

    OpenRouter's strict schema mode expects the OpenAI-style wrapper:
    ``{"type": "json_schema", "json_schema": {"name", "strict", "schema"}}``.
    WorldFork callers often hold the inner JSON Schema directly, so normalize
    both shapes here before a provider sees the request.
    """
    if isinstance(response_format, str):
        if response_format == "json_object":
            return {"type": "json_object"}
        if response_format != "json_schema":
            return {"type": "json_object"}

    if isinstance(response_format, dict):
        if response_format.get("type") == "json_object":
            return {"type": "json_object"}
        if response_format.get("type") == "json_schema":
            schema_config = response_format.get("json_schema")
            if isinstance(schema_config, dict) and isinstance(schema_config.get("schema"), dict):
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": _schema_name(
                            name
                            or schema_config.get("name")
                            or response_format.get("name")
                            or "worldfork_response"
                        ),
                        "strict": bool(schema_config.get("strict", response_format.get("strict", strict))),
                        "schema": schema_config["schema"],
                    },
                }
            if isinstance(schema_config, dict):
                json_schema = schema_config
        elif _looks_like_json_schema(response_format):
            json_schema = response_format

    if json_schema:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(name or "worldfork_response"),
                "strict": strict,
                "schema": json_schema,
            },
        }
    return {"type": "json_object"}


def openrouter_options_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}

    provider = metadata.get("openrouter_provider")
    if isinstance(provider, dict):
        options["provider"] = provider
    elif metadata.get("openrouter_require_parameters") is True:
        options["provider"] = {"require_parameters": True}

    plugins = metadata.get("openrouter_plugins")
    if isinstance(plugins, list):
        options["plugins"] = plugins
    elif metadata.get("openrouter_response_healing") is True:
        options["plugins"] = [{"id": "response-healing"}]

    return options


def openrouter_options_from_response_format(response_format: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response_format, dict):
        return {}
    value = response_format.get("openrouter")
    return value if isinstance(value, dict) else {}


def response_format_override_from_metadata(metadata: dict[str, Any]) -> dict[str, Any] | str | None:
    value = metadata.get("openrouter_response_format")
    if isinstance(value, (dict, str)):
        return value
    value = metadata.get("response_format")
    if isinstance(value, (dict, str)):
        return value
    return None


def response_format_override_from_openrouter_options(options: dict[str, Any]) -> dict[str, Any] | str | None:
    value = options.get("response_format")
    if isinstance(value, (dict, str)):
        return value
    return None


def _looks_like_json_schema(value: dict[str, Any]) -> bool:
    return value.get("type") == "object" or "properties" in value or "$schema" in value


def _schema_name(value: Any) -> str:
    text = str(value or "worldfork_response").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "worldfork_response"
    if text[0].isdigit():
        text = f"schema_{text}"
    return text[:64]
