from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

LOCAL_OPENAI_COMPATIBLE_PROVIDERS = {
    "ollama",
    "vllm",
    "lmstudio",
    "lm-studio",
    "localai",
}
LOCAL_OPENAI_COMPATIBLE_API_SHAPES = {
    "ollama",
    "ollama-openai",
    "vllm",
    "vllm-openai",
    "lmstudio",
    "lmstudio-openai",
    "lm-studio",
    "lm-studio-openai",
    "localai",
    "localai-openai",
    "local-openai-compatible",
    "no-auth-openai-compatible",
}
OPENAI_COMPATIBLE_API_SHAPES = {
    "openai-compatible",
    "openai-chat-completions",
    "chat-completions",
    *LOCAL_OPENAI_COMPATIBLE_API_SHAPES,
}
NO_AUTH_API_KEY_ENVS = {"", "none", "local", "dummy", "not_required", "not-required"}
LOCAL_HOSTNAMES = {
    "localhost",
    "host.docker.internal",
    "docker.for.mac.localhost",
    "docker.for.mac.host.internal",
}


def normalize_provider_name(provider: str | None) -> str:
    return str(provider or "").strip().lower()


def normalize_api_shape(api_shape: str | None) -> str:
    return str(api_shape or "").strip().lower()


def no_auth_api_key_env(api_key_env: str | None) -> bool:
    return str(api_key_env or "").strip().lower() in NO_AUTH_API_KEY_ENVS


def is_explicit_local_openai_provider(provider: str | None, api_shape: str | None) -> bool:
    return (
        normalize_provider_name(provider) in LOCAL_OPENAI_COMPATIBLE_PROVIDERS
        or normalize_api_shape(api_shape) in LOCAL_OPENAI_COMPATIBLE_API_SHAPES
    )


def base_url_looks_local(base_url: str | None) -> bool:
    raw = str(base_url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    hostname = (parsed.hostname or "").strip("[]").lower()
    if not hostname:
        return False
    if hostname in LOCAL_HOSTNAMES or hostname.endswith(".local"):
        return True
    if "." not in hostname:
        return True
    try:
        address = ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def should_allow_no_auth_openai_provider(
    *,
    provider: str | None,
    api_key_env: str | None,
    api_shape: str | None,
    base_url: str | None,
) -> bool:
    if is_explicit_local_openai_provider(provider, api_shape):
        return no_auth_api_key_env(api_key_env) and base_url_looks_local(base_url)
    return no_auth_api_key_env(api_key_env) and base_url_looks_local(base_url)
