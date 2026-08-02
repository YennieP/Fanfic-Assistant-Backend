"""测试专用 settings。

设计目标（见 测试模块-设计与落地方案.md §4）：
- SQLite（:memory:）：快、零基础设施。P0 测试刻意不碰向量路径，无需 Postgres+pgvector。
- locmem cache：限流测试可控、测试间可 cache.clear() 隔离。
- 固定 ENCRYPTION_KEY / SECRET_KEY：无需任何外部 .env 或 CI secret，开箱即跑。

注意：users/encryption.py 直接读 os.environ['ENCRYPTION_KEY']（不走 Django settings），
所以必须在 import core.settings 之前就把它放进 os.environ。
"""
import base64
import os

# 固定的测试用 Fernet key（32 字节 base64）。仅用于测试，绝不用于生产。
os.environ.setdefault('ENCRYPTION_KEY', base64.urlsafe_b64encode(b'0' * 32).decode())
os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')

from core.settings import *  # noqa: E402,F401,F403

# 显式兜底，确保即便外部环境未设也有值。
SECRET_KEY = 'test-secret-key-not-for-production'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-locmem',
    }
}

# 测试不需要安全哈希，用最快的 hasher 提速。
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# 摘掉 REST API 请求日志中间件：它把日志丢进内存队列，由后台线程异步写
# RestApiLog 到 DB。在 SQLite 测试库里跨线程写会撞 "database table is locked"，
# 产生与被测逻辑无关的噪音 traceback。该中间件不属于被测范围（纯基础设施日志），
# 摘掉后 request_id_var 仅为 None，log_llm_call / _log_vector_search 均已 .get(None) 兜底。
# 同理摘掉 WhiteNoise（静态文件服务，不在被测范围），消除 staticfiles 目录缺失警告。
_DROP_MIDDLEWARE = {
    'logs.middleware.RequestLoggingMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
}
MIDDLEWARE = [m for m in MIDDLEWARE if m not in _DROP_MIDDLEWARE]  # noqa: F405
