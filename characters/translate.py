"""
角色卡多语言翻译

POST /api/characters/{canonical_id}/translate/
  接收字段列表，返回翻译预览（不写库），前端确认后调用创建端点写库。

GET /api/characters/{canonical_id}/versions/
  返回该 canonical_id 下所有语言版本的概要信息。
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
from .models import BaseCard

logger = logging.getLogger(__name__)

LANG_NAMES = {'zh': '中文', 'en': 'English'}

# camelCase（前端）→ snake_case（Django model 属性）
FIELD_MAPPING = {
    'name':                   'name',
    'fandom':                 'fandom',
    'mbtiNotes':              'mbti_notes',
    'coreValues':             'core_values',
    'coreFears':              'core_fears',
    'keyExperiences':         'key_experiences',
    'quickLabels':            'quick_labels',
    'forbiddenBehaviors':     'forbidden_behaviors',
    'behavioralPatterns':     'behavioral_patterns',
    'defaultState':           'default_state',
    'emotionExpressionStyle': 'emotion_expression_style',
    'recoveryPattern':        'recovery_pattern',
    'emotionalTriggers':      'emotional_triggers',
    'physicalTraits':         'physical_traits',
}

TRANSLATE_SYSTEM_PROMPT = """你是一个专业的角色卡翻译助手。
任务：将角色卡字段从 {source_lang} 翻译到 {target_lang}。

【翻译规则】
- 翻译所有文字内容，保持原意、风格和语气
- 不添加或删减内容
- 对于角色名（name 字段）：根据提供的角色基本信息推断最合适的写法（罗马字、英文名等）
- 对于结构化字段（嵌套对象、列表）：只翻译字符串内容，保持结构完全不变
- 列表中的每条内容单独翻译，保持条目数量不变

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释。
返回的 JSON 结构必须与输入完全相同，只是所有字符串内容被翻译成 {target_lang}。"""


def _strip_ids(value):
    """递归移除 'id' 字段，减少 token、避免 LLM 误翻 ID。"""
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    if isinstance(value, dict):
        return {k: _strip_ids(v) for k, v in value.items() if k != 'id'}
    return value


def _reattach_ids(translated_value, original_value):
    """把原始 ID 回填到翻译结果对应位置。"""
    if isinstance(original_value, list) and isinstance(translated_value, list):
        result = []
        for orig, trans in zip(original_value, translated_value):
            if isinstance(orig, dict) and 'id' in orig and isinstance(trans, dict):
                result.append({'id': orig['id'], **{k: v for k, v in trans.items() if k != 'id'}})
            else:
                result.append(trans)
        # LLM 截断时，用原始补全剩余条目
        for orig in original_value[len(translated_value):]:
            result.append(orig)
        return result
    return translated_value


def _parse_json(text: str):
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


def _get_provider(request):
    """复用 generation/views.py 的 provider 获取模式。返回 (provider, error_msg)。"""
    try:
        llm_config = request.user.llm_config
    except Exception:
        return None, '未配置 API Key，请先在设置页配置'

    try:
        key_obj = UserProviderKey.objects.get(user=request.user, provider=llm_config.provider)
        api_key = decrypt_key(key_obj.api_key_encrypted)
    except Exception:
        return None, f'未找到 {llm_config.provider} 的 API Key，请在设置页保存'

    provider_map = {
        'anthropic':  AnthropicProvider,
        'groq':       GroqProvider,
        'cerebras':   CerebrasProvider,
        'openrouter': OpenRouterProvider,
    }
    ProviderClass = provider_map.get(llm_config.provider, GeminiProvider)
    return ProviderClass(api_key), None


class TranslateView(APIView):
    """
    POST /api/characters/{canonical_id}/translate/

    Request body:
        {
          "sourceLang": "zh",
          "targetLang": "en",
          "fields": ["name", "coreValues", ...]   ← camelCase
        }

    Response:
        {
          "translations": {
            "name": "Tomiyasu Yu",
            "coreValues": [{"id": "...", "content": "A sense of being needed"}, ...],
            ...
          }
        }
    翻译预览，不写库，前端确认后调用 POST /api/characters/ 创建新语言版本。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, canonical_id):
        source_lang = request.data.get('source_lang', 'zh')
        target_lang = request.data.get('target_lang', 'en')
        fields_camel = request.data.get('fields', list(FIELD_MAPPING.keys()))

        # 获取源语言 BaseCard
        try:
            source_card = BaseCard.objects.get(
                canonical_id=canonical_id,
                language=source_lang,
                owner=request.user,
            )
        except BaseCard.DoesNotExist:
            return Response({'error': f'未找到 {source_lang} 版本的角色卡'}, status=404)

        provider, err = _get_provider(request)
        if err:
            return Response({'error': err}, status=400)

        # 构建翻译输入：camelCase → model attr，strip IDs
        to_translate = {}
        for camel_field in fields_camel:
            snake_field = FIELD_MAPPING.get(camel_field)
            if not snake_field:
                continue
            value = getattr(source_card, snake_field, None)
            if not value and value != 0:
                continue
            to_translate[camel_field] = (
                _strip_ids(value) if isinstance(value, (list, dict)) else value
            )

        if not to_translate:
            return Response({'translations': {}})

        # 角色背景信息作为翻译上下文（帮助 LLM 推断角色名正确写法）
        context_lines = [f'角色名：{source_card.name}']
        if source_card.fandom:
            context_lines.append(f'来源作品：{source_card.fandom}')
        if source_card.mbti:
            context_lines.append(f'MBTI：{source_card.mbti}')
        context = '\n'.join(context_lines)

        source_name = LANG_NAMES.get(source_lang, source_lang)
        target_name = LANG_NAMES.get(target_lang, target_lang)

        system = TRANSLATE_SYSTEM_PROMPT.format(
            source_lang=source_name,
            target_lang=target_name,
        )
        user_prompt = (
            f'角色基本信息（仅供推断角色名时参考，无需翻译）：\n{context}\n\n'
            f'请将以下字段从{source_name}翻译到{target_name}：\n'
            f'{json.dumps(to_translate, ensure_ascii=False, indent=2)}'
        )

        try:
            result = provider.complete(
                system_prompt=system,
                user_prompt=user_prompt,
                max_tokens=3000,
            )
        except Exception as e:
            logger.exception('translate LLM call failed')
            return Response({'error': str(e)}, status=500)

        if not result.text:
            return Response({'error': '翻译返回为空，请重试'}, status=500)

        parsed = _parse_json(result.text)
        if not isinstance(parsed, dict):
            return Response({'error': '翻译结果格式异常，请重试'}, status=500)

        # 回填 ID，camelCase 键名直接返回（DRF camelCase 中间件会转换响应键）
        translations = {}
        for camel_field, translated_val in parsed.items():
            snake_field = FIELD_MAPPING.get(camel_field)
            if not snake_field:
                continue
            original_val = getattr(source_card, snake_field, None)
            if original_val is not None:
                translations[camel_field] = _reattach_ids(translated_val, original_val)
            else:
                translations[camel_field] = translated_val

        return Response({'translations': translations})


class VersionsView(APIView):
    """
    GET /api/characters/{canonical_id}/versions/

    返回该角色所有语言版本的概要（id、language、name、updatedAt）。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, canonical_id):
        versions = BaseCard.objects.filter(
            canonical_id=canonical_id,
            owner=request.user,
        ).values('id', 'language', 'name', 'updated_at')

        return Response({
            'versions': [
                {
                    'id': str(v['id']),
                    'language': v['language'],
                    'name': v['name'],
                    'updatedAt': v['updated_at'].isoformat() if v['updated_at'] else None,
                }
                for v in versions
            ]
        })