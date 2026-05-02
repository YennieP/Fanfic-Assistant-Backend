"""
LLM pipeline：文章情节切割 + TAXONOMY 标签推断。

主要变更：
- segment_article 返回 list[dict]（含 text/type/start/end），原来只返回 list[str]
- 切割 prompt 要求 LLM 覆盖全部行并分类 story/skip
- infer_tags 接受 language 参数，按语言使用对应 TAXONOMY
- 新增 global_start / overlap_context 支持智能重切割
"""
import json
import re
import logging

from core.taxonomy import TAXONOMY, TAXONOMY_EN

logger = logging.getLogger(__name__)

MAX_CHARS_PER_CHUNK = 3000


# ── Segmentation ────────────────────────────────────────────────────────────

SEGMENTATION_SYSTEM_PROMPT = """你是一个专业的同人文分析助手。
你的任务是将用户提供的文章按情节切割，返回每个片段的起止行号和类型。

【切割规则】
- 每个片段是一个"情节场景单元"，按情节完整性和情感弧度切割
- 必须覆盖所有行，不得遗漏任何一行（章节标题、作者注、过渡段也要纳入，标记为 skip）
- 行号已在内容中标注，使用标注的实际行号填写 start 和 end
- start 和 end 均为包含关系（end 行也属于该片段）
- type 取值：
    story = 有情节价值、可作为风格参考入库的对话/叙事片段
    skip  = 章节标题、作者注、纯场景描述、无情节价值的过渡段等

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{"segments": [{"start": 0, "end": 5, "type": "story"}, {"start": 6, "end": 7, "type": "skip"}, ...]}"""


def segment_article(
    article_content: str,
    provider,
    global_start: int = 0,
    overlap_context: str | None = None,
) -> list[dict]:
    """
    LLM 切割文章，返回 list[dict]，每个 dict 包含：
        text  : 片段原文
        type  : 'story' | 'skip'
        start : 绝对行号（在完整原文中的起始行）
        end   : 绝对行号（在完整原文中的终止行，含）

    global_start: 本次切割内容在完整原文中的起始行号（用于智能重切割时保持行号一致性）
    overlap_context: 上一个已确认片段的文字（作为上下文，让 LLM 判断新内容是否应与之合并）
    """
    lines = article_content.splitlines()
    if not lines:
        return []

    # 行号使用绝对编号（含 global_start 偏移）
    numbered = '\n'.join(f'{i + global_start}: {line}' for i, line in enumerate(lines))
    chunks = _split_numbered_lines(numbered)
    all_segments: list[dict] = []

    for chunk_idx, (chunk_text, _chunk_line_count) in enumerate(chunks):
        # 仅在第一块且有上下文时注入，避免每块都重复
        context_prefix = ''
        if chunk_idx == 0 and overlap_context:
            context_prefix = (
                '【前一个已确认片段（作为上下文参考，不要重新切割这部分内容）：】\n'
                f'{overlap_context}\n\n'
            )

        result = provider.complete(
            system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            user_prompt=f'{context_prefix}请切割以下文章（行号已标注）：\n\n{chunk_text}',
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

            # 转换为本地行索引提取文字
            local_start = abs_start - global_start
            local_end   = abs_end   - global_start
            local_start = max(0, local_start)
            local_end   = min(len(lines) - 1, local_end)

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


def _split_numbered_lines(numbered_text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[tuple[str, int]]:
    """
    按字符数切分带行号的文本，保持行完整性。
    返回 (chunk_text, 该块行数) 列表。
    """
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
    """将 taxonomy dict 转为 prompt 中的可选值说明文字。"""
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
    """
    使用 LLM 为片段推断 TAXONOMY 标签。

    language: 'zh'（默认）或 'en'。
    语言决定 TAXONOMY 选项，从而决定存储的标签值所用语言。
    前端 tag 显示时需按存储语言匹配对应 dropdown 选项。
    """
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
    """去除 markdown 代码块标记后解析 JSON，截断时尝试修复。"""
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
    """过滤 null 值，保持结构干净。"""
    cleaned = {}
    for k, v in tags.items():
        if k == 'emotion' and isinstance(v, dict):
            emotion_clean = {ek: ev for ek, ev in v.items() if ev and ev != 'null'}
            if emotion_clean:
                cleaned['emotion'] = emotion_clean
        elif v and v != 'null':
            cleaned[k] = v
    return cleaned