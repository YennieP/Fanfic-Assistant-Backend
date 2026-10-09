from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    provider_class: str
    model: str
    supports_video: bool = False
    supports_embedding: bool = False


PROVIDER_CATALOG = MappingProxyType({
    'anthropic': ProviderDefinition(
        name='anthropic',
        provider_class='generation.providers.anthropic.AnthropicProvider',
        model='claude-sonnet-4-6',
    ),
    'gemini': ProviderDefinition(
        name='gemini',
        provider_class='generation.providers.gemini.GeminiProvider',
        model='gemini-2.5-flash',
        supports_video=True,
        supports_embedding=True,
    ),
    'groq': ProviderDefinition(
        name='groq',
        provider_class='generation.providers.groq.GroqProvider',
        model='openai/gpt-oss-120b',
    ),
    'cerebras': ProviderDefinition(
        name='cerebras',
        provider_class='generation.providers.cerebras.CerebrasProvider',
        model='gpt-oss-120b',
    ),
    'openrouter': ProviderDefinition(
        name='openrouter',
        provider_class='generation.providers.openrouter.OpenRouterProvider',
        model='meta-llama/llama-3.3-70b-instruct:free',
    ),
})


def get_provider_definition(name: str) -> ProviderDefinition | None:
    return PROVIDER_CATALOG.get(name)


def provider_names() -> tuple[str, ...]:
    return tuple(PROVIDER_CATALOG)
