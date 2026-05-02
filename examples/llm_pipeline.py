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
"""
import json
import re
import logging

from core.taxonomy import TAXONOMY, TAXONOMY_EN

logger = logging.getLogger(__name__)

MAX_CHARS_PER_CHUNK = 3000

# 上下文截取行数：只取前置片段的最后 N 行 / 后置片段的最前 N 行
# 聚焦在边界附近，减少 token 消耗和 LLM 干扰
CONTEXT_BOUNDARY_LINES = 6


# ── Segmentation ────────────────────────────────────────────────────────────

SEGMENTATION_SYSTEM_PROMPT = """你是一个专业的同人文分析助手。
你的任务是将用户提供的文章片段按情节切割，返回每个片段的起止行号和类型。

【切割规则】
- 每个片段是一个"情节场景单元"，按情节完整性和情感弧度切割
- 必须覆盖所有行，不得遗漏任何一行（章节标题、作者注等也要纳入，标记为 skip）
- 行号已在内容中标注，使用标注的实际行号填写 start 和 end（inclusive）
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
{"segments": [{"start": 0, "end": 5, "type": "story"}, {"start": 6, "end": 7, "type": "skip"}, ...]}"""


def segment_article(
    article_content: str,
    provider,
    global_start: int = 0,
    prev_context: str | None = None,
    next_context: str | None = None,
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

    for chunk_idx, (chunk_text, _) in enumerate(chunks):
        is_first = chunk_idx == 0
        is_last  = chunk_idx == len(chunks) - 1

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

        result = provider.complete(
            system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            user_prompt=f'{prefix}请切割以下文章片段（行号已标注）：\n\n{chunk_text}{suffix}',
            max_tokens=800,
        )
        if not result.text:
            logger.warning('Segment chunk %d returned empty response, skipping', chunk_idx)
            continue

        data = _parse_json(result.text)
        for seg in data.get('segments', []):
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


def _split_numbered_lines(numbered_text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[tuple[str, int]]:
    all_lines = numbered_text.split('\n')
    chunks: list[tuple[str, int]] = []
    current_lines: list[str] = []
    current_len = 0

    for line in all_lines:
        if current_len + len(line) > max_chars and current_lines:
            chunks.append(('\n'.join(current_lines), len(current_lines)))
            current_lines = [line]
            current_len   = len(line)
        else:
            current_lines.append(line)
            current_len += len(line)

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


TAG_INFERENCE_SYSTEM_PROMPT_TEMPLATE = """你是一个专业的同人文片段分析助手。
你的任务是为给定的文章片段打上场景标签。

【TAXONOMY 可选值】
{taxonomy_options}

【打标签规则】
- 每个字段只能从该字段的可选值列表中选一个（复制原始字符串），或填 null
- 不要自己创造不在列表中的值
- emotion 包含两个子字段：shared（情绪类型）和 intensity（强度）

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{{
  "scene_type": "选项原文或null",
  "scene_privacy": "选项原文或null",
  "initiative": "选项原文或null",
  "emotion": {{
    "shared": "选项原文或null",
    "intensity": "选项原文或null"
  }},
  "target_type": "选项原文或null",
  "target_count": "选项原文或null",
  "relationship_state": "选项原文或null",
  "speech_intent": "选项原文或null"
}}"""


def infer_tags(fragment_text: str, provider, language: str = 'zh') -> dict:
    taxonomy = TAXONOMY_EN if language == 'en' else TAXONOMY
    system = TAG_INFERENCE_SYSTEM_PROMPT_TEMPLATE.format(
        taxonomy_options=_build_taxonomy_options(taxonomy)
    )
    result = provider.complete(
        system_prompt=system,
        user_prompt=f'请为以下片段打标签：\n\n{fragment_text}',
        max_tokens=1000,
    )
    logger.info('[infer_tags] raw LLM output: %s', result.text)
    raw = _parse_json(result.text)
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
    cleaned = {}
    for k, v in tags.items():
        if k == 'emotion' and isinstance(v, dict):
            emotion_clean = {ek: ev for ek, ev in v.items() if ev and ev != 'null'}
            if emotion_clean:
                cleaned['emotion'] = emotion_clean
        elif v and v != 'null':
            cleaned[k] = v
    return cleaned