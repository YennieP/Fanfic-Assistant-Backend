from characters.effective_context import (
    EffectiveCharacterContext,
    compose_effective_character_context,
)
from characters.models import BaseCard, AUMod


JUDGE_SYSTEM = """\
你是专业的中文同人文角色一致性评估专家。
任务：对照角色设定，评估生成文本中该角色的言行是否符合人设。

评分标准（0-10 整数）：
10 — 完全符合，角色言行、情绪、用语均贴合设定
8-9 — 基本符合，有细微偏差但不破坏整体人设
6-7 — 部分符合，有明显但非核心的偏差
4-5 — 明显偏差，多处言行与设定矛盾
2-3 — 严重崩人设，角色核心特质被违反
0-1 — 完全崩人设，或直接违反了人设红线

输出格式（严格遵守，不输出任何其他内容，不加 markdown 代码块）：
{"score": <0-10整数>, "reasoning": "<重点指出符合或违反的具体细节，50-150字>"}\
"""


def build_judge_prompt(
    character: BaseCard,
    au_mod: AUMod | None,
    generated_text: str,
    effective_context: EffectiveCharacterContext | None = None,
) -> tuple[str, str]:
    """
    返回 (system_prompt, user_prompt)

    Judge 与 generation 消费同一个 EffectiveCharacterContext，但保留自己的展示模板，
    包括略去生成专用的称呼规则。
    """
    context = effective_context or compose_effective_character_context(character, au_mod)
    char_summary = _build_char_summary(character, au_mod, context)
    user_prompt = f'【角色设定】\n{char_summary}\n\n【待评估文本】\n{generated_text}'
    return JUDGE_SYSTEM, user_prompt


def _build_char_summary(
    character: BaseCard,
    au_mod: AUMod | None,
    effective_context: EffectiveCharacterContext,
) -> str:
    lines = [f'角色名：{character.name}']

    # 性别代词
    if character.gender and character.gender != 'other':
        lines.append(f'性别代词：{character.gender}')
    elif character.gender == 'other' and character.gender_pronoun:
        lines.append(f'性别代词：{character.gender_pronoun}')

    # 性格标签（合并并应用 inherit_exclude）
    labels = effective_context.quick_labels
    if labels:
        lines.append(f'性格标签：{"、".join(item.content for item in labels)}')

    # 核心价值观（强信号：行动方向违反最珍视的东西是最直接可观测的崩人设）
    if character.core_values:
        lines.append(f'核心价值观：{"、".join(v["content"] for v in character.core_values)}')

    # 核心恐惧（中等信号：场景触发恐惧时判断情绪反应是否符合预期）
    if character.core_fears:
        lines.append(f'核心恐惧：{"、".join(f["content"] for f in character.core_fears)}')

    # 形成性经历（双重盲点修复：决定角色对特定情境的隐性反应）
    if character.key_experiences:
        lines.append('\n形成性经历：')
        for exp in character.key_experiences:
            lines.append(f'  · {exp["content"]}')

    # 人设红线（最关键的评估依据）
    forbidden_behaviors = effective_context.forbidden_behaviors
    if forbidden_behaviors:
        lines.append('\n人设红线（违反即扣分）：')
        for item in forbidden_behaviors:
            lines.append(f'  ✕ {item.content}')

    # 行为模式（最多 3 条，足够给 judge 参考）
    patterns = character.behavioral_patterns or []
    if patterns:
        lines.append('\n典型行为模式：')
        for p in patterns[:3]:
            t = (p.get('trigger') or {}).get('immediate', '')
            r = (p.get('response') or {}).get('immediate', '')
            if t and r:
                lines.append(f'  · 触发：{t} → {r}')

    # 情绪表达
    if character.emotion_expression_style:
        lines.append(f'\n情绪表达方式：{character.emotion_expression_style}')

    # 台词风格标签（双重盲点修复：言语风格是文本最直接可观测特质）
    speech_tags = character.speech_style_custom_tags or {}
    scene_tags = speech_tags.get('sceneType') or speech_tags.get('scene_type') or []
    target_tags = speech_tags.get('targetType') or speech_tags.get('target_type') or []
    if scene_tags or target_tags:
        lines.append('\n台词风格标签：')
        if scene_tags:
            lines.append(f'  场景类型惯用风格：{"、".join(scene_tags)}')
        if target_tags:
            lines.append(f'  对象类型惯用风格：{"、".join(target_tags)}')

    # AU 设定
    if au_mod:
        lines.append(f'\nAU设定（{au_mod.au_name}）：')
        if au_mod.setting:
            lines.append(f'  世界观：{au_mod.setting}')
        if au_mod.role_title:
            lines.append(f'  身份：{au_mod.role_title}')

    # 关系上下文（结构性缺口修复）
    # 注入：关系基调 + 该角色在此关系中的性格切面 + 关系专属红线
    # 略去：称呼规则（生成专用，judge 评估不需要）
    visible_relationships = [
        relationship
        for relationship in effective_context.relationships
        if (
            relationship.overall_tone
            or relationship.quick_labels
            or relationship.forbidden_behaviors
        )
    ]
    if visible_relationships:
        lines.append('\n生成时激活的关系设定（评估时作为参照）：')
        if len(visible_relationships) > 1:
            lines.append(
                '以下关系按优先级从高到低排列；若要求无法同时满足，'
                '评估时以靠前关系为准，并兼顾其余不冲突信息。'
            )
        for priority, relationship in enumerate(visible_relationships, 1):
            if len(visible_relationships) > 1:
                lines.append(f'\n  关系优先级 {priority}：')
            if relationship.overall_tone:
                lines.append(f'  关系基调：{relationship.overall_tone}')

            if relationship.quick_labels:
                label_str = '、'.join(item.content for item in relationship.quick_labels)
                lines.append(f'  {character.name} 在此关系中：{label_str}')

            if relationship.forbidden_behaviors:
                lines.append('  关系专属红线：')
                for item in relationship.forbidden_behaviors:
                    lines.append(f'    ✕ {item.content}')

    return '\n'.join(lines)
