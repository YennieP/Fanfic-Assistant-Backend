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
    character: BaseCard, au_mod: AUMod | None, generated_text: str
) -> tuple[str, str]:
    char_summary = _build_char_summary(character, au_mod)
    user_prompt = f'【角色设定】\n{char_summary}\n\n【待评估文本】\n{generated_text}'
    return JUDGE_SYSTEM, user_prompt


def _build_char_summary(character: BaseCard, au_mod: AUMod | None) -> str:
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

    # 人设红线（最关键的评估依据，放最前）
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

    # AU 设定
    if au_mod:
        lines.append(f'\nAU设定（{au_mod.au_name}）：')
        if au_mod.setting:
            lines.append(f'  世界观：{au_mod.setting}')
        if au_mod.role_title:
            lines.append(f'  身份：{au_mod.role_title}')

    return '\n'.join(lines)