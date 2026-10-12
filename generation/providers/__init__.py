"""Provider factory backed by the single catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog import get_provider_definition


if TYPE_CHECKING:
    from .base import BaseProvider


def get_provider(name: str, api_key: str) -> BaseProvider:
    """Construct a known provider; unknown names fail closed."""
    # Keep catalog imports usable by dependency-free operational scripts.
    # Django and provider runtime types are required only when constructing an
    # implementation, not when reading the pure-data catalog.
    from django.utils.module_loading import import_string
    from .base import ProviderError

    definition = get_provider_definition(name)
    if definition is None:
        raise ProviderError(
            '当前配置的 provider 不可用',
            code='provider_model_unavailable',
            http_status=503,
        ) from None
    provider_cls = import_string(definition.provider_class)
    return provider_cls(api_key)
