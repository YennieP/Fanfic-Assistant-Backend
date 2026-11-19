from django.apps import AppConfig
import logging


class GenerationConfig(AppConfig):
    name = 'generation'

    def ready(self):
        # Install the standard routing before request threads start. The console
        # filter sanitizes only the known SDK enum warning; no warning is muted.
        logging.captureWarnings(True)
