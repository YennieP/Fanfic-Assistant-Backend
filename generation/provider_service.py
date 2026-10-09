from dataclasses import dataclass
import logging

from django.core.exceptions import ObjectDoesNotExist

from users.encryption import decrypt_key
from users.models import UserProviderKey

from .providers import get_provider
from .providers.base import BaseProvider, ProviderError
from .providers.catalog import ProviderDefinition, get_provider_definition


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedProvider:
    name: str
    definition: ProviderDefinition
    instance: BaseProvider


def resolve_active_provider(user) -> ResolvedProvider:
    try:
        provider_name = user.llm_config.provider
    except ObjectDoesNotExist:
        raise ProviderError(
            '未配置 API Key，请在设置页配置',
            code='no_api_key',
            http_status=400,
        ) from None

    definition = get_provider_definition(provider_name)
    if definition is None:
        raise ProviderError(
            '当前配置的 provider 不可用',
            code='provider_model_unavailable',
            http_status=503,
        ) from None

    try:
        key_obj = UserProviderKey.objects.get(
            user=user,
            provider=provider_name,
        )
    except UserProviderKey.DoesNotExist:
        raise ProviderError(
            '未配置 API Key，请在设置页配置',
            code='no_api_key',
            http_status=400,
        ) from None

    try:
        api_key = decrypt_key(key_obj.api_key_encrypted)
    except Exception as exc:
        logger.error(
            'Provider key decryption failed provider=%s type=%s',
            provider_name,
            type(exc).__name__,
        )
        raise ProviderError(
            'Provider 配置暂时不可用，请稍后重试',
            code='provider_temporarily_unavailable',
            http_status=503,
        ) from None

    try:
        instance = get_provider(provider_name, api_key)
    except ProviderError:
        raise
    except Exception as exc:
        logger.error(
            'Provider construction failed provider=%s type=%s',
            provider_name,
            type(exc).__name__,
        )
        raise ProviderError(
            'Provider 配置暂时不可用，请稍后重试',
            code='provider_temporarily_unavailable',
            http_status=503,
        ) from None

    return ResolvedProvider(
        name=provider_name,
        definition=definition,
        instance=instance,
    )
