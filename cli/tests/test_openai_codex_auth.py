from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

import worldfork_cli.main as cli_main
from worldfork_cli.main import main
from worldfork_cli.openai_codex_auth import (
    OpenAICodexLoginResult,
    login_openai_codex_device_code,
)


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


def test_device_flow_writes_worldfork_auth_file_without_codex_cli(tmp_path) -> None:
    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.posts = []
            self.responses = [
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

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            return self.responses.pop(0)

    prompts = []
    auth_file = tmp_path / "worldfork-auth.json"
    result = login_openai_codex_device_code(
        auth_file=auth_file,
        client=FakeClient(),
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
