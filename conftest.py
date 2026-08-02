"""全局 pytest fixture。

提供三类复用件：
- api_client        : DRF APIClient（接口测试入口）
- user_factory      : factory_boy User 工厂
- make_user_with_key: 造一个「带 LLM 配置 + 已加密 provider key」的可生成用户
- _clear_cache      : 每个测试前后清 locmem cache，隔离限流计数

导入一律延迟到 fixture 函数体内，避免 conftest 模块导入期依赖 Django app registry。
"""
import pytest


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture(autouse=True)
def _clear_cache():
    """限流计数存在 cache 里，跨测试会污染 —— 每个测试前后都清。"""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user_factory(db):
    from tests.factories import UserFactory
    return UserFactory


@pytest.fixture
def make_user_with_key(db):
    """返回一个工厂函数：造 User + UserLLMConfig + 已加密 UserProviderKey。

    用法：user = make_user_with_key(provider='gemini')
    """
    from tests.factories import UserFactory
    from users.models import UserLLMConfig, UserProviderKey
    from users.encryption import encrypt_key

    def _make(provider='gemini', api_key='fake-test-key'):
        user = UserFactory()
        UserLLMConfig.objects.create(user=user, provider=provider)
        UserProviderKey.objects.create(
            user=user,
            provider=provider,
            api_key_encrypted=encrypt_key(api_key),
        )
        return user

    return _make
