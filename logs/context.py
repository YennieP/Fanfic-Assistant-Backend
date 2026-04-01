from contextvars import ContextVar

# 全局 ContextVar，每个执行上下文（线程/协程/Celery task）独立隔离
# Middleware 写入，service 层读取，零耦合
request_id_var: ContextVar[str] = ContextVar('request_id', default='')