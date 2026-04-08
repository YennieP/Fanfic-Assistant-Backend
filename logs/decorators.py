import time
import inspect
import logging
from functools import wraps
from .context import request_id_var
from .queue import get_queue_handler

logger = logging.getLogger('logs.llm_call')
logger.addHandler(get_queue_handler())
logger.setLevel(logging.INFO)
logger.propagate = False


def log_llm_call(feature: str, sync: bool = False):
    """
    装饰 LLM 调用函数，自动记录 latency、token 用量、成功/失败。

    参数：
        feature: 功能模块枚举，例：character_generate、consistency_check
        sync:    True 时绕过 QueueHandler，在迭代结束后同步写入数据库。
                 用于需要在 yield done 之前保证落库的 SSE streaming 场景，
                 以及需要在写入后立即按 generation_id 查询记录的场景。

    支持两种返回形式：
      1. Generator（stream 路径）：末尾 yield UsageInfo sentinel，
         decorator 过滤 sentinel、透传 str chunk，迭代结束后写 LlmCallLog。
      2. CompleteResult（complete 路径）：decorator 提取 text 返回给调用方，
         用量信息写 LlmCallLog。

    被装饰函数可接收 keyword argument：
      user:          Django User 对象或 None
      generation_id: UUID，写入 LlmCallLog.generation_id，用于关联评估记录
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            user = kwargs.get('user', None)
            generation_id = kwargs.get('generation_id', None)
            user_id = user.id if user else None
            req_id = request_id_var.get() or None

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                latency = int((time.monotonic() - start) * 1000)
                _log(
                    sync=sync, generation_id=generation_id,
                    feature=feature, model_name='',
                    prompt_tokens=0, completion_tokens=0,
                    latency_ms=latency, status='error',
                    error_message=str(e), request_id=req_id, user_id=user_id,
                )
                raise

            if inspect.isgenerator(result):
                return _wrap_generator(
                    result, feature, start, user_id, req_id, sync, generation_id
                )

            # CompleteResult 路径（complete() 调用）
            from generation.providers.base import CompleteResult
            latency = int((time.monotonic() - start) * 1000)
            if isinstance(result, CompleteResult):
                _log(
                    sync=sync, generation_id=generation_id,
                    feature=feature, model_name=result.model,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    latency_ms=latency, status='success',
                    error_message='', request_id=req_id, user_id=user_id,
                )
                return result.text  # 调用方只拿到文本
            else:
                # 旧同步路径（兼容保留，不应再有新调用走这里）
                _log(
                    sync=sync, generation_id=generation_id,
                    feature=feature,
                    model_name=getattr(result, 'model', ''),
                    prompt_tokens=getattr(getattr(result, 'usage', None), 'input_tokens', 0),
                    completion_tokens=getattr(getattr(result, 'usage', None), 'output_tokens', 0),
                    latency_ms=latency, status='success',
                    error_message='', request_id=req_id, user_id=user_id,
                )
                return result

        return wrapper
    return decorator


def _wrap_generator(gen, feature, start, user_id, req_id, sync, generation_id):
    """
    透传 str chunk，过滤 UsageInfo sentinel，迭代结束后写 LlmCallLog。

    时序保证（sync=True）：
      _write_sync 在最后一个 chunk yield 之后、_wrap_generator 返回之前执行。
      _wrap_generator 返回后，event_stream() 才执行 yield done 事件。
      因此前端收到 done 时，LlmCallLog 已落库，generationId 可立即用于查询。
    """
    from generation.providers.base import UsageInfo

    usage_info = None
    try:
        for item in gen:
            if isinstance(item, UsageInfo):
                usage_info = item
            else:
                yield item
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        _log(
            sync=sync, generation_id=generation_id,
            feature=feature, model_name='',
            prompt_tokens=0, completion_tokens=0,
            latency_ms=latency, status='error',
            error_message=str(e), request_id=req_id, user_id=user_id,
        )
        raise
    else:
        latency = int((time.monotonic() - start) * 1000)
        _log(
            sync=sync, generation_id=generation_id,
            feature=feature,
            model_name=usage_info.model if usage_info else '',
            prompt_tokens=usage_info.prompt_tokens if usage_info else 0,
            completion_tokens=usage_info.completion_tokens if usage_info else 0,
            latency_ms=latency, status='success',
            error_message='', request_id=req_id, user_id=user_id,
        )


def _log(sync: bool, generation_id, **kwargs):
    """路由到同步写库或异步队列。"""
    if sync:
        _write_sync(generation_id=generation_id, **kwargs)
    else:
        _emit(**kwargs)


def _write_sync(generation_id=None, **kwargs):
    """
    直接 ORM 写入 LlmCallLog，绕过 QueueHandler。
    调用返回时记录已落库，调用方可立即按 generation_id 查询。
    """
    from logs.models import LlmCallLog
    LlmCallLog.objects.create(
        request_id=kwargs.get('request_id'),
        user_id=kwargs.get('user_id'),
        feature=kwargs.get('feature', ''),
        model=kwargs.get('model_name', ''),
        prompt_tokens=kwargs.get('prompt_tokens', 0),
        completion_tokens=kwargs.get('completion_tokens', 0),
        latency_ms=kwargs.get('latency_ms', 0),
        status=kwargs.get('status', 'success'),
        error_message=kwargs.get('error_message', ''),
        generation_id=generation_id,
    )


def _emit(**kwargs):
    """把日志对象扔进内存队列，由 QueueListener 后台写入。"""
    record = logging.LogRecord(
        name='logs.llm_call', level=logging.INFO,
        pathname='', lineno=0, msg='', args=(), exc_info=None,
    )
    record.log_type = 'llm_call'
    for k, v in kwargs.items():
        setattr(record, k, v)
    logger.handle(record)