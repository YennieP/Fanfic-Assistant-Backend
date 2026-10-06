import time
import uuid
import logging
from .context import request_id_var
from .queue import get_queue_handler

logger = logging.getLogger('logs.rest_api')
logger.addHandler(get_queue_handler())
logger.setLevel(logging.INFO)
logger.propagate = False  # 不往 Django 默认 logger 传，避免重复记录

EXCLUDE_PREFIXES = [
    '/health/',
    '/admin/',
    '/api/token/',
    '/static/',
    '/media/',
]


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # 命中黑名单直接跳过
        if any(path.startswith(p) for p in EXCLUDE_PREFIXES):
            return self.get_response(request)

        # 生成 request_id，写入 ContextVar（service 层可读取）
        req_id = uuid.uuid4()
        # StreamingHttpResponse 会在中间件返回后才迭代；把 ID 同时挂到 request
        # 上，view 可显式捕获并传给延迟执行的生成器。
        request.request_id = str(req_id)
        token = request_id_var.set(str(req_id))

        start = time.monotonic()
        response = self.get_response(request)
        latency = int((time.monotonic() - start) * 1000)

        # 对普通响应是 view 处理耗时；对 StreamingHttpResponse 是流准备耗时，
        # 不包含首包等待或完整流迭代时间。
        try:
            user_id = request.user.id if request.user.is_authenticated else None
            record = logging.LogRecord(
                name='logs.rest_api', level=logging.INFO,
                pathname='', lineno=0, msg='', args=(), exc_info=None,
            )
            record.log_type = 'rest_api'
            record.request_id = req_id
            record.user_id = user_id
            record.http_method = request.method
            record.path = path
            record.status_code = response.status_code
            record.latency_ms = latency
            logger.handle(record)
        except Exception:
            pass  # 日志失败不影响响应

        request_id_var.reset(token)
        return response
