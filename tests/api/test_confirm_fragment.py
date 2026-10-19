"""单片段向量化确认的并发写入边界测试。"""

import pytest
from django.utils import timezone

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


def _endpoint(fragment):
    return f'/api/examples/fragments/{fragment.id}/confirm/'


def _article(user, character):
    return Article.objects.create(
        owner=user,
        character=character,
        title='测试文章',
        content='测试正文',
    )


def _fragment(article, text='普通片段'):
    return Fragment.objects.create(
        owner=article.owner,
        article=article,
        character=article.character,
        text=text,
        tags={'scene_type': '日常'},
        order=1,
        fragment_type='story',
    )


@pytest.mark.django_db
def test_confirms_an_unchanged_fragment(api_client, make_user_with_key, monkeypatch):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: [0.1] * 768,
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    assert response.status_code == 200
    assert response.data['is_confirmed'] is True
    fragment.refresh_from_db()
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == [0.1] * 768


@pytest.mark.django_db
def test_rejects_a_concurrent_edit_without_overwriting_it(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '原始正文')

    def fake_embedding(_text, _api_key):
        Fragment.objects.filter(id=fragment.id).update(
            text='并发修改后的正文',
            tags={'scene_type': '争执'},
            fragment_type='skip',
            updated_at=timezone.now(),
        )
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    assert response.status_code == 409
    assert response.data == {
        'code': 'fragment_state_conflict',
        'detail': '片段状态已变化，请刷新后重试',
    }
    fragment.refresh_from_db()
    assert fragment.text == '并发修改后的正文'
    assert fragment.tags == {'scene_type': '争执'}
    assert fragment.fragment_type == 'skip'
    assert fragment.is_confirmed is False
    assert fragment.embedding is None


@pytest.mark.django_db
def test_rejects_a_concurrent_confirmation_without_replacing_its_embedding(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)

    def fake_embedding(_text, _api_key):
        Fragment.objects.filter(id=fragment.id).update(
            embedding=[0.2] * 768,
            is_confirmed=True,
            updated_at=timezone.now(),
        )
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    assert response.status_code == 409
    assert response.data == {
        'code': 'fragment_state_conflict',
        'detail': '片段状态已变化，请刷新后重试',
    }
    fragment.refresh_from_db()
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == [0.2] * 768


@pytest.mark.django_db
def test_rejects_a_concurrent_deletion_without_recreating_the_fragment(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article)
    fragment_id = fragment.id

    def fake_embedding(_text, _api_key):
        Fragment.objects.filter(id=fragment_id).delete()
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(_endpoint(fragment), format='json')

    assert response.status_code == 409
    assert response.data == {
        'code': 'fragment_state_conflict',
        'detail': '片段状态已变化，请刷新后重试',
    }
    assert not Fragment.objects.filter(id=fragment_id).exists()
