"""Adapter registry.

The only place that knows which selector profile and which base URL a platform
should use. Pointing the whole agent at the mock storefront is a config change
here — `AgentConfig.browser.base_url_override` — and nothing in the runner or
the adapters is aware that it happened.
"""

from __future__ import annotations

from ..config import AgentConfig
from ..models import Platform
from . import amazon, flipkart
from .base import AdapterContext, LoginRequired, OrderNotFound, PlatformAdapter, Selectors

LIVE_BASE_URLS = {
    Platform.FLIPKART: "https://www.flipkart.com",
    Platform.AMAZON: "https://www.amazon.in",
}

_ADAPTERS = {
    Platform.FLIPKART: (flipkart.FlipkartAdapter, flipkart.LIVE_SELECTORS, flipkart.MOCK_SELECTORS),
    Platform.AMAZON: (amazon.AmazonAdapter, amazon.LIVE_SELECTORS, amazon.MOCK_SELECTORS),
}


def build_adapter(platform: Platform, ctx: AdapterContext, config: AgentConfig) -> PlatformAdapter:
    if platform not in _ADAPTERS:
        raise KeyError(f"no adapter registered for {platform}")

    adapter_cls, live_selectors, mock_selectors = _ADAPTERS[platform]
    override = config.browser.base_url_override.get(platform.value)

    if override:
        # Mock storefront: it is built to the mock selector profile on purpose,
        # so the flow logic under test is the same code the live run executes.
        return adapter_cls(ctx, mock_selectors, override)

    return adapter_cls(ctx, live_selectors, LIVE_BASE_URLS[platform])


__all__ = [
    "AdapterContext",
    "LoginRequired",
    "OrderNotFound",
    "PlatformAdapter",
    "Selectors",
    "build_adapter",
]
