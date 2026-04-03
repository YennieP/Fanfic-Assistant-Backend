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


def log_llm_call(feature: str):
    """
    装饰 LLM 调用函数，自动记录 latency、token 用量、成功/失败。

    同时支持两种返回形式：
      1. 普通返回值（原有行为）：返回有 .usage.input_tokens / .usage.output_tokens / .model 的对象
      2. Generator（新）：stream() 末尾 yield UsageInfo sentinel，decorator 过滤后透传 str chunk

    用法（generator 形式）：
        @log_llm_call(feature="character_generate")
        def get_stream(user=None):
            return provider.stream(system_prompt, user_prompt)

        for chunk in get_stream(user=request.user):
            ...  # 只会收到 str，UsageInfo 已被过滤

    被装饰函数签名约定：
        - 可接收 keyword argument `user`（Django User 对象或 None）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            user = kwargs.get('user', None)
            user_id = user.id if user else None
            req_id = request_id_var.get() or None

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                # 函数本身（非 generator 内部）抛出的异常
                latency = int((time.monotonic() - start) * 1000)
                _emit(
                    feature=feature, model_name='',
                    prompt_tokens=0, completion_tokens=0,
                    latency_ms=latency, status='error',
                    error_message=str(e), request_id=req_id, user_id=user_id,
                )
                raise

            if inspect.isgenerator(result):
                # Generator 路径：返回包装后的 generator，logging 在迭代结束时写入
                return _wrap_generator(result, feature, start, user_id, req_id)
            else:
                # 原有同步路径
                latency = int((time.monotonic() - start) * 1000)
                _emit(
                    feature=feature,
                    model_name=getattr(result, 'model', ''),
                    prompt_tokens=getattr(result.usage, 'input_tokens', 0),
                    completion_tokens=getattr(result.usage, 'output_tokens', 0),
                    latency_ms=latency, status='success',
                    error_message='', request_id=req_id, user_id=user_id,
                )
                return result

        return wrapper
    return decorator


def _wrap_generator(gen, feature, start, user_id, req_id):
    """
    透传 str chunk，过滤 UsageInfo sentinel，迭代结束后写 LlmCallLog。
    异常发生时同样写 log（status=error）后重新抛出。
    """
    # 延迟导入，避免循环导入（generation 导入 logs，logs 不导入 generation）
    from generation.providers.base import UsageInfo

    usage_info = None
    try:
        for item in gen:
            if isinstance(item, UsageInfo):
                usage_info = item  # 捕获 sentinel，不向外 yield
            else:
                yield item         # 透传 str chunk
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        _emit(
            feature=feature, model_name='',
            prompt_tokens=0, completion_tokens=0,
            latency_ms=latency, status='error',
            error_message=str(e), request_id=req_id, user_id=user_id,
        )
        raise
    else:
        latency = int((time.monotonic() - start) * 1000)
        _emit(
            feature=feature,
            model_name=usage_info.model if usage_info else '',
            prompt_tokens=usage_info.prompt_tokens if usage_info else 0,
            completion_tokens=usage_info.completion_tokens if usage_info else 0,
            latency_ms=latency, status='success',
            error_message='', request_id=req_id, user_id=user_id,
        )


def _emit(**kwargs):
    record = logging.LogRecord(
        name='logs.llm_call', level=logging.INFO,
        pathname='', lineno=0, msg='', args=(), exc_info=None,
    )
    record.log_type = 'llm_call'
    for k, v in kwargs.items():
        setattr(record, k, v)
    logger.handle(record)