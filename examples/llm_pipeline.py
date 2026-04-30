"""
LLM pipeline：文章情节切割 + TAXONOMY 标签推断。
"""
import json
import re
import logging

from core.taxonomy import TAXONOMY

logger = logging.getLogger(__name__)

# 每块最大字符数，按段落边界切分
# 2000字/块 × 4000 output tokens 足够容纳切割结果
MAX_CHARS_PER_CHUNK = 2000


# ── Segmentation ────────────────────────────────────────────────────────────

SEGMENTATION_SYSTEM_PROMPT = """你是一个专业的同人文分析助手。
你的任务是将用户提供的文章按情节切割，返回每个片段的起止行号。

【切割规则】
- 每个片段是一个"情节场景单元"，按情节完整性和情感弧度切割
- 一个情节片段可包含多个自然段
- 行号从 0 开始计数
- start 和 end 均为包含关系（end 行也属于该片段）

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{"segments": [{"start": 0, "end": 5}, {"start": 6, "end": 12}, ...]}"""


def segment_article(article_content: str, provider) -> list[str]:
    """
    让 LLM 返回行号边界，后端自己切原文。
    输出 token 从 8000+ 降至约 200，彻底避免截断和 503。
    """
    lines = article_content.splitlines()
    # 给每行加行号方便 LLM 定位
    numbered = '\n'.join(f'{i}: {line}' for i, line in enumerate(lines))

    chunks = _split_numbered_lines(numbered)
    all_segments = []
    line_offset = 0

    for i, (chunk_text, chunk_line_count) in enumerate(chunks):
        result = provider.complete(
            system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            user_prompt=f'请切割以下文章（行号已标注）：\n\n{chunk_text}',
            max_tokens=800,   # 只返回行号，800 token 绰绰有余
        )
        if not result.text:
            logger.warning('Segment chunk %d returned empty response, skipping', i)
            line_offset += chunk_line_count
            continue

        data = _parse_json(result.text)
        for seg in data.get('segments', []):
            start = seg.get('start', 0) + line_offset
            end = seg.get('end', start) + line_offset
            # 按行号切原文
            fragment_lines = lines[start:end + 1]
            text = '\n'.join(fragment_lines).strip()
            if text:
                all_segments.append(text)

        line_offset += chunk_line_count

    return all_segments

def _split_numbered_lines(numbered_text: str, max_chars: int = 3000) -> list[tuple[str, int]]:
    """
    按字符数切分带行号的文本。
    返回 (chunk_text, 该块包含的行数) 的列表。
    """
    all_lines = numbered_text.split('\n')
    chunks = []
    current_lines = []
    current_len = 0

    for line in all_lines:
        if current_len + len(line) > max_chars and current_lines:
            chunks.append(('\n'.join(current_lines), len(current_lines)))
            current_lines = [line]
            current_len = len(line)
        else:
            current_lines.append(line)
            current_len += len(line)

    if current_lines:
        chunks.append(('\n'.join(current_lines), len(current_lines)))

    return chunks or [(numbered_text, len(all_lines))]


# ── Tag Inference ────────────────────────────────────────────────────────────

def _build_taxonomy_options() -> str:
    lines = []
    for field, values in TAXONOMY.items():
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


def infer_tags(fragment_text: str, provider) -> dict:
    """使用 LLM 为片段推断 TAXONOMY 标签。"""
    system = TAG_INFERENCE_SYSTEM_PROMPT_TEMPLATE.format(
        taxonomy_options=_build_taxonomy_options()
    )
    result = provider.complete(
        system_prompt=system,
        user_prompt=f'请为以下片段打标签：\n\n{fragment_text}',
        max_tokens=1000,
    )
    raw = _parse_json(result.text)
    return _clean_tags(raw)


# ── Utilities ────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    """去除 markdown 代码块标记后解析 JSON，截断时尝试修复。"""
    if not text:
        return {}
    text = text.strip()
    
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 输出被截断时，尝试找到最后一个完整的字符串元素并补全
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