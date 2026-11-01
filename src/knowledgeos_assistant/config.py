"""Gateway configuration from environment variables (no secrets are ever logged or returned)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

PROVIDERS = ("anthropic", "openai_compatible", "demo")
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai_compatible": "", "demo": "demo-fixed-examples"}
DEFAULT_BASE_URLS = {"anthropic": "https://api.anthropic.com", "openai_compatible": "", "demo": ""}


class ConfigurationError(ValueError):
    pass


def _flag(value, default):
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class GatewayConfig:
    kernel_url: str
    provider: str | None = None
    model: str = ""
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    timeout: int = 120
    max_tokens: int = 8000
    max_repairs: int = 2
    json_mode: bool = True
    send_document_samples: bool = True
    requests_per_minute: int = 20

    @property
    def configured(self):
        if self.provider == "demo":
            return True
        return bool(self.provider and self.model and self.api_key and self.base_url)

    def describe(self):
        return {"configured": self.configured, "provider": self.provider, "model": self.model or None,
                "demo": self.provider == "demo", "send_document_samples": self.send_document_samples,
                "max_repairs": self.max_repairs}

    @classmethod
    def from_env(cls, environ=None):
        env = environ if environ is not None else os.environ
        kernel = (env.get("KNOWLEDGEOS_API_BASE_URL") or "http://127.0.0.1:8787").strip()
        provider = (env.get("KNOWLEDGEOS_ASSISTANT_PROVIDER") or "").strip() or None
        if provider and provider not in PROVIDERS:
            raise ConfigurationError(f"KNOWLEDGEOS_ASSISTANT_PROVIDER must be one of {', '.join(PROVIDERS)}")
        try:
            timeout = int(env.get("KNOWLEDGEOS_ASSISTANT_TIMEOUT") or 120)
            max_tokens = int(env.get("KNOWLEDGEOS_ASSISTANT_MAX_TOKENS") or 8000)
            max_repairs = int(env.get("KNOWLEDGEOS_ASSISTANT_MAX_REPAIRS") or 2)
            rpm = int(env.get("KNOWLEDGEOS_ASSISTANT_REQUESTS_PER_MINUTE") or 20)
        except ValueError as exc:
            raise ConfigurationError("assistant numeric settings must be integers") from exc
        if not 5 <= timeout <= 600 or not 256 <= max_tokens <= 64000 or not 0 <= max_repairs <= 4 or not 1 <= rpm <= 600:
            raise ConfigurationError("assistant numeric settings are out of range")
        return cls(kernel_url=kernel, provider=provider,
                   model=(env.get("KNOWLEDGEOS_ASSISTANT_MODEL") or DEFAULT_MODELS.get(provider or "", "")).strip(),
                   api_key=(env.get("KNOWLEDGEOS_ASSISTANT_API_KEY") or "").strip(),
                   base_url=(env.get("KNOWLEDGEOS_ASSISTANT_BASE_URL") or DEFAULT_BASE_URLS.get(provider or "", "")).strip().rstrip("/"),
                   timeout=timeout, max_tokens=max_tokens, max_repairs=max_repairs,
                   json_mode=_flag(env.get("KNOWLEDGEOS_ASSISTANT_JSON_MODE"), True),
                   send_document_samples=_flag(env.get("KNOWLEDGEOS_ASSISTANT_SEND_DOCUMENT_SAMPLES"), True),
                   requests_per_minute=rpm)
