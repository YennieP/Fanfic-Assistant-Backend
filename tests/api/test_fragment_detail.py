"""普通片段 PATCH 的字段更新、版本与并发边界测试。"""

import pytest
from django.db.models.query import QuerySet
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


STATE_CONFLICT = {
    'code': 'fragment_state_conflict',
    'detail': '片段状态已变化，请刷新后重试',
}


def _endpoint(fragment):
    return f'/api/examples/fragments/{fragment.id}/'


def _article(user, character):
    return Article.objects.create(
        owner=user,
        character=character,
        title='测试文章',
        content='测试正文',
    )


def _fragment(article, **overrides):
    values = {
        'owner': article.owner,
        'article': article,
        'character': article.character,
        'text': '原始正文',
        'tags': {'scene_type': '日常'},
        'embedding': [0.2] * 768,
        'is_confirmed': True,
        'order': 1,
        'fragment_type': 'story',
        'start_line': 1,
        'end_line': 1,
    }
    values.update(overrides)
    return Fragment.objects.create(**values)


def _run_after_fragment_read(monkeypatch, fragment, action):
    """在 detail view 读到目标片段后确定性插入一次并发动作。"""
    original_get = QuerySet.get
    triggered = False

    def get_with_concurrent_action(queryset, *args, **kwargs):
        nonlocal triggered
        instance = original_get(queryset, *args, **kwargs)
        if (
            not triggered
            and queryset.model is Fragment
            and instance.id == fragment.id
        ):
            triggered = True
            action()
        return instance

    monkeypatch.setattr(QuerySet, 'get', get_with_concurrent_action)
    return lambda: triggered


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('payload', 'expected_text', 'expected_tags'),
    [
        ({'text': '修改后的正文'}, '修改后的正文', {'scene_type': '日常'}),
        ({'tags': {'scene_type': '动作'}}, '原始正文', {'scene_type': '动作'}),
    ],
)
def test_patch_updates_only_requested_fields_and_invalidates_confirmation(
    api_client, user_factory, payload, expected_text, expected_tags,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    original_updated_at = fragment.updated_at
    payload = {**payload, 'updatedAt': original_updated_at.isoformat()}
    api_client.force_authenticate(user=user)

    response = api_client.patch(_endpoint(fragment), data=payload, format='json')

    assert response.status_code == 200
    fragment.refresh_from_db()
    assert fragment.text == expected_text
    assert fragment.tags == expected_tags
    assert fragment.fragment_type == 'story'
    assert (fragment.start_line, fragment.end_line) == (1, 1)
    assert fragment.is_confirmed is False
    assert fragment.embedding is None
    assert fragment.updated_at > original_updated_at
    assert parse_datetime(response.data['updated_at']) == fragment.updated_at


@pytest.mark.django_db
def test_patch_without_client_version_remains_compatible_and_logs_safe_signal(
    api_client, user_factory, caplog,
):
    user = user_factory(username='private-user-marker')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    private_text = 'private-body-marker'
    api_client.force_authenticate(user=user)

    with caplog.at_level('INFO', logger='examples.views'):
        response = api_client.patch(
            _endpoint(fragment),
            data={'text': private_text},
            format='json',
        )

    assert response.status_code == 200
    fragment.refresh_from_db()
    assert fragment.text == private_text
    records = [
        record
        for record in caplog.records
        if record.name == 'examples.views'
    ]
    assert [record.getMessage() for record in records] == [
        'Fragment PATCH missing updatedAt',
    ]
    assert [record.levelname for record in records] == ['WARNING']
    assert 'private-user-marker' not in caplog.text
    assert 'private-body-marker' not in caplog.text
    assert str(fragment.id) not in caplog.text


@pytest.mark.django_db
def test_rejects_concurrent_text_and_layout_change_without_overwriting_it(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)

    def concurrent_change():
        Fragment.objects.filter(id=fragment.id).update(
            text='并发修改后的正文',
            fragment_type='skip',
            start_line=8,
            end_line=9,
            updated_at=timezone.now(),
        )

    was_triggered = _run_after_fragment_read(monkeypatch, fragment, concurrent_change)
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        _endpoint(fragment),
        data={'tags': {'scene_type': '动作'}},
        format='json',
    )

    assert was_triggered()
    assert response.status_code == 409
    assert response.data == STATE_CONFLICT
    fragment.refresh_from_db()
    assert fragment.text == '并发修改后的正文'
    assert fragment.tags == {'scene_type': '日常'}
    assert fragment.fragment_type == 'skip'
    assert (fragment.start_line, fragment.end_line) == (8, 9)
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == [0.2] * 768


@pytest.mark.django_db
def test_rejects_concurrent_tag_change_without_overwriting_it(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    concurrent_tags = {'scene_type': '争执'}

    def concurrent_change():
        Fragment.objects.filter(id=fragment.id).update(
            tags=concurrent_tags,
            updated_at=timezone.now(),
        )

    _run_after_fragment_read(monkeypatch, fragment, concurrent_change)
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        _endpoint(fragment),
        data={'text': '用户提交的新正文'},
        format='json',
    )

    assert response.status_code == 409
    assert response.data == STATE_CONFLICT
    fragment.refresh_from_db()
    assert fragment.text == '原始正文'
    assert fragment.tags == concurrent_tags
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == [0.2] * 768


@pytest.mark.django_db
def test_does_not_resurrect_fragment_deleted_after_read(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    fragment_id = fragment.id

    def concurrent_delete():
        Fragment.objects.filter(id=fragment_id).delete()

    _run_after_fragment_read(monkeypatch, fragment, concurrent_delete)
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        _endpoint(fragment),
        data={'text': '不应保存的正文'},
        format='json',
    )

    assert response.status_code == 409
    assert response.data == STATE_CONFLICT
    assert not Fragment.objects.filter(id=fragment_id).exists()


@pytest.mark.django_db
def test_same_client_version_can_only_succeed_once(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, is_confirmed=False, embedding=None)
    original_version = fragment.updated_at.isoformat()
    api_client.force_authenticate(user=user)

    first_response = api_client.patch(
        _endpoint(fragment),
        data={'text': '第一次修改', 'updatedAt': original_version},
        format='json',
    )
    second_response = api_client.patch(
        _endpoint(fragment),
        data={'tags': {'scene_type': '第二次修改'}, 'updatedAt': original_version},
        format='json',
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.data == STATE_CONFLICT
    fragment.refresh_from_db()
    assert fragment.text == '第一次修改'
    assert fragment.tags == {'scene_type': '日常'}


@pytest.mark.django_db
def test_patch_requires_authentication(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)

    response = api_client.patch(
        _endpoint(fragment),
        data={'text': '未认证修改'},
        format='json',
    )

    assert response.status_code == 401
    fragment.refresh_from_db()
    assert fragment.text == '原始正文'


@pytest.mark.django_db
def test_patch_hides_another_users_fragment(api_client, user_factory):
    owner = user_factory()
    other_user = user_factory()
    character = BaseCardFactory(owner=owner)
    article = _article(owner, character)
    fragment = _fragment(article)
    api_client.force_authenticate(user=other_user)

    response = api_client.patch(
        _endpoint(fragment),
        data={'text': '越权修改', 'updatedAt': fragment.updated_at.isoformat()},
        format='json',
    )

    assert response.status_code == 404
    fragment.refresh_from_db()
    assert fragment.text == '原始正文'


@pytest.mark.django_db
def test_get_on_shared_detail_path_is_unchanged(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    api_client.force_authenticate(user=user)

    response = api_client.get(_endpoint(fragment))

    assert response.status_code == 200
    assert response.data['id'] == str(fragment.id)
    assert response.data['text'] == '原始正文'


@pytest.mark.django_db
def test_delete_on_shared_detail_path_is_unchanged(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    fragment_id = fragment.id
    api_client.force_authenticate(user=user)

    response = api_client.delete(_endpoint(fragment))

    assert response.status_code == 204
    assert not Fragment.objects.filter(id=fragment_id).exists()
