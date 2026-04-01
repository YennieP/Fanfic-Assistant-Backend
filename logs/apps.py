from django.apps import AppConfig


class LogsConfig(AppConfig):
    name = 'logs'

    def ready(self):
        # Django 启动完成后启动后台写入线程
        from .queue import start_log_listener
        start_log_listener()