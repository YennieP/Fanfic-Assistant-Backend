"""LLM provider 包。

对外暴露 get_provider() —— provider 实例的**唯一构造点**。
views.py 不再硬编码 if/elif new 各家 provider，而是统一走这个工厂，
从而获得一个干净的可打桩接缝：测试通过
`monkeypatch.setattr('generation.views.get_provider', ...)` 注入 FakeProvider，
保证所有 LLM 相关测试不联网、不花钱、确定可复现。
"""
from .base import BaseProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .groq import GroqProvider
from .cerebras import CerebrasProvider
from .openrouter import OpenRouterProvider

# provider 名 → 实现类。未知名回退 GeminiProvider，保持原 if/elif 的兜底行为。
_REGISTRY = {
    'anthropic': AnthropicProvider,
    'groq': GroqProvider,
    'cerebras': CerebrasProvider,
    'openrouter': OpenRouterProvider,
    'gemini': GeminiProvider,
}


def get_provider(name: str, api_key: str) -> BaseProvider:
    """按 provider 名构造实例；未知名回退 Gemini（与重构前行为一致）。"""
    provider_cls = _REGISTRY.get(name, GeminiProvider)
    return provider_cls(api_key)
