"""批量采用冲突新版接口的权限、并发状态与事务契约测试。"""

import pytest

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


ENDPOINT = '/api/examples/fragments/resolve-conflicts/'


def _article(user, character, title='测试文章'):
    return Article.objects.create(
        owner=user,
        character=character,
        title=title,
        content='测试正文',
    )


def _fragment(article, text, *, confirmed, order):
    return Fragment.objects.create(
        owner=article.owner,
        article=article,
        character=article.character,
        text=text,
        is_confirmed=confirmed,
        order=order,
        fragment_type='story',
    )


def _pair(old_fragment, new_fragment):
    return {
        'oldFragmentId': str(old_fragment.id),
        'newFragmentId': str(new_fragment.id),
        'oldUpdatedAt': old_fragment.updated_at.isoformat(),
        'newUpdatedAt': new_fragment.updated_at.isoformat(),
    }


@pytest.mark.django_db
def test_adopts_all_new_versions_and_appends_residuals_in_one_request(
    api_client, user_factory,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_one = _fragment(article, '共同一\n旧一', confirmed=True, order=1)
    new_one = _fragment(article, '共同一\n新一', confirmed=False, order=2)
    old_two = _fragment(article, '共同二\n旧二', confirmed=True, order=3)
    new_two = _fragment(article, '共同二\n新二', confirmed=False, order=4)
    _fragment(article, '普通片段', confirmed=False, order=9)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data={'conflicts': [_pair(old_one, new_one), _pair(old_two, new_two)]},
        format='json',
    )

    assert response.status_code == 200
    body = response.json()
    assert body['resolvedCount'] == 2
    assert body['residualCount'] == 2
    assert [item['text'] for item in body['residualFragments']] == ['旧一', '旧二']
    assert [item['order'] for item in body['residualFragments']] == [10, 11]
    assert not Fragment.objects.filter(id__in=[old_one.id, old_two.id]).exists()
    assert Fragment.objects.filter(id__in=[new_one.id, new_two.id]).count() == 2


@pytest.mark.django_db
def test_rejects_a_stale_pair_without_resolving_any_other_pair(
    api_client, user_factory,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_one = _fragment(article, '共同一\n旧一', confirmed=True, order=1)
    new_one = _fragment(article, '共同一\n新一', confirmed=False, order=2)
    old_two = _fragment(article, '共同二\n旧二', confirmed=True, order=3)
    new_two = _fragment(article, '共同二\n新二', confirmed=False, order=4)
    payload = {'conflicts': [_pair(old_one, new_one), _pair(old_two, new_two)]}
    new_two.text = '并发修改后的新版'
    new_two.save(update_fields=['text', 'updated_at'])
    api_client.force_authenticate(user=user)

    response = api_client.post(ENDPOINT, data=payload, format='json')

    assert response.status_code == 409
    assert Fragment.objects.filter(id__in=[old_one.id, new_one.id, old_two.id, new_two.id]).count() == 4
    assert article.fragments.count() == 4


@pytest.mark.django_db
def test_rejects_pairs_from_different_articles_without_writes(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    first_article = _article(user, character, '文章一')
    second_article = _article(user, character, '文章二')
    old_fragment = _fragment(first_article, '共同内容\n旧版', confirmed=True, order=1)
    new_fragment = _fragment(second_article, '共同内容\n新版', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data={'conflicts': [_pair(old_fragment, new_fragment)]},
        format='json',
    )

    assert response.status_code == 400
    assert Fragment.objects.filter(id__in=[old_fragment.id, new_fragment.id]).count() == 2


@pytest.mark.django_db
def test_does_not_resolve_another_users_pair(api_client, user_factory):
    user = user_factory()
    other_user = user_factory()
    character = BaseCardFactory(owner=user)
    other_character = BaseCardFactory(owner=other_user)
    article = _article(user, character)
    other_article = _article(other_user, other_character)
    own_old = _fragment(article, '自己的旧版', confirmed=True, order=1)
    other_new = _fragment(other_article, '别人的新版', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data={'conflicts': [_pair(own_old, other_new)]},
        format='json',
    )

    assert response.status_code == 404
    assert Fragment.objects.filter(id__in=[own_old.id, other_new.id]).count() == 2


@pytest.mark.django_db(transaction=True)
def test_rolls_back_every_pair_when_residual_creation_fails(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_one = _fragment(article, '共同一\n旧一', confirmed=True, order=1)
    new_one = _fragment(article, '共同一\n新一', confirmed=False, order=2)
    old_two = _fragment(article, '共同二\n旧二', confirmed=True, order=3)
    new_two = _fragment(article, '共同二\n新二', confirmed=False, order=4)
    api_client.force_authenticate(user=user)
    api_client.raise_request_exception = False

    def fail_bulk_create(*_args, **_kwargs):
        raise RuntimeError('simulated residual insert failure')

    monkeypatch.setattr(Fragment.objects, 'bulk_create', fail_bulk_create)

    response = api_client.post(
        ENDPOINT,
        data={'conflicts': [_pair(old_one, new_one), _pair(old_two, new_two)]},
        format='json',
    )

    assert response.status_code == 500
    assert Fragment.objects.filter(id__in=[old_one.id, new_one.id, old_two.id, new_two.id]).count() == 4
    assert article.fragments.count() == 4


@pytest.mark.django_db
def test_repeated_batch_request_does_not_create_duplicate_residuals(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(article, '共同内容\n旧版独有', confirmed=True, order=1)
    new_fragment = _fragment(article, '共同内容\n新版独有', confirmed=False, order=2)
    api_client.force_authenticate(user=user)
    payload = {'conflicts': [_pair(old_fragment, new_fragment)]}

    first_response = api_client.post(ENDPOINT, data=payload, format='json')
    second_response = api_client.post(ENDPOINT, data=payload, format='json')

    assert first_response.status_code == 200
    assert second_response.status_code == 404
    assert list(article.fragments.values_list('text', flat=True)) == [
        '共同内容\n新版独有',
        '旧版独有',
    ]
