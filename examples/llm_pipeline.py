"""
LLM pipeline：文章情节切割 + TAXONOMY 标签推断。

主要变更：
- segment_article 返回 list[dict]（含 text/type/start/end）
- 切割 prompt 要求 LLM 覆盖全部行并分类 story/skip
- prev_context / next_context：双向上下文（前置/后置已确认片段）支持三种缺口场景：
    - 缺口在正文开头（只有 next_context）
    - 缺口在正文中间（prev_context + next_context）
    - 缺口在正文末尾（只有 prev_context）
- infer_tags 接受 language 参数，按语言使用对应 TAXONOMY
- emotion.shared 现为数组（支持复合情绪），prompt 和 _clean_tags 同步更新
"""
import json
import re
import uuid
import logging
from logs.decorators import log_llm_call

from core.taxonomy import TAXONOMY, TAXONOMY_EN

logger = logging.getLogger(__name__)

MAX_CHARS_PER_CHUNK = 3000
MAX_LINES_PER_CHUNK = 100
CHUNK_BOUNDARY_LOOKBACK_LINES = 20
SEGMENTATION_MAX_OUTPUT_TOKENS = 4000

# 上下文截取行数：只取前置片段的最后 N 行 / 后置片段的最前 N 行
# 聚焦在边界附近，减少 token 消耗和 LLM 干扰
CONTEXT_BOUNDARY_LINES = 6


# ── Segmentation ────────────────────────────────────────────────────────────

SEGMENTATION_SYSTEM_PROMPT = """你是一个专业的同人文分析助手。
你的任务是将用户提供的文章片段按情节切割，返回每个片段的起止行号和类型。

【切割规则】
- 每个片段是一个"情节场景单元"，按情节完整性和情感弧度切割
- 每个非空行必须恰好属于一个片段，不得遗漏或重复覆盖
- 空白行可以并入相邻片段，也可以不分配；不得为纯空白行单独创建片段
- 行号已在内容中标注，start 和 end 必须复制输入中实际出现的行号，不要从 0 重新编号
- start 和 end 均包含在片段范围内（inclusive），且不得超出本次允许返回的行号范围
- type 取值：
    story = 有情节价值、可作为风格参考入库的对话/叙事片段
    skip  = 章节标题、作者注、过渡段等无情节价值的内容

【上下文说明】
- 若提供了前置/后置已确认片段，仅作为边界参考，不要将其纳入切割范围
- 前置片段告诉你：紧接在待切割内容之前的内容是什么
- 后置片段告诉你：紧接在待切割内容之后的内容是什么
- 据此判断待切割内容的首尾是否应该与前后已有片段衔接

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
顶层对象只能包含 segments 数组；数组中每项只能包含整数 start、整数 end 和字符串 type。"""


def segment_article(
    article_content: str,
    provider,
    global_start: int = 0,
    prev_context: str | None = None,
    next_context: str | None = None,
    user=None,
) -> list[dict]:
    """
    LLM 切割一段文章内容，返回 list[dict]，每个 dict 包含：
        text  : 片段原文
        type  : 'story' | 'skip'
        start : 绝对行号（在完整原文中的起始行）
        end   : 绝对行号（在完整原文中的终止行，含）

    global_start: 本次切割内容在完整原文中的起始行号
    prev_context: 该缺口前方最近的已确认片段（后 CONTEXT_BOUNDARY_LINES 行）
    next_context: 该缺口后方最近的已确认片段（前 CONTEXT_BOUNDARY_LINES 行）
    """
    lines = article_content.splitlines()
    if not lines:
        return []

    numbered = '\n'.join(f'{i + global_start}: {line}' for i, line in enumerate(lines))
    chunks = _split_numbered_lines(numbered)

    prev_snippet = _tail_lines(prev_context, CONTEXT_BOUNDARY_LINES) if prev_context else None
    next_snippet = _head_lines(next_context, CONTEXT_BOUNDARY_LINES) if next_context else None

    all_segments: list[dict] = []

    chunk_line_offset = 0
    for chunk_idx, (chunk_text, chunk_line_count) in enumerate(chunks):
        is_first = chunk_idx == 0
        is_last  = chunk_idx == len(chunks) - 1
        chunk_start = global_start + chunk_line_offset
        chunk_end = chunk_start + chunk_line_count - 1
        chunk_line_offset += chunk_line_count

        prefix = ''
        if is_first and prev_snippet:
            prefix = (
                '【前置已确认片段末尾（边界参考，不要重新切割此部分）：】\n'
                f'{prev_snippet}\n\n'
            )

        suffix = ''
        if is_last and next_snippet:
            suffix = (
                '\n\n【后置已确认片段开头（边界参考，不要重新切割此部分）：】\n'
                f'{next_snippet}'
            )

        format_example = json.dumps({
            'segments': [{
                'start': chunk_start,
                'end': chunk_end,
                'type': 'story',
            }],
        }, ensure_ascii=False, separators=(',', ':'))
        _seg_prompt = (
            f'{prefix}'
            f'本次允许返回的行号范围：{chunk_start} 到 {chunk_end}（均包含）。\n'
            '只复制下方每行冒号前的实际行号，不要重新编号。\n'
            '以下内容只演示合法 JSON 语法和本次可用的行号；'
            '不要照抄分组，仍须按情节决定片段数量：\n'
            f'{format_example}\n'
            f'请切割以下文章片段（行号已标注）：\n\n{chunk_text}'
            f'{suffix}'
        )

        @log_llm_call(feature='segment_article', sync=True)
        def _call_segment(user=None, generation_id=None):
            return provider.complete(
                system_prompt=SEGMENTATION_SYSTEM_PROMPT,
                user_prompt=_seg_prompt,
                # Gemini 2.5 may spend part of this budget on internal
                # reasoning. Production capacity checks exhausted 2000 tokens
                # on a 2763-character, 215-line chunk before JSON completed.
                max_tokens=SEGMENTATION_MAX_OUTPUT_TOKENS,
            )

        chunk_text_result = _call_segment(user=user, generation_id=uuid.uuid4())
        if not chunk_text_result:
            logger.warning('Segment chunk %d returned empty response, skipping', chunk_idx)
            continue

        data = _parse_json(chunk_text_result)
        segments = data.get('segments') if isinstance(data, dict) else None
        if not isinstance(segments, list) or not segments:
            parsed_keys = (
                sorted(str(key) for key in data)
                if isinstance(data, dict)
                else []
            )
            logger.warning(
                'Segment chunk %d produced no usable segments: '
                'response_length=%d parsed_type=%s parsed_keys=%s '
                'object_balance=%d array_balance=%d contains_segments_key=%s',
                chunk_idx,
                len(chunk_text_result),
                type(data).__name__,
                parsed_keys,
                chunk_text_result.count('{') - chunk_text_result.count('}'),
                chunk_text_result.count('[') - chunk_text_result.count(']'),
                'segments' in chunk_text_result,
            )
            continue

        for seg in segments:
            abs_start = seg.get('start', 0)
            abs_end   = seg.get('end', abs_start)
            seg_type  = seg.get('type', 'story')
            if seg_type not in ('story', 'skip'):
                seg_type = 'story'

            local_start = max(0, abs_start - global_start)
            local_end   = min(len(lines) - 1, abs_end - global_start)
            fragment_lines = lines[local_start:local_end + 1]
            text = '\n'.join(fragment_lines).strip()
            if not text:
                continue

            all_segments.append({
                'text':  text,
                'type':  seg_type,
                'start': abs_start,
                'end':   abs_end,
            })

    return all_segments


def _head_lines(text: str, n: int) -> str:
    return '\n'.join(text.splitlines()[:n])


def _tail_lines(text: str, n: int) -> str:
    return '\n'.join(text.splitlines()[-n:])


def _split_numbered_lines(
    numbered_text: str,
    max_chars: int = MAX_CHARS_PER_CHUNK,
    max_lines: int = MAX_LINES_PER_CHUNK,
) -> list[tuple[str, int]]:
    all_lines = numbered_text.split('\n')
    chunks: list[tuple[str, int]] = []
    current_lines: list[str] = []
    current_len = 0

    for line in all_lines:
        # Account for the newline that join() inserts between source lines and
        # cap line count so short-line inputs cannot demand an unbounded JSON
        # segment list. A single source line may still be larger than max_chars
        # because splitting inside it would break the absolute-line protocol.
        while current_lines:
            next_len = current_len + 1 + len(line)
            exceeds_chars = next_len > max_chars
            exceeds_lines = len(current_lines) >= max_lines
            if not (exceeds_chars or exceeds_lines):
                break

            split_at = len(current_lines)
            lookback_start = max(
                0, split_at - CHUNK_BOUNDARY_LOOKBACK_LINES,
            )
            for index in range(split_at - 1, lookback_start - 1, -1):
                if re.fullmatch(r'\d+:\s*', current_lines[index]):
                    split_at = index + 1
                    break

            chunk_lines = current_lines[:split_at]
            chunks.append(('\n'.join(chunk_lines), len(chunk_lines)))
            current_lines = current_lines[split_at:]
            current_len = len('\n'.join(current_lines))

        next_len = current_len + (1 if current_lines else 0) + len(line)
        current_lines.append(line)
        current_len = next_len

    if current_lines:
        chunks.append(('\n'.join(current_lines), len(current_lines)))

    return chunks or [(numbered_text, len(all_lines))]


# ── Tag Inference ────────────────────────────────────────────────────────────

def _build_taxonomy_options(taxonomy: dict) -> str:
    lines: list[str] = []
    for field, values in taxonomy.items():
        if field == 'emotion':
            lines.append(f'emotion.shared 可选值：{values["shared"]}')
            lines.append(f'emotion.intensity 可选值：{values["intensity"]}')
        elif isinstance(values, list):
            lines.append(f'{field} 可选值：{values}')
    return '\n'.join(lines)


# emotion.shared 改为数组：prompt 明确说明可选多个，用数组返回
TAG_INFERENCE_SYSTEM_PROMPT_TEMPLATE = """你是一个专业的同人文片段分析助手。
你的任务是为给定的文章片段打上场景标签。

【TAXONOMY 可选值】
{taxonomy_options}

【打标签规则】
- 除 emotion.shared 外，每个字段只能从该字段的可选值列表中选一个（复制原始字符串），或填 null
- emotion.shared 可以选多个值（用数组表示），当角色情绪复杂时填入多个；只有一种情绪时填单元素数组
- emotion.intensity 仍只选一个（整体情绪强度）
- 不要自己创造不在列表中的值

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{{
  "scene_type": "选项原文或null",
  "scene_privacy": "选项原文或null",
  "initiative": "选项原文或null",
  "emotion": {{
    "shared": ["情绪1", "情绪2"] 或 null,
    "intensity": "选项原文或null"
  }},
  "target_type": "选项原文或null",
  "target_count": "选项原文或null",
  "relationship_state": "选项原文或null",
  "speech_intent": "选项原文或null"
}}"""


def infer_tags(fragment_text: str, provider, language: str = 'zh', user=None) -> dict:
    taxonomy = TAXONOMY_EN if language == 'en' else TAXONOMY
    system = TAG_INFERENCE_SYSTEM_PROMPT_TEMPLATE.format(
        taxonomy_options=_build_taxonomy_options(taxonomy)
    )
    _infer_prompt = f'请为以下片段打标签：\n\n{fragment_text}'

    @log_llm_call(feature='tag_inference', sync=True)
    def _call_infer(user=None, generation_id=None):
        return provider.complete(
            system_prompt=system,
            user_prompt=_infer_prompt,
            max_tokens=1000,
        )

    raw_text = _call_infer(user=user, generation_id=uuid.uuid4())
    logger.info('[infer_tags] raw LLM output: %s', raw_text)
    raw = _parse_json(raw_text)
    logger.info('[infer_tags] parsed result: %s', raw)
    return _clean_tags(raw)


# ── Utilities ────────────────────────────────────────────────────────────────

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
        last_complete = text.rfind('",')
        if last_complete != -1:
            truncated = text[:last_complete + 1]
            if '"segments"' in truncated:
                try:
                    return json.loads(truncated + ']}')
                except json.JSONDecodeError:
                    pass
        return {}


def _clean_tags(tags: dict) -> dict:
    """
    清理 LLM 推断结果：去掉 null 值，规范化 emotion.shared 为数组。
    兼容 LLM 返回字符串的情况（部分模型可能忽略 prompt 中的数组要求）。
    """
    cleaned = {}
    for k, v in tags.items():
        if k == 'emotion' and isinstance(v, dict):
            emotion_clean: dict = {}
            shared = v.get('shared')
            if isinstance(shared, list):
                # LLM 正确返回数组
                valid = [s for s in shared if s and s != 'null']
                if valid:
                    emotion_clean['shared'] = valid
            elif isinstance(shared, str) and shared and shared != 'null':
                # LLM 返回字符串（兼容处理，统一转为数组）
                emotion_clean['shared'] = [shared]

            intensity = v.get('intensity')
            if intensity and intensity != 'null':
                emotion_clean['intensity'] = intensity

            if emotion_clean:
                cleaned['emotion'] = emotion_clean
        elif v and v != 'null':
            cleaned[k] = v
    return cleaned
