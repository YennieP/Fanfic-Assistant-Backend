"""一致性评估 API 的关系上下文契约测试。"""
import uuid
from types import SimpleNamespace

import pytest

from characters.models import Relationship, RelationshipMembership
from evaluation.models import ConsistencyScore
from logs.models import LlmCallLog
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider


class CapturingProvider(FakeProvider):
    MODEL = 'fake'

    def __init__(self):
        super().__init__()
        self.calls = []

    def complete(self, system_prompt, user_prompt, *, options):
        self.calls.append({
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'max_tokens': options.max_tokens,
            'options': options,
        })
        return super().complete(
            system_prompt,
            user_prompt,
            options=options,
        )


def _generation_log(user):
    return LlmCallLog.objects.create(
        user=user,
        generation_id=uuid.uuid4(),
        feature='character_generate',
        model='fake',
        latency_ms=1,
        status=LlmCallLog.Status.SUCCESS,
    )


def _post_evaluation(
    api_client,
    character,
    generation_log,
    relationship_ids,
    resolved_relationship_ids=None,
):
    data = {
        # 使用前端真实 camelCase 载荷，覆盖 CamelCaseJSONParser 路径。
        'generationId': str(generation_log.generation_id),
        'generatedText': '她没有退让。',
        'characterId': str(character.id),
        'activeRelationshipIds': [str(rel_id) for rel_id in relationship_ids],
    }
    if resolved_relationship_ids is not None:
        data['resolvedRelationshipIds'] = [
            str(rel_id) for rel_id in resolved_relationship_ids
        ]
    return api_client.post(
        '/api/evaluation/score/',
        data=data,
        format='json',
    )


def _assert_error(response, *, status, code, detail):
    assert response.status_code == status
    assert response.json() == {'code': code, 'detail': detail}


@pytest.mark.django_db
def test_evaluation_missing_required_fields_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/evaluation/score/',
        data={'generatedText': '她没有退让。'},
        format='json',
    )

    _assert_error(
        response,
        status=400,
        code='missing_required_fields',
        detail='缺少必填参数',
    )


@pytest.mark.django_db
def test_evaluation_missing_generation_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/evaluation/score/',
        data={
            'generationId': str(uuid.uuid4()),
            'generatedText': '她没有退让。',
            'characterId': str(uuid.uuid4()),
        },
        format='json',
    )

    _assert_error(
        response,
        status=404,
        code='generation_not_found',
        detail='生成记录不存在',
    )


@pytest.mark.django_db
def test_evaluation_missing_character_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    generation_log = _generation_log(user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/evaluation/score/',
        data={
            'generationId': str(generation_log.generation_id),
            'generatedText': '她没有退让。',
            'characterId': str(uuid.uuid4()),
        },
        format='json',
    )

    _assert_error(
        response,
        status=404,
        code='character_not_found',
        detail='角色不存在',
    )


@pytest.mark.django_db
def test_evaluation_invalid_judge_result_uses_standard_error_contract(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='not-json')
    monkeypatch.setattr(
        'evaluation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            instance=provider,
            definition=SimpleNamespace(model='fake'),
        ),
    )

    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    generation_log = _generation_log(user)
    api_client.force_authenticate(user=user)

    response = _post_evaluation(
        api_client,
        character,
        generation_log,
        [],
    )

    _assert_error(
        response,
        status=500,
        code='evaluation_result_invalid',
        detail='评估结果格式异常，请重试',
    )
    assert ConsistencyScore.objects.count() == 0


@pytest.mark.django_db
def test_rating_missing_value_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        f'/api/evaluation/score/{uuid.uuid4()}/rate/',
        data={},
        format='json',
    )

    _assert_error(
        response,
        status=400,
        code='missing_user_rating',
        detail='缺少 user_rating 参数',
    )


@pytest.mark.django_db
def test_rating_invalid_value_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        f'/api/evaluation/score/{uuid.uuid4()}/rate/',
        data={'userRating': 11},
        format='json',
    )

    _assert_error(
        response,
        status=400,
        code='invalid_user_rating',
        detail='user_rating 必须为 0-10 整数',
    )


@pytest.mark.django_db
def test_rating_missing_evaluation_uses_standard_error_contract(
    api_client, user_factory,
):
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        f'/api/evaluation/score/{uuid.uuid4()}/rate/',
        data={'userRating': 8},
        format='json',
    )

    _assert_error(
        response,
        status=404,
        code='evaluation_not_found',
        detail='评估记录不存在',
    )


@pytest.mark.django_db
def test_rating_success_contract_and_write_are_unchanged(
    api_client, user_factory,
):
    user = user_factory()
    character = BaseCardFactory(owner=user)
    generation_log = _generation_log(user)
    score = ConsistencyScore.objects.create(
        user=user,
        character=character,
        generation_log=generation_log,
        generated_text='她没有退让。',
        score=6,
        judge_reasoning='符合角色设定',
        judge_model='fake',
    )
    api_client.force_authenticate(user=user)

    response = api_client.patch(
        f'/api/evaluation/score/{score.id}/rate/',
        data={'userRating': 8},
        format='json',
    )

    assert response.status_code == 200
    assert response.json() == {
        'evaluationId': str(score.id),
        'userRating': 8,
        'llmScore': 6,
        'finalScore': 7.4,
    }
    score.refresh_from_db()
    assert score.user_rating == 8
    assert score.user_rated_at is not None


@pytest.mark.django_db
def test_evaluation_injects_owned_relationship_context(
    api_client, make_user_with_key, user_factory, monkeypatch,
):
    provider = CapturingProvider()
    monkeypatch.setattr(
        'evaluation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            instance=provider,
            definition=SimpleNamespace(model='fake'),
        ),
    )

    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user, name='陈默')
    generation_log = _generation_log(user)
    api_client.force_authenticate(user=user)

    relationship = Relationship.objects.create(
        owner=user,
        overall_tone='彼此信任但交流克制',
    )
    RelationshipMembership.objects.create(
        relationship=relationship,
        character=character,
        quick_labels=[{'id': 'rel-label', 'content': '保护欲强'}],
        forbidden_behaviors=[{'id': 'rel-fb', 'content': '公开羞辱对方'}],
    )
    partner = BaseCardFactory(owner=user)
    RelationshipMembership.objects.create(
        relationship=relationship,
        character=partner,
    )

    omitted_relationship = Relationship.objects.create(
        owner=user,
        overall_tone='用户明确排除的同级关系',
    )
    RelationshipMembership.objects.create(
        relationship=omitted_relationship,
        character=character,
        quick_labels=[{'id': 'omit', 'content': '不应进入评估'}],
    )
    RelationshipMembership.objects.create(
        relationship=omitted_relationship,
        character=partner,
    )

    # 即使客户端传入其他用户的关系 ID，
    # 也不得注入当前用户的 judge prompt。
    other_user = user_factory()
    other_character = BaseCardFactory(owner=other_user)
    other_relationship = Relationship.objects.create(
        owner=other_user,
        overall_tone='不应泄露的其他用户关系',
    )
    RelationshipMembership.objects.create(
        relationship=other_relationship,
        character=other_character,
    )

    response = _post_evaluation(
        api_client,
        character,
        generation_log,
        [relationship.id, omitted_relationship.id, other_relationship.id],
        [relationship.id],
    )

    assert response.status_code == 200
    assert response.data['score'] == 8
    assert len(provider.calls) == 1

    user_prompt = provider.calls[0]['user_prompt']
    assert '关系基调：彼此信任但交流克制' in user_prompt
    assert '陈默 在此关系中：保护欲强' in user_prompt
    assert '关系专属红线：' in user_prompt
    assert '✕ 公开羞辱对方' in user_prompt
    assert '用户明确排除的同级关系' not in user_prompt
    assert '不应进入评估' not in user_prompt
    assert '不应泄露的其他用户关系' not in user_prompt


@pytest.mark.django_db
def test_evaluation_without_relationship_ids_keeps_relationship_section_absent(
    api_client, make_user_with_key, monkeypatch,
):
    provider = CapturingProvider()
    monkeypatch.setattr(
        'evaluation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            instance=provider,
            definition=SimpleNamespace(model='fake'),
        ),
    )

    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user, name='陈默')
    generation_log = _generation_log(user)
    api_client.force_authenticate(user=user)

    response = _post_evaluation(api_client, character, generation_log, [])

    assert response.status_code == 200
    assert len(provider.calls) == 1
    assert '生成时激活的关系设定' not in provider.calls[0]['user_prompt']
