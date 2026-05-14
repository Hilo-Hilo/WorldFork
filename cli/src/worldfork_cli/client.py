from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = (
    os.getenv("WORLD_FORK_API_BASE")
    or os.getenv("BACKEND_API_BASE")
    or "http://127.0.0.1:8003"
)
DEFAULT_API_PREFIX = os.getenv("WORLD_FORK_API_PREFIX", "/api")


class CliError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class WorldForkClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_prefix: str = DEFAULT_API_PREFIX,
        timeout: float | None = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix.strip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def normalize_path(self, path: str, *, use_api_prefix: bool = True) -> str:
        raw = path.strip()
        if raw.startswith(("http://", "https://")):
            return raw
        trimmed = raw.lstrip("/")
        if not use_api_prefix:
            return trimmed
        if self.api_prefix and not trimmed.startswith(f"{self.api_prefix}/") and trimmed != self.api_prefix:
            return f"{self.api_prefix}/{trimmed}"
        return trimmed

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        use_api_prefix: bool = True,
        timeout: float | None = None,
    ) -> Any:
        response = self.response(
            method,
            path,
            params=params,
            json_body=json_body,
            use_api_prefix=use_api_prefix,
            timeout=timeout,
        )
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def response(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        use_api_prefix: bool = True,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = self.normalize_path(path, use_api_prefix=use_api_prefix)
        request_kwargs = {"params": params, "json": json_body}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        try:
            response = self._http.request(method, url, **request_kwargs)
        except httpx.TimeoutException as exc:
            raise CliError(f"request timed out for {url}", exit_code=124) from exc
        except httpx.RequestError as exc:
            raise CliError(f"request failed for {url}: {exc}") from exc

        if response.status_code >= 400:
            detail = _error_detail(response)
            raise CliError(f"HTTP {response.status_code} {method.upper()} {url}: {detail}")
        return response


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        if detail:
            return str(detail)
    return str(payload)
