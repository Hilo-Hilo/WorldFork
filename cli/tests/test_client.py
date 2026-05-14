from __future__ import annotations

from worldfork_cli.client import WorldForkClient


class _Response:
    status_code = 204
    content = b""
    headers: dict[str, str] = {}


def test_request_uses_client_default_timeout_when_no_override() -> None:
    calls = []
    client = WorldForkClient("http://example.test", timeout=30)

    class FakeHttp:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return _Response()

    client._http = FakeHttp()  # type: ignore[assignment]

    assert client.request("GET", "/readyz") is None

    assert calls == [("GET", "api/readyz", {"params": None, "json": None})]


def test_request_allows_explicit_timeout_override() -> None:
    calls = []
    client = WorldForkClient("http://example.test", timeout=30)

    class FakeHttp:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return _Response()

    client._http = FakeHttp()  # type: ignore[assignment]

    assert client.request("GET", "/readyz", timeout=2) is None

    assert calls == [("GET", "api/readyz", {"params": None, "json": None, "timeout": 2})]
