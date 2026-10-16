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


def _line_block(text: str, start: str, end: str) -> list[str]:
    lines = text.splitlines()
    return lines[lines.index(start):lines.index(end) + 1]


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
def test_explicit_relationship_resolution_is_an_active_subset_and_only_reorders_peers():
    character = BaseCardFactory()
    owner = character.owner
    partner = BaseCardFactory(owner=owner)
    third = BaseCardFactory(owner=owner)

    pair_a = Relationship.objects.create(owner=owner, overall_tone='关系 A')
    RelationshipMembership.objects.create(
        relationship=pair_a,
        character=character,
        quick_labels=[{'id': 'a', 'content': 'A 标签'}],
    )
    RelationshipMembership.objects.create(relationship=pair_a, character=partner)

    pair_b = Relationship.objects.create(owner=owner, overall_tone='关系 B')
    RelationshipMembership.objects.create(
        relationship=pair_b,
        character=character,
        quick_labels=[{'id': 'b', 'content': 'B 标签'}],
    )
    RelationshipMembership.objects.create(relationship=pair_b, character=partner)

    group = Relationship.objects.create(owner=owner, overall_tone='群体关系')
    RelationshipMembership.objects.create(
        relationship=group,
        character=character,
        quick_labels=[{'id': 'group', 'content': '群体标签'}],
    )
    RelationshipMembership.objects.create(relationship=group, character=partner)
    RelationshipMembership.objects.create(relationship=group, character=third)

    reordered = build_effective_character_context(
        character,
        None,
        [pair_a.id, pair_b.id, group.id],
        # 群体关系故意放在最前；跨级仍必须是一对一优先，同级则采用 B → A。
        [group.id, pair_b.id, pair_a.id],
    )
    assert reordered.active_relationship_ids == (
        str(pair_b.id),
        str(pair_a.id),
        str(group.id),
    )
    generation_system, _ = build_prompt(
        character,
        None,
        {'location': '会议室', 'intent': '协商'},
        effective_context=reordered,
    )
    _, evaluation_user = build_judge_prompt(
        character,
        None,
        '她最后同意了。',
        effective_context=reordered,
    )
    for prompt in (generation_system, evaluation_user):
        assert '关系按优先级从高到低排列' in prompt
        assert prompt.index('关系 B') < prompt.index('关系 A') < prompt.index('群体关系')

    selected = build_effective_character_context(
        character,
        None,
        [pair_a.id, pair_b.id, group.id],
        [pair_b.id],
    )
    assert selected.active_relationship_ids == (str(pair_b.id),)

    empty = build_effective_character_context(
        character,
        None,
        [pair_a.id, pair_b.id, group.id],
        [],
    )
    assert empty.relationships == ()

    # resolved 中不在自动激活集合里的 ID 必须 fail closed。
    not_active = Relationship.objects.create(owner=owner, overall_tone='未激活')
    RelationshipMembership.objects.create(relationship=not_active, character=character)
    RelationshipMembership.objects.create(relationship=not_active, character=partner)
    ignored = build_effective_character_context(
        character,
        None,
        [pair_a.id],
        [not_active.id],
    )
    assert ignored.relationships == ()


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

    assert _line_block(
        generation_system,
        '性格标签：冷静、警觉',
        '称呼 林宇 为：阿宇',
    ) == [
        '性格标签：冷静、警觉',
        '',
        '【人设红线（绝对不能违反）】',
        '  ✕ 迁怒无辜',
        '',
        '【AU设定：调查员 AU】',
        '身份：调查员',
        '',
        '【激活的关系设定】',
        '关系基调：互相信任但很少直说',
        '陈默 在此关系中：保护欲强',
        '关系专属红线：',
        '  ✕ 公开羞辱林宇',
        '称呼 林宇 为：阿宇',
    ]
    assert _line_block(
        evaluation_user,
        '性格标签：冷静、警觉',
        '    ✕ 公开羞辱林宇',
    ) == [
        '性格标签：冷静、警觉',
        '',
        '人设红线（违反即扣分）：',
        '  ✕ 迁怒无辜',
        '',
        'AU设定（调查员 AU）：',
        '  身份：调查员',
        '',
        '生成时激活的关系设定（评估时作为参照）：',
        '  关系基调：互相信任但很少直说',
        '  陈默 在此关系中：保护欲强',
        '  关系专属红线：',
        '    ✕ 公开羞辱林宇',
    ]

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
