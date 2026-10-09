"""Provider factory backed by the single catalog."""

from django.utils.module_loading import import_string

from .base import BaseProvider, ProviderError
from .catalog import get_provider_definition


def get_provider(name: str, api_key: str) -> BaseProvider:
    """Construct a known provider; unknown names fail closed."""
    definition = get_provider_definition(name)
    if definition is None:
        raise ProviderError(
            '当前配置的 provider 不可用',
            code='provider_model_unavailable',
            http_status=503,
        ) from None
    provider_cls = import_string(definition.provider_class)
    return provider_cls(api_key)
