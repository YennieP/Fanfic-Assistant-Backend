"""
core/handler500.py — JSON 格式的 500 handler

使用场景：DRF EXCEPTION_HANDLER 覆盖所有 APIView 路径；
本 handler 覆盖非 DRF 路径（中间件崩溃、URL 路由失败等边缘情况）。

在 core/urls.py 中注册：
    from core.handler500 import server_error
    handler500 = 'core.handler500.server_error'
"""
import json
import logging
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def server_error(request, *args, **kwargs):
    logger.error('handler500 triggered for %s %s', request.method, request.path)
    return JsonResponse(
        {'code': 'internal_error', 'detail': 'An unexpected error occurred'},
        status=500,
    )