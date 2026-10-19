"""显式片段列表批量入库接口的范围与失败语义测试。"""

import pytest
from django.utils import timezone

from examples.models import Article, Fragment
from tests.factories import BaseCardFactory


def _endpoint(article):
    return f'/api/examples/articles/{article.id}/confirm-selected/'


def _retired_endpoint(article):
    return f'/api/examples/articles/{article.id}/confirm-all/'


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
    assert response.data == {
        'code': 'fragment_selection_invalid',
        'detail': '所选片段必须全部属于当前文章',
    }
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
@pytest.mark.parametrize(
    'fragment_kwargs',
    [
        {'confirmed': True},
        {'fragment_type': 'skip'},
    ],
    ids=['already-confirmed', 'not-story'],
)
def test_rejects_the_whole_selection_before_embedding_when_fragment_state_changed(
    api_client, user_factory, monkeypatch, fragment_kwargs,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(
        article,
        '状态已变化片段',
        order=1,
        **fragment_kwargs,
    )
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: pytest.fail('state conflict must not start embedding'),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(fragment.id)]},
        format='json',
    )

    assert response.status_code == 409
    assert response.data == {
        'code': 'fragment_state_conflict',
        'detail': '所选片段状态已变化，请刷新后重试',
    }
    fragment.refresh_from_db()
    assert fragment.embedding is None


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
    assert response.data == {
        'code': 'fragment_tags_invalid',
        'detail': '所选片段必须全部包含有效标签',
    }
    valid_fragment.refresh_from_db()
    ineffective_fragment.refresh_from_db()
    assert valid_fragment.is_confirmed is False
    assert ineffective_fragment.is_confirmed is False


@pytest.mark.django_db
def test_requires_gemini_key_before_embedding(api_client, user_factory, monkeypatch):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '等待向量化片段', order=1)
    monkeypatch.setattr(
        'examples.views.get_embedding',
        lambda *_args, **_kwargs: pytest.fail('missing key must not start embedding'),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(fragment.id)]},
        format='json',
    )

    assert response.status_code == 400
    assert response.data == {
        'code': 'no_api_key',
        'detail': '向量化需要 Gemini API Key。请在设置页配置 Gemini Key 后重试。',
    }
    fragment.refresh_from_db()
    assert fragment.is_confirmed is False
    assert fragment.embedding is None


@pytest.mark.django_db
def test_reports_a_concurrent_edit_as_failed_without_overwriting_it(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    edited = _fragment(article, '原始正文', order=1, tags={'scene_type': '日常'})
    unaffected = _fragment(article, '正常片段', order=2, tags={'scene_type': '动作'})
    embedding_calls = []

    def fake_embedding(text, api_key):
        embedding_calls.append((text, api_key))
        if len(embedding_calls) == 1:
            Fragment.objects.filter(id=edited.id).update(
                text='并发修改后的正文',
                tags={'scene_type': '争执'},
                fragment_type='skip',
                updated_at=timezone.now(),
            )
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(edited.id), str(unaffected.id)]},
        format='json',
    )

    assert response.status_code == 200
    assert response.json() == {
        'confirmed': 1,
        'errors': 1,
        'errorIds': [str(edited.id)],
    }
    edited.refresh_from_db()
    unaffected.refresh_from_db()
    assert edited.text == '并发修改后的正文'
    assert edited.tags == {'scene_type': '争执'}
    assert edited.fragment_type == 'skip'
    assert edited.is_confirmed is False
    assert edited.embedding is None
    assert unaffected.is_confirmed is True
    assert len(embedding_calls) == 2


@pytest.mark.django_db
def test_reports_a_concurrent_confirmation_as_failed_without_replacing_its_embedding(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '并发确认片段', order=1)

    def fake_embedding(_text, _api_key):
        Fragment.objects.filter(id=fragment.id).update(
            embedding=[0.2] * 768,
            is_confirmed=True,
            updated_at=timezone.now(),
        )
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(fragment.id)]},
        format='json',
    )

    assert response.status_code == 200
    assert response.json() == {
        'confirmed': 0,
        'errors': 1,
        'errorIds': [str(fragment.id)],
    }
    fragment.refresh_from_db()
    assert fragment.is_confirmed is True
    assert list(fragment.embedding) == [0.2] * 768


@pytest.mark.django_db
def test_reports_a_concurrent_deletion_as_failed_without_recreating_the_fragment(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '并发删除片段', order=1)
    fragment_id = fragment.id

    def fake_embedding(_text, _api_key):
        Fragment.objects.filter(id=fragment_id).delete()
        return [0.1] * 768

    monkeypatch.setattr('examples.views.get_embedding', fake_embedding)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _endpoint(article),
        data={'fragmentIds': [str(fragment_id)]},
        format='json',
    )

    assert response.status_code == 200
    assert response.json() == {
        'confirmed': 0,
        'errors': 1,
        'errorIds': [str(fragment_id)],
    }
    assert not Fragment.objects.filter(id=fragment_id).exists()


@pytest.mark.django_db
def test_retired_confirm_all_route_is_not_available(api_client, user_factory):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    article = _article(user, character)
    fragment = _fragment(article, '不能被旧入口入库', order=1)
    api_client.force_authenticate(user=user)

    response = api_client.post(_retired_endpoint(article), data={}, format='json')

    assert response.status_code == 404
    fragment.refresh_from_db()
    assert fragment.is_confirmed is False
