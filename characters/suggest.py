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

【重要原则】
- 只推断基于现有信息能够合理推断的内容，不要无中生有
- 每条建议必须引用具体的已填字段作为依据，说明推断逻辑
- 置信度低时，在 reasoning 中明确说明「依据不足，仅供参考」
- 只对明显为空（空字符串/空列表）或内容稀少（少于 2 条）的字段给出建议
- 每个字段最多建议 3 条新内容，不重复已有内容

【字段类型说明】
- list_append：向列表字段追加新条目（items 为字符串数组）
- text：纯文本字段（value 为字符串）
- pattern：行为模式（包含完整 trigger + response 结构）
- trigger：情绪触发点（trigger + reaction 两个字段）

【字段名规则】
field 字段必须使用准确的 camelCase 英文字段名，即提示中括号内标注的名称。

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{
  "suggestions": [
    {
      "field": "英文字段名（camelCase，如 coreValues）",
      "label": "字段中文显示名",
      "type": "list_append|text|pattern|trigger",
      "items": ["条目1", "条目2"],
      "value": "文本内容",
      "pattern": {
        "trigger": {
          "immediate": "直接触发情境",
          "priorContext": "前因背景",
          "relationship": "涉及的对象和关系",
          "stakes": "对角色意味着什么"
        },
        "response": {
          "immediate": "当下可见反应（台词/动作/表情）",
          "followUp": "事后行为",
          "internal": "内心实际感受"
        }
      },
      "triggerItem": {
        "trigger": "触发情境描述",
        "reaction": "具体反应方式"
      },
      "reasoning": "推断依据（引用具体已填字段内容）"
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


def build_suggest_prompt(character_data: dict) -> str:
    name = character_data.get('name', '未知角色')
    fandom = character_data.get('fandom', '')

    # ── 已填字段汇总（作为推断上下文）────────────────────────────────────
    context_lines = [f'角色名：{name}']
    if fandom:
        context_lines.append(f'来源：{fandom}')

    mbti = (character_data.get('mbti') or '').strip()
    if mbti:
        context_lines.append(f'MBTI：{mbti}')
        notes = (character_data.get('mbtiNotes') or '').strip()
        if notes:
            context_lines.append(f'MBTI 体现：{notes}')

    for field, label in [
        ('coreValues',        '核心价值观'),
        ('coreFears',         '核心恐惧'),
        ('keyExperiences',    '重要经历'),
        ('quickLabels',       '性格标签'),
        ('forbiddenBehaviors','人设红线'),
    ]:
        items = _list_contents(character_data.get(field) or [])
        if items:
            context_lines.append(f'{label}：{"、".join(items)}')

    for field, label in [
        ('defaultState',          '日常情绪基调'),
        ('emotionExpressionStyle','情绪表达方式'),
        ('recoveryPattern',       '情绪恢复方式'),
    ]:
        val = (character_data.get(field) or '').strip()
        if val:
            context_lines.append(f'{label}：{val}')

    patterns = character_data.get('behavioralPatterns') or []
    if patterns:
        context_lines.append(f'已有行为模式（{len(patterns)} 条，摘录前 2 条）：')
        for i, p in enumerate(patterns[:2]):
            t = p.get('trigger', {}) if isinstance(p, dict) else {}
            r = p.get('response', {}) if isinstance(p, dict) else {}
            imm = (t.get('immediate') or '').strip()
            resp = (r.get('immediate') or '').strip()
            if imm or resp:
                context_lines.append(f'  模式{i+1}：触发="{imm}" → 当下="{resp}"')

    triggers = character_data.get('emotionalTriggers') or []
    if triggers:
        context_lines.append(f'已有情绪触发点（{len(triggers)} 条）')

    context = '\n'.join(context_lines)

    # ── 确定需要补全的字段 ────────────────────────────────────────────────
    empty_fields = []

    for field, label in [
        ('coreValues',        '核心价值观'),
        ('coreFears',         '核心恐惧'),
        ('keyExperiences',    '重要经历'),
        ('quickLabels',       '性格标签'),
        ('forbiddenBehaviors','人设红线'),
    ]:
        if len(_list_contents(character_data.get(field) or [])) < 2:
            empty_fields.append(f'{label}（field: {field}，type: list_append）')

    for field, label in [
        ('defaultState',          '日常情绪基调'),
        ('emotionExpressionStyle','情绪表达方式'),
        ('recoveryPattern',       '情绪恢复方式'),
    ]:
        if not (character_data.get(field) or '').strip():
            empty_fields.append(f'{label}（field: {field}，type: text）')

    if len(patterns) < 3:
        empty_fields.append(
            f'行为模式（field: behavioralPatterns，type: pattern，'
            f'当前 {len(patterns)} 条，建议补充 1-2 条新场景）'
        )

    if len(triggers) < 2:
        empty_fields.append(
            f'情绪触发点（field: emotionalTriggers，type: trigger，'
            f'当前 {len(triggers)} 条）'
        )

    if not empty_fields:
        return (
            f'{context}\n\n'
            '当前角色卡各字段内容已较为充分。请返回 {"suggestions": []}'
        )

    target_list = '\n'.join(f'  - {f}' for f in empty_fields)
    return (
        f'以下是角色 {name} 已填写的信息：\n\n{context}\n\n'
        f'---\n\n'
        f'请为以下空白或内容不足的字段提供补全建议：\n{target_list}\n\n'
        f'注意：严格使用括号内标注的 field 名称；不重复已有内容；label 填中文显示名。'
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

        user_prompt = build_suggest_prompt(character_data)

        try:
            result = provider.complete(
                system_prompt=SUGGEST_SYSTEM_PROMPT,
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