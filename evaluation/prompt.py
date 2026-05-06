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
    active_rel_contexts: list | None = None,
) -> tuple[str, str]:
    """
    返回 (system_prompt, user_prompt)

    active_rel_contexts: [(Relationship, RelationshipMembership | None), ...]
      与 generation/prompt.py 格式保持一致。
      Judge 注入关系基调、性格切面、关系红线，略去称呼规则（生成专用，评估不需要）。
      为 None 或空列表时行为与原实现完全一致（向后兼容）。
    """
    char_summary = _build_char_summary(character, au_mod, active_rel_contexts)
    user_prompt = f'【角色设定】\n{char_summary}\n\n【待评估文本】\n{generated_text}'
    return JUDGE_SYSTEM, user_prompt


def _build_char_summary(
    character: BaseCard,
    au_mod: AUMod | None,
    active_rel_contexts: list | None = None,
) -> str:
    lines = [f'角色名：{character.name}']

    # 性别代词
    if character.gender and character.gender != 'other':
        lines.append(f'性别代词：{character.gender}')
    elif character.gender == 'other' and character.gender_pronoun:
        lines.append(f'性别代词：{character.gender_pronoun}')

    # 性格标签（合并并应用 inherit_exclude）
    labels = list(character.quick_labels or [])
    if au_mod and au_mod.quick_labels:
        exclude = (au_mod.inherit_exclude or {}).get('quick_labels', [])
        labels = [l for l in labels if l.get('id') not in exclude]
        labels += au_mod.quick_labels
    if labels:
        lines.append(f'性格标签：{"、".join(l["content"] for l in labels)}')

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
    fb = list(character.forbidden_behaviors or [])
    if au_mod and au_mod.forbidden_behaviors:
        exclude = (au_mod.inherit_exclude or {}).get('forbidden_behaviors', [])
        fb = [f for f in fb if f.get('id') not in exclude]
        fb += au_mod.forbidden_behaviors
    if fb:
        lines.append('\n人设红线（违反即扣分）：')
        for f in fb:
            lines.append(f'  ✕ {f["content"]}')

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
    if active_rel_contexts:
        for rel, membership in active_rel_contexts:
            lines.append('\n生成时激活的关系设定（评估时作为参照）：')
            if rel.overall_tone:
                lines.append(f'  关系基调：{rel.overall_tone}')

            if membership is None:
                continue

            if membership.quick_labels:
                label_str = '、'.join(
                    l['content'] for l in membership.quick_labels
                    if isinstance(l, dict) and l.get('content')
                )
                if label_str:
                    lines.append(f'  {character.name} 在此关系中：{label_str}')

            if membership.forbidden_behaviors:
                lines.append('  关系专属红线：')
                for fb_item in membership.forbidden_behaviors:
                    content = fb_item.get('content', str(fb_item)) if isinstance(fb_item, dict) else str(fb_item)
                    lines.append(f'    ✕ {content}')

    return '\n'.join(lines)