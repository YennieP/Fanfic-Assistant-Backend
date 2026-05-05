"""
LLM 辅助角色卡推断与补全
POST /api/characters/suggest-completions/

接收前端当前表单状态（可未保存），推断空白字段，以草稿形式返回建议。
不读写角色卡数据库，纯计算端点。
"""
import json
import re
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from users.encryption import decrypt_key
from users.models import UserProviderKey
from generation.providers.anthropic import AnthropicProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.cerebras import CerebrasProvider
from generation.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


SUGGEST_SYSTEM_PROMPT = """你是一个专业的同人文角色卡分析助手。
任务：基于用户已填写的角色信息，为空白或内容较少的字段提供补全建议。

【信息权重（严格遵守）】
推断必须按以下优先级进行，不得违反高权重信息：

1. 已填写的行为模式（behavioral_patterns）— 最高权重
   这是用户基于真实素材观察到的具体行为证据，是所有推断的基础和硬约束。
   所有建议必须与已有行为模式在逻辑上一致，不得矛盾。

2. 核心价值观、核心恐惧、人设红线、性格标签 — 高权重
   用户已经填写的性格约束字段，推断必须在这些约束范围内进行。

3. MBTI — 辅助解释层，不是推断驱动层
   MBTI 只能用于解释已有行为模式的内在逻辑，或在没有任何行为证据时提供非常有限的参考。
   不能用 MBTI 的「通用人格描述」推断该角色的具体行为——MBTI 是解释层，不是生成层。
   如果已有行为模式与 MBTI 的通用描述不符，以行为模式为准，不要试图调和。

【推断原则】
- 优先从已有行为模式里归纳规律，推断同类型的其他场景
- 优先从核心恐惧和人设红线反推可能的行为约束
- 置信度低时，在 reasoning 里明确说明「依据不足」或「仅作参考」
- 如果现有信息不足以支撑某字段的合理推断，不要强行给出建议
- 每条建议必须明确引用它依赖的具体已填字段内容作为依据

【字段类型说明】
- list_append：向列表字段追加新条目（items 为字符串数组）
- text：纯文本字段（value 为字符串）
- pattern：行为模式（包含完整 trigger + response 结构）
- trigger：情绪触发点（trigger + reaction 两个字段）

【字段名规则】
field 字段必须使用括号内标注的 camelCase 英文字段名。

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{
  "suggestions": [
    {
      "field": "英文字段名（camelCase）",
      "label": "字段中文显示名",
      "type": "list_append|text|pattern|trigger",
      "items": ["条目1", "条目2"],
      "value": "文本内容",
      "pattern": {
            "trigger": {
                "immediate": "直接触发情境，例：有人当众质疑他的判断",
                "priorContext": "前因背景，例：上周刚因类似事情被批评过",
                "relationship": "涉及的具体对象及关系，例：队友陈默，两人关系紧张中（若无具体对象可留空）",
                "stakes": "对角色意味着什么，例：直接威胁到他对自我掌控感的认知"
            },
            "response": {
                "immediate": "当下可见反应（台词/动作/表情），例：神情不变，简短回应，随后拉开物理距离",
                "followUp": "事后行为，例：独处两小时后主动找对方谈",
                "internal": "内心实际感受（与外在不同），例：表面平静但内心已在评估是否需要重新界定边界"
            }
        },
      "triggerItem": {
        "trigger": "触发情境描述",
        "reaction": "具体反应方式"
      },
      "reasoning": "推断依据（必须引用具体已填字段的内容，说明推断逻辑）"
    }
  ]
}"""

SUGGEST_SYSTEM_PROMPT_EN = """You are a professional character card analysis assistant for fanfiction.
Task: Based on the information the user has already filled in, provide completion suggestions for empty or sparse fields.

[Information Weight — Strictly Observed]
All inferences must follow this priority order and must not contradict higher-weight information:

1. Existing behavioral_patterns — Highest weight
   These are specific behavioral observations based on real source material and serve as the hard constraint for all inferences.
   All suggestions must be logically consistent with existing patterns.

2. core_values, core_fears, forbidden_behaviors, quick_labels — High weight
   Already-filled personality constraints. Inferences must stay within these bounds.

3. MBTI — Interpretive layer only, not a generative layer
   MBTI may only be used to explain the internal logic of existing patterns, or provide very limited reference when no behavioral evidence exists.
   Do NOT use generic MBTI personality descriptions to infer specific behaviors.
   If existing patterns conflict with generic MBTI descriptions, defer to the patterns.

[Inference Principles]
- Derive patterns from existing behavioral evidence first, then infer similar scenarios
- Use core_fears and forbidden_behaviors to back-infer behavioral constraints
- When confidence is low, explicitly state "insufficient evidence" or "for reference only" in reasoning
- Do not force suggestions when existing information cannot support a reasonable inference
- Each suggestion must cite specific already-filled field content as evidence

[Field Types]
- list_append: Append new items to a list field (items is a string array)
- text: Plain text field (value is a string)
- pattern: Behavioral pattern (full trigger + response structure)
- trigger: Emotional trigger (trigger + reaction fields)

[Field Name Rules]
The field value must use the camelCase English field name shown in parentheses.

[Return Format]
Return strict JSON only. No markdown code blocks. No explanations:
{
  "suggestions": [
    {
      "field": "camelCase field name",
      "label": "field display name",
      "type": "list_append|text|pattern|trigger",
      "items": ["item1", "item2"],
      "value": "text content",
      "pattern": {
            "trigger": {
                "immediate": "Direct trigger situation, e.g. Someone publicly questions his judgment",
                "priorContext": "Background context, e.g. Was criticized for something similar last week",
                "relationship": "Specific person and relationship involved, e.g. Teammate Chen Mo, relationship currently tense (leave blank if no specific person)",
                "stakes": "What this means to the character, e.g. Directly threatens his sense of self-control"
            },
            "response": {
                "immediate": "Visible immediate reaction (dialogue/action/expression), e.g. Expression unchanged, brief reply, then creates physical distance",
                "followUp": "Post-event behavior, e.g. Initiates a conversation with the person after 2 hours alone",
                "internal": "Actual inner feelings (may differ from outward appearance), e.g. Calm on the surface but internally reassessing whether to redefine boundaries"
            }
        },
      "triggerItem": {
        "trigger": "Description of trigger situation",
        "reaction": "Specific reaction"
      },
      "reasoning": "Reasoning (must cite specific already-filled field content and explain the inference logic)"
    }
  ]
}"""


def _list_contents(items: list) -> list[str]:
    """从 [{id, content}] 或 [str] 中提取纯文本列表，过滤空值。"""
    result = []
    for item in items:
        c = item.get('content', '') if isinstance(item, dict) else str(item)
        if c.strip():
            result.append(c.strip())
    return result


def build_suggest_prompt(character_data: dict, output_language: str = 'zh') -> str:
    en = output_language == 'en'
    name = character_data.get('name', 'Unknown Character' if en else '未知角色')
    fandom = character_data.get('fandom', '')

    context_lines = [f'{'Character' if en else '角色名'}：{name}']
    if fandom:
        context_lines.append(f"{'Source' if en else '来源'}：{fandom}")

    # ── 高权重字段优先呈现 ────────────────────────────────────────────────
    _hw_fields = [
        ('core_values',        '核心价值观（高权重）',        'Core Values (high weight)'),
        ('core_fears',         '核心恐惧（高权重）',          'Core Fears (high weight)'),
        ('quick_labels',       '性格标签（高权重）',          'Personality Tags (high weight)'),
        ('forbidden_behaviors','人设红线（高权重，硬约束）',  'Character Limits (high weight, hard constraint)'),
    ]
    for field_snake, label_zh, label_en in _hw_fields:
        label = label_en if en else label_zh
        items = _list_contents(character_data.get(field_snake) or [])
        if items:
            context_lines.append(f'{label}：{"、".join(items)}')

    # ── 行为模式：最高权重，全部展示，不截断 ─────────────────────────────
    patterns = character_data.get('behavioral_patterns') or []
    if patterns:
        context_lines.append(f"\n{'[Observed Behavioral Patterns (highest weight — all inferences must be consistent with these)]' if en else '【已观察到的行为模式（最高权重，所有推断必须与此一致，不得矛盾）】'}")
        for i, p in enumerate(patterns):
            t = p.get('trigger', {}) if isinstance(p, dict) else {}
            r = p.get('response', {}) if isinstance(p, dict) else {}
            imm      = (t.get('immediate') or '').strip()
            prior    = (t.get('prior_context') or t.get('priorContext') or '').strip()
            rel      = (t.get('relationship') or '').strip()
            stakes   = (t.get('stakes') or '').strip()
            resp_imm = (r.get('immediate') or '').strip()
            resp_fol = (r.get('follow_up') or r.get('followUp') or '').strip()
            resp_int = (r.get('internal') or '').strip()

            _t = 'Trigger' if en else '触发'
            _p = 'Prior' if en else '前因'
            _r = 'Rel' if en else '关系'
            _s = 'Stakes' if en else '利害'
            _ri = 'Response' if en else '当下'
            _f = 'FollowUp' if en else '事后'
            _in = 'Internal' if en else '内心'
            line = f'  Pattern{i+1}：{_t}="{imm}"' if en else f'  模式{i+1}：触发="{imm}"'
            if prior:   line += f'，{_p}="{prior}"'
            if rel:     line += f'，{_r}="{rel}"'
            if stakes:  line += f'，{_s}="{stakes}"'
            line += f' → {_ri}="{resp_imm}"'
            if resp_fol: line += f'，{_f}="{resp_fol}"'
            if resp_int: line += f'，{_in}="{resp_int}"'
            context_lines.append(line)

    # ── 情绪触发点：全部展示 ──────────────────────────────────────────────
    triggers = character_data.get('emotional_triggers') or []
    if triggers:
        context_lines.append(f"\n{'Existing Emotional Triggers' if en else '已有情绪触发点'} ({len(triggers)}):")
        for tr in triggers:
            if isinstance(tr, dict):
                tr_text  = (tr.get('trigger') or '').strip()
                tr_react = (tr.get('reaction') or '').strip()
                if tr_text or tr_react:
                    _tl = 'Trigger' if en else '触发'
                    _rl = 'Reaction' if en else '反应'
                    context_lines.append(f'  {_tl}="{tr_text}" → {_rl}="{tr_react}"')

    # ── 其他字段 ──────────────────────────────────────────────────────────
    _text_fields = [
        ('default_state',           '日常情绪基调',    'Default Emotional State'),
        ('emotion_expression_style','情绪表达方式',    'Emotion Expression Style'),
        ('recovery_pattern',        '情绪恢复方式',    'Recovery Pattern'),
    ]
    for field_snake, label_zh, label_en in _text_fields:
        label = label_en if en else label_zh
        val = (character_data.get(field_snake) or '').strip()
        if val:
            context_lines.append(f'{label}：{val}')

    key_exp = _list_contents(character_data.get('key_experiences') or [])
    if key_exp:
        context_lines.append(f"{'Key Experiences' if en else '重要经历'}：{'，'.join(key_exp)}")

    # ── MBTI 最后呈现，明确标注为辅助解释层 ──────────────────────────────
    mbti = (character_data.get('mbti') or '').strip()
    if mbti:
        notes = (character_data.get('mbti_notes') or '').strip()
        if en:
            mbti_line = (
                f'\nMBTI: {mbti}'
                f' (Interpretive layer only — use only to explain existing behavioral patterns, '
                f'not to infer new behaviors or override existing patterns)'
            )
            if notes:
                mbti_line += f'\nHow MBTI manifests in this character: {notes}'
        else:
            mbti_line = (
                f'\nMBTI：{mbti}'
                f'（辅助参考层——只能用于解释已有行为模式的内在逻辑，'
                f'不能作为推断新行为的依据，不能覆盖已有行为模式）'
            )
            if notes:
                mbti_line += f'\nMBTI 在该角色身上的具体体现：{notes}'
        context_lines.append(mbti_line)

    context = '\n'.join(context_lines)

    # ── 确定需要补全的字段 ────────────────────────────────────────────────
    empty_fields = []

    _list_field_defs = [
        ('core_values',         'coreValues',         '核心价值观',    'Core Values'),
        ('core_fears',          'coreFears',          '核心恐惧',      'Core Fears'),
        ('key_experiences',     'keyExperiences',     '重要经历',      'Key Experiences'),
        ('quick_labels',        'quickLabels',        '性格标签',      'Personality Tags'),
        ('forbidden_behaviors', 'forbiddenBehaviors', '人设红线',      'Character Limits'),
    ]
    for field_snake, field_camel, label_zh, label_en in _list_field_defs:
        label = label_en if en else label_zh
        if len(_list_contents(character_data.get(field_snake) or [])) < 2:
            empty_fields.append(f'{label} (field: {field_camel}, type: list_append)')

    _text_field_defs = [
        ('default_state',           'defaultState',           '日常情绪基调',    'Default Emotional State'),
        ('emotion_expression_style','emotionExpressionStyle', '情绪表达方式',    'Emotion Expression Style'),
        ('recovery_pattern',        'recoveryPattern',        '情绪恢复方式',    'Recovery Pattern'),
    ]
    for field_snake, field_camel, label_zh, label_en in _text_field_defs:
        label = label_en if en else label_zh
        if not (character_data.get(field_snake) or '').strip():
            empty_fields.append(f'{label} (field: {field_camel}, type: text)')

    if len(patterns) < 3:
        if en:
            empty_fields.append(
                f'Behavioral Patterns (field: behavioralPatterns, type: pattern, '
                f'current: {len(patterns)}, suggest 1-2 new scenarios consistent with existing patterns)'
            )
        else:
            empty_fields.append(
                f'行为模式（field: behavioralPatterns，type: pattern，'
                f'当前 {len(patterns)} 条，建议补充 1-2 条新场景，必须与已有模式风格一致）'
            )

    if len(triggers) < 2:
        if en:
            empty_fields.append(
                f'Emotional Triggers (field: emotionalTriggers, type: trigger, '
                f'current: {len(triggers)})'
            )
        else:
            empty_fields.append(
                f'情绪触发点（field: emotionalTriggers，type: trigger，'
                f'当前 {len(triggers)} 条）'
            )

    if not empty_fields:
        return (
            f'{context}\n\n'
            + ('The character card is sufficiently complete. Return {"suggestions": []}' if en
               else '当前角色卡各字段内容已较为充分。请返回 {"suggestions": []}')
        )

    target_list = '\n'.join(f'  - {f}' for f in empty_fields)
    if en:
        return (
            f"Here is the filled information for character {name}:\n\n{context}\n\n"
            f"---\n\n"
            f"Please provide completion suggestions for the following empty or sparse fields:\n{target_list}\n\n"
            f"Note: Use the exact camelCase field name shown in parentheses; "
            f"do not repeat existing content; write label as the English display name; "
            f"all suggestions must be consistent with existing behavioral patterns."
        )
    else:
        return (
            f'以下是角色 {name} 已填写的信息：\n\n{context}\n\n'
            f'---\n\n'
            f'请为以下空白或内容不足的字段提供补全建议：\n{target_list}\n\n'
            f'注意：严格使用括号内标注的 field 名称（camelCase）；'
            f'不重复已有内容；label 填中文显示名；'
            f'所有建议必须与已有行为模式保持一致，不得矛盾。'
        )

def _parse_json(text: str) -> dict:
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


class SuggestCompletionsView(APIView):
    """
    POST /api/characters/suggest-completions/

    Request body:
        { "character_data": { ...表单字段（camelCase）... } }

    Response:
        { "suggestions": [ { field, label, type, items?, value?, pattern?, triggerItem?, reasoning } ] }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        character_data = request.data.get('character_data', {})

        if not (character_data.get('name') or '').strip():
            return Response({'error': '请先填写角色名'}, status=400)

        try:
            llm_config = request.user.llm_config
        except Exception:
            return Response({'error': '未配置 API Key，请先在设置页配置'}, status=400)

        try:
            key_obj = UserProviderKey.objects.get(
                user=request.user, provider=llm_config.provider
            )
            api_key = decrypt_key(key_obj.api_key_encrypted)
        except Exception:
            return Response(
                {'error': f'未找到 {llm_config.provider} 的 API Key，请在设置页保存'},
                status=400,
            )

        provider_map = {
            'anthropic': AnthropicProvider,
            'groq':      GroqProvider,
            'cerebras':  CerebrasProvider,
            'openrouter':OpenRouterProvider,
        }
        ProviderClass = provider_map.get(llm_config.provider, GeminiProvider)
        provider = ProviderClass(api_key)

        output_language = request.data.get('output_language') or request.data.get('outputLanguage', 'zh')
        system = SUGGEST_SYSTEM_PROMPT_EN if output_language == 'en' else SUGGEST_SYSTEM_PROMPT
        user_prompt = build_suggest_prompt(character_data, output_language)

        try:
            result = provider.complete(
                system_prompt=system,
                user_prompt=user_prompt,
                max_tokens=2000,
            )
        except Exception as e:
            logger.exception('suggest_completions LLM call failed')
            return Response({'error': str(e)}, status=500)

        if not result.text:
            return Response({'error': '推断返回为空，请重试'}, status=500)

        data = _parse_json(result.text)
        suggestions = data.get('suggestions', [])

        return Response({'suggestions': suggestions})