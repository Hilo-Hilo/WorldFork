from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import worldfork_cli.main as cli_main
from worldfork_cli.main import main
from worldfork_cli.openai_codex_auth import (
    OpenAICodexLoginResult,
    _poll_device_code,
    _request_device_code,
    login_openai_codex_device_code,
)


class FakeResponse:
    def __init__(self, status_code, payload, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else str(payload)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.posts = []
        self.responses = list(responses)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


def test_openai_codex_login_uses_worldfork_device_flow(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_login(*, auth_file, timeout_seconds, on_verification):
        calls.append({"auth_file": auth_file, "timeout_seconds": timeout_seconds})
        on_verification(
            SimpleNamespace(
                verification_url="https://auth.openai.com/codex/device",
                user_code="ABCD-EFGH",
                expires_in_seconds=900,
            )
        )
        return OpenAICodexLoginResult(auth_file=auth_file, expires_at="2026-05-08T00:00:00+00:00")

    monkeypatch.setattr(cli_main, "login_openai_codex_device_code", fake_login)
    auth_file = tmp_path / "codex-auth.json"

    result = CliRunner().invoke(
        main,
        ["settings", "openai-codex-login", "--auth-file", str(auth_file), "--timeout", "900"],
    )

    assert result.exit_code == 0
    assert "https://auth.openai.com/codex/device" in result.output
    assert "ABCD-EFGH" in result.output
    assert '"provider": "openai-codex"' in result.output
    assert calls == [{"auth_file": auth_file, "timeout_seconds": 900}]


@pytest.mark.parametrize(
    ("response_field", "verification_url"),
    [
        ("verification_uri", "https://auth.openai.com/device"),
        ("verification_url", "https://auth.openai.com/verify"),
    ],
)
def test_device_code_uses_provider_verification_url(response_field, verification_url) -> None:
    device = _request_device_code(
        FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "device_auth_id": "device-auth-id",
                        "user_code": "ABCD-EFGH",
                        response_field: verification_url,
                    },
                )
            ]
        )
    )

    assert device["verification_url"] == verification_url


def test_device_code_uses_provider_expiry() -> None:
    device = _request_device_code(
        FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "device_auth_id": "device-auth-id",
                        "user_code": "ABCD-EFGH",
                        "expires_in": 600,
                    },
                )
            ]
        )
    )

    assert device["expires_in_seconds"] == 600


def test_device_code_accepts_decimal_string_interval() -> None:
    device = _request_device_code(
        FakeClient(
            [
                FakeResponse(
                    200,
                    {
                        "device_auth_id": "device-auth-id",
                        "user_code": "ABCD-EFGH",
                        "interval": "1.5",
                    },
                )
            ]
        )
    )

    assert device["interval_seconds"] == 1.5


def test_device_login_prompt_uses_provider_expiry(tmp_path) -> None:
    prompts = []
    client = FakeClient(
        [
            FakeResponse(
                200,
                {
                    "device_auth_id": "device-auth-id",
                    "user_code": "ABCD-EFGH",
                    "expires_in": 600,
                },
            ),
            FakeResponse(200, {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}),
            FakeResponse(200, {"access_token": "access-token", "refresh_token": "refresh-token"}),
        ]
    )

    login_openai_codex_device_code(
        auth_file=tmp_path / "auth.json",
        timeout_seconds=900,
        client=client,
        sleep=lambda _seconds: None,
        on_verification=prompts.append,
    )

    assert prompts[0].expires_in_seconds == 600


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "authorization_pending"},
        {"error": "Authorization_Pending"},
        {"error": " authorization_pending ", "error_description": "Waiting for approval"},
    ],
)
def test_poll_device_code_treats_structured_authorization_pending_as_retryable(payload) -> None:
    sleeps = []
    result = _poll_device_code(
        FakeClient(
            [
                FakeResponse(400, payload),
                FakeResponse(200, {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}),
            ]
        ),
        device_auth_id="device-auth-id",
        user_code="ABCD-EFGH",
        interval_seconds=1,
        timeout_seconds=30,
        sleep=sleeps.append,
    )

    assert result == {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}
    assert sleeps == [1]


def test_poll_device_code_treats_structured_slow_down_as_retryable() -> None:
    sleeps = []
    result = _poll_device_code(
        FakeClient(
            [
                FakeResponse(400, {"error": "slow_down"}),
                FakeResponse(200, {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}),
            ]
        ),
        device_auth_id="device-auth-id",
        user_code="ABCD-EFGH",
        interval_seconds=1,
        timeout_seconds=30,
        sleep=sleeps.append,
    )

    assert result == {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}
    assert sleeps == [6]


def test_poll_device_code_slow_down_increases_subsequent_authorization_pending_wait() -> None:
    sleeps = []
    result = _poll_device_code(
        FakeClient(
            [
                FakeResponse(400, {"error": "slow_down"}),
                FakeResponse(400, {"error": "authorization_pending"}),
                FakeResponse(200, {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}),
            ]
        ),
        device_auth_id="device-auth-id",
        user_code="ABCD-EFGH",
        interval_seconds=1,
        timeout_seconds=30,
        sleep=sleeps.append,
    )

    assert result == {"authorization_code": "authorization-code", "code_verifier": "code-verifier"}
    assert sleeps == [6, 6]


def test_poll_device_code_still_rejects_unretryable_structured_errors() -> None:
    with pytest.raises(RuntimeError, match="access_denied"):
        _poll_device_code(
            FakeClient([FakeResponse(400, {"error": "access_denied"})]),
            device_auth_id="device-auth-id",
            user_code="ABCD-EFGH",
            interval_seconds=1,
            timeout_seconds=30,
            sleep=lambda _seconds: None,
        )


def test_device_flow_writes_worldfork_auth_file_without_codex_cli(tmp_path) -> None:
    class DeviceFlowClient(FakeClient):
        def __init__(self):
            super().__init__(
                [
                FakeResponse(
                    200,
                    {
                        "device_auth_id": "device-auth-id",
                        "user_code": "ABCD-EFGH",
                        "interval": 1,
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "authorization_code": "authorization-code",
                        "code_verifier": "code-verifier",
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                    },
                ),
                ]
            )

    prompts = []
    auth_file = tmp_path / "worldfork-auth.json"
    result = login_openai_codex_device_code(
        auth_file=auth_file,
        client=DeviceFlowClient(),
        sleep=lambda _seconds: None,
        on_verification=prompts.append,
    )

    assert result.auth_file == auth_file
    assert prompts[0].verification_url == "https://auth.openai.com/codex/device"
    assert prompts[0].user_code == "ABCD-EFGH"
    saved = auth_file.read_text()
    assert '"provider": "openai-codex"' in saved
    assert '"access_token": "access-token"' in saved
    assert '"refresh_token": "refresh-token"' in saved
