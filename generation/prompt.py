from characters.effective_context import (
    EffectiveCharacterContext,
    compose_effective_character_context,
)
from characters.models import BaseCard, AUMod


def build_prompt(
    character: BaseCard,
    au_mod: AUMod | None,
    scene_input: dict,
    style_fragments: list | None = None,
    output_language: str = 'zh',
    effective_context: EffectiveCharacterContext | None = None,
) -> tuple[str, str]:
    """
    返回 (system_prompt, user_prompt)

    output_language: 生成文本的目标语言（'zh' = 简体中文，'en' = English）
      默认 'zh'，向后兼容。
      前端写作页「输出语言」下拉传入，优先于 LLM 自行判断（实测不注入时会输出繁体中文）。

    effective_context 由 characters.effective_context 统一解析；为空时仅合成
    BaseCard 与 AU Mod，方便不经过 API view 的调用方保持兼容。
    """
    context = effective_context or compose_effective_character_context(character, au_mod)
    system = _build_system(character, au_mod, context, style_fragments, output_language)
    user = _build_user(scene_input, output_language)
    return system, user


def _build_system(
    character: BaseCard,
    au_mod: AUMod | None,
    effective_context: EffectiveCharacterContext,
    style_fragments: list | None = None,
    output_language: str = 'zh',
) -> str:
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

    # 语言指令：明确注入，避免 LLM 自行判断输出语言（实测不注入时会输出繁体中文）
    # 放在 system prompt 第一行，优先级最高
    if output_language == 'en':
        lang_instruction = 'Write the entire text in English. Do not use any other language.'
        role_intro = f'You are a professional fanfiction writing assistant. Follow the character settings strictly and do not violate character limits.'
        narration_instruction = f'Use third-person narration. Write naturally with emotions matching the character. {pronoun_instruction}Start writing directly without explaining your approach.'
    else:
        lang_instruction = '全文使用简体中文创作，不得使用繁体中文或其他语言。'
        role_intro = '你是一个专业的中文同人文写作助手。严格依照以下角色设定创作，不得违反人设红线。'
        narration_instruction = f'用第三人称叙述，语言自然流畅，情绪表达符合角色特质。{pronoun_instruction}直接开始创作，不解释思路。'

    lines = [
        lang_instruction,
        role_intro,
        narration_instruction,
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

    labels = effective_context.quick_labels
    if labels:
        lines.append(f'性格标签：{"、".join(item.content for item in labels)}')

    if character.core_values:
        lines.append(f'核心价值观：{"、".join(v["content"] for v in character.core_values)}')
    if character.core_fears:
        lines.append(f'核心恐惧：{"、".join(f["content"] for f in character.core_fears)}')

    # ── 新增：形成性经历 ────────────────────────────────────────────────────
    # 决定角色对特定情境的隐性反应（如有被背叛经历的角色对陌生人不应立刻全然信任）
    if character.key_experiences:
        lines.append('\n【形成性经历】')
        for exp in character.key_experiences:
            lines.append(f'  · {exp["content"]}')
    # ── 新增结束 ────────────────────────────────────────────────────────────

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

    forbidden_behaviors = effective_context.forbidden_behaviors
    if forbidden_behaviors:
        lines.append('\n【人设红线（绝对不能违反）】')
        for item in forbidden_behaviors:
            lines.append(f'  ✕ {item.content}')

    if character.default_state:
        lines.append(f'\n日常基调：{character.default_state}')
    if character.emotion_expression_style:
        lines.append(f'情绪表达：{character.emotion_expression_style}')

    # ── 新增：台词风格标签 ──────────────────────────────────────────────────
    # 放在情绪表达之后，作为言语层面的补充约束
    # 用标签形式而非规则描述：标签直接来自作者归纳，比规则更准确
    speech_tags = character.speech_style_custom_tags or {}
    scene_tags = speech_tags.get('sceneType') or speech_tags.get('scene_type') or []
    target_tags = speech_tags.get('targetType') or speech_tags.get('target_type') or []
    if scene_tags or target_tags:
        lines.append('\n【台词风格标签】')
        if scene_tags:
            lines.append(f'  场景类型惯用风格：{"、".join(scene_tags)}')
        if target_tags:
            lines.append(f'  对象类型惯用风格：{"、".join(target_tags)}')
    # ── 新增结束 ────────────────────────────────────────────────────────────

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

    # ── Scaffold: 关系实体注入（phase1.md §9）──────────────────────────────
    # 空的关系上下文不产生空标题。
    # 注入位置：AU设定之后、风格示例之前
    if effective_context.relationships:
        for relationship in effective_context.relationships:
            if not (
                relationship.overall_tone
                or relationship.quick_labels
                or relationship.forbidden_behaviors
                or relationship.nicknames_for_others
            ):
                continue
            lines.append(f'\n【激活的关系设定】')
            if relationship.overall_tone:
                lines.append(f'关系基调：{relationship.overall_tone}')

            # 该角色在此关系中的性格切面
            if relationship.quick_labels:
                label_str = '、'.join(item.content for item in relationship.quick_labels)
                lines.append(f'{character.name} 在此关系中：{label_str}')

            # 关系专属红线
            if relationship.forbidden_behaviors:
                lines.append('关系专属红线：')
                for item in relationship.forbidden_behaviors:
                    lines.append(f'  ✕ {item.content}')

            # 称呼规则
            for calls, names in relationship.nicknames_for_others:
                lines.append(f'称呼 {calls} 为：{"、".join(names)}')
    # ── 关系注入结束 ────────────────────────────────────────────────────────

    # ── Phase 2：风格示例注入 ────────────────────────────────────────────────
    if style_fragments:
        lines.append('\n【台词风格参考（来自作者历史作品，仅参考语言风格和叙事习惯，不要复制原文）】')
        for i, fragment in enumerate(style_fragments, 1):
            lines.append(f'\n参考片段 {i}：')
            lines.append(fragment.text)
    # ── 注入结束 ────────────────────────────────────────────────────────────

    # 尾部语言指令：LLM 对靠近末尾的指令响应更可靠
    # 用强硬措辞再次强调，防止被大量中文 character card 内容覆盖
    if output_language == 'en':
        lines.append(
            '\n[MANDATORY OUTPUT LANGUAGE INSTRUCTION]\n'
            'You MUST write the ENTIRE response in English ONLY. '
            'The character card above is written in Chinese for reference only. '
            'Your output text must be in English. '
            'Do NOT write any Chinese characters in your response.'
        )
    else:
        lines.append(
            '\n【语言输出指令（强制）】\n'
            '全文必须使用简体中文创作，不得出现繁体字或其他语言。'
        )

    return '\n'.join(lines)


def _build_user(scene_input: dict, output_language: str = 'zh') -> str:
    if output_language == 'en':
        labels = {
            'intro':      'Please write based on the following scene:',
            'location':   'Location',
            'chars':      'Present characters',
            'secondary':  'Background characters',
            'time':       'Time',
            'tone':       'Tone',
            'perspective':'Perspective',
            'scene_role': 'Scene role',
            'target':     'Target state',
            'length':     'Length',
            'intent':     'Writing intent',
            'restrict':   '[RESTRICTIONS FOR THIS SCENE]',
        }
        length_map = {
            'short':  'Short (under 300 chars)',
            'medium': 'Medium (300–800 chars)',
            'long':   'Long (800+ chars)',
        }
    else:
        labels = {
            'intro':      '请根据以下场景信息创作：',
            'location':   '地点',
            'chars':      '主要在场角色',
            'secondary':  '次要/背景角色',
            'time':       '时间',
            'tone':       '基调',
            'perspective':'叙述视角',
            'scene_role': '场景作用',
            'target':     '目标状态',
            'length':     '篇幅',
            'intent':     '写作意图',
            'restrict':   '【本场景禁止出现】',
        }
        length_map = {
            'short':  '短篇（300字以内）',
            'medium': '中篇（300～800字）',
            'long':   '长篇（800字以上）',
        }

    lines = [labels['intro'], '']

    if scene_input.get('location'):
        lines.append(f'{labels["location"]}：{scene_input["location"]}')

    chars = scene_input.get('characters', [])
    if chars:
        lines.append(f'{labels["chars"]}：{", ".join(chars)}')

    secondary = scene_input.get('secondary_characters', [])
    if secondary:
        lines.append(f'{labels["secondary"]}：{", ".join(secondary)}')

    if scene_input.get('time'):
        lines.append(f'{labels["time"]}：{scene_input["time"]}')
    if scene_input.get('tone'):
        lines.append(f'{labels["tone"]}：{scene_input["tone"]}')
    if scene_input.get('perspective'):
        lines.append(f'{labels["perspective"]}：{scene_input["perspective"]}')

    if scene_input.get('scene_role'):
        lines.append(f'{labels["scene_role"]}：{scene_input["scene_role"]}')
    if scene_input.get('target_state'):
        lines.append(f'{labels["target"]}：{scene_input["target_state"]}')
    if scene_input.get('desired_length'):
        label = length_map.get(scene_input['desired_length'], '')
        if label:
            lines.append(f'{labels["length"]}：{label}')

    if scene_input.get('intent'):
        lines.append(f'\n{labels["intent"]}：{scene_input["intent"]}')

    if scene_input.get('scene_restrictions'):
        lines.append(f'\n{labels["restrict"]}\n{scene_input["scene_restrictions"]}')

    return '\n'.join(lines)
