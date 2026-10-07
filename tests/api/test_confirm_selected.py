"""显式片段列表批量入库接口的范围与失败语义测试。"""

import pytest

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


def _endpoint(article):
    return f'/api/examples/articles/{article.id}/confirm-selected/'


def _article(user, character, title='测试文章'):
    return Article.objects.create(
        owner=user,
        character=character,
        title=title,
        content='测试正文',
    )


def _fragment(article, text, *, order, tags=None, confirmed=False, fragment_type='story'):
    return Fragment.objects.create(
        owner=article.owner,
        article=article,
        character=article.character,
        text=text,
        tags=tags or {'scene_type': '日常'},
        is_confirmed=confirmed,
        order=order,
        fragment_type=fragment_type,
    )


@pytest.mark.django_db
def test_confirms_only_explicitly_selected_fragments(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    selected = _fragment(article, '普通片段一', order=1)
    omitted = _fragment(article, '普通片段二', order=2)
    embedding_calls = []

    def fake_embedding(text, api_key):
        embedding_calls.append((text, api_key))
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(selected.id)]},
        format='json',
    )

    assert response.status_code == 200
    assert response.json() == {
        'confirmed': 1,
        'errors': 0,
        'errorIds': [],
    }
    selected.refresh_from_db()
    omitted.refresh_from_db()
    assert selected.is_confirmed is True
    assert omitted.is_confirmed is False
    assert len(embedding_calls) == 1


@pytest.mark.django_db
def test_rejects_the_whole_selection_before_embedding_when_an_id_is_outside_article(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character, '文章一')
    other_article = _article(user, character, '文章二')
    own_fragment = _fragment(article, '文章一片段', order=1)
    outside_fragment = _fragment(other_article, '文章二片段', order=1)
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: pytest.fail('invalid selection must not start embedding'),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(own_fragment.id), str(outside_fragment.id)]},
        format='json',
    )

    assert response.status_code == 400
    own_fragment.refresh_from_db()
    outside_fragment.refresh_from_db()
    assert own_fragment.is_confirmed is False
    assert outside_fragment.is_confirmed is False


@pytest.mark.django_db
def test_rejects_duplicate_fragment_ids(api_client, make_user_with_key, monkeypatch):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '普通片段', order=1)
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: pytest.fail('duplicate selection must not start embedding'),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(fragment.id), str(fragment.id)]},
        format='json',
    )

    assert response.status_code == 400
    fragment.refresh_from_db()
    assert fragment.is_confirmed is False


@pytest.mark.django_db
def test_rejects_the_whole_selection_before_embedding_when_tags_have_no_effective_value(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    valid_fragment = _fragment(article, '有效标签片段', order=1)
    ineffective_fragment = _fragment(
        article,
        '空标签片段',
        order=2,
        tags={'emotion': {}},
    )
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: pytest.fail('invalid selection must not start embedding'),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(valid_fragment.id), str(ineffective_fragment.id)]},
        format='json',
    )

    assert response.status_code == 400
    assert response.json()['error'] == '所选片段必须全部包含有效标签'
    valid_fragment.refresh_from_db()
    ineffective_fragment.refresh_from_db()
    assert valid_fragment.is_confirmed is False
    assert ineffective_fragment.is_confirmed is False
