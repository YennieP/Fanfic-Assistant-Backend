"""ARC-005 有效角色上下文的确定性契约测试。"""

import pytest

from characters.effective_context import (
    EffectiveCharacterContext,
    EffectiveListItem,
    EffectiveRelationshipContext,
    build_effective_character_context,
)
from characters.models import AUMod, Relationship, RelationshipMembership
from evaluation.prompt import build_judge_prompt
from generation.prompt import build_prompt
from tests.factories import BaseCardFactory


@pytest.mark.django_db
def test_context_applies_exclusions_without_append_and_ignores_invalid_items():
    character = BaseCardFactory(
        quick_labels=[
            {'id': 'base-keep', 'content': ' 冷静 '},
            {'id': 'base-drop', 'content': '敏锐'},
            {'id': 'blank', 'content': '   '},
            'broken',
        ],
        forbidden_behaviors=[
            {'id': 'fb-drop', 'content': '背叛同伴'},
            {'id': 'fb-keep', 'content': '公开失控'},
            {'id': 'fb-empty', 'content': ''},
        ],
    )
    au_mod = AUMod.objects.create(
        character=character,
        au_name='仅排除 AU',
        quick_labels=[],
        forbidden_behaviors=[],
        inherit_exclude={
            'quick_labels': ['base-drop', '', 42],
            'forbidden_behaviors': ['fb-drop'],
        },
    )

    context = build_effective_character_context(character, au_mod, [])

    assert context == EffectiveCharacterContext(
        quick_labels=(EffectiveListItem(id='base-keep', content='冷静'),),
        forbidden_behaviors=(EffectiveListItem(id='fb-keep', content='公开失控'),),
        relationships=(),
    )


@pytest.mark.django_db
def test_relationship_order_deduplication_and_ownership_are_deterministic(user_factory):
    character = BaseCardFactory(
        quick_labels=[{'id': 'base-label', 'content': '克制'}],
        forbidden_behaviors=[{'id': 'base-fb', 'content': '抛下同伴'}],
    )
    owner = character.owner
    other_a = BaseCardFactory(owner=owner)
    other_b = BaseCardFactory(owner=owner)

    # 先创建群体关系，确保结果不是数据库默认更新时间或请求 ID 顺序。
    group = Relationship.objects.create(owner=owner, overall_tone='长期并肩')
    RelationshipMembership.objects.create(
        relationship=group,
        character=character,
        quick_labels=[{'id': 'group-label', 'content': '保护欲强'}],
        forbidden_behaviors=[{'id': 'group-fb', 'content': '当众示弱'}],
    )
    RelationshipMembership.objects.create(relationship=group, character=other_a)
    RelationshipMembership.objects.create(relationship=group, character=other_b)

    pair = Relationship.objects.create(owner=owner, overall_tone='默契但克制')
    RelationshipMembership.objects.create(
        relationship=pair,
        character=character,
        quick_labels=[
            # 与低特异性关系内容重复：一对一来源必须保留，群体来源去重。
            {'id': 'pair-label', 'content': ' 保护欲强 '},
            {'id': 'pair-empty', 'content': ' '},
        ],
        forbidden_behaviors=[
            # 与 BaseCard ID 重复：关系来源优先，BaseCard 来源去重。
            {'id': 'base-fb', 'content': '绝不抛下对方'},
        ],
        inherit_exclude={'quick_labels': ['base-label']},
        nicknames_for_others=[
            {'calls': ' 林宇 ', 'as': [' 阿宇 ', '', '阿宇']},
            {'calls': '', 'as': ['无效']},
        ],
    )
    RelationshipMembership.objects.create(relationship=pair, character=other_a)

    foreign_owner = user_factory()
    foreign_character = BaseCardFactory(owner=foreign_owner)
    foreign = Relationship.objects.create(owner=foreign_owner, overall_tone='不得泄露')
    RelationshipMembership.objects.create(relationship=foreign, character=foreign_character)
    RelationshipMembership.objects.create(
        relationship=foreign,
        character=BaseCardFactory(owner=foreign_owner),
    )

    context = build_effective_character_context(
        character,
        None,
        # 故意按群体、越权、一对一排列。
        [group.id, foreign.id, pair.id],
    )

    assert context.quick_labels == ()
    assert context.forbidden_behaviors == ()
    assert context.relationships == (
        EffectiveRelationshipContext(
            id=str(pair.id),
            participant_count=2,
            overall_tone='默契但克制',
            quick_labels=(EffectiveListItem(id='pair-label', content='保护欲强'),),
            forbidden_behaviors=(
                EffectiveListItem(id='base-fb', content='绝不抛下对方'),
            ),
            nicknames_for_others=(('林宇', ('阿宇',)),),
        ),
        EffectiveRelationshipContext(
            id=str(group.id),
            participant_count=3,
            overall_tone='长期并肩',
            quick_labels=(),
            forbidden_behaviors=(
                EffectiveListItem(id='group-fb', content='当众示弱'),
            ),
            nicknames_for_others=(),
        ),
    )


@pytest.mark.django_db
def test_generation_and_evaluation_golden_prompts_share_effective_context():
    character = BaseCardFactory(
        name='陈默',
        quick_labels=[
            {'id': 'base-calm', 'content': '冷静'},
            {'id': 'base-soft', 'content': '心软'},
        ],
        forbidden_behaviors=[{'id': 'base-fb', 'content': '迁怒无辜'}],
    )
    au_mod = AUMod.objects.create(
        character=character,
        au_name='调查员 AU',
        role_title='调查员',
        quick_labels=[{'id': 'au-alert', 'content': '警觉'}],
        inherit_exclude={'quick_labels': ['base-soft']},
    )
    partner = BaseCardFactory(owner=character.owner, name='林宇')
    relationship = Relationship.objects.create(
        owner=character.owner,
        overall_tone='互相信任但很少直说',
    )
    RelationshipMembership.objects.create(
        relationship=relationship,
        character=character,
        quick_labels=[{'id': 'rel-protect', 'content': '保护欲强'}],
        forbidden_behaviors=[{'id': 'rel-fb', 'content': '公开羞辱林宇'}],
        nicknames_for_others=[{'calls': '林宇', 'as': ['阿宇']}],
    )
    RelationshipMembership.objects.create(
        relationship=relationship,
        character=partner,
    )
    context = build_effective_character_context(
        character, au_mod, [relationship.id],
    )

    generation_system, _ = build_prompt(
        character,
        au_mod,
        {'location': '档案室', 'intent': '交换线索'},
        effective_context=context,
    )
    _, evaluation_user = build_judge_prompt(
        character,
        au_mod,
        '陈默把档案递给林宇。',
        effective_context=context,
    )

    assert '性格标签：冷静、警觉' in generation_system
    assert '性格标签：冷静、警觉' in evaluation_user
    assert '心软' not in generation_system
    assert '心软' not in evaluation_user
    assert '陈默 在此关系中：保护欲强' in generation_system
    assert '陈默 在此关系中：保护欲强' in evaluation_user
    assert '公开羞辱林宇' in generation_system
    assert '公开羞辱林宇' in evaluation_user
    assert '称呼 林宇 为：阿宇' in generation_system
    assert '称呼 林宇 为：阿宇' not in evaluation_user

    generation_order = [
        generation_system.index('性格标签：冷静、警觉'),
        generation_system.index('【AU设定：调查员 AU】'),
        generation_system.index('【激活的关系设定】'),
    ]
    evaluation_order = [
        evaluation_user.index('性格标签：冷静、警觉'),
        evaluation_user.index('AU设定（调查员 AU）：'),
        evaluation_user.index('生成时激活的关系设定（评估时作为参照）：'),
    ]
    assert generation_order == sorted(generation_order)
    assert evaluation_order == sorted(evaluation_order)
