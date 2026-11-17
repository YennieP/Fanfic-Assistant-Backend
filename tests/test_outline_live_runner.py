"""Offline contracts for the independent Gemini execution entry."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import httpx
import pytest

FIXTURE = (
    Path(__file__).resolve().parents[1] / "scripts/experiments/fixtures/outline-dry-run"
)
KEY = "AIza" + "X" * 35  # Format-only dummy; never a real credential.
OWNED_IDS = {}


def module():
    from scripts.experiments import outline_live_runner

    return outline_live_runner


@pytest.fixture
def pack(tmp_path):
    task = tmp_path / "task"
    shutil.copytree(FIXTURE, task)
    return task


@pytest.fixture
def storage():
    from scripts.experiments import outline_runner

    root = outline_runner.RUNTIME_ROOT / "live-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    OWNED_IDS[str(root)] = []
    yield root
    for aid in OWNED_IDS.pop(str(root)):
        claim = outline_runner.RUNTIME_ROOT / "outline-execution-claims" / aid
        if claim.exists():
            shutil.rmtree(claim)
    shutil.rmtree(root)


def approved_plan(pack, storage):
    m = json.loads((pack / "manifest.json").read_text())
    p = module().draft_plan(pack)
    OWNED_IDS[str(storage)].append(p["authorization_id"])
    p.update(
        execution_authorized=True,
        external_transmission_authorized=True,
        expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    p["pricing"].update(
        reviewed=True, input_usd_per_million="1", output_usd_per_million="2"
    )
    p["budget"]["max_cost_usd"] = "1"
    file = storage / "execution.json"
    file.write_text(json.dumps(p))
    return file, p


def response(group="D0", **overrides):
    text = json.loads((FIXTURE / "dry-run-responses.json").read_text())["responses"][
        group
    ]["text"]
    value = {
        "modelVersion": "gemini-2.5-flash",
        "candidates": [
            {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}
        ],
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 20,
            "totalTokenCount": 120,
        },
    }
    value.update(overrides)
    return value


class Network:
    def __init__(self, modify=None):
        self.requests = []
        self.modify = modify

    def handle(self, req):
        self.requests.append(req)
        body = json.loads(req.content)
        group = "A1" if "CONTEXT_MAP" in json.dumps(body, ensure_ascii=False) else "D0"
        if req.url.path.endswith(":countTokens"):
            result = httpx.Response(200, json={"totalTokens": 100})
        else:
            result = httpx.Response(200, json=response(group))
        value = self.modify(req, result, self) if self.modify else result
        return httpx.Response(
            value.status_code,
            headers=value.headers,
            stream=httpx.ByteStream(value.content),
        )

    def factory(self, **kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return httpx.Client(
            transport=httpx.MockTransport(self.handle),
            timeout=kwargs["timeout"],
            follow_redirects=False,
            trust_env=False,
        )


def execute(pack, storage, plan, network):
    result = module().run_live_pair(
        pack,
        plan,
        storage / "runs",
        client_factory=network.factory,
        credential_reader=lambda source: KEY,
    )
    return result, json.loads(Path(result["receipt"]).read_text())


def test_full_pair_has_explicit_wire_parameters_counts_and_isolated_review(
    pack, storage
):
    plan, data = approved_plan(pack, storage)
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 0 and r["comparable_pair"]
    assert r["http_requests_attempted"] == len(net.requests) == 4
    assert r["model_generation_requests_attempted"] == 2
    assert r["model_judging_calls"] == 0 and r["source_unchanged"]
    assert all(
        req.url.host == "generativelanguage.googleapis.com" for req in net.requests
    )
    assert all(req.headers["x-goog-api-key"] == KEY for req in net.requests)
    requests = [
        json.loads(req.content)
        for req in net.requests
        if req.url.path.endswith(":generateContent")
    ]
    for body in requests:
        assert body["generationConfig"] == {
            "temperature": 0.7,
            "topP": 0.95,
            "maxOutputTokens": 4096,
            "candidateCount": 1,
            "responseMimeType": "text/plain",
            "thinkingConfig": {"thinkingBudget": 0, "includeThoughts": False},
        }
        assert "AUTHOR_FORM_ONLY_SENTINEL" not in json.dumps(body)
        assert "EVALUATION_ONLY_SENTINEL" not in json.dumps(body)
    run = Path(result["receipt"]).parents[1]
    index = json.loads((run / "review/index.json").read_text())
    a = next(v for v in r["results"] if v["condition"] == "A1")
    assert a["blinding"] == "intact"
    assert "CONTEXT_MAP" not in (run / "review" / (a["output_id"] + ".md")).read_text()
    assert "execution_order" not in index and "estimated_cost_usd" not in index
    for path in run.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
    assert r["estimated_cost_usd"] == "0.00028"


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(execution_authorized=False),
        lambda p: p.update(external_transmission_authorized=False),
        lambda p: p.update(task_manifest_sha256="0" * 64),
        lambda p: p.update(runtime_root_sha256="0" * 64),
        lambda p: p["generation"].update(thinking_budget=-1),
        lambda p: p["budget"].update(max_http_requests=5),
        lambda p: p["budget"].update(max_generation_requests=3),
        lambda p: p["budget"].update(max_retries=1),
        lambda p: p["pricing"].update(reviewed=False),
        lambda p: p["pricing"].update(output_usd_per_million="NaN"),
        lambda p: p.update(expires_at_utc="2000-01-01T00:00:00+00:00"),
        lambda p: p.update(api_key="do-not-store"),
    ],
)
def test_plan_refuses_before_private_content_credentials_or_network(
    pack, storage, change
):
    m = json.loads((pack / "manifest.json").read_text())
    m["provenance"]["source_type"] = "author_private_outline_minimal_extract"
    (pack / "manifest.json").write_text(json.dumps(m))
    plan, p = approved_plan(pack, storage)
    change(p)
    plan.write_text(json.dumps(p))
    (pack / "common-input.md").unlink()

    def no_credentials(source):
        raise AssertionError("credential read before guard")

    def no_network(**kwargs):
        raise AssertionError("network client before guard")

    result = module().run_live_pair(
        pack,
        plan,
        storage / "runs",
        client_factory=no_network,
        credential_reader=no_credentials,
    )
    assert result["exit_code"] == 2 and result["http_requests_attempted"] == 0


def test_frozen_private_manifest_uses_separate_bound_permission_without_rewriting(
    pack, storage
):
    m = json.loads((pack / "manifest.json").read_text())
    m["provenance"]["source_type"] = "author_private_outline_minimal_extract"
    (pack / "manifest.json").write_text(json.dumps(m))
    original = (pack / "manifest.json").read_bytes()
    plan, p = approved_plan(pack, storage)
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 0 and (pack / "manifest.json").read_bytes() == original
    )
    assert m["execution_authorized"] is False


def test_budget_preflight_never_starts_generation_when_reserved_cost_too_high(
    pack, storage
):
    plan, p = approved_plan(pack, storage)
    p["budget"]["max_cost_usd"] = "0.0001"
    plan.write_text(json.dumps(p))
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 1 and r["error_code"] == "cost_budget_insufficient"
    assert len(net.requests) == r["http_requests_attempted"] == 2
    assert r["model_generation_requests_attempted"] == 0


def test_authorization_id_is_one_use_even_after_network_error(pack, storage):
    plan, p = approved_plan(pack, storage)

    def error(req, result, net):
        raise httpx.ReadTimeout("vendor " + KEY)

    net = Network(error)
    first, r = execute(pack, storage, plan, net)
    assert first["exit_code"] != 0 and len(net.requests) == 1
    second, r2 = execute(pack, storage, plan, Network())
    assert second["exit_code"] == 2 and r2["error_code"] == "authorization_already_used"
    assert r2["http_requests_attempted"] == 0


@pytest.mark.parametrize("status", [302, 401, 429, 503])
def test_http_error_never_retries_follows_redirects_or_saves_vendor_secret(
    pack, storage, status
):
    plan, p = approved_plan(pack, storage)

    def error(req, result, net):
        return httpx.Response(
            status,
            headers={"Location": "https://untrusted.example/"},
            text="vendor " + KEY,
        )

    net = Network(error)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] != 0 and len(net.requests) == 1
    for path in Path(result["receipt"]).parents[1].rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_plan_revocation_between_calls_stops_further_transmission(pack, storage):
    plan, p = approved_plan(pack, storage)

    def revoke(req, result, net):
        if len(net.requests) == 1:
            p["external_transmission_authorized"] = False
            plan.write_text(json.dumps(p))
        return result

    net = Network(revoke)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] != 0 and len(net.requests) == 1


@pytest.mark.parametrize(
    "reason,expected",
    [("MAX_TOKENS", "truncated"), ("SAFETY", "refused"), ("OTHER", "finish_error")],
)
def test_failed_a1_preserves_raw_and_degraded_index_without_retry(
    pack, storage, reason, expected
):
    plan, p = approved_plan(pack, storage)

    def change(req, result, net):
        if (
            req.url.path.endswith(":generateContent")
            and "CONTEXT_MAP" in req.content.decode()
        ):
            v = result.json()
            v["candidates"][0]["finishReason"] = reason
            return httpx.Response(200, json=v)
        return result

    net = Network(change)
    result, r = execute(pack, storage, plan, net)
    a = next(v for v in r["results"] if v["condition"] == "A1")
    assert (
        result["exit_code"] == 1
        and a["status"] == expected
        and a["blinding"] == "degraded"
    )
    assert len(net.requests) == 4 and not r["comparable_pair"]
    run = Path(result["receipt"]).parents[1]
    idx = json.loads((run / "review/index.json").read_text())
    assert (
        next(v for v in idx["outputs"] if v["output_id"] == a["output_id"])["blinding"]
        == "degraded"
    )
    assert "CONTEXT_MAP" in (run / "review" / (a["output_id"] + ".md")).read_text()


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(modelVersion="different-model"),
        lambda v: v.pop("usageMetadata"),
        lambda v: v["usageMetadata"].update(promptTokenCount=101),
        lambda v: v["usageMetadata"].update(totalTokenCount=9999),
        lambda v: v["usageMetadata"].update(thoughtsTokenCount=1, totalTokenCount=121),
        lambda v: v["candidates"][0]["content"]["parts"].append(
            {"functionCall": {"name": "do-not-execute"}}
        ),
    ],
)
def test_unreliable_model_usage_or_parts_prevents_comparison_and_more_generation(
    pack, storage, change
):
    plan, p = approved_plan(pack, storage)

    def changed(req, result, net):
        if req.url.path.endswith(":generateContent"):
            v = result.json()
            change(v)
            return httpx.Response(200, json=v)
        return result

    net = Network(changed)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] != 0 and not r["comparable_pair"]
    assert len(net.requests) == 3


def test_valid_response_that_echoes_key_is_redacted_and_unusable(pack, storage):
    plan, p = approved_plan(pack, storage)

    def changed(req, result, net):
        if req.url.path.endswith(":generateContent"):
            v = result.json()
            v["candidates"][0]["content"]["parts"][0]["text"] += KEY
            return httpx.Response(200, json=v)
        return result

    net = Network(changed)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] != 0 and not r["comparable_pair"]
    for path in Path(result["receipt"]).parents[1].rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_count_query_fee_ceiling_is_checked_before_any_network(pack, storage):
    plan, p = approved_plan(pack, storage)
    p["pricing"]["count_request_usd_upper_bound"] = "2"
    plan.write_text(json.dumps(p))
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 2
        and result["http_requests_attempted"] == 0
        and not net.requests
    )


def test_failed_generation_attempt_is_not_mislabeled_not_attempted(pack, storage):
    plan, p = approved_plan(pack, storage)

    def failure(req, result, net):
        if req.url.path.endswith(":generateContent"):
            return httpx.Response(503, text="vendor " + KEY)
        return result

    net = Network(failure)
    result, r = execute(pack, storage, plan, net)
    assert (
        r["http_requests_attempted"] == 3
        and r["model_generation_requests_attempted"] == 1
    )
    assert (
        len(
            [
                v
                for v in r["results"]
                if v["attempt"] == 1 and v["status"] == "request_failed"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                v
                for v in r["results"]
                if v["attempt"] == 0 and v["status"] == "not_attempted"
            ]
        )
        == 1
    )
    assert r["estimated_cost_usd"] is None and r["cost_estimate_complete"] is False


def test_cancellation_after_attempt_reservation_keeps_one_use_claim_and_counts(
    pack, storage
):
    plan, p = approved_plan(pack, storage)

    def cancel(req, result, net):
        if req.url.path.endswith(":generateContent"):
            raise KeyboardInterrupt()
        return result

    net = Network(cancel)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 1 and r["http_requests_attempted"] == 3
    assert (
        r["model_generation_requests_attempted"] == 1
        and r["error_code"] == "execution_interrupted"
    )
    again, _ = execute(pack, storage, plan, Network())
    assert (
        again["exit_code"] == 2 and again["error_code"] == "authorization_already_used"
    )


@pytest.mark.parametrize("value", [True, -1, 2**31, 1.2])
def test_invalid_token_count_response_never_starts_generation(pack, storage, value):
    plan, p = approved_plan(pack, storage)
    net = Network(
        lambda req, result, net: httpx.Response(200, json={"totalTokens": value})
    )
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 1 and len(net.requests) == 1
    assert r["model_generation_requests_attempted"] == 0


@pytest.mark.parametrize("data", [b"not json", b"\xff", b"{}"])
def test_invalid_provider_payload_is_hash_only_and_cannot_be_success(
    pack, storage, data
):
    plan, p = approved_plan(pack, storage)
    net = Network(lambda req, result, net: httpx.Response(200, content=data))
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 1 and len(net.requests) == 1
    assert not r["comparable_pair"]


def test_source_change_after_first_count_uses_frozen_input_and_invalidates_pair(
    pack, storage
):
    plan, p = approved_plan(pack, storage)
    initial = (pack / "common-input.md").read_text()

    def change(req, result, net):
        if len(net.requests) == 1:
            (pack / "common-input.md").write_text("changed source")
        return result

    net = Network(change)
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 1
        and r["source_unchanged"] is False
        and len(net.requests) == 4
    )
    for req in net.requests:
        body = json.loads(req.content)
        body = body.get("generateContentRequest", body)
        assert body["contents"][0]["parts"][0]["text"].startswith(initial + "\n\n")
    assert (
        next(v for v in r["results"] if v["condition"] == "A1")["blinding"] == "intact"
    )


def test_draft_preparation_is_metadata_only_and_never_authorizes_private_content(
    pack, storage
):
    (pack / "common-input.md").unlink()
    p = module().draft_plan(pack)
    assert (
        p["execution_authorized"] is False
        and p["external_transmission_authorized"] is False
    )
    assert p["pricing"]["reviewed"] is False


def test_success_retains_original_http_bytes_with_matching_digest(pack, storage):
    plan, p = approved_plan(pack, storage)
    result, r = execute(pack, storage, plan, Network())
    run = Path(result["receipt"]).parents[1]
    for file in (run / "internal/requests").glob("*-response.json"):
        meta = json.loads(file.read_text())
        assert (
            hashlib.sha256(meta["response_body_utf8"].encode()).hexdigest()
            == meta["response_sha256"]
        )


def test_credential_in_input_is_refused_before_snapshot_or_transmission(pack, storage):
    file = pack / "common-input.md"
    file.write_text(file.read_text() + KEY)
    m = json.loads((pack / "manifest.json").read_text())
    m["files"]["common-input.md"]["sha256"] = hashlib.sha256(
        file.read_bytes()
    ).hexdigest()
    (pack / "manifest.json").write_text(json.dumps(m))
    plan, p = approved_plan(pack, storage)
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 2
        and result["error_code"] == "credential_found_in_experiment_input"
    )
    assert not net.requests
    for path in Path(result["receipt"]).parents[1].rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_manifest_change_between_plan_guard_and_loader_is_not_authorized(
    pack, storage, monkeypatch
):
    plan, p = approved_plan(pack, storage)
    live = module()
    original = live.base.load_task

    def changed(*args, **kwargs):
        m = json.loads((pack / "manifest.json").read_text())
        m["ignored_extra"] = "changed after approval check"
        (pack / "manifest.json").write_text(json.dumps(m))
        return original(*args, **kwargs)

    monkeypatch.setattr(live.base, "load_task", changed)
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 2
        and result["error_code"] == "execution_task_manifest_mismatch"
    )
    assert not net.requests


def test_encoded_response_is_rejected_without_decompression(pack, storage):
    plan, p = approved_plan(pack, storage)
    net = Network(
        lambda req, result, net: httpx.Response(
            200, headers={"content-encoding": "custom"}, content=b"opaque"
        )
    )
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 1
        and result["error_code"] == "encoded_response_unsupported"
    )
    assert len(net.requests) == 1


def test_output_io_failure_after_transmission_never_reports_zero_attempts(
    pack, storage, monkeypatch, capsys
):
    live = module()
    plan, p = approved_plan(pack, storage)
    net = Network()
    original_transport = live.GeminiTransport
    monkeypatch.setattr(
        live,
        "GeminiTransport",
        lambda key, timeout, factory=None: original_transport(
            key, timeout, net.factory
        ),
    )
    monkeypatch.setattr(live, "read_credential", lambda source: KEY)
    original_write = live.base.write_json

    def late_error(path, value):
        if path.name == "receipt.json":
            raise OSError("simulated late output failure")
        return original_write(path, value)

    monkeypatch.setattr(live.base, "write_json", late_error)
    code = live.main(
        [
            "--execute",
            "--task-dir",
            str(pack),
            "--execution-plan",
            str(plan),
            "--output-root",
            str(storage / "out"),
        ]
    )
    value = json.loads(capsys.readouterr().out)
    assert code == 2 and value["http_requests_attempted"] is None
    assert "unavailable" in value["request_count_status"]
    assert len(net.requests) == 4
    assert len(list((storage / "out").glob("*/internal/requests/*-started.json"))) == 4


def test_provider_seed_is_fixed_independently_from_order_seed(pack, storage):
    plan, p = approved_plan(pack, storage)
    p["generation"]["seed"] = 42
    p["order_seed"] = 77
    plan.write_text(json.dumps(p))
    net = Network()
    result, r = execute(pack, storage, plan, net)
    assert (
        result["exit_code"] == 0 and r["provider_seed"] == 42 and r["order_seed"] == 77
    )
    for req in net.requests:
        b = json.loads(req.content)
        b = b.get("generateContentRequest", b)
        assert b["generationConfig"]["seed"] == 42


def test_cookies_are_not_shared_between_independent_requests(pack, storage):
    plan, p = approved_plan(pack, storage)

    def cookies(req, result, net):
        assert "cookie" not in req.headers
        result.headers["set-cookie"] = "provider-session=do-not-reuse; Path=/"
        return result

    net = Network(cookies)
    result, r = execute(pack, storage, plan, net)
    assert result["exit_code"] == 0 and len(net.requests) == 4


def test_actual_default_client_has_no_retry_proxy_or_redirect_inheritance(monkeypatch):
    from scripts.experiments import gemini_transport

    arguments = {}

    class Client:
        def __init__(self, **kwargs):
            arguments.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(gemini_transport.httpx, "Client", Client)
    transport = gemini_transport.GeminiTransport(KEY, 17)
    assert arguments["trust_env"] is False and arguments["follow_redirects"] is False
    assert arguments["timeout"] == 17
    assert arguments["transport"]._pool._retries == 0
    transport.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("program_sha256", "0" * 64),
        ("runtime_versions", {"python": "unapproved", "httpx": "unapproved"}),
    ],
)
def test_changed_code_or_runtime_is_refused_before_content_or_credentials(
    pack, storage, field, value
):
    plan, p = approved_plan(pack, storage)
    p[field] = value
    plan.write_text(json.dumps(p))
    (pack / "common-input.md").unlink()

    def no_key(source):
        raise AssertionError("credential read")

    def no_client(**kwargs):
        raise AssertionError("network client created")

    result = module().run_live_pair(
        pack, plan, storage / "runs", credential_reader=no_key, client_factory=no_client
    )
    assert result["exit_code"] == 2 and result["http_requests_attempted"] == 0


@pytest.fixture(autouse=True)
def disallow_external_network(monkeypatch):
    import socket

    original = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("Transport tests must not contact external hosts")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)


@pytest.mark.parametrize("failure", [False, True])
def test_default_transport_over_local_http_has_exact_counts_and_no_cookie_context(
    pack, storage, monkeypatch, failure
):
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    import threading
    from scripts.experiments import gemini_transport

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, dict(self.headers), body))
            status = 503 if failure else 200
            if failure:
                value = {"error": {"message": "vendor " + KEY}}
            elif self.path.endswith(":countTokens"):
                value = {"totalTokens": 100}
            else:
                value = response("A1" if "CONTEXT_MAP" in json.dumps(body) else "D0")
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", "context=do-not-carry; Path=/")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        gemini_transport,
        "API_ROOT",
        "http://127.0.0.1:" + str(server.server_port) + "/models/gemini-2.5-flash",
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    plan, p = approved_plan(pack, storage)
    try:
        result = module().run_live_pair(
            pack, plan, storage / "runs", credential_reader=lambda source: KEY
        )
        r = json.loads(Path(result["receipt"]).read_text())
        assert len(seen) == r["http_requests_attempted"] == (1 if failure else 4)
        assert result["exit_code"] == (1 if failure else 0)
        assert r["model_generation_requests_attempted"] == (0 if failure else 2)
        assert all(not any(k.lower() == "cookie" for k in h) for _, h, _ in seen)
        assert all(
            next(v for k, v in h.items() if k.lower() == "x-goog-api-key") == KEY
            for _, h, _ in seen
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_prompt_level_refusal_is_preserved_even_when_version_and_usage_are_absent(
    pack, storage
):
    plan, p = approved_plan(pack, storage)

    def refused(req, result, net):
        if req.url.path.endswith(":generateContent"):
            return httpx.Response(
                200, json={"promptFeedback": {"blockReason": "SAFETY"}}
            )
        return result

    net = Network(refused)
    result, r = execute(pack, storage, plan, net)
    attempted = next(v for v in r["results"] if v["attempt"] == 1)
    assert attempted["status"] == "refused" and attempted["finish_reason"] == "SAFETY"
    assert attempted["error_code"] == "model_version_unavailable"
    assert len(net.requests) == 3 and not r["comparable_pair"]
    assert r["estimated_cost_usd"] is None and r["cost_estimate_complete"] is False


def test_used_authorization_refuses_before_reading_content_again(pack, storage):
    plan, p = approved_plan(pack, storage)
    net = Network(
        lambda req, result, net: httpx.Response(503, text="temporary failure")
    )
    first, r = execute(pack, storage, plan, net)
    assert len(net.requests) == 1
    (pack / "common-input.md").unlink()

    def forbidden(source):
        raise AssertionError("reused authorization read credential")

    again = module().run_live_pair(
        pack,
        plan,
        storage / "runs",
        credential_reader=forbidden,
        client_factory=net.factory,
    )
    assert (
        again["exit_code"] == 2 and again["error_code"] == "authorization_already_used"
    )
    assert again["http_requests_attempted"] == 0 and len(net.requests) == 1


def test_closed_tty_credential_input_is_recorded_before_any_http(
    pack, storage, monkeypatch
):
    from scripts.experiments import execution_plan

    plan, p = approved_plan(pack, storage)
    monkeypatch.setattr(execution_plan.sys.stdin, "isatty", lambda: True)

    def closed(*args):
        raise EOFError()

    monkeypatch.setattr(execution_plan.getpass, "getpass", closed)

    def no_client(**kwargs):
        raise AssertionError("client created after closed credential input")

    result = module().run_live_pair(
        pack, plan, storage / "runs", client_factory=no_client
    )
    assert (
        result["exit_code"] == 2 and result["error_code"] == "credential_input_closed"
    )
    assert result["http_requests_attempted"] == 0
