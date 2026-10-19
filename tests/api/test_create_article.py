import pytest

from examples.models import Article
from tests.factories import BaseCardFactory


ARTICLES_URL = '/api/examples/articles/'


@pytest.mark.django_db
def test_create_article_requires_authentication_without_writing(api_client):
    response = api_client.post(
        ARTICLES_URL,
        {
            'characterId': '00000000-0000-0000-0000-000000000001',
            'content': '正文',
        },
        format='json',
    )

    assert response.status_code == 401
    assert response.data['code'] == 'unauthorized'
    assert not Article.objects.exists()


@pytest.mark.django_db
def test_create_article_missing_character_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ARTICLES_URL,
        {'content': '正文'},
        format='json',
    )

    assert response.status_code == 400
    assert response.data == {
        'code': 'missing_required_fields',
        'detail': '请选择关联角色',
    }
    assert not Article.objects.exists()


@pytest.mark.django_db
def test_create_article_blank_content_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ARTICLES_URL,
        {'characterId': str(character.id), 'content': '   '},
        format='json',
    )

    assert response.status_code == 400
    assert response.data == {
        'code': 'missing_required_fields',
        'detail': '文章内容不能为空',
    }
    assert not Article.objects.exists()


@pytest.mark.django_db
def test_create_article_rejects_another_users_character_without_writing(
    api_client, user_factory,
):
    user = user_factory()
    another_user = user_factory()
    character = BaseCardFactory(owner=another_user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ARTICLES_URL,
        {'characterId': str(character.id), 'content': '正文'},
        format='json',
    )

    assert response.status_code == 404
    assert response.data['code'] == 'not_found'
    assert not Article.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('character_field', ['character_id', 'characterId'])
def test_create_article_accepts_both_character_field_names_and_trims_text(
    api_client, user_factory, character_field,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ARTICLES_URL,
        {
            character_field: str(character.id),
            'title': '  测试标题  ',
            'content': '  第一行\n第二行  ',
        },
        format='json',
    )

    assert response.status_code == 201
    body = response.data
    assert body['title'] == '测试标题'
    assert body['content'] == '第一行\n第二行'
    assert str(body['character']) == str(character.id)
    article = Article.objects.get(owner=user)
    assert article.character == character
    assert article.title == '测试标题'
    assert article.content == '第一行\n第二行'


@pytest.mark.django_db
def test_create_article_uses_default_title_for_blank_title(
    api_client, user_factory,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        ARTICLES_URL,
        {
            'characterId': str(character.id),
            'title': '   ',
            'content': '正文',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['title'] == '未命名文章'
    assert Article.objects.get(owner=user).title == '未命名文章'


@pytest.mark.django_db
def test_article_list_get_keeps_user_and_character_filtering(
    api_client, user_factory,
):
    user = user_factory()
    another_user = user_factory()
    first_character = BaseCardFactory(owner=user)
    second_character = BaseCardFactory(owner=user)
    foreign_character = BaseCardFactory(owner=another_user)
    first_article = Article.objects.create(
        owner=user,
        character=first_character,
        title='目标文章',
        content='正文',
    )
    Article.objects.create(
        owner=user,
        character=second_character,
        title='同用户其他角色文章',
        content='正文',
    )
    Article.objects.create(
        owner=another_user,
        character=foreign_character,
        title='其他用户文章',
        content='正文',
    )
    api_client.force_authenticate(user=user)

    response = api_client.get(
        ARTICLES_URL,
        {'character_id': str(first_character.id)},
    )

    assert response.status_code == 200
    assert [str(item['id']) for item in response.data] == [str(first_article.id)]
