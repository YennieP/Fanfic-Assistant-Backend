"""片段 collection 路径契约测试：保留 GET 列表，移除已弃用的 POST 创建（API-001）。"""

import pytest

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


ENDPOINT = '/api/examples/fragments/'


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
        'is_confirmed': False,
        'order': 1,
        'fragment_type': 'story',
    }
    values.update(overrides)
    return Fragment.objects.create(**values)


@pytest.mark.django_db
def test_get_lists_only_current_user_fragments_with_filters(api_client, user_factory):
    user = user_factory()
    other = user_factory()
    character = BaseCardFactory(owner=user)
    other_character = BaseCardFactory(owner=user)
    article = _article(user, character)
    other_article = _article(user, other_character)
    confirmed = _fragment(article, is_confirmed=True)
    draft = _fragment(article, order=2)
    other_character_fragment = _fragment(other_article, is_confirmed=True)
    foreign_character = BaseCardFactory(owner=other)
    _fragment(_article(other, foreign_character), is_confirmed=True)
    api_client.force_authenticate(user=user)

    all_ids = {item['id'] for item in api_client.get(ENDPOINT).data}
    by_character = api_client.get(ENDPOINT, {'character': str(character.id)})
    confirmed_only = api_client.get(
        ENDPOINT,
        {'character_id': str(character.id), 'is_confirmed': 'true'},
    )

    assert all_ids == {str(confirmed.id), str(draft.id), str(other_character_fragment.id)}
    assert by_character.status_code == 200
    assert {item['id'] for item in by_character.data} == {str(confirmed.id), str(draft.id)}
    assert confirmed_only.status_code == 200
    assert [item['id'] for item in confirmed_only.data] == [str(confirmed.id)]


@pytest.mark.django_db
def test_get_requires_authentication(api_client):
    response = api_client.get(ENDPOINT)

    assert response.status_code == 401


@pytest.mark.django_db
def test_post_is_removed_without_creating_fragment(api_client, user_factory):
    user = user_factory()
    article = _article(user, BaseCardFactory(owner=user))
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data={'article_id': str(article.id), 'text': '不应创建的片段', 'order': 3},
        format='json',
    )

    assert response.status_code == 405
    assert response.data['code'] == 'method_not_allowed'
    assert Fragment.objects.count() == 0


@pytest.mark.django_db
def test_unauthenticated_post_is_rejected_without_creating_fragment(api_client, user_factory):
    user = user_factory()
    article = _article(user, BaseCardFactory(owner=user))

    response = api_client.post(
        ENDPOINT,
        data={'article_id': str(article.id), 'text': '未认证片段'},
        format='json',
    )

    assert response.status_code == 401
    assert Fragment.objects.count() == 0
