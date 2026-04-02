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
    lines = [
        '你是一个专业的中文同人文写作助手。严格依照以下角色设定创作，不得违反人设红线。',
        '用第三人称叙述，语言自然流畅，情绪表达符合角色特质。直接开始创作，不解释思路。',
        '',
        '【角色设定】',
        f'角色名：{character.name}',
    ]

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


def _build_user(scene_input: dict) -> str:
    lines = ['请根据以下场景信息创作：', '']

    if scene_input.get('location'):
        lines.append(f'地点：{scene_input["location"]}')
    if scene_input.get('characters'):
        chars = scene_input['characters']
        if isinstance(chars, list):
            lines.append(f'在场角色：{", ".join(chars)}')
    if scene_input.get('time'):
        lines.append(f'时间：{scene_input["time"]}')
    if scene_input.get('tone'):
        lines.append(f'基调：{scene_input["tone"]}')
    if scene_input.get('perspective'):
        lines.append(f'叙述视角：{scene_input["perspective"]}')
    if scene_input.get('intent'):
        lines.append(f'\n写作意图：{scene_input["intent"]}')

    return '\n'.join(lines)