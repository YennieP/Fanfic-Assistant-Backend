"""
core/exceptions.py — 全局 DRF 异常处理器

职责分工：
  - 本文件：兜底捕获各 view 未处理的异常（OperationalError、未预期错误）
  - generation/views.py：SSE 流式错误（必须在 generator 内部捕获，发出 SSE error 事件）
  - 各 view：业务错误（DoesNotExist、参数校验等）保持原有处理

错误响应格式统一为 {"code": "...", "detail": "..."}，便于前端识别错误类型。
SSE 路径的错误格式（{"type": "error", "code": "..."}）由 generation/views.py 单独维护，
不经过本处理器。
"""
import logging
from django.db import OperationalError
from rest_framework.views import exception_handler
from rest_framework.response import Response
from generation.providers.base import ProviderError

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    DRF 异常处理链：
    1. OperationalError → 503（数据库不可用）
    2. ProviderError → 400（provider 业务错误，code 透传前端）
       注意：SSE 路径中 ProviderError 由 views.py 内部捕获，不会到达这里
    3. DRF 内置异常（404/403/ValidationError 等）→ 交给 DRF 原有处理
    4. 其他未预期异常 → 500，记录完整 traceback
    """
    # ── 1. 数据库不可用 ──────────────────────────────────────────────────────
    if isinstance(exc, OperationalError):
        logger.error('Database OperationalError in %s: %s', _view_name(context), exc)
        return Response(
            {'code': 'db_unavailable', 'detail': 'Service temporarily unavailable, please try again later'},
            status=503,
        )

    # ── 2. Provider 业务错误（非 SSE 路径）──────────────────────────────────
    if isinstance(exc, ProviderError):
        logger.warning('ProviderError [%s] in %s: %s', exc.code, _view_name(context), exc)
        return Response(
            {'code': exc.code, 'detail': str(exc)},
            status=400,
        )

    # ── 3. DRF 内置异常（ValidationError / NotAuthenticated / PermissionDenied 等）
    response = exception_handler(exc, context)
    if response is not None:
        # 统一注入 code 字段，保持响应格式一致
        if isinstance(response.data, dict) and 'code' not in response.data:
            response.data['code'] = _status_to_code(response.status_code)
        return response

    # ── 4. 未预期异常：记录完整 traceback，返回通用 500 ──────────────────────
    logger.exception('Unhandled exception in %s', _view_name(context))
    return Response(
        {'code': 'internal_error', 'detail': 'An unexpected error occurred'},
        status=500,
    )


def _view_name(context: dict) -> str:
    """从 context 提取 view 类名，用于日志定位。"""
    view = context.get('view')
    return type(view).__name__ if view else 'unknown'


def _status_to_code(status: int) -> str:
    return {
        400: 'bad_request',
        401: 'unauthorized',
        403: 'forbidden',
        404: 'not_found',
        405: 'method_not_allowed',
        429: 'rate_limited',
    }.get(status, f'http_{status}')