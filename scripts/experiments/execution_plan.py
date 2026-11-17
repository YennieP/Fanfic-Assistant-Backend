"""One-use local execution declarations; never load production configuration."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import secrets
import sys
import getpass
import httpx
import json

from . import outline_runner as base

PRICE_REFERENCE = "https://ai.google.dev/gemini-api/docs/pricing"
PLAN_KEYS = {
    "version",
    "authorization_id",
    "execution_authorized",
    "external_transmission_authorized",
    "expires_at_utc",
    "runtime_root_sha256",
    "program_sha256",
    "runtime_versions",
    "task_manifest_sha256",
    "file_sha256",
    "source_type",
    "provider",
    "model",
    "expected_model_version",
    "credential_source",
    "generation",
    "budget",
    "pricing",
    "request_timeout_seconds",
    "order_seed",
}


def runtime_hash():
    return base.digest(str(base.RUNTIME_ROOT.resolve()).encode())


def program_hash():
    directory = Path(__file__).resolve().parent
    names = (
        "outline_runner.py",
        "execution_plan.py",
        "gemini_transport.py",
        "outline_live_runner.py",
    )
    entries = [
        (name, base.digest(base.read_regular(directory / name))) for name in names
    ]
    return base.digest(json.dumps(entries, separators=(",", ":")).encode())


def runtime_versions():
    return {"python": sys.version.split()[0], "httpx": httpx.__version__}


def draft_plan(task_dir: Path):
    """Read manifest metadata only. Draft permissions and prices are unapproved."""
    raw = base.read_regular(task_dir / "manifest.json")
    m = base.object_json(raw, "invalid_manifest")
    entries, provenance = m.get("files"), m.get("provenance")
    if not isinstance(entries, dict) or not isinstance(provenance, dict):
        raise base.GuardError("invalid_manifest")
    hashes = {}
    for name in base.REQUIRED_FILES:
        entry = entries.get(name)
        value = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
            raise base.GuardError("missing_input_hashes")
        hashes[name] = value
    return {
        "version": 1,
        "authorization_id": secrets.token_hex(16),
        "execution_authorized": False,
        "external_transmission_authorized": False,
        "expires_at_utc": "",
        "runtime_root_sha256": runtime_hash(),
        "program_sha256": program_hash(),
        "runtime_versions": runtime_versions(),
        "task_manifest_sha256": base.digest(raw),
        "file_sha256": hashes,
        "source_type": provenance.get("source_type"),
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "expected_model_version": "gemini-2.5-flash",
        "credential_source": "prompt",
        "generation": {
            "temperature": 0.7,
            "top_p": 0.95,
            "max_output_tokens": 4096,
            "thinking_budget": 0,
            "seed": None,
        },
        "budget": {
            "max_http_requests": 4,
            "max_generation_requests": 2,
            "max_retries": 0,
            "max_cost_usd": "0.01",
        },
        "pricing": {
            "input_usd_per_million": "0",
            "output_usd_per_million": "0",
            "count_request_usd_upper_bound": "0",
            "reference": PRICE_REFERENCE,
            "reviewed": False,
        },
        "request_timeout_seconds": 120,
        "order_seed": 17,
    }


def money(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}(?:\.\d{1,9})?", value):
        raise base.GuardError("invalid_price_or_budget")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise base.GuardError("invalid_price_or_budget") from None
    return result


def expiry(plan):
    try:
        value = datetime.fromisoformat(plan["expires_at_utc"])
    except (ValueError, TypeError):
        raise base.GuardError("invalid_execution_expiry") from None
    now = datetime.now(timezone.utc)
    if value.tzinfo is None or not now < value <= now + timedelta(days=1):
        raise base.GuardError("execution_expired_or_unbounded")


def load_plan(path: Path, task_dir: Path):
    base.no_symlinks(path.absolute())
    try:
        path.resolve().relative_to(base.RUNTIME_ROOT.resolve())
    except ValueError:
        raise base.GuardError("execution_plan_outside_runtime") from None
    raw = base.read_regular(path)
    p = base.object_json(raw, "invalid_execution_plan")
    if set(p) != PLAN_KEYS or type(p["version"]) is not int or p["version"] != 1:
        raise base.GuardError("invalid_execution_plan")
    if (
        p["execution_authorized"] is not True
        or p["external_transmission_authorized"] is not True
    ):
        raise base.GuardError("execution_and_transmission_approval_required")
    expiry(p)
    if p["runtime_root_sha256"] != runtime_hash():
        raise base.GuardError("execution_environment_mismatch")
    if (
        p["program_sha256"] != program_hash()
        or p["runtime_versions"] != runtime_versions()
    ):
        raise base.GuardError("execution_program_or_runtime_mismatch")
    if not isinstance(p["authorization_id"], str) or not re.fullmatch(
        "[0-9a-f]{32}", p["authorization_id"]
    ):
        raise base.GuardError("invalid_authorization_id")
    if p["provider"] != "gemini" or p["model"] != "gemini-2.5-flash":
        raise base.GuardError("unsupported_experiment_model")
    if not isinstance(p["expected_model_version"], str) or not re.fullmatch(
        r"gemini-2\.5-flash(?:-[a-z0-9._-]{1,32})?", p["expected_model_version"]
    ):
        raise base.GuardError("invalid_expected_model_version")
    if p["credential_source"] not in ("prompt", "experiment_env"):
        raise base.GuardError("unsupported_credential_source")
    g, b, prices = p["generation"], p["budget"], p["pricing"]
    if not isinstance(g, dict) or set(g) != {
        "temperature",
        "top_p",
        "max_output_tokens",
        "thinking_budget",
        "seed",
    }:
        raise base.GuardError("invalid_generation_parameters")
    for field, lower, upper in [("temperature", 0, 2), ("top_p", 0, 1)]:
        if type(g[field]) not in (int, float) or not lower <= g[field] <= upper:
            raise base.GuardError("invalid_generation_parameters")
    if (
        type(g["max_output_tokens"]) is not int
        or not 1 <= g["max_output_tokens"] <= 32768
    ):
        raise base.GuardError("invalid_generation_parameters")
    if type(g["thinking_budget"]) is not int or g["thinking_budget"] != 0:
        raise base.GuardError("thinking_must_be_disabled")
    if g["seed"] is not None and (
        type(g["seed"]) is not int or not 0 <= g["seed"] < 2**31
    ):
        raise base.GuardError("invalid_provider_seed")
    if not isinstance(b, dict) or set(b) != {
        "max_http_requests",
        "max_generation_requests",
        "max_retries",
        "max_cost_usd",
    }:
        raise base.GuardError("invalid_request_budget")
    if any(
        type(b[k]) is not int or b[k] != v
        for k, v in [
            ("max_http_requests", 4),
            ("max_generation_requests", 2),
            ("max_retries", 0),
        ]
    ):
        raise base.GuardError("invalid_request_budget")
    if money(b["max_cost_usd"]) <= 0:
        raise base.GuardError("invalid_price_or_budget")
    if not isinstance(prices, dict) or set(prices) != {
        "input_usd_per_million",
        "output_usd_per_million",
        "count_request_usd_upper_bound",
        "reference",
        "reviewed",
    }:
        raise base.GuardError("invalid_pricing")
    if prices["reviewed"] is not True or prices["reference"] != PRICE_REFERENCE:
        raise base.GuardError("reviewed_price_bounds_required")
    money(prices["input_usd_per_million"])
    money(prices["output_usd_per_million"])
    if 2 * money(prices["count_request_usd_upper_bound"]) > money(b["max_cost_usd"]):
        raise base.GuardError("count_request_cost_budget_insufficient")
    if (
        type(p["request_timeout_seconds"]) is not int
        or not 1 <= p["request_timeout_seconds"] <= 120
    ):
        raise base.GuardError("invalid_request_timeout")
    if type(p["order_seed"]) is not int or not 0 <= p["order_seed"] < 2**64:
        raise base.GuardError("invalid_order_seed")
    if not isinstance(p["file_sha256"], dict) or set(p["file_sha256"]) != set(
        base.REQUIRED_FILES
    ):
        raise base.GuardError("invalid_approved_file_scope")
    if any(
        not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
        for v in p["file_sha256"].values()
    ):
        raise base.GuardError("invalid_approved_file_scope")
    # This check happens before the task loader reads any private content.
    manifest = base.read_regular(task_dir / "manifest.json")
    m = base.object_json(manifest, "invalid_manifest")
    if base.digest(manifest) != p["task_manifest_sha256"]:
        raise base.GuardError("execution_task_manifest_mismatch")
    provenance = m.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("source_type") != p["source_type"]
    ):
        raise base.GuardError("execution_source_type_mismatch")
    if p["source_type"] not in (
        "synthetic",
        "synthetic_fixture",
        "author_private_outline_minimal_extract",
    ):
        raise base.GuardError("unsupported_execution_source")
    entries = m.get("files")
    if not isinstance(entries, dict) or any(
        not isinstance(entries.get(n), dict) or entries[n].get("sha256") != v
        for n, v in p["file_sha256"].items()
    ):
        raise base.GuardError("execution_file_hash_mismatch")
    return p, raw


def recheck(path, expected_hash, plan):
    if base.digest(base.read_regular(path)) != expected_hash:
        raise base.GuardError("execution_plan_changed")
    expiry(plan)
    if (
        plan["program_sha256"] != program_hash()
        or plan["runtime_versions"] != runtime_versions()
    ):
        raise base.GuardError("execution_program_or_runtime_changed")


def claim(plan, run_dir, plan_hash):
    root = base.RUNTIME_ROOT / "outline-execution-claims"
    base.no_symlinks(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / plan["authorization_id"]
    base.no_symlinks(directory)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise base.GuardError("authorization_already_used") from None
    base.write_json(
        directory / "claim.json",
        {
            "run_id": run_dir.name,
            "plan_sha256": plan_hash,
            "scope": "one invocation in this exact runtime root; never auto-released",
        },
    )


def read_credential(source):
    if source == "experiment_env":
        key = os.environ.get("FANFIC_EXPERIMENT_GEMINI_API_KEY", "")
    else:
        if not sys.stdin.isatty():
            raise base.GuardError("credential_prompt_requires_tty")
        key = getpass.getpass("Experiment Gemini API key: ")
    if not isinstance(key, str) or not re.fullmatch(r"AIza[A-Za-z0-9_-]{35}", key):
        raise base.GuardError("experiment_credential_unavailable")
    return key
