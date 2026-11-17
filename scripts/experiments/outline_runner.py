"""Run one synthetic D0/A1 pair without importing production or model clients."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import stat
import sys
import time
from typing import Any


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / '.runtime'
REQUIRED_FILES = (
    'common-input.md', 'evaluation-reference.md', 'pilot-protocol.md',
    'generation-conditions.md', 'author-review-form.md',
)
PROTOCOL_FILES = set(REQUIRED_FILES[2:])
GROUPS = ('D0', 'A1')
MAX_FILE_BYTES = 1_048_576
MAX_FAKE_RESPONSE_BYTES = 262_144


class GuardError(Exception):
    """Stable, non-content error code suitable for a local receipt."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def no_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise GuardError('symlink_not_allowed')


def read_regular(path: Path) -> bytes:
    no_symlinks(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise GuardError('invalid_input_file')
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise GuardError('input_unavailable') from None
    if len(data) > MAX_FILE_BYTES:
        raise GuardError('input_too_large')
    return data


def object_json(data: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise GuardError(code) from None
    if not isinstance(value, dict):
        raise GuardError(code)
    return value


def scoped_output_root(output_root: Path) -> Path:
    path = output_root.absolute()
    no_symlinks(path)
    try:
        path.resolve().relative_to(RUNTIME_ROOT.resolve())
    except ValueError:
        raise GuardError('output_outside_runtime') from None
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise GuardError('invalid_output_root')
    return path


def write_new(path: Path, data: bytes) -> None:
    no_symlinks(path)
    try:
        path.resolve().relative_to(RUNTIME_ROOT.resolve())
    except ValueError:
        raise GuardError('output_outside_runtime') from None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def write_json(path: Path, value: Any) -> None:
    write_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def parse_condition_templates(text: str) -> dict[str, str]:
    templates: dict[str, str] = {}
    title = ''
    fence: str | None = None
    role: str | None = None
    body: list[str] = []
    for line in text.splitlines(keepends=True):
        marker = re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n?', line)
        if fence is not None:
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= len(fence) and not marker.group(2).strip()):
                if role is not None:
                    content = ''.join(body)
                    if role in templates or not content.strip():
                        raise GuardError('invalid_condition_templates')
                    templates[role] = content
                fence, role, body = None, None, []
            elif role is not None:
                body.append(line)
            continue
        if marker:
            fence = marker.group(1)
            # Ignore other fenced examples entirely, including their headings.
            if fence == '```' and marker.group(2).strip() == 'text':
                if re.match(r'D0\b', title):
                    role = 'D0'
                elif re.match(r'A1\b', title):
                    role = 'A1'
                elif 'system instruction' in title.casefold():
                    role = 'shared'
                else:
                    raise GuardError('invalid_condition_templates')
            continue
        heading = re.match(r'^##[ \t]+([^\r\n]+)', line)
        if heading:
            title = heading.group(1)
    if fence is not None or set(templates) != {'shared', 'D0', 'A1'}:
        raise GuardError('invalid_condition_templates')
    return templates


def load_task(task_dir: Path) -> dict[str, Any]:
    directory = task_dir.absolute()
    manifest_bytes = read_regular(directory / 'manifest.json')
    manifest = object_json(manifest_bytes, 'invalid_manifest')
    provenance = manifest.get('provenance')
    approval = manifest.get('approval')
    protocol = manifest.get('protocol_approval')
    if not isinstance(provenance, dict):
        raise GuardError('invalid_provenance')
    # Reject private packs before reading common-input or any response fixture.
    if provenance.get('source_type') not in ('synthetic', 'synthetic_fixture'):
        raise GuardError('synthetic_task_required')
    if (
        type(manifest.get('schema_version')) is not int
        or manifest.get('schema_version') != 1
        or manifest.get('author_approved') is not True
        or manifest.get('dataset_frozen') is not True
        or not isinstance(approval, dict)
        or approval.get('protocol_approved') is not True
        or not isinstance(protocol, dict)
    ):
        raise GuardError('approval_required')
    if (
        manifest.get('execution_authorized') is not False
        or provenance.get('external_model_transmission_authorized') is not False
        or provenance.get('actual_character_or_relationship_database_export') is not False
        or manifest.get('model_generation_calls') != 0
        or manifest.get('model_judging_calls') != 0
        or type(manifest.get('model_generation_calls')) is not int
        or type(manifest.get('model_judging_calls')) is not int
    ):
        raise GuardError('synthetic_only_authorization_required')
    task_id = manifest.get('task_id')
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
        raise GuardError('invalid_task_id')
    data_scope = approval.get('scope')
    protocol_scope = protocol.get('scope')
    if (
        not isinstance(data_scope, list)
        or any(not isinstance(v, str) for v in data_scope)
        or not {'common-input.md', 'evaluation-reference.md'}.issubset(data_scope)
        or not isinstance(protocol_scope, list)
        or any(not isinstance(v, str) for v in protocol_scope)
        or not PROTOCOL_FILES.issubset(protocol_scope)
    ):
        raise GuardError('approval_scope_incomplete')
    entries = manifest.get('files')
    approved_hashes = protocol.get('file_sha256')
    if not isinstance(entries, dict) or not isinstance(approved_hashes, dict):
        raise GuardError('missing_input_hashes')
    frozen: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    for name in REQUIRED_FILES:
        entry = entries.get(name)
        expected = entry.get('sha256') if isinstance(entry, dict) else None
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise GuardError('missing_input_hashes')
        if name in PROTOCOL_FILES and approved_hashes.get(name) != expected:
            raise GuardError('protocol_hash_mismatch')
        raw = read_regular(directory / name)
        if digest(raw) != expected:
            raise GuardError('input_hash_mismatch')
        try:
            raw.decode('utf-8')
        except UnicodeError:
            raise GuardError('input_not_utf8') from None
        frozen[name] = raw
        hashes[name] = expected
    templates = parse_condition_templates(frozen['generation-conditions.md'].decode())
    return {
        'id': task_id, 'directory': directory, 'manifest_hash': digest(manifest_bytes),
        'files': frozen, 'hashes': hashes, 'system': templates['shared'],
        'tails': {group: templates[group] for group in GROUPS},
    }


def compile_prompt(task: dict[str, Any], group: str) -> dict[str, str]:
    if group not in GROUPS:
        raise GuardError('unknown_condition')
    return {
        'system': task['system'],
        'user': task['files']['common-input.md'].decode() + '\n\n' + task['tails'][group],
    }


def split_context_map(text: str) -> tuple[str, str]:
    markers = {}
    for name in ('CONTEXT_MAP', 'FINAL_OUTLINE'):
        matches = list(re.finditer(
            r'^[ \t]*(?:#{1,6}[ \t]+)?\u3010' + name + r'\u3011[ \t]*(?:\r?\n|$)',
            text, re.MULTILINE,
        ))
        if len(matches) != 1:
            raise GuardError('ambiguous_output_blocks')
        markers[name] = matches[0]
    first, final = markers['CONTEXT_MAP'], markers['FINAL_OUTLINE']
    if first.start() >= final.start() or text[:first.start()].strip():
        raise GuardError('invalid_output_block_order')
    context = text[first.end():final.start()]
    outline = text[final.end():]
    if not context.strip() or not outline.strip():
        raise GuardError('empty_output_block')
    # Preserve each captured body exactly; remove only structural marker lines.
    return context, outline


class FakeProvider:
    """Consumes prewritten synthetic responses; never constructs a model client."""

    def __init__(self, task_dir: Path, response_file: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.json', response_file):
            raise GuardError('invalid_response_filename')
        raw = read_regular(task_dir / response_file)
        spec = object_json(raw, 'invalid_fake_responses')
        if type(spec.get('version')) is not int or spec.get('version') != 1 or set(spec) != {'version', 'responses'}:
            raise GuardError('invalid_fake_responses')
        responses = spec['responses']
        if not isinstance(responses, dict) or set(responses) != set(GROUPS):
            raise GuardError('invalid_fake_responses')
        for response in responses.values():
            if not isinstance(response, dict) or set(response) != {'text', 'status', 'usage', 'finish_reason'}:
                raise GuardError('invalid_fake_responses')
            if not isinstance(response['text'], str):
                raise GuardError('invalid_fake_responses')
            try:
                encoded_text = response['text'].encode()
            except UnicodeError:
                raise GuardError('invalid_fake_responses') from None
            if len(encoded_text) > MAX_FAKE_RESPONSE_BYTES:
                raise GuardError('invalid_fake_responses')
            if response['status'] not in ('ok', 'refused', 'timeout', 'transport_error', 'rate_limit'):
                raise GuardError('invalid_fake_responses')
            if response['finish_reason'] not in ('STOP', 'MAX_TOKENS', 'ERROR'):
                raise GuardError('invalid_fake_responses')
            usage = response['usage']
            if not isinstance(usage, dict) or set(usage) != {'prompt_tokens', 'output_tokens', 'total_tokens'}:
                raise GuardError('invalid_fake_responses')
            if any(type(v) is not int or v < 0 for v in usage.values()):
                raise GuardError('invalid_fake_responses')
            if usage['total_tokens'] < usage['prompt_tokens'] + usage['output_tokens']:
                raise GuardError('invalid_fake_responses')
        self.responses = responses
        self.fixture_bytes = raw
        self.response_hash = digest(raw)
        self.calls = 0

    def invoke(self, group: str, prompt: dict[str, str]) -> dict[str, Any]:
        self.calls += 1
        return self.responses[group]


def run_pair(
    task_dir: Path, output_root: Path, *, seed: int | None = None,
    max_requests: int = 2, max_output_tokens: int = 4096,
    response_file: str = 'dry-run-responses.json',
) -> dict[str, Any]:
    base = scoped_output_root(output_root)
    run_dir = base / secrets.token_hex(16)
    run_dir.mkdir(mode=0o700)
    receipt: dict[str, Any] = {
        'version': 1, 'dry_run': True, 'status': 'blocked',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'runner_sha256': digest(Path(__file__).read_bytes()),
        'python_version': sys.version.split()[0], 'provider': 'fake_fixture', 'model': None,
        'real_model_calls': 0, 'model_judging_calls': 0,
        'simulated_requests': 0, 'results': [],
    }
    exit_code = 2
    try:
        if type(max_requests) is not int or max_requests != 2:
            raise GuardError('pair_requires_two_simulated_requests')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
            raise GuardError('invalid_simulated_output_budget')
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2 ** 64):
            raise GuardError('invalid_order_seed')
        task = load_task(task_dir)
        provider = FakeProvider(task['directory'], response_file)
        order_seed = secrets.randbits(64) if seed is None else seed
        rng = random.Random(order_seed)
        order = list(GROUPS)
        rng.shuffle(order)
        receipt.update(
            task_id=task['id'], manifest_sha256=task['manifest_hash'],
            input_sha256=task['hashes'], response_fixture_sha256=provider.response_hash,
            order_seed=order_seed, provider_seed=None, execution_order=order,
            simulated_limits={'requests': 2, 'output_tokens_per_request': max_output_tokens},
            authorization_snapshot={'source_type': 'synthetic_fixture',
                                    'input_and_protocol_approved': True,
                                    'real_execution_authorized': False,
                                    'external_transmission_authorized': False},
        )
        for name, raw in task['files'].items():
            write_new(run_dir / 'internal' / 'inputs' / name, raw)
        write_new(run_dir / 'internal' / 'inputs' / response_file, provider.fixture_bytes)
        write_new(run_dir / 'review' / 'author-review-form.md', task['files']['author-review-form.md'])
        review = []
        for group in order:
            prompt = compile_prompt(task, group)
            write_json(run_dir / 'internal' / 'prompts' / (group + '.json'), prompt)
            started_at = datetime.now(timezone.utc).isoformat()
            start = time.perf_counter()
            response = provider.invoke(group, prompt)
            receipt['simulated_requests'] = provider.calls
            elapsed_ms = (time.perf_counter() - start) * 1000
            raw = response['text'].encode()
            write_new(run_dir / 'internal' / 'raw' / (group + '.txt'), raw)
            result = {
                'condition': group, 'attempt': 1, 'status': response['status'],
                'started_at_utc': started_at,
                'completed_at_utc': datetime.now(timezone.utc).isoformat(),
                'finish_reason': response['finish_reason'], 'usage': response['usage'],
                'usage_source': 'synthetic_fixture', 'elapsed_ms': elapsed_ms,
                'latency_source': 'local_fake_call', 'output_sha256': digest(raw),
                'output_bytes': len(raw), 'blinding': 'degraded' if group == 'A1' else 'intact',
            }
            if response['status'] == 'ok' and response['finish_reason'] == 'ERROR':
                result.update(status='finish_error', error_code='error_finish_reason')
            elif response['status'] == 'ok' and (
                response['finish_reason'] == 'MAX_TOKENS'
                or response['usage']['output_tokens'] > max_output_tokens
            ):
                result['status'] = 'truncated'
            final = response['text']
            if result['status'] == 'ok' and not final.strip():
                result['status'] = 'empty_response'
            if group == 'A1' and result['status'] == 'ok':
                try:
                    context, final = split_context_map(final)
                    write_new(run_dir / 'internal' / 'context-map.txt', context.encode())
                    result['blinding'] = 'intact'
                except GuardError as error:
                    result.update(status='parse_error', error_code=str(error), blinding='degraded')
                    final = response['text']
            neutral_id = secrets.token_hex(8)
            result['output_id'] = neutral_id
            # Failed and partial output remains readable, but is not a valid pair.
            write_new(run_dir / 'review' / (neutral_id + '.md'), final.encode())
            review.append({'output_id': neutral_id, 'file': neutral_id + '.md',
                           'status': result['status'], 'blinding': result['blinding']})
            receipt['results'].append(result)
        rng.shuffle(review)
        write_json(run_dir / 'review' / 'index.json', {
            'dry_run': True, 'real_model_calls': 0,
            'notice': 'Synthetic workflow outputs, not model or literary results.',
            'outputs': review,
        })
        try:
            unchanged = digest(read_regular(task['directory'] / 'manifest.json')) == task['manifest_hash']
            for name, expected in task['hashes'].items():
                unchanged = digest(read_regular(task['directory'] / name)) == expected and unchanged
            unchanged = digest(read_regular(task['directory'] / response_file)) == provider.response_hash and unchanged
        except GuardError as error:
            unchanged = False
            receipt['source_integrity_error'] = str(error)
        receipt['source_unchanged'] = unchanged
        success = unchanged and all(r['status'] == 'ok' for r in receipt['results'])
        receipt.update(status='completed' if success else 'completed_with_failures',
                       simulated_requests=provider.calls, comparable_pair=success)
        exit_code = 0 if success else 1
    except GuardError as error:
        receipt['error_code'] = str(error)
    except OSError:
        receipt['error_code'] = 'artifact_io_error'
    receipt['exit_code'] = exit_code
    receipt['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    receipt['artifact_sha256'] = {}
    for path in run_dir.rglob('*'):
        no_symlinks(path)
        if path.is_file():
            receipt['artifact_sha256'][str(path.relative_to(run_dir))] = digest(path.read_bytes())
    write_json(run_dir / 'internal' / 'receipt.json', receipt)
    return {
        'status': receipt['status'], 'exit_code': exit_code,
        'dry_run': True, 'real_model_calls': 0, 'model_judging_calls': 0,
        'simulated_requests': receipt['simulated_requests'],
        'review_directory': str(run_dir / 'review'),
        'receipt': str(run_dir / 'internal' / 'receipt.json'),
        **({'error_code': receipt['error_code']} if 'error_code' in receipt else {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=RUNTIME_ROOT / 'outline-experiments')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--max-requests', type=int, default=2)
    parser.add_argument('--max-output-tokens', type=int, default=4096)
    parser.add_argument('--responses-file', default='dry-run-responses.json')
    args = parser.parse_args(argv)
    try:
        result = run_pair(args.task_dir, args.output_root, seed=args.seed,
                          max_requests=args.max_requests,
                          max_output_tokens=args.max_output_tokens,
                          response_file=args.responses_file)
    except (GuardError, OSError) as error:
        code = str(error) if isinstance(error, GuardError) else 'artifact_io_error'
        print(json.dumps({'status': 'blocked', 'error_code': code,
                          'real_model_calls': 0, 'model_judging_calls': 0}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
