"""片段标签推断在外部 LLM 调用后的并发写入边界测试。"""

from types import SimpleNamespace

import pytest
from django.utils import timezone

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


def _endpoint(fragment):
    return f'/api/examples/fragments/{fragment.id}/infer-tags/'


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
        'order': 1,
        'fragment_type': 'story',
    }
    values.update(overrides)
    return Fragment.objects.create(**values)


def _stub_provider(monkeypatch):
    provider = object()
    monkeypatch.setattr(
        'examples.views.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    return provider


def _assert_state_conflict(response):
    assert response.status_code == 409
    assert response.data == {
        'code': 'fragment_state_conflict',
        'detail': '片段状态已变化，请刷新后重试',
    }


@pytest.mark.django_db
def test_applies_inferred_tags_when_fragment_is_unchanged(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    original_embedding = [0.3] * 768
    fragment = _fragment(
        article,
        is_confirmed=True,
        embedding=original_embedding,
    )
    provider = _stub_provider(monkeypatch)
    inferred_tags = {'scene_type': '动作'}
    calls = []

    def fake_infer(text, resolved_provider, *, language, user):
        calls.append((text, resolved_provider, language, user))
        return inferred_tags

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(fragment),
        data={'lang': 'en'},
        format='json',
    )

    assert response.status_code == 200
    assert calls == [('原始正文', provider, 'en', user)]
    assert response.data['tags'] == inferred_tags
    assert response.data['is_confirmed'] is False
    fragment.refresh_from_db()
    assert fragment.tags == inferred_tags
    assert fragment.is_confirmed is False
    assert fragment.embedding is None


@pytest.mark.django_db
def test_rejects_a_concurrent_text_edit_without_overwriting_it(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, start_line=2, end_line=2)
    _stub_provider(monkeypatch)

    def fake_infer(*_args, **_kwargs):
        Fragment.objects.filter(id=fragment.id).update(
            text='并发修改后的正文',
            fragment_type='skip',
            start_line=4,
            end_line=5,
            updated_at=timezone.now(),
        )
        return {'scene_type': '动作'}

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    fragment.refresh_from_db()
    assert fragment.text == '并发修改后的正文'
    assert fragment.fragment_type == 'skip'
    assert (fragment.start_line, fragment.end_line) == (4, 5)
    assert fragment.tags == {'scene_type': '日常'}
    _assert_state_conflict(response)


@pytest.mark.django_db
def test_rejects_a_concurrent_tag_edit_without_overwriting_it(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    _stub_provider(monkeypatch)
    concurrent_tags = {'scene_type': '争执'}

    def fake_infer(*_args, **_kwargs):
        Fragment.objects.filter(id=fragment.id).update(
            tags=concurrent_tags,
            updated_at=timezone.now(),
        )
        return {'scene_type': '动作'}

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    fragment.refresh_from_db()
    assert fragment.tags == concurrent_tags
    assert fragment.is_confirmed is False
    assert fragment.embedding is None
    _assert_state_conflict(response)


@pytest.mark.django_db
def test_rejects_a_concurrent_confirmation_without_clearing_it(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    _stub_provider(monkeypatch)
    confirmed_embedding = [0.2] * 768

    def fake_infer(*_args, **_kwargs):
        Fragment.objects.filter(id=fragment.id).update(
            is_confirmed=True,
            embedding=confirmed_embedding,
            updated_at=timezone.now(),
        )
        return {'scene_type': '动作'}

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    fragment.refresh_from_db()
    assert fragment.tags == {'scene_type': '日常'}
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == confirmed_embedding
    _assert_state_conflict(response)


@pytest.mark.django_db
def test_rejects_a_concurrent_deletion_without_recreating_the_fragment(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    fragment_id = fragment.id
    _stub_provider(monkeypatch)

    def fake_infer(*_args, **_kwargs):
        Fragment.objects.filter(id=fragment_id).delete()
        return {'scene_type': '动作'}

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    assert not Fragment.objects.filter(id=fragment_id).exists()
    _assert_state_conflict(response)


@pytest.mark.django_db
def test_rejects_a_later_inference_without_overwriting_the_first_result(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    _stub_provider(monkeypatch)
    first_result = {'scene_type': '争执'}

    def fake_infer(*_args, **_kwargs):
        Fragment.objects.filter(id=fragment.id).update(
            tags=first_result,
            is_confirmed=False,
            embedding=None,
            updated_at=timezone.now(),
        )
        return {'scene_type': '动作'}

    monkeypatch.setattr('examples.views.infer_tags', fake_infer)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    fragment.refresh_from_db()
    assert fragment.tags == first_result
    assert fragment.is_confirmed is False
    assert fragment.embedding is None
    _assert_state_conflict(response)
