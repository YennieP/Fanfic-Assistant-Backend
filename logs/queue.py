import logging
import queue
import atexit
from django.db import close_old_connections

# 内存队列，主线程只往这里扔，后台线程消费写 DB
_log_queue: queue.Queue = queue.Queue()
_listener = None


class _DbHandler(logging.Handler):
    """消费队列，将日志记录写入 PostgreSQL"""

    def emit(self, record):
        close_old_connections()
        try:
            log_type = getattr(record, 'log_type', None)
            if log_type == 'rest_api':
                self._write_rest(record)
            elif log_type == 'llm_call':
                self._write_llm(record)
        except Exception:
            # 写入失败只记录 warning，不影响主业务
            self.handleError(record)

    def _write_rest(self, record):
        from .models import RestApiLog
        RestApiLog.objects.create(
            request_id=record.request_id,
            user_id=record.user_id,
            method=record.http_method,
            path=record.path,
            status_code=record.status_code,
            latency_ms=record.latency_ms,
        )

    def _write_llm(self, record):
        from .models import LlmCallLog
        LlmCallLog.objects.create(
            request_id=record.request_id or None,
            user_id=record.user_id,
            feature=record.feature,
            model=record.model_name,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            latency_ms=record.latency_ms,
            status=record.status,
            error_message=record.error_message,
        )


def start_log_listener():
    """在 AppConfig.ready() 里调用，启动后台消费线程"""
    global _listener
    if _listener is not None:
        return  # 防止 Django dev server 热重载时重复启动

    from logging.handlers import QueueHandler, QueueListener
    handler = _DbHandler()
    _listener = QueueListener(_log_queue, handler, respect_handler_level=True)
    _listener.start()
    atexit.register(_listener.stop)  # 进程退出时 flush 队列再停止


def get_queue_handler() -> logging.Handler:
    """返回一个往内存队列写的 handler，供 middleware 和 decorator 使用"""
    from logging.handlers import QueueHandler
    return QueueHandler(_log_queue)