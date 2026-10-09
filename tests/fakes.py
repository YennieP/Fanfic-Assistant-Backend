"""测试替身：FakeProvider。

实现 BaseProvider 契约，不联网、不花钱、确定可复现。
通过 monkeypatch 替换调用方的 `resolve_active_provider`，
注入到生成链路，替换真实 LLM provider。

三种用法：
- 成功链路：默认构造，stream 吐固定 chunks
- 错误码  ：FakeProvider(error=ProviderError('x', code='provider_key_invalid'))
- 评估链路：FakeProvider(complete_text='{"score": 8, "reasoning": "ok"}')
"""
from generation.providers.base import (
    BaseProvider,
    CompleteResult,
    UsageInfo,
)


class FakeProvider(BaseProvider):
    supports_embedding = True

    def __init__(
        self,
        api_key='x',
        chunks=None,
        error=None,
        complete_text='{"score": 8, "reasoning": "ok"}',
    ):
        super().__init__(api_key)
        self._chunks = chunks if chunks is not None else ['你好', '，', '世界']
        self._error = error
        self._complete_text = complete_text

    def stream(self, system_prompt, user_prompt):
        if self._error:
            raise self._error
        for c in self._chunks:
            yield c
        # 末尾 yield UsageInfo sentinel —— 与真实 provider 契约一致，
        # log_llm_call 装饰器据此过滤用量、写 LlmCallLog。
        yield UsageInfo(model='fake', prompt_tokens=10, completion_tokens=3)

    def complete(self, system_prompt, user_prompt, max_tokens=2000):
        if self._error:
            raise self._error
        return CompleteResult(
            text=self._complete_text,
            model='fake',
            prompt_tokens=10,
            completion_tokens=5,
        )
