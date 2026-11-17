"""Contract tests for the public dry-run CLI and its real file pipeline."""

import json
from pathlib import Path
import shutil
import socket
import uuid

import pytest

from scripts.experiments import outline_runner as runner


FIXTURE = Path(__file__).resolve().parents[1] / 'scripts/experiments/fixtures/outline-dry-run'


@pytest.fixture
def task(tmp_path):
    directory = tmp_path / 'task'
    shutil.copytree(FIXTURE, directory)
    return directory


@pytest.fixture
def output():
    directory = runner.RUNTIME_ROOT / 'outline-test-runs' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    yield directory
    # Only this test's freshly created directory, never a caller-owned path.
    shutil.rmtree(directory)


def change_json(path, update):
    value = json.loads(path.read_text())
    update(value)
    path.write_text(json.dumps(value) + '\n')


def receipt(result):
    return json.loads(Path(result['receipt']).read_text())


def execute(task, output, **kwargs):
    result = runner.run_pair(task, output, seed=17, **kwargs)
    return result, receipt(result)


def test_full_cli_is_offline_and_separates_review_from_internal(task, output, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise AssertionError('Network access attempted')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-secret-that-must-not-be-read')
    code = runner.main(['--dry-run', '--task-dir', str(task), '--output-root', str(output), '--seed', '17'])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    saved = receipt(result)
    assert saved['real_model_calls'] == saved['model_judging_calls'] == 0
    assert saved['simulated_requests'] == 2
    assert saved['source_unchanged'] and saved['comparable_pair']
    assert {r['condition'] for r in saved['results']} == {'D0', 'A1'}
    assert all(r['attempt'] == 1 and r['usage_source'] == 'synthetic_fixture' for r in saved['results'])
    directory = Path(result['receipt']).parents[1]
    for group in runner.GROUPS:
        prompt = json.loads((directory / 'internal/prompts' / (group + '.json')).read_text())
        common = (task / 'common-input.md').read_text()
        assert prompt['user'].startswith(common + '\n\n')
        assert 'EVALUATION_ONLY_SENTINEL' not in str(prompt)
        assert 'AUTHOR_FORM_ONLY_SENTINEL' not in str(prompt)
        assert 'synthetic-secret-that-must-not-be-read' not in str(prompt)
    index = json.loads((directory / 'review/index.json').read_text())
    assert 'order_seed' not in index and 'execution_order' not in index
    assert len({r['output_id'] for r in saved['results']}) == 2
    fake = json.loads((task / 'dry-run-responses.json').read_text())['responses']
    for row in saved['results']:
        raw = (directory / 'internal/raw' / (row['condition'] + '.txt')).read_bytes()
        assert raw == fake[row['condition']]['text'].encode()
        visible = (directory / 'review' / (row['output_id'] + '.md')).read_bytes()
        if row['condition'] == 'D0':
            assert visible == raw
        else:
            assert visible == fake['A1']['text'].split('【FINAL_OUTLINE】\n', 1)[1].encode()
            assert b'CONTEXT_MAP' not in visible
    for relative, expected in saved['artifact_sha256'].items():
        assert runner.digest((directory / relative).read_bytes()) == expected


@pytest.mark.parametrize('filename', runner.REQUIRED_FILES)
def test_any_frozen_file_tampering_blocks_all_calls(task, output, filename):
    with (task / filename).open('ab') as stream:
        stream.write(b'\nchanged')
    result, saved = execute(task, output)
    assert result['exit_code'] == 2
    assert saved['error_code'] == 'input_hash_mismatch'
    assert saved['simulated_requests'] == 0 and saved['results'] == []


def test_private_pack_is_rejected_before_any_content_read(task, output, monkeypatch):
    change_json(task / 'manifest.json', lambda m: m['provenance'].update(source_type='author_private_outline_minimal_extract'))
    original = runner.read_regular
    reads = []
    def read(path):
        reads.append(path.name)
        assert path.name == 'manifest.json'
        return original(path)
    monkeypatch.setattr(runner, 'read_regular', read)
    result, saved = execute(task, output)
    assert saved['error_code'] == 'synthetic_task_required'
    assert reads == ['manifest.json'] and result['real_model_calls'] == 0


@pytest.mark.parametrize('update', [
    lambda m: m.update(author_approved=False),
    lambda m: m.update(dataset_frozen=False),
    lambda m: m['approval'].update(protocol_approved=False),
    lambda m: m.update(schema_version=True),
    lambda m: m['approval'].update(scope=[{}]),
    lambda m: m['protocol_approval'].update(scope=[]),
    lambda m: m.update(execution_authorized=True),
    lambda m: m['provenance'].update(external_model_transmission_authorized=True),
])
def test_approval_and_scope_guards_fail_without_calls(task, output, update):
    change_json(task / 'manifest.json', update)
    result, saved = execute(task, output)
    assert result['exit_code'] == 2
    assert saved['simulated_requests'] == 0 and saved['results'] == []


def test_approved_protocol_hash_must_match_file_hash(task, output):
    change_json(task / 'manifest.json', lambda m: m['protocol_approval']['file_sha256'].update({'pilot-protocol.md': '0' * 64}))
    result, saved = execute(task, output)
    assert saved['error_code'] == 'protocol_hash_mismatch' and result['simulated_requests'] == 0


@pytest.mark.parametrize('text', [
    'No block markers, but preserve this result.\n',
    '【CONTEXT_MAP】\nmap\n【FINAL_OUTLINE】\nfirst\n【FINAL_OUTLINE】\nsecond\n',
    '【FINAL_OUTLINE】\noutline\n【CONTEXT_MAP】\nmap\n',
    '【CONTEXT_MAP】not a marker line\nmap\n【FINAL_OUTLINE】\noutline\n',
    '【CONTEXT_MAP】\n\n【FINAL_OUTLINE】\noutline\n',
])
def test_ambiguous_a1_preserves_raw_and_declares_blinding_failure(task, output, text):
    change_json(task / 'dry-run-responses.json', lambda v: v['responses']['A1'].update(text=text))
    result, saved = execute(task, output)
    row = next(r for r in saved['results'] if r['condition'] == 'A1')
    assert row['status'] == 'parse_error' and row['blinding'] == 'degraded'
    assert result['exit_code'] == 1 and not saved['comparable_pair']
    assert (Path(result['review_directory']) / (row['output_id'] + '.md')).read_bytes() == text.encode()
    assert saved['simulated_requests'] == 2


def test_markdown_markers_preserve_crlf_body():
    context, outline = runner.split_context_map('## 【CONTEXT_MAP】\r\nmap\r\n## 【FINAL_OUTLINE】\r\noutline\r\n')
    assert context == 'map\r\n' and outline == 'outline\r\n'


@pytest.mark.parametrize('status', ['refused', 'timeout', 'transport_error', 'rate_limit'])
def test_failure_is_retained_without_retry_or_skipping_other_condition(task, output, status):
    change_json(task / 'dry-run-responses.json', lambda v: v['responses']['D0'].update(status=status, finish_reason='ERROR', text='simulated failure'))
    result, saved = execute(task, output)
    assert result['exit_code'] == 1
    assert len(saved['results']) == 2 and saved['simulated_requests'] == 2
    assert next(r for r in saved['results'] if r['condition'] == 'D0')['status'] == status
    assert next(r for r in saved['results'] if r['condition'] == 'A1')['status'] == 'ok'
    assert saved['real_model_calls'] == 0


def test_truncated_output_is_not_reported_as_comparable(task, output):
    change_json(task / 'dry-run-responses.json', lambda v: v['responses']['A1'].update(finish_reason='MAX_TOKENS'))
    result, saved = execute(task, output)
    assert result['exit_code'] == 1
    assert next(r for r in saved['results'] if r['condition'] == 'A1')['status'] == 'truncated'
    assert not saved['comparable_pair']


@pytest.mark.parametrize('budget', [0, 1, 3])
def test_request_budget_never_starts_a_partial_or_expanded_pair(task, output, budget):
    result, saved = execute(task, output, max_requests=budget)
    assert result['exit_code'] == 2 and saved['simulated_requests'] == 0


def test_output_budget_applies_to_map_and_final_combined(task, output):
    result, saved = execute(task, output, max_output_tokens=50)
    assert next(r for r in saved['results'] if r['condition'] == 'D0')['status'] == 'ok'
    assert next(r for r in saved['results'] if r['condition'] == 'A1')['status'] == 'truncated'
    assert result['exit_code'] == 1


def test_output_outside_runtime_is_not_created(task, tmp_path):
    # The repository wrapper intentionally puts tmp_path inside .runtime.
    outside = runner.RUNTIME_ROOT.parent / ('outside-runtime-' + uuid.uuid4().hex)
    with pytest.raises(runner.GuardError, match='output_outside_runtime'):
        runner.run_pair(task, outside)
    assert not outside.exists()


def test_symlink_input_and_response_traversal_are_rejected(task, output, tmp_path):
    common = task / 'common-input.md'
    target = tmp_path / 'other.md'
    target.write_bytes(common.read_bytes())
    common.unlink()
    try:
        common.symlink_to(target)
    except OSError:
        pytest.skip('Symlink creation unavailable on this host')
    result, saved = execute(task, output)
    assert saved['error_code'] == 'symlink_not_allowed'
    common.unlink()
    common.write_bytes(target.read_bytes())
    result, saved = execute(task, output, response_file='../secret.json')
    assert saved['error_code'] == 'invalid_response_filename' and saved['simulated_requests'] == 0


@pytest.mark.parametrize('changed_file', ['common-input.md', 'dry-run-responses.json'])
def test_mutation_during_calls_does_not_change_second_prompt_or_restore_source(task, output, monkeypatch, changed_file):
    original = runner.FakeProvider
    initial = (task / 'common-input.md').read_bytes()
    class MutatingProvider(original):
        def invoke(self, group, prompt):
            response = super().invoke(group, prompt)
            if self.calls == 1:
                (task / changed_file).write_bytes(b'changed during the pair')
            return response
    monkeypatch.setattr(runner, 'FakeProvider', MutatingProvider)
    result, saved = execute(task, output)
    assert saved['simulated_requests'] == 2 and not saved['source_unchanged']
    assert result['exit_code'] == 1
    directory = Path(result['receipt']).parents[1]
    for group in runner.GROUPS:
        prompt = json.loads((directory / 'internal/prompts' / (group + '.json')).read_text())
        assert prompt['user'].startswith(initial.decode() + '\n\n')
    assert (task / changed_file).read_bytes() == b'changed during the pair'


def test_seed_reproduces_execution_order_but_not_ids(task, output):
    first, a = execute(task, output)
    second, b = execute(task, output)
    assert a['execution_order'] == b['execution_order']
    assert first['receipt'] != second['receipt']
    assert {r['output_id'] for r in a['results']}.isdisjoint({r['output_id'] for r in b['results']})


def test_cli_has_no_live_or_provider_option(task, output):
    with pytest.raises(SystemExit) as error:
        runner.main(['--dry-run', '--task-dir', str(task), '--output-root', str(output), '--provider', 'gemini'])
    assert error.value.code == 2


def test_symlink_output_root_does_not_write_through(task, output, tmp_path):
    target = tmp_path / 'outside-target'
    target.mkdir()
    link = output / 'linked-root'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation unavailable on this host')
    with pytest.raises(runner.GuardError, match='symlink_not_allowed'):
        runner.run_pair(task, link)
    assert list(target.iterdir()) == []


def test_invalid_approved_template_layout_still_blocks(task, output):
    file = task / 'generation-conditions.md'
    file.write_text(file.read_text() + '\n```text\nextra block\n```\n')
    sha = runner.digest(file.read_bytes())
    def update(m):
        m['files']['generation-conditions.md']['sha256'] = sha
        m['protocol_approval']['file_sha256']['generation-conditions.md'] = sha
    change_json(task / 'manifest.json', update)
    result, saved = execute(task, output)
    assert saved['error_code'] == 'invalid_condition_templates'
    assert result['simulated_requests'] == 0


def test_invalid_fake_usage_does_not_start_requests(task, output):
    change_json(task / 'dry-run-responses.json', lambda v: v['responses']['A1']['usage'].update(output_tokens=-1))
    result, saved = execute(task, output)
    assert saved['error_code'] == 'invalid_fake_responses'
    assert result['simulated_requests'] == 0


def test_existing_run_is_not_overwritten(task, output, monkeypatch):
    first, saved = execute(task, output)
    path = Path(first['receipt'])
    original_bytes = path.read_bytes()
    run_id = path.parents[1].name
    original = runner.secrets.token_hex
    monkeypatch.setattr(runner.secrets, 'token_hex', lambda n: run_id if n == 16 else original(n))
    with pytest.raises(FileExistsError):
        execute(task, output)
    assert path.read_bytes() == original_bytes


def test_unencodable_fake_output_is_recorded_as_blocked(task, output):
    change_json(task / 'dry-run-responses.json', lambda v: v['responses']['A1'].update(text='\ud800'))
    result, saved = execute(task, output)
    assert saved['error_code'] == 'invalid_fake_responses'
    assert result['simulated_requests'] == 0


def test_condition_labels_determine_prompt_even_if_sections_are_reordered(task, output):
    file = task / 'generation-conditions.md'
    text = file.read_text()
    shared, sections = text.split('## D0\n', 1)
    d0, a1 = sections.split('## A1\n', 1)
    file.write_text(shared + '## A1\n' + a1 + '\n## D0\n' + d0)
    sha = runner.digest(file.read_bytes())
    def update(m):
        m['files']['generation-conditions.md']['sha256'] = sha
        m['protocol_approval']['file_sha256']['generation-conditions.md'] = sha
    change_json(task / 'manifest.json', update)
    loaded = runner.load_task(task)
    direct = runner.compile_prompt(loaded, 'D0')
    mapped = runner.compile_prompt(loaded, 'A1')
    assert 'Directly propose' in direct['user'] and 'CONTEXT_MAP' not in direct['user']
    assert 'CONTEXT_MAP' in mapped['user'] and 'Directly propose' not in mapped['user']


def approve_conditions(task, text):
    file = task / 'generation-conditions.md'
    file.write_bytes(text.encode())
    sha = runner.digest(file.read_bytes())
    def update(manifest):
        manifest['files']['generation-conditions.md']['sha256'] = sha
        manifest['protocol_approval']['file_sha256']['generation-conditions.md'] = sha
    change_json(task / 'manifest.json', update)


@pytest.mark.parametrize('example', [
    '```text\nShared instruction.\n## A1\n```\n',
    '```text\nShared instruction.\n```\n\n```python\n## A1\n```\n',
    '```text\nShared instruction.\n```\n\n````text\n## A1\n````\n',
    '```text\nShared instruction.\n```\n\n~~~text\n## A1\n~~~\n',
])
def test_fenced_headings_cannot_supply_missing_condition_roles(task, output, capsys, example):
    approve_conditions(task, '## Shared system instruction\n\n' + example +
                       '\n```text\nDIRECT_TAIL\n## D0\n```\n'
                       '\n```text\nMAP_TAIL\n```\n')
    code = runner.main(['--dry-run', '--task-dir', str(task), '--output-root', str(output)])
    saved = receipt(json.loads(capsys.readouterr().out))
    assert code == 2
    assert saved['error_code'] == 'invalid_condition_templates'
    assert saved['simulated_requests'] == 0 and saved['results'] == []
    assert not saved.get('comparable_pair', False)


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_structural_roles_preserve_fenced_heading_content(task, output, newline):
    text = ('## Shared system instruction\n\n```text\nShared.\n## A1\n```\n'
            '\n```python\n## D0\n```\n'
            '\n## A1\n\n```text\nMAP_TAIL\n## D0\n```\n'
            '\n## D0\n\n```text\nDIRECT_TAIL\n## A1\n```\n')
    approve_conditions(task, text.replace('\n', newline))
    result, saved = execute(task, output)
    assert result['exit_code'] == 0 and saved['comparable_pair']
    directory = Path(result['receipt']).parents[1]
    prompts = {g: json.loads((directory / 'internal/prompts' / (g + '.json')).read_text())
               for g in runner.GROUPS}
    assert prompts['D0']['system'] == 'Shared.' + newline + '## A1' + newline
    common = (task / 'common-input.md').read_bytes().decode()
    assert prompts['D0']['user'] == common + '\n\nDIRECT_TAIL' + newline + '## A1' + newline
    assert prompts['A1']['user'] == common + '\n\nMAP_TAIL' + newline + '## D0' + newline


@pytest.mark.parametrize('group', runner.GROUPS)
@pytest.mark.parametrize('empty,budget', [(False, 4096), (True, 4096), (False, 1)])
def test_error_finish_reason_is_retained_as_failure(task, output, capsys, group, empty, budget):
    def update(spec):
        spec['responses'][group]['finish_reason'] = 'ERROR'
        if empty:
            spec['responses'][group]['text'] = ''
    change_json(task / 'dry-run-responses.json', update)
    raw = json.loads((task / 'dry-run-responses.json').read_text())['responses'][group]['text']
    code = runner.main(['--dry-run', '--task-dir', str(task), '--output-root', str(output),
                        '--max-output-tokens', str(budget)])
    result = json.loads(capsys.readouterr().out)
    saved = receipt(result)
    row = next(r for r in saved['results'] if r['condition'] == group)
    assert code == 1 and saved['status'] == 'completed_with_failures'
    assert row['status'] == 'finish_error' and row['finish_reason'] == 'ERROR'
    assert row['error_code'] == 'error_finish_reason'
    assert row['blinding'] == ('degraded' if group == 'A1' else 'intact')
    assert not saved['comparable_pair']
    assert saved['simulated_requests'] == 2 and len(saved['results']) == 2
    assert saved['real_model_calls'] == saved['model_judging_calls'] == 0
    directory = Path(result['receipt']).parents[1]
    assert (directory / 'internal/raw' / (group + '.txt')).read_bytes() == raw.encode()
    assert (directory / 'review' / (row['output_id'] + '.md')).read_bytes() == raw.encode()
    if budget == 4096:
        assert next(r for r in saved['results'] if r['condition'] != group)['status'] == 'ok'


@pytest.mark.parametrize('status', ['refused', 'timeout', 'transport_error', 'rate_limit'])
def test_a1_error_finish_keeps_explicit_provider_failure_status(task, output, status):
    change_json(task / 'dry-run-responses.json', lambda s: s['responses']['A1'].update(
        status=status, finish_reason='ERROR'))
    result, saved = execute(task, output)
    assert result['exit_code'] == 1 and not saved['comparable_pair']
    assert next(r for r in saved['results'] if r['condition'] == 'A1')['status'] == status
    assert next(r for r in saved['results'] if r['condition'] == 'D0')['status'] == 'ok'
    assert saved['simulated_requests'] == 2
