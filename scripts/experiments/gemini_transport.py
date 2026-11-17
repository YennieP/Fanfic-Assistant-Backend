"""Fixed-endpoint Gemini REST transport with explicit zero retry policy."""

import json
import time
from typing import Any

import httpx

from . import outline_runner as base

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash"
MAX_RESPONSE_BYTES = 1_048_576


class TransportError(Exception):
    def __init__(self, code, metadata):
        self.code, self.metadata = code, metadata
        super().__init__(code)


def redact(value, key):
    """Validate decoded response strings and redact identifiable credentials."""
    found = False

    def visit(v):
        nonlocal found
        if isinstance(v, str):
            # JSON escapes may decode to lone surrogates despite UTF-8 wire bytes.
            v.encode("utf-8")
            if key in v:
                found = True
                return v.replace(key, "[REDACTED_CREDENTIAL]")
            return v
        if isinstance(v, list):
            return [visit(x) for x in v]
        if isinstance(v, dict):
            return {visit(k): visit(x) for k, x in v.items()}
        return v

    return visit(value), lambda: found


def unique_response_members(pairs):
    value = {}
    for name, member in pairs:
        if name in value:
            # Discarding an earlier member could hide a Key from redaction.
            raise ValueError("duplicate_response_member")
        value[name] = member
    return value


class GeminiTransport:
    def __init__(self, key, timeout, client_factory=None):
        self.key = key
        self.timeout = timeout
        transport = httpx.HTTPTransport(retries=0, trust_env=False)
        factory = client_factory or httpx.Client
        try:
            self.client = factory(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )
        except BaseException:
            transport.close()
            raise
        self.owned_transport = transport

    def close(self):
        self.client.close()
        self.owned_transport.close()

    def request(self, endpoint, body):
        if endpoint not in ("countTokens", "generateContent"):
            raise base.GuardError("unsupported_experiment_endpoint")
        began = time.perf_counter()
        metadata = {
            "endpoint": endpoint,
            "http_status": None,
            "response_sha256": None,
            "response_bytes": 0,
            "elapsed_ms": None,
        }
        try:
            raw = bytearray()
            self.client.cookies.clear()
            with self.client.stream(
                "POST",
                API_ROOT + ":" + endpoint,
                headers={"x-goog-api-key": self.key, "accept-encoding": "identity"},
                json=body,
            ) as response:
                metadata["http_status"] = response.status_code
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise TransportError("encoded_response_unsupported", metadata)
                for chunk in response.iter_raw():
                    if time.perf_counter() - began > self.timeout:
                        raise TransportError("provider_request_deadline", metadata)
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        metadata["response_bytes"] = len(raw) + len(chunk)
                        raise TransportError("response_too_large", metadata)
                    raw.extend(chunk)
            metadata.update(response_sha256=base.digest(raw), response_bytes=len(raw))
            if response.status_code != 200:
                codes = {
                    401: "credential_rejected",
                    403: "permission_rejected",
                    429: "rate_limited",
                }
                code = (
                    "redirect_rejected"
                    if 300 <= response.status_code < 400
                    else codes.get(response.status_code, "provider_http_error")
                )
                # Error text can echo credentials; retain only status/size/hash.
                raise TransportError(code, metadata)
            try:
                value = json.loads(raw, object_pairs_hook=unique_response_members)
                if not isinstance(value, dict):
                    raise base.GuardError("provider_response_invalid")
                clean, detected = redact(value, self.key)
            except (base.GuardError, ValueError, UnicodeError, RecursionError):
                raise TransportError("provider_response_invalid", metadata) from None
            metadata["credential_redacted"] = detected()
            metadata["response_json"] = clean
            if detected():
                raise TransportError("credential_exposure", metadata)
            try:
                metadata["response_body_utf8"] = bytes(raw).decode("utf-8")
            except UnicodeError:
                raise TransportError("provider_response_invalid", metadata) from None
            return clean, metadata
        except httpx.TimeoutException:
            raise TransportError("provider_timeout", metadata) from None
        except httpx.HTTPError:
            raise TransportError("provider_connection_failed", metadata) from None
        except httpx.StreamError:
            raise TransportError("provider_stream_invalid", metadata) from None
        finally:
            metadata["elapsed_ms"] = (time.perf_counter() - began) * 1000


def generation_body(prompt, config):
    body = {
        "systemInstruction": {"parts": [{"text": prompt["system"]}]},
        "contents": [{"role": "user", "parts": [{"text": prompt["user"]}]}],
        "generationConfig": {
            "temperature": config["temperature"],
            "topP": config["top_p"],
            "maxOutputTokens": config["max_output_tokens"],
            "candidateCount": 1,
            "responseMimeType": "text/plain",
            "thinkingConfig": {"thinkingBudget": 0, "includeThoughts": False},
        },
    }
    if config["seed"] is not None:
        body["generationConfig"]["seed"] = config["seed"]
    return body


def integer(value):
    if type(value) is not int or not 0 <= value <= 2**31 - 1:
        raise base.GuardError("provider_usage_invalid")
    return value


def parse_generation(body, expected_model_version, counted_input, max_output):
    feedback = body.get("promptFeedback")
    block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
    blocked = isinstance(block_reason, str) and block_reason not in (
        "",
        "BLOCK_REASON_UNSPECIFIED",
    )
    result = {
        "text": "",
        "status": "ok",
        "finish_reason": "UNKNOWN",
        "usage": {"prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "model_version": body.get("modelVersion"),
        "fatal": False,
    }
    try:
        if not isinstance(body.get("modelVersion"), str) or not body["modelVersion"]:
            raise base.GuardError("model_version_unavailable")
        if body["modelVersion"] != expected_model_version:
            raise base.GuardError("model_version_mismatch")
        usage = body.get("usageMetadata")
        if not isinstance(usage, dict):
            raise base.GuardError("provider_usage_unavailable")
        prompt = integer(usage.get("promptTokenCount"))
        candidates_tokens = integer(usage.get("candidatesTokenCount", 0))
        thoughts = integer(usage.get("thoughtsTokenCount", 0))
        total = integer(usage.get("totalTokenCount"))
        tools = integer(usage.get("toolUsePromptTokenCount", 0))
        if total < prompt + candidates_tokens + thoughts + tools:
            raise base.GuardError("provider_usage_invalid")
        billed_output = max(total - prompt, candidates_tokens + thoughts)
        result["usage"] = {
            "prompt_tokens": prompt,
            "output_tokens": billed_output,
            "total_tokens": total,
        }
        result["usage_details"] = {
            "candidates_tokens": candidates_tokens,
            "thoughts_tokens": thoughts,
            "unattributed_tokens": total
            - prompt
            - candidates_tokens
            - thoughts
            - tools,
        }
        if thoughts:
            raise base.GuardError("unexpected_thinking_tokens")
        if prompt > counted_input or billed_output > max_output or tools:
            raise base.GuardError("reported_usage_exceeds_approved_bounds")
        candidates = body.get("candidates")
        if not candidates:
            feedback = body.get("promptFeedback")
            if isinstance(feedback, dict) and feedback.get("blockReason"):
                result.update(
                    status="refused", finish_reason=str(feedback["blockReason"])
                )
                return result
            raise base.GuardError("provider_candidates_invalid")
        if (
            not isinstance(candidates, list)
            or len(candidates) != 1
            or not isinstance(candidates[0], dict)
        ):
            raise base.GuardError("provider_candidates_invalid")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if not isinstance(finish, str):
            raise base.GuardError("provider_finish_reason_unavailable")
        result["finish_reason"] = finish
        content = candidate.get("content", {})
        if not isinstance(content, dict):
            raise base.GuardError("provider_parts_invalid")
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            raise base.GuardError("provider_parts_invalid")
        texts = []
        for part in parts:
            if (
                not isinstance(part, dict)
                or not isinstance(part.get("text"), str)
                or part.get("thought")
            ):
                raise base.GuardError("unsupported_provider_part")
            texts.append(part["text"])
        result["text"] = "".join(texts)
        result["text"].encode()
        if finish == "MAX_TOKENS":
            result["status"] = "truncated"
        elif finish in (
            "SAFETY",
            "RECITATION",
            "BLOCKLIST",
            "PROHIBITED_CONTENT",
            "SPII",
        ):
            result["status"] = "refused"
        elif finish != "STOP":
            result["status"] = "finish_error"
        return result
    except (base.GuardError, UnicodeError) as error:
        result.update(
            status="refused" if blocked else "response_invalid",
            error_code=(
                str(error)
                if isinstance(error, base.GuardError)
                else "provider_text_invalid"
            ),
            fatal=True,
        )
        if blocked:
            result["finish_reason"] = block_reason
        # Keep valid textual model output when available, but never expose unsupported parts.
        return result
