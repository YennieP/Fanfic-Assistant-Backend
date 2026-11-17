"""Execute one separately authorized Gemini pair; no Django or production Key lookup."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import secrets
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.experiments import outline_runner as base
from scripts.experiments.execution_plan import (
    draft_plan,
    load_plan,
    claim,
    recheck,
    read_credential,
    money,
)
from scripts.experiments.gemini_transport import (
    GeminiTransport,
    TransportError,
    generation_body,
    parse_generation,
    integer,
)


def cost(plan, prompt, output):
    p = plan["pricing"]
    return (
        money(p["input_usd_per_million"]) * prompt
        + money(p["output_usd_per_million"]) * output
    ) / 1_000_000


def run_live_pair(
    task_dir, plan_file, output_root, *, client_factory=None, credential_reader=None
):
    root = base.scoped_output_root(output_root)
    run = root / secrets.token_hex(16)
    run.mkdir(mode=0o700)
    receipt = {
        "version": 1,
        "dry_run": False,
        "status": "blocked",
        "exit_code": 2,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "runner_sha256": base.digest(Path(__file__).read_bytes()),
        "http_requests_attempted": 0,
        "count_requests_attempted": 0,
        "model_generation_requests_attempted": 0,
        "generation_attempt_conditions": [],
        "model_judging_calls": 0,
        "results": [],
        "comparable_pair": False,
        "estimated_cost_usd": None,
        "known_cost_estimate_usd": "0",
        "cost_estimate_complete": False,
        "cost_basis": "reported tokens and attempted count queries times approved price upper bounds; not provider invoice",
    }
    client = None
    plan = None
    task = None
    order = list(base.GROUPS)
    rng = None
    try:
        plan, raw_plan = load_plan(plan_file, task_dir)
        plan_hash = base.digest(raw_plan)
        claim(plan, run, plan_hash)
        candidate_task = base.load_task(
            task_dir, private_manifest_sha256=plan["task_manifest_sha256"]
        )
        if candidate_task["manifest_hash"] != plan["task_manifest_sha256"]:
            raise base.GuardError("execution_task_manifest_mismatch")
        if candidate_task["hashes"] != plan["file_sha256"]:
            raise base.GuardError("execution_file_hash_mismatch")
        task = candidate_task
        receipt.update(
            provider="gemini",
            requested_model=plan["model"],
            expected_model_version=plan["expected_model_version"],
            authorization_id=plan["authorization_id"],
            program_sha256=plan["program_sha256"],
            runtime_versions=plan["runtime_versions"],
            plan_sha256=plan_hash,
            task_manifest_sha256=task["manifest_hash"],
            input_sha256=task["hashes"],
            generation_parameters=plan["generation"],
            approved_budget=plan["budget"],
            approved_pricing=plan["pricing"],
            credential_source=plan["credential_source"],
            python_version=sys.version.split()[0],
        )
        rng = random.Random(plan["order_seed"])
        rng.shuffle(order)
        receipt.update(
            execution_order=order,
            order_seed=plan["order_seed"],
            provider_seed=plan["generation"]["seed"],
            version_identity_basis="provider-reported modelVersion; backend weights and deterministic output not verified",
        )
        prompts = {g: base.compile_prompt(task, g) for g in order}
        bodies = {g: generation_body(prompts[g], plan["generation"]) for g in order}
        recheck(plan_file, plan_hash, plan)
        key = (credential_reader or read_credential)(plan["credential_source"])
        if not isinstance(key, str) or not re.fullmatch(r"AIza[A-Za-z0-9_-]{35}", key):
            raise base.GuardError("experiment_credential_unavailable")
        if any(
            key.encode() in raw
            for raw in [raw_plan, task["manifest_bytes"], *task["files"].values()]
        ):
            raise base.GuardError("credential_found_in_experiment_input")
        base.write_new(run / "internal/inputs/manifest.json", task["manifest_bytes"])
        base.write_new(run / "internal/execution-plan.json", raw_plan)
        for name, raw in task["files"].items():
            base.write_new(run / "internal/inputs" / name, raw)
        base.write_new(
            run / "review/author-review-form.md", task["files"]["author-review-form.md"]
        )
        for g in order:
            base.write_json(run / "internal/prompts" / (g + ".json"), prompts[g])
        client = GeminiTransport(key, plan["request_timeout_seconds"], client_factory)

        def request(group, endpoint, body):
            recheck(plan_file, plan_hash, plan)
            if receipt["http_requests_attempted"] >= 4:
                raise base.GuardError("http_request_budget_exhausted")
            if (
                endpoint == "generateContent"
                and receipt["model_generation_requests_attempted"] >= 2
            ):
                raise base.GuardError("generation_request_budget_exhausted")
            n = receipt["http_requests_attempted"] + 1
            base.write_json(
                run / "internal/requests" / (str(n) + "-started.json"),
                {
                    "condition": group,
                    "endpoint": endpoint,
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "delivery": "request attempt reserved before dispatch; receipt does not prove provider acceptance",
                },
            )
            receipt["http_requests_attempted"] = n
            if endpoint == "countTokens":
                receipt["count_requests_attempted"] += 1
                receipt["known_cost_estimate_usd"] = str(
                    receipt["count_requests_attempted"]
                    * money(plan["pricing"]["count_request_usd_upper_bound"])
                )
            if endpoint == "generateContent":
                receipt["model_generation_requests_attempted"] += 1
                receipt["generation_attempt_conditions"].append(group)
            try:
                value, meta = client.request(endpoint, body)
            except TransportError as error:
                base.write_json(
                    run / "internal/requests" / (str(n) + "-response.json"),
                    dict(error.metadata, error_code=error.code),
                )
                raise base.GuardError(error.code) from None
            base.write_json(
                run / "internal/requests" / (str(n) + "-response.json"), meta
            )
            return value, meta

        counts = {}
        for group in order:
            value, _ = request(
                group,
                "countTokens",
                {
                    "generateContentRequest": dict(
                        bodies[group], model="models/gemini-2.5-flash"
                    )
                },
            )
            counts[group] = integer(value.get("totalTokens"))
        count_cost = 2 * money(plan["pricing"]["count_request_usd_upper_bound"])
        reserved = count_cost + sum(
            cost(plan, counts[g], plan["generation"]["max_output_tokens"])
            for g in order
        )
        receipt.update(counted_input_tokens=counts, reserved_cost_usd=str(reserved))
        if reserved > money(plan["budget"]["max_cost_usd"]):
            raise base.GuardError("cost_budget_insufficient")
        estimated = count_cost
        for group in order:
            value, meta = request(group, "generateContent", bodies[group])
            response = parse_generation(
                value,
                plan["expected_model_version"],
                counts[group],
                plan["generation"]["max_output_tokens"],
            )
            state, final, context = base.prepare_review_output(
                group, response, plan["generation"]["max_output_tokens"]
            )
            neutral = secrets.token_hex(8)
            row = {
                "condition": group,
                "attempt": 1,
                "output_id": neutral,
                "finish_reason": response["finish_reason"],
                "model_version": response["model_version"],
                "usage": response["usage"],
                "usage_details": response.get("usage_details"),
                "usage_source": "gemini_api",
                "elapsed_ms": meta["elapsed_ms"],
                "latency_source": "actual_http_call",
                "output_sha256": base.digest(response["text"].encode()),
                **state,
            }
            if response.get("error_code"):
                row["error_code"] = response["error_code"]
            base.write_new(
                run / "internal/raw" / (group + ".txt"), response["text"].encode()
            )
            if context is not None:
                base.write_new(run / "internal/context-map.txt", context.encode())
            base.write_new(run / "review" / (neutral + ".md"), final.encode())
            if response["fatal"]:
                row["estimated_cost_usd"] = None
                receipt["cost_estimate_complete"] = False
            else:
                c = cost(
                    plan,
                    response["usage"]["prompt_tokens"],
                    response["usage"]["output_tokens"],
                )
                row["estimated_cost_usd"] = str(c)
                estimated += c
            receipt["results"].append(row)
            receipt["known_cost_estimate_usd"] = str(estimated)
            if response["fatal"]:
                raise base.GuardError(response["error_code"])
        receipt["exit_code"] = (
            0 if all(r["status"] == "ok" for r in receipt["results"]) else 1
        )
        receipt["cost_estimate_complete"] = True
        receipt["estimated_cost_usd"] = str(estimated)
    except base.GuardError as error:
        receipt["error_code"] = str(error)
    except OSError:
        receipt["error_code"] = "artifact_io_error"
    except KeyboardInterrupt:
        receipt["error_code"] = "execution_interrupted"
    finally:
        if client is not None:
            client.close()
    if task is not None:
        try:
            unchanged = (
                base.digest(base.read_regular(task_dir / "manifest.json"))
                == task["manifest_hash"]
            )
            for name, sha in task["hashes"].items():
                unchanged = (
                    base.digest(base.read_regular(task_dir / name)) == sha and unchanged
                )
            receipt["source_unchanged"] = unchanged
        except base.GuardError:
            receipt["source_unchanged"] = False
        if not receipt["source_unchanged"]:
            receipt["exit_code"] = 1 if receipt["http_requests_attempted"] else 2
            receipt["source_integrity_error"] = "source_changed"
            receipt.setdefault("error_code", "source_changed")
    if plan is not None:
        try:
            recheck(plan_file, plan_hash, plan)
        except base.GuardError as error:
            receipt["exit_code"] = 1 if receipt["http_requests_attempted"] else 2
            receipt["authorization_integrity_error"] = str(error)
            receipt.setdefault("error_code", str(error))
    if receipt["http_requests_attempted"]:
        done = {r["condition"] for r in receipt["results"]}
        for g in order:
            if g not in done:
                attempted = g in receipt["generation_attempt_conditions"]
                receipt["results"].append(
                    {
                        "condition": g,
                        "attempt": 1 if attempted else 0,
                        "status": "request_failed" if attempted else "not_attempted",
                        "reason": receipt.get("error_code", "run_incomplete"),
                        "blinding": "degraded",
                        "output_id": None,
                    }
                )
        if receipt["exit_code"] == 2:
            receipt["exit_code"] = 1
    outputs = [
        {
            "output_id": r["output_id"],
            "file": r["output_id"] + ".md",
            "status": r["status"],
            "blinding": r["blinding"],
        }
        for r in receipt["results"]
        if r.get("output_id")
    ]
    if rng is not None:
        # Replaying the first two-item permutation would expose fixed group positions.
        rng.shuffle(outputs)
    receipt["comparable_pair"] = (
        receipt["exit_code"] == 0
        and len(receipt["results"]) == 2
        and receipt.get("source_unchanged") is True
    )
    receipt["status"] = (
        "completed"
        if receipt["comparable_pair"]
        else (
            "completed_with_failures"
            if receipt["http_requests_attempted"]
            else "blocked"
        )
    )
    base.write_json(
        run / "review/index.json",
        {
            "dry_run": False,
            "outputs": outputs,
            "comparable_pair": receipt["comparable_pair"],
        },
    )
    receipt["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["artifact_sha256"] = {
        str(p.relative_to(run)): base.digest(p.read_bytes())
        for p in run.rglob("*")
        if p.is_file()
    }
    base.write_json(run / "internal/receipt.json", receipt)
    return {
        "status": receipt["status"],
        "exit_code": receipt["exit_code"],
        "http_requests_attempted": receipt["http_requests_attempted"],
        "model_generation_requests_attempted": receipt[
            "model_generation_requests_attempted"
        ],
        "receipt": str(run / "internal/receipt.json"),
        "review_directory": str(run / "review"),
        **({"error_code": receipt["error_code"]} if "error_code" in receipt else {}),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-plan", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--execution-plan", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=base.RUNTIME_ROOT / "outline-live"
    )
    args = parser.parse_args(argv)
    try:
        if args.prepare_plan:
            base.write_json(args.execution_plan, draft_plan(args.task_dir))
            print(
                json.dumps(
                    {
                        "status": "draft_plan_created",
                        "execution_authorized": False,
                        "external_transmission_authorized": False,
                        "http_requests_attempted": 0,
                    }
                )
            )
            return 0
        value = run_live_pair(args.task_dir, args.execution_plan, args.output_root)
        print(json.dumps(value))
        return value["exit_code"]
    except (base.GuardError, OSError) as error:
        code = str(error) if isinstance(error, base.GuardError) else "artifact_io_error"
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": code,
                    "http_requests_attempted": None,
                    "request_count_status": "unavailable; inspect execution journal before considering another run",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
