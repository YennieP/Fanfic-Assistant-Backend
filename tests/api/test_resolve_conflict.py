"""片段冲突原子处理接口的行为与事务契约测试。"""

import pytest

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


ENDPOINT = '/api/examples/fragments/resolve-conflict/'


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


def _payload(old_fragment, new_fragment, action, edited_new_text):
    # 使用前端真实 camelCase 载荷，覆盖 CamelCaseJSONParser 路径。
    return {
        'oldFragmentId': str(old_fragment.id),
        'newFragmentId': str(new_fragment.id),
        'action': action,
        'editedNewText': edited_new_text,
    }


@pytest.mark.django_db
def test_keep_old_deletes_new_and_appends_residual_blocks(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(
        article,
        '共同第一行\n旧版内容\n共同末行',
        confirmed=True,
        order=2,
    )
    new_fragment = _fragment(
        article,
        '共同第一行\n新版原文\n共同末行',
        confirmed=False,
        order=7,
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(
            old_fragment,
            new_fragment,
            'keepOld',
            '共同第一行\n新增甲\n新增乙\n共同末行\n尾部新增',
        ),
        format='json',
    )

    assert response.status_code == 200
    body = response.json()
    assert body['preservedFragment']['id'] == str(old_fragment.id)
    assert body['deletedFragmentId'] == str(new_fragment.id)
    assert body['residualCount'] == 2
    assert [item['text'] for item in body['residualFragments']] == [
        '新增甲\n新增乙',
        '尾部新增',
    ]

    old_fragment.refresh_from_db()
    assert old_fragment.text == '共同第一行\n旧版内容\n共同末行'
    assert old_fragment.is_confirmed is True
    assert not Fragment.objects.filter(id=new_fragment.id).exists()

    residuals = list(article.fragments.filter(is_confirmed=False).order_by('order'))
    assert [fragment.text for fragment in residuals] == ['新增甲\n新增乙', '尾部新增']
    assert [fragment.order for fragment in residuals] == [8, 9]
    assert all(fragment.owner_id == user.id for fragment in residuals)
    assert all(fragment.character_id == character.id for fragment in residuals)


@pytest.mark.django_db
def test_keep_new_saves_edited_text_deletes_old_and_preserves_old_residual(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(
        article,
        '共同内容\n只在旧版',
        confirmed=True,
        order=1,
    )
    new_fragment = _fragment(
        article,
        '共同内容\n新版原文',
        confirmed=False,
        order=2,
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, new_fragment, 'keepNew', '共同内容\n用户编辑后的新版'),
        format='json',
    )

    assert response.status_code == 200
    body = response.json()
    assert body['preservedFragment']['id'] == str(new_fragment.id)
    assert body['preservedFragment']['text'] == '共同内容\n用户编辑后的新版'
    assert body['deletedFragmentId'] == str(old_fragment.id)
    assert body['residualCount'] == 1
    assert body['residualFragments'][0]['text'] == '只在旧版'

    assert not Fragment.objects.filter(id=old_fragment.id).exists()
    new_fragment.refresh_from_db()
    assert new_fragment.text == '共同内容\n用户编辑后的新版'
    assert new_fragment.is_confirmed is False
    assert new_fragment.embedding is None


@pytest.mark.django_db
@pytest.mark.parametrize('action', ['unknown', '', 'keep_new'])
def test_rejects_invalid_action_without_writes(api_client, user_factory, action):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(article, '旧版', confirmed=True, order=1)
    new_fragment = _fragment(article, '新版', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, new_fragment, action, '新版'),
        format='json',
    )

    assert response.status_code == 400
    assert Fragment.objects.filter(id__in=[old_fragment.id, new_fragment.id]).count() == 2


@pytest.mark.django_db
def test_rejects_same_fragment_id_without_writes(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '同一片段', confirmed=True, order=1)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(fragment, fragment, 'keepOld', '同一片段'),
        format='json',
    )

    assert response.status_code == 400
    assert Fragment.objects.filter(id=fragment.id).exists()


@pytest.mark.django_db
def test_rejects_fragments_from_different_articles_without_writes(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    first_article = _article(user, character, '文章一')
    second_article = _article(user, character, '文章二')
    old_fragment = _fragment(first_article, '旧版', confirmed=True, order=1)
    new_fragment = _fragment(second_article, '新版', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, new_fragment, 'keepOld', '新版'),
        format='json',
    )

    assert response.status_code == 400
    assert Fragment.objects.filter(id__in=[old_fragment.id, new_fragment.id]).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('old_confirmed', 'new_confirmed'),
    [(False, False), (True, True)],
)
def test_rejects_stale_conflict_state_without_writes(
    api_client, user_factory, old_confirmed, new_confirmed,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(article, '旧版', confirmed=old_confirmed, order=1)
    new_fragment = _fragment(article, '新版', confirmed=new_confirmed, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, new_fragment, 'keepOld', '新版'),
        format='json',
    )

    assert response.status_code == 409
    assert Fragment.objects.filter(id__in=[old_fragment.id, new_fragment.id]).count() == 2


@pytest.mark.django_db
def test_does_not_resolve_another_users_fragment(api_client, user_factory):
    user = user_factory()
    other_user = user_factory()
    character = BaseCardFactory(owner=user)
    other_character = BaseCardFactory(owner=other_user)
    article = _article(user, character)
    other_article = _article(other_user, other_character)
    old_fragment = _fragment(article, '自己的旧版', confirmed=True, order=1)
    other_new_fragment = _fragment(other_article, '别人的新版', confirmed=False, order=2)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, other_new_fragment, 'keepOld', '别人的新版'),
        format='json',
    )

    assert response.status_code == 404
    assert Fragment.objects.filter(id__in=[old_fragment.id, other_new_fragment.id]).count() == 2


@pytest.mark.django_db(transaction=True)
def test_rolls_back_all_writes_when_residual_creation_fails(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(article, '共同内容\n旧版独有', confirmed=True, order=1)
    new_fragment = _fragment(article, '共同内容\n新版原文', confirmed=False, order=2)
    api_client.force_authenticate(user=user)
    api_client.raise_request_exception = False

    def fail_bulk_create(*_args, **_kwargs):
        raise RuntimeError('simulated residual insert failure')

    monkeypatch.setattr(Fragment.objects, 'bulk_create', fail_bulk_create)

    response = api_client.post(
        ENDPOINT,
        data=_payload(old_fragment, new_fragment, 'keepNew', '共同内容\n编辑后新版'),
        format='json',
    )

    assert response.status_code == 500
    old_fragment.refresh_from_db()
    new_fragment.refresh_from_db()
    assert old_fragment.text == '共同内容\n旧版独有'
    assert new_fragment.text == '共同内容\n新版原文'
    assert article.fragments.count() == 2


@pytest.mark.django_db
def test_repeated_request_does_not_create_duplicate_residuals(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    old_fragment = _fragment(article, '共同内容\n旧版独有', confirmed=True, order=1)
    new_fragment = _fragment(article, '共同内容\n新版独有', confirmed=False, order=2)
    api_client.force_authenticate(user=user)
    payload = _payload(old_fragment, new_fragment, 'keepOld', '共同内容\n新版独有')

    first_response = api_client.post(ENDPOINT, data=payload, format='json')
    second_response = api_client.post(ENDPOINT, data=payload, format='json')

    assert first_response.status_code == 200
    assert second_response.status_code == 404
    assert list(article.fragments.values_list('text', flat=True)) == ['共同内容\n旧版独有', '新版独有']
