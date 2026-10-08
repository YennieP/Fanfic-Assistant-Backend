"""文章自动切割的两阶段替换与事务契约测试。"""

import json
import pytest
from django.db import connection

from examples.llm_pipeline import (
    MAX_CHARS_PER_CHUNK,
    SEGMENTATION_MAX_OUTPUT_TOKENS,
    _split_numbered_lines,
    segment_article,
)
from examples.models import Article, Fragment
from examples.views import _validate_segment_results
from tests.capacity_samples import (
    high_line_density_sample,
    near_limit_narrative_sample,
)
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider


class RecordingProvider(FakeProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=2000):
        self.calls.append({
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'max_tokens': max_tokens,
        })
        return super().complete(system_prompt, user_prompt, max_tokens)


def _endpoint(article):
    return f'/api/examples/articles/{article.id}/segment/'


def _article(user, character):
    return Article.objects.create(
        owner=user,
        character=character,
        title='测试文章',
        content='零\n一\n已确认\n三\n四',
    )


def _fragment(article, text, *, confirmed, start, end, order):
    return Fragment.objects.create(
        owner=article.owner,
        article=article,
        character=article.character,
        text=text,
        is_confirmed=confirmed,
        fragment_type='story',
        start_line=start,
        end_line=end,
        order=order,
    )


def _setup_article(make_user_with_key, api_client):
    user = make_user_with_key()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    confirmed = _fragment(
        article, '已确认', confirmed=True, start=2, end=2, order=2,
    )
    first_draft = _fragment(
        article, '上一轮开头草稿', confirmed=False, start=0, end=1, order=0,
    )
    second_draft = _fragment(
        article, '上一轮结尾草稿', confirmed=False, start=3, end=4, order=3,
    )
    api_client.force_authenticate(user=user)
    return article, confirmed, first_draft, second_draft


def _result_for(content, global_start):
    lines = content.splitlines()
    return [{
        'text': content,
        'type': 'story',
        'start': global_start,
        'end': global_start + len(lines) - 1,
    }]


def test_numbered_chunk_limit_counts_newline_separators_for_many_short_lines():
    numbered = '\n'.join(f'{index}: x' for index in range(1000))

    chunks = _split_numbered_lines(numbered)

    assert len(chunks) > 1
    assert sum(line_count for _, line_count in chunks) == 1000
    assert all(len(chunk) <= MAX_CHARS_PER_CHUNK for chunk, _ in chunks)


def test_single_oversized_source_line_is_not_split_inside_its_line_number():
    numbered = f'120: {"x" * MAX_CHARS_PER_CHUNK}'

    chunks = _split_numbered_lines(numbered)

    assert chunks == [(numbered, 1)]


@pytest.mark.parametrize(
    ('sample_factory', 'minimum_lines', 'minimum_numbered_chars'),
    [
        (near_limit_narrative_sample, 80, 2800),
        (high_line_density_sample, 200, 2700),
    ],
    ids=['near-limit-narrative', 'high-line-density'],
)
def test_dep003_capacity_samples_each_fit_one_near_limit_chunk(
    sample_factory, minimum_lines, minimum_numbered_chars,
):
    content = sample_factory()
    numbered = '\n'.join(
        f'{index}: {line}' for index, line in enumerate(content.splitlines())
    )

    chunks = _split_numbered_lines(numbered)

    assert len(content.splitlines()) >= minimum_lines
    assert minimum_numbered_chars <= len(numbered) <= MAX_CHARS_PER_CHUNK
    assert chunks == [(numbered, len(content.splitlines()))]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('content', 'provider_ranges', 'expected_ranges'),
    [
        (
            '甲一\n甲二\n\n乙一\n乙二',
            [(0, 1, 'story'), (3, 4, 'story')],
            [(0, 1), (3, 4)],
        ),
        (
            '甲一\n甲二\n\n乙一\n乙二',
            [(0, 1, 'story'), (2, 2, 'skip'), (3, 4, 'story')],
            [(0, 1), (3, 4)],
        ),
        (
            '\n正文',
            [(1, 1, 'story')],
            [(1, 1)],
        ),
    ],
    ids=['blank-between-segments', 'blank-only-segment-filtered', 'leading-blank'],
)
def test_real_segmentation_pipeline_allows_unassigned_blank_lines(
    content, provider_ranges, expected_ranges,
):
    provider = FakeProvider(complete_text=json.dumps({
        'segments': [
            {'start': start, 'end': end, 'type': fragment_type}
            for start, end, fragment_type in provider_ranges
        ],
    }))

    results = segment_article(content, provider)
    lines = content.splitlines()
    validated = _validate_segment_results(results, lines, 0, len(lines) - 1)

    assert [(item['start'], item['end']) for item in validated] == expected_ranges


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('article_lines', 'gap_start', 'gap_end', 'provider_ranges', 'expected_ranges'),
    [
        (
            ['', '开头正文', '已确认'],
            0,
            1,
            [(1, 1, 'story')],
            [(1, 1)],
        ),
        (
            ['已确认前文', '', '中间一', '', '中间二', '已确认后文'],
            1,
            4,
            [(2, 2, 'story'), (4, 4, 'story')],
            [(2, 2), (4, 4)],
        ),
        (
            ['已确认前文', '结尾正文', ''],
            1,
            2,
            [(1, 1, 'story')],
            [(1, 1)],
        ),
    ],
    ids=['leading-gap', 'nonzero-middle-gap', 'trailing-gap'],
)
def test_real_segmentation_pipeline_uses_absolute_line_numbers_at_boundaries(
    article_lines, gap_start, gap_end, provider_ranges, expected_ranges,
):
    provider = RecordingProvider(complete_text=json.dumps({
        'segments': [
            {'start': start, 'end': end, 'type': fragment_type}
            for start, end, fragment_type in provider_ranges
        ],
    }))
    gap_content = '\n'.join(article_lines[gap_start:gap_end + 1])

    results = segment_article(gap_content, provider, global_start=gap_start)
    validated = _validate_segment_results(
        results, article_lines, gap_start, gap_end,
    )

    assert [(item['start'], item['end']) for item in validated] == expected_ranges


@pytest.mark.django_db
def test_segmentation_prompt_matches_the_validator_protocol_for_a_nonzero_gap():
    provider = RecordingProvider(complete_text=json.dumps({
        'segments': [
            {'start': 121, 'end': 121, 'type': 'story'},
            {'start': 123, 'end': 123, 'type': 'story'},
        ],
    }))

    segment_article(
        '\n正文一\n\n正文二',
        provider,
        global_start=120,
        prev_context='前置已确认内容',
        next_context='后置已确认内容',
    )

    call = provider.calls[0]
    system_prompt = call['system_prompt']
    user_prompt = call['user_prompt']
    assert SEGMENTATION_MAX_OUTPUT_TOKENS == 4000
    assert call['max_tokens'] == SEGMENTATION_MAX_OUTPUT_TOKENS
    assert '每个非空行必须恰好属于一个片段' in system_prompt
    assert '空白行可以并入相邻片段，也可以不分配' in system_prompt
    assert '不得为纯空白行单独创建片段' in system_prompt
    assert '不要从 0 重新编号' in system_prompt
    assert '"start": 0' not in system_prompt
    assert '本次允许返回的行号范围：120 到 123（均包含）' in user_prompt
    assert (
        '{"segments":[{"start":120,"end":123,"type":"story"}]}'
        in user_prompt
    )
    assert '120: ' in user_prompt
    assert '121: 正文一' in user_prompt
    assert '123: 正文二' in user_prompt


@pytest.mark.django_db
def test_segmentation_logs_safe_shape_when_provider_output_has_no_segments(caplog):
    provider = RecordingProvider(complete_text='这不是 JSON')

    with caplog.at_level('WARNING', logger='examples.llm_pipeline'):
        results = segment_article('正文', provider)

    assert results == []
    assert (
        'Segment chunk 0 produced no usable segments: '
        'response_length=8 parsed_type=dict parsed_keys=[] '
        'object_balance=0 array_balance=0 contains_segments_key=False'
        in caplog.text
    )
    assert '这不是 JSON' not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_api_replaces_old_draft_when_gap_starts_with_a_blank_line(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key()
    character = BaseCardFactory(owner=user)
    article = Article.objects.create(
        owner=user,
        character=character,
        title='前导空行',
        content='\n正文',
    )
    old_draft = _fragment(
        article,
        '上一轮草稿',
        confirmed=False,
        start=0,
        end=1,
        order=0,
    )
    provider = FakeProvider(complete_text=json.dumps({
        'segments': [{'start': 1, 'end': 1, 'type': 'story'}],
    }))
    monkeypatch.setattr('examples.views._get_provider', lambda _config: provider)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 200
    assert response.json()['count'] == 1
    assert not Fragment.objects.filter(id=old_draft.id).exists()
    assert list(
        article.fragments.filter(is_confirmed=False)
        .values_list('text', 'start_line', 'end_line')
    ) == [('正文', 1, 1)]


@pytest.mark.django_db(transaction=True)
def test_keeps_previous_drafts_when_a_later_gap_generation_fails(
    api_client, make_user_with_key, monkeypatch,
):
    article, confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    calls = []

    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())

    def fail_second_gap(content, _provider, *, global_start, **_kwargs):
        assert connection.in_atomic_block is False
        calls.append(global_start)
        if len(calls) == 2:
            raise RuntimeError('simulated second gap failure')
        return _result_for(content, global_start)

    monkeypatch.setattr('examples.views.segment_article', fail_second_gap)

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 500
    assert calls == [0, 3]
    assert Fragment.objects.filter(id=confirmed.id).exists()
    assert Fragment.objects.filter(id=first_draft.id).exists()
    assert Fragment.objects.filter(id=second_draft.id).exists()
    assert article.fragments.filter(is_confirmed=False).count() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    'generated',
    [
        [],
        [{'text': '零', 'type': 'story', 'start': 0, 'end': 0}],
        [{'text': '三\n四', 'type': 'story', 'start': 3, 'end': 5}],
        [
            {'text': '零\n一', 'type': 'story', 'start': 0, 'end': 1},
            {'text': '一', 'type': 'story', 'start': 1, 'end': 1},
        ],
    ],
    ids=['empty', 'incomplete', 'outside-gap', 'overlapping'],
)
def test_keeps_previous_drafts_when_a_gap_result_is_invalid(
    api_client, make_user_with_key, monkeypatch, generated,
):
    article, _confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())
    monkeypatch.setattr('examples.views.segment_article', lambda *_args, **_kwargs: generated)

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 503
    assert Fragment.objects.filter(id=first_draft.id).exists()
    assert Fragment.objects.filter(id=second_draft.id).exists()


@pytest.mark.django_db(transaction=True)
def test_replaces_all_previous_drafts_in_one_short_transaction(
    api_client, make_user_with_key, monkeypatch,
):
    article, confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())

    def generate_gap(content, _provider, *, global_start, **_kwargs):
        assert connection.in_atomic_block is False
        return _result_for(content, global_start)

    monkeypatch.setattr('examples.views.segment_article', generate_gap)
    original_bulk_create = Fragment.objects.bulk_create
    bulk_create_atomic_states = []

    def record_bulk_create(*args, **kwargs):
        bulk_create_atomic_states.append(connection.in_atomic_block)
        return original_bulk_create(*args, **kwargs)

    monkeypatch.setattr(Fragment.objects, 'bulk_create', record_bulk_create)

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 200
    assert response.json()['count'] == 2
    assert bulk_create_atomic_states == [True]
    assert Fragment.objects.filter(id=confirmed.id).exists()
    assert not Fragment.objects.filter(id=first_draft.id).exists()
    assert not Fragment.objects.filter(id=second_draft.id).exists()
    assert list(
        article.fragments.filter(is_confirmed=False)
        .order_by('start_line')
        .values_list('text', 'start_line', 'end_line')
    ) == [('零\n一', 0, 1), ('三\n四', 3, 4)]


@pytest.mark.django_db(transaction=True)
def test_success_replaces_an_old_draft_that_spans_a_gap(
    api_client, make_user_with_key, monkeypatch,
):
    article, _confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    spanning_draft = _fragment(
        article,
        '跨越已确认片段的旧草稿',
        confirmed=False,
        start=0,
        end=4,
        order=5,
    )
    legacy_draft = _fragment(
        article,
        '缺少行号的旧格式草稿',
        confirmed=False,
        start=None,
        end=None,
        order=6,
    )
    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())
    monkeypatch.setattr(
        'examples.views.segment_article',
        lambda content, _provider, *, global_start, **_kwargs: _result_for(content, global_start),
    )

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 200
    assert not Fragment.objects.filter(
        id__in=[first_draft.id, second_draft.id, spanning_draft.id, legacy_draft.id],
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_rolls_back_draft_deletion_when_bulk_insert_fails(
    api_client, make_user_with_key, monkeypatch,
):
    article, _confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())
    monkeypatch.setattr(
        'examples.views.segment_article',
        lambda content, _provider, *, global_start, **_kwargs: _result_for(content, global_start),
    )
    monkeypatch.setattr(
        Fragment.objects,
        'bulk_create',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('simulated insert failure')),
    )
    api_client.raise_request_exception = False

    response = api_client.post(_endpoint(article), format='json')

    assert response.status_code == 500
    assert Fragment.objects.filter(id=first_draft.id).exists()
    assert Fragment.objects.filter(id=second_draft.id).exists()
    assert article.fragments.filter(is_confirmed=False).count() == 2


@pytest.mark.django_db(transaction=True)
def test_retry_replaces_previous_drafts_only_after_a_complete_success(
    api_client, make_user_with_key, monkeypatch,
):
    article, _confirmed, first_draft, second_draft = _setup_article(
        make_user_with_key, api_client,
    )
    monkeypatch.setattr('examples.views._get_provider', lambda _config: object())
    call_count = 0

    def fail_once_then_generate(content, _provider, *, global_start, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError('simulated transient failure')
        return _result_for(content, global_start)

    monkeypatch.setattr('examples.views.segment_article', fail_once_then_generate)

    first_response = api_client.post(_endpoint(article), format='json')
    assert first_response.status_code == 500
    assert Fragment.objects.filter(id=first_draft.id).exists()
    assert Fragment.objects.filter(id=second_draft.id).exists()

    second_response = api_client.post(_endpoint(article), format='json')

    assert second_response.status_code == 200
    assert second_response.json()['count'] == 2
    assert not Fragment.objects.filter(id=first_draft.id).exists()
    assert not Fragment.objects.filter(id=second_draft.id).exists()
    assert article.fragments.filter(is_confirmed=False).count() == 2
