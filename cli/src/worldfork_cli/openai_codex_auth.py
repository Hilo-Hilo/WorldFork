from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

OPENAI_AUTH_BASE_URL = "https://auth.openai.com"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_DEVICE_CALLBACK_URL = f"{OPENAI_AUTH_BASE_URL}/deviceauth/callback"
DEFAULT_TIMEOUT_SECONDS = 15 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class OpenAICodexDevicePrompt:
    verification_url: str
    user_code: str
    expires_in_seconds: int


@dataclass(frozen=True)
class OpenAICodexLoginResult:
    auth_file: Path
    expires_at: str | None


def default_worldfork_codex_auth_file() -> Path:
    root = os.environ.get("WORLDFORK_HOME") or os.environ.get("WORLD_FORK_HOME")
    if root:
        return Path(root).expanduser() / "openai-codex-auth.json"
    return Path.home() / ".worldfork" / "openai-codex-auth.json"


def login_openai_codex_device_code(
    *,
    auth_file: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_verification: Callable[[OpenAICodexDevicePrompt], None] | None = None,
    client: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> OpenAICodexLoginResult:
    close_client = client is None
    http_client = client or httpx.Client(timeout=30.0)
    try:
        device = _request_device_code(http_client)
        prompt = OpenAICodexDevicePrompt(
            verification_url=device["verification_url"],
            user_code=device["user_code"],
            expires_in_seconds=int(device["expires_in_seconds"]),
        )
        if on_verification:
            on_verification(prompt)
        authorization = _poll_device_code(
            http_client,
            device_auth_id=device["device_auth_id"],
            user_code=device["user_code"],
            interval_seconds=device["interval_seconds"],
            timeout_seconds=timeout_seconds,
            sleep=sleep,
        )
        token = _exchange_device_code(
            http_client,
            authorization_code=authorization["authorization_code"],
            code_verifier=authorization["code_verifier"],
        )
    finally:
        if close_client:
            http_client.close()

    target = Path(auth_file).expanduser() if auth_file else default_worldfork_codex_auth_file()
    expires_at = _write_auth_file(target, token)
    return OpenAICodexLoginResult(auth_file=target, expires_at=expires_at)


def _request_device_code(client: Any) -> dict[str, Any]:
    response = client.post(
        f"{OPENAI_AUTH_BASE_URL}/api/accounts/deviceauth/usercode",
        json={"client_id": OPENAI_CODEX_CLIENT_ID},
    )
    body = _response_json(response, "OpenAI device code request failed")
    device_auth_id = _trimmed_string(body.get("device_auth_id"))
    user_code = _trimmed_string(body.get("user_code")) or _trimmed_string(body.get("usercode"))
    if not device_auth_id or not user_code:
        raise RuntimeError("OpenAI device code response was missing the device code or user code.")
    interval_seconds = _positive_number(body.get("interval")) or DEFAULT_POLL_INTERVAL_SECONDS
    expires_in_seconds = _positive_number(body.get("expires_in")) or DEFAULT_TIMEOUT_SECONDS
    verification_url = (
        _trimmed_string(body.get("verification_uri"))
        or _trimmed_string(body.get("verification_url"))
        or f"{OPENAI_AUTH_BASE_URL}/codex/device"
    )
    return {
        "device_auth_id": device_auth_id,
        "user_code": user_code,
        "verification_url": verification_url,
        "interval_seconds": interval_seconds,
        "expires_in_seconds": expires_in_seconds,
    }


def _poll_device_code(
    client: Any,
    *,
    device_auth_id: str,
    user_code: str,
    interval_seconds: float,
    timeout_seconds: int,
    sleep: Callable[[float], None],
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    current_interval = max(interval_seconds, 1.0)
    while time.monotonic() < deadline:
        response = client.post(
            f"{OPENAI_AUTH_BASE_URL}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
        )
        if _is_success(response):
            body = _parse_response_json(response)
            authorization_code = _trimmed_string(body.get("authorization_code"))
            code_verifier = _trimmed_string(body.get("code_verifier"))
            if not authorization_code or not code_verifier:
                raise RuntimeError("OpenAI device authorization response was missing the exchange code.")
            return {"authorization_code": authorization_code, "code_verifier": code_verifier}
        retry_error = _device_auth_retry_error(response)
        if response.status_code in (403, 404) or retry_error:
            if retry_error == "slow_down":
                current_interval += 5.0
            remaining = max(0.0, deadline - time.monotonic())
            sleep(min(current_interval, remaining))
            continue
        _raise_response_error(response, "OpenAI device authorization failed")
    raise RuntimeError(f"OpenAI device authorization timed out after {timeout_seconds} seconds.")


def _exchange_device_code(
    client: Any,
    *,
    authorization_code: str,
    code_verifier: str,
) -> dict[str, Any]:
    response = client.post(
        f"{OPENAI_AUTH_BASE_URL}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": OPENAI_CODEX_DEVICE_CALLBACK_URL,
            "client_id": OPENAI_CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        },
    )
    body = _response_json(response, "OpenAI device token exchange failed")
    access = _trimmed_string(body.get("access_token"))
    refresh = _trimmed_string(body.get("refresh_token"))
    if not access or not refresh:
        raise RuntimeError("OpenAI token exchange succeeded but did not return OAuth tokens.")
    return body


def _write_auth_file(path: Path, token: dict[str, Any]) -> str | None:
    access = _trimmed_string(token.get("access_token"))
    refresh = _trimmed_string(token.get("refresh_token"))
    if not access or not refresh:
        raise RuntimeError("OpenAI token exchange result was missing OAuth tokens.")
    expires_at = _expires_at_iso(token.get("expires_in"))
    payload = {
        "auth_mode": "chatgpt",
        "provider": "openai-codex",
        "last_refresh": datetime.now(UTC).isoformat(),
        "expires_at": expires_at,
        "tokens": {
            "access_token": access,
            "refresh_token": refresh,
        },
    }
    id_token = _trimmed_string(token.get("id_token"))
    if id_token:
        payload["tokens"]["id_token"] = id_token

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return expires_at


def _expires_at_iso(expires_in: Any) -> str | None:
    seconds = _positive_number(expires_in)
    if seconds is None:
        return None
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _response_json(response: Any, prefix: str) -> dict[str, Any]:
    if not _is_success(response):
        _raise_response_error(response, prefix)
    return _parse_response_json(response)


def _parse_response_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("OpenAI auth response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("OpenAI auth response JSON root was not an object.")
    return data


def _raise_response_error(response: Any, prefix: str) -> None:
    message = ""
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        error = _trimmed_string(data.get("error"))
        description = _trimmed_string(data.get("error_description"))
        message = " ".join(part for part in (error, description) if part)
    if not message:
        message = _trimmed_string(getattr(response, "text", "")) or ""
    status_code = getattr(response, "status_code", "unknown")
    raise RuntimeError(f"{prefix}: HTTP {status_code} {message}".strip())


def _device_auth_retry_error(response: Any) -> str | None:
    try:
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    error = _trimmed_string(data.get("error"))
    if error is None:
        return None
    normalized = error.casefold()
    if normalized in {"authorization_pending", "slow_down"}:
        return normalized
    return None


def _is_success(response: Any) -> bool:
    status_code = getattr(response, "status_code", 0)
    return 200 <= int(status_code) < 300


def _trimmed_string(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _positive_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None
