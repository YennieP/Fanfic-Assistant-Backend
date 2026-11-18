"""Shared Developer API transport policy for generation and embeddings."""
from contextlib import contextmanager

from google import genai
from google.genai import types

from .base import close_provider_resource


@contextmanager
def managed_gemini_client(api_key: str):
    responses = []

    def release_responses():
        # SDK generator.close() does not explicitly close every streamed HTTP
        # response. Use the supported HTTPX response hook to retain ownership.
        pending = responses[:]
        responses.clear()
        for response in pending:
            close_provider_resource(response)

    # Milliseconds per HTTP I/O. Application retries remain outside this client;
    # this is not an end-to-end wall-clock deadline (nor Gunicorn's timeout).
    client = genai.Client(
        api_key=api_key,
        vertexai=False,
        http_options=types.HttpOptions(
            api_version='v1beta',
            timeout=120_000,
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={'event_hooks': {'response': [responses.append]}},
        ),
    )
    try:
        yield client, release_responses
    finally:
        release_responses()
        close_provider_resource(client)
