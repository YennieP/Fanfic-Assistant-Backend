"""片段合并原子接口的权限、并发状态与事务契约测试。"""

import pytest

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


ENDPOINT = '/api/examples/fragments/merge/'


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


def _payload(keep_fragment, delete_fragment, merged_text):
    return {
        'keepFragmentId': str(keep_fragment.id),
        'deleteFragmentId': str(delete_fragment.id),
        'keepUpdatedAt': keep_fragment.updated_at.isoformat(),
        'deleteUpdatedAt': delete_fragment.updated_at.isoformat(),
        'mergedText': merged_text,
    }


@pytest.mark.django_db
def test_merges_two_fragments_in_one_request(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    keep_fragment = _fragment(article, '后一段', confirmed=True, order=2)
    delete_fragment = _fragment(article, '前一段', confirmed=False, order=1)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(keep_fragment, delete_fragment, '前一段\n后一段'),
        format='json',
    )

    assert response.status_code == 200
    body = response.json()
    assert body['mergedFragment']['id'] == str(keep_fragment.id)
    assert body['mergedFragment']['text'] == '前一段\n后一段'
    assert body['deletedFragmentId'] == str(delete_fragment.id)
    keep_fragment.refresh_from_db()
    assert keep_fragment.text == '前一段\n后一段'
    assert keep_fragment.is_confirmed is False
    assert keep_fragment.embedding is None
    assert not Fragment.objects.filter(id=delete_fragment.id).exists()


@pytest.mark.django_db
def test_rejects_fragments_from_different_articles(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    first_article = _article(user, character, '文章一')
    second_article = _article(user, character, '文章二')
    keep_fragment = _fragment(first_article, '一', confirmed=False, order=1)
    delete_fragment = _fragment(second_article, '二', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT, data=_payload(keep_fragment, delete_fragment, '一\n二'), format='json',
    )

    assert response.status_code == 400
    assert Fragment.objects.filter(id__in=[keep_fragment.id, delete_fragment.id]).count() == 2


@pytest.mark.django_db
def test_does_not_merge_another_users_fragment(api_client, user_factory):
    user = user_factory()
    other_user = user_factory()
    character = BaseCardFactory(owner=user)
    other_character = BaseCardFactory(owner=other_user)
    article = _article(user, character)
    other_article = _article(other_user, other_character)
    keep_fragment = _fragment(article, '自己的', confirmed=False, order=1)
    delete_fragment = _fragment(other_article, '别人的', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(keep_fragment, delete_fragment, '自己的\n别人的'),
        format='json',
    )

    assert response.status_code == 404
    assert Fragment.objects.filter(id__in=[keep_fragment.id, delete_fragment.id]).count() == 2


@pytest.mark.django_db
def test_rejects_stale_fragment_versions(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    keep_fragment = _fragment(article, '一', confirmed=False, order=1)
    delete_fragment = _fragment(article, '二', confirmed=False, order=2)
    payload = _payload(keep_fragment, delete_fragment, '一\n二')
    keep_fragment.text = '并发修改'
    keep_fragment.save(update_fields=['text', 'updated_at'])
    api_client.force_authenticate(user=user)

    response = api_client.post(ENDPOINT, data=payload, format='json')

    assert response.status_code == 409
    keep_fragment.refresh_from_db()
    assert keep_fragment.text == '并发修改'
    assert Fragment.objects.filter(id=delete_fragment.id).exists()


@pytest.mark.django_db(transaction=True)
def test_rolls_back_update_when_delete_fails(api_client, user_factory, monkeypatch):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    keep_fragment = _fragment(article, '一', confirmed=True, order=1)
    delete_fragment = _fragment(article, '二', confirmed=False, order=2)
    api_client.force_authenticate(user=user)
    api_client.raise_request_exception = False

    original_delete = Fragment.delete

    def fail_selected_delete(instance, *args, **kwargs):
        if instance.pk == delete_fragment.pk:
            raise RuntimeError('simulated delete failure')
        return original_delete(instance, *args, **kwargs)

    monkeypatch.setattr(Fragment, 'delete', fail_selected_delete)

    response = api_client.post(
        ENDPOINT, data=_payload(keep_fragment, delete_fragment, '一\n二'), format='json',
    )

    assert response.status_code == 500
    keep_fragment.refresh_from_db()
    assert keep_fragment.text == '一'
    assert keep_fragment.is_confirmed is True
    assert Fragment.objects.filter(id=delete_fragment.id).exists()


@pytest.mark.django_db
def test_repeated_request_does_not_apply_twice(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    keep_fragment = _fragment(article, '一', confirmed=False, order=1)
    delete_fragment = _fragment(article, '二', confirmed=False, order=2)
    api_client.force_authenticate(user=user)
    payload = _payload(keep_fragment, delete_fragment, '一\n二')

    first_response = api_client.post(ENDPOINT, data=payload, format='json')
    second_response = api_client.post(ENDPOINT, data=payload, format='json')

    assert first_response.status_code == 200
    assert second_response.status_code == 404
    assert list(article.fragments.values_list('text', flat=True)) == ['一\n二']
