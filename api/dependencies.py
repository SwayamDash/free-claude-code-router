"""Dependency injection for FastAPI."""

from typing import Optional
from config.settings import Settings, get_settings as _get_settings, NVIDIA_NIM_BASE_URL
from providers.base import BaseProvider, ProviderConfig


# Global provider instance (singleton)
_provider: Optional[BaseProvider] = None


def get_settings() -> Settings:
    """Get application settings via dependency injection."""
    return _get_settings()


def _build_nim_extra_params(settings: Settings) -> dict:
    """Build NIM-specific extra_params from settings."""
    params = {
        "temperature": settings.nvidia_nim_temperature,
        "top_p": settings.nvidia_nim_top_p,
        "max_tokens": settings.nvidia_nim_max_tokens,
    }
    # Only include non-default values to avoid overriding request-level settings
    if settings.nvidia_nim_top_k != -1:
        params["top_k"] = settings.nvidia_nim_top_k
    if settings.nvidia_nim_presence_penalty != 0.0:
        params["presence_penalty"] = settings.nvidia_nim_presence_penalty
    if settings.nvidia_nim_frequency_penalty != 0.0:
        params["frequency_penalty"] = settings.nvidia_nim_frequency_penalty
    if settings.nvidia_nim_min_p != 0.0:
        params["min_p"] = settings.nvidia_nim_min_p
    if settings.nvidia_nim_repetition_penalty != 1.0:
        params["repetition_penalty"] = settings.nvidia_nim_repetition_penalty
    if settings.nvidia_nim_seed is not None:
        params["seed"] = settings.nvidia_nim_seed
    if settings.nvidia_nim_stop:
        params["stop"] = settings.nvidia_nim_stop
    return params


def get_provider() -> BaseProvider:
    """Get or create the provider instance based on settings.provider_type."""
    global _provider
    if _provider is None:
        settings = get_settings()

        if settings.provider_type == "nvidia_nim":
            from providers.nvidia_nim import NvidiaNimProvider

            config = ProviderConfig(
                api_key=settings.nvidia_nim_api_key,
                base_url=NVIDIA_NIM_BASE_URL,
                rate_limit=settings.nvidia_nim_rate_limit,
                rate_window=settings.nvidia_nim_rate_window,
                extra_params=_build_nim_extra_params(settings),
            )
            _provider = NvidiaNimProvider(config)
        else:
            raise ValueError(
                f"Unknown provider_type: '{settings.provider_type}'. "
                f"Supported: 'nvidia_nim'"
            )
    return _provider


async def cleanup_provider():
    """Cleanup provider resources."""
    global _provider
    if _provider:
        client = getattr(_provider, "_client", None)
        if client and hasattr(client, "aclose"):
            await client.aclose()
    _provider = None
