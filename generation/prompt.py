from characters.models import BaseCard, AUMod


def build_prompt(character: BaseCard, au_mod: AUMod | None, scene_input: dict) -> tuple[str, str]:
    """
    返回 (system_prompt, user_prompt)
    system_prompt: 写作规则和角色设定
    user_prompt: 具体场景指令
    """
    system = _build_system(character, au_mod)
    user = _build_user(scene_input)
    return system, user


def _build_system(character: BaseCard, au_mod: AUMod | None) -> str:
    # 他/她/它/祂 直接用选项值作为代词；other 用用户自定义
    if character.gender and character.gender != 'other':
        pronoun = character.gender
    elif character.gender == 'other':
        pronoun = character.gender_pronoun or ''
    else:
        pronoun = ''

    pronoun_instruction = (
        f'全文统一使用「{pronoun}」称呼{character.name}，不得使用其他人称代词。'
        if pronoun else ''
    )

    lines = [
        '你是一个专业的中文同人文写作助手。严格依照以下角色设定创作，不得违反人设红线。',
        f'用第三人称叙述，语言自然流畅，情绪表达符合角色特质。{pronoun_instruction}直接开始创作，不解释思路。',
        '',
        '【角色设定】',
        f'角色名：{character.name}',
    ]

    if pronoun:
        if character.gender == 'other' and character.gender_type:
            lines.append(f'性别：{character.gender_type}（代词：{pronoun}）')
        else:
            lines.append(f'性别代词：{pronoun}')

    if character.fandom:
        lines.append(f'来源：{character.fandom}')
    if character.mbti:
        lines.append(f'MBTI：{character.mbti}')
        if character.mbti_notes:
            lines.append(f'MBTI说明：{character.mbti_notes}')

    # 性格标签（合并 BaseCard + AUMod，应用 inherit_exclude）
    labels = list(character.quick_labels or [])
    if au_mod and au_mod.quick_labels:
        exclude = (au_mod.inherit_exclude or {}).get('quick_labels', [])
        labels = [l for l in labels if l.get('id') not in exclude]
        labels += au_mod.quick_labels
    if labels:
        lines.append(f'性格标签：{"、".join(l["content"] for l in labels)}')

    # 核心价值观与恐惧
    if character.core_values:
        lines.append(f'核心价值观：{"、".join(v["content"] for v in character.core_values)}')
    if character.core_fears:
        lines.append(f'核心恐惧：{"、".join(f["content"] for f in character.core_fears)}')

    # 行为模式（最多注入5条，避免 prompt 过长）
    patterns = character.behavioral_patterns or []
    if patterns:
        lines.append('\n【行为模式】')
        for p in patterns[:5]:
            trigger = p.get('trigger', {})
            resp = p.get('response', {})
            t = trigger.get('immediate', '')
            r_now = resp.get('immediate', '')
            r_int = resp.get('internal', '')
            if t and r_now:
                entry = f'触发：{t} → 当下：{r_now}'
                if r_int:
                    entry += f'（内心：{r_int}）'
                lines.append(f'  · {entry}')

    # 人设红线（合并 BaseCard + AUMod）
    fb = list(character.forbidden_behaviors or [])
    if au_mod and au_mod.forbidden_behaviors:
        exclude = (au_mod.inherit_exclude or {}).get('forbidden_behaviors', [])
        fb = [f for f in fb if f.get('id') not in exclude]
        fb += au_mod.forbidden_behaviors
    if fb:
        lines.append('\n【人设红线（绝对不能违反）】')
        for f in fb:
            lines.append(f'  ✕ {f["content"]}')

    # 情绪模式
    if character.default_state:
        lines.append(f'\n日常基调：{character.default_state}')
    if character.emotion_expression_style:
        lines.append(f'情绪表达：{character.emotion_expression_style}')

    # AUMod 设定
    if au_mod:
        lines.append(f'\n【AU设定：{au_mod.au_name}】')
        if au_mod.setting:
            lines.append(f'世界观：{au_mod.setting}')
        if au_mod.role_title:
            lines.append(f'身份：{au_mod.role_title}')
        if au_mod.role_age:
            lines.append(f'年龄：{au_mod.role_age}')
        if au_mod.role_current_situation:
            lines.append(f'当前处境：{au_mod.role_current_situation}')

    return '\n'.join(lines)


_LENGTH_MAP = {
    'short': '短篇（300字以内）',
    'medium': '中篇（300～800字）',
    'long': '长篇（800字以上）',
}


def _build_user(scene_input: dict) -> str:
    lines = ['请根据以下场景信息创作：', '']

    # 必填
    if scene_input.get('location'):
        lines.append(f'地点：{scene_input["location"]}')

    # 主要在场角色
    chars = scene_input.get('characters', [])
    if chars:
        lines.append(f'主要在场角色：{", ".join(chars)}')

    # 次要/背景角色
    secondary = scene_input.get('secondary_characters', [])
    if secondary:
        lines.append(f'次要/背景角色：{", ".join(secondary)}')

    # 叙事设置
    if scene_input.get('time'):
        lines.append(f'时间：{scene_input["time"]}')
    if scene_input.get('tone'):
        lines.append(f'基调：{scene_input["tone"]}')
    if scene_input.get('perspective'):
        lines.append(f'叙述视角：{scene_input["perspective"]}')

    # 创作指引
    if scene_input.get('scene_role'):
        lines.append(f'场景作用：{scene_input["scene_role"]}')
    if scene_input.get('target_state'):
        lines.append(f'目标状态：{scene_input["target_state"]}')
    if scene_input.get('desired_length'):
        label = _LENGTH_MAP.get(scene_input['desired_length'], '')
        if label:
            lines.append(f'篇幅：{label}')

    # 写作意图（放最后，紧接生成指令）
    if scene_input.get('intent'):
        lines.append(f'\n写作意图：{scene_input["intent"]}')

    # 场景禁止项（单独成段，确保模型不遗漏）
    if scene_input.get('scene_restrictions'):
        lines.append(f'\n【本场景禁止出现】\n{scene_input["scene_restrictions"]}')

    return '\n'.join(lines)