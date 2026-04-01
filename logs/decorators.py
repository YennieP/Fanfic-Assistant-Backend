import time
import uuid
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

    用法：
        @log_llm_call(feature="character_generate")
        def call_llm(prompt, user=None):
            ...  # 返回值需包含 .usage.input_tokens / .usage.output_tokens / .model
    
    被装饰函数签名约定：
        - 可接收 keyword argument `user`（Django User 对象或 None）
        - 返回值有 .usage.input_tokens、.usage.output_tokens、.model 属性
          （与 Anthropic SDK 返回格式一致，其他 SDK 接入时在此适配）
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
                latency = int((time.monotonic() - start) * 1000)
                _emit(
                    feature=feature,
                    model_name=getattr(result, 'model', ''),
                    prompt_tokens=getattr(result.usage, 'input_tokens', 0),
                    completion_tokens=getattr(result.usage, 'output_tokens', 0),
                    latency_ms=latency,
                    status='success',
                    error_message='',
                    request_id=req_id,
                    user_id=user_id,
                )
                return result
            except Exception as e:
                latency = int((time.monotonic() - start) * 1000)
                _emit(
                    feature=feature,
                    model_name='',
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=latency,
                    status='error',
                    error_message=str(e),
                    request_id=req_id,
                    user_id=user_id,
                )
                raise  # 原始异常继续向上传，不吞掉

        return wrapper
    return decorator


def _emit(**kwargs):
    record = logging.LogRecord(
        name='logs.llm_call', level=logging.INFO,
        pathname='', lineno=0, msg='', args=(), exc_info=None,
    )
    record.log_type = 'llm_call'
    for k, v in kwargs.items():
        setattr(record, k, v)
    logger.handle(record)