"""一致性评估 API 的关系上下文契约测试。"""
import uuid
from types import SimpleNamespace

import pytest

from characters.models import Relationship, RelationshipMembership
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


def _post_evaluation(api_client, character, generation_log, relationship_ids):
    return api_client.post(
        '/api/evaluation/score/',
        data={
            # 使用前端真实 camelCase 载荷，覆盖 CamelCaseJSONParser 路径。
            'generationId': str(generation_log.generation_id),
            'generatedText': '她没有退让。',
            'characterId': str(character.id),
            'activeRelationshipIds': [str(rel_id) for rel_id in relationship_ids],
        },
        format='json',
    )


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
        [relationship.id, other_relationship.id],
    )

    assert response.status_code == 200
    assert response.data['score'] == 8
    assert len(provider.calls) == 1

    user_prompt = provider.calls[0]['user_prompt']
    assert '关系基调：彼此信任但交流克制' in user_prompt
    assert '陈默 在此关系中：保护欲强' in user_prompt
    assert '关系专属红线：' in user_prompt
    assert '✕ 公开羞辱对方' in user_prompt
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
