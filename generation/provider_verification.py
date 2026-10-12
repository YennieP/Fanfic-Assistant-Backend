"""Low-cost functional verification for one provider credential.

The probe deliberately asks for a tiny text response. It proves that the
credential and the catalog model can complete a real request without coupling
verification to any product JSON schema. Prompts, responses, and keys are not
logged or persisted here.
"""

import logging

from .providers import get_provider
from .providers.base import CompleteResult, CompletionOptions, ProviderError
from .providers.catalog import get_provider_definition


logger = logging.getLogger(__name__)

VERIFICATION_OPTIONS = CompletionOptions(
    response_format='text',
    max_tokens=64,
    reasoning_effort='low',
)


def verify_provider_api_key(provider_name: str, api_key: str) -> CompleteResult:
    definition = get_provider_definition(provider_name)
    if definition is None:
        raise ProviderError(
            '当前配置的 provider 不可用',
            code='provider_model_unavailable',
            http_status=503,
        )

    try:
        provider = get_provider(provider_name, api_key)
        return provider.complete(
            'You are a provider connectivity check. Reply briefly.',
            'Reply with OK.',
            options=VERIFICATION_OPTIONS,
        )
    except ProviderError:
        raise
    except Exception as exc:
        logger.error(
            'Provider verification failed provider=%s type=%s',
            provider_name,
            type(exc).__name__,
        )
        raise ProviderError(
            'Provider 暂时不可用，请稍后重试',
            code='provider_temporarily_unavailable',
            http_status=503,
        ) from None
