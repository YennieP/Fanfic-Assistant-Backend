"""
LLM pipeline：文章情节切割 + TAXONOMY 标签推断。
"""
import json
import re

from core.taxonomy import TAXONOMY

# 每块最大字符数，按段落边界切分
# 2000字/块 × 4000 output tokens 足够容纳切割结果
MAX_CHARS_PER_CHUNK = 2000


# ── Segmentation ────────────────────────────────────────────────────────────

SEGMENTATION_SYSTEM_PROMPT = """你是一个专业的同人文分析助手。
你的任务是将用户提供的文章切割成情节片段。

【切割规则】
- 每个片段是一个"情节场景单元"，不是自然段
- 按情节的完整性和情感弧度切割，不按空行机械分段
- 一个情节片段可包含多个自然段（叙述+对话+心理描写可以在同一片段内）
- 每个片段应包含完整的一个情节动作或情感转变
- 片段长度通常在 50-400 字之间
- 不要修改原文任何字符，原文切割后拼接应等于原文

【返回格式】
严格返回 JSON，不要 markdown 代码块，不要任何解释：
{"segments": ["片段1完整原文", "片段2完整原文", ...]}"""


def _split_content(content: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """
    按段落边界切分长文章，每块不超过 max_chars 字符。
    段落分隔符为一个或多个空行（\n\n+）。
    保证段落完整性，不在段落中间截断。
    """
    # 按一个或多个空行分割段落
    paragraphs = re.split(r'\n{2,}', content)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 如果文章没有空行，退化为按字符数硬切（每块不超过 max_chars）
    if len(paragraphs) <= 1:
        chunks = []
        for i in range(0, len(content), max_chars):
            chunks.append(content[i:i + max_chars])
        return chunks

    chunks = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > max_chars and current:
            chunks.append('\n\n'.join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        chunks.append('\n\n'.join(current))

    return chunks or [content]


def segment_article(article_content: str, provider) -> list[str]:
    """
    使用 LLM 将文章切割成情节片段。
    长文章自动分块处理，每块独立切割后合并结果。
    每块使用 max_tokens=4000 避免截断。
    """
    chunks = _split_content(article_content)
    all_segments = []

    for chunk in chunks:
        result = provider.complete(
            system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            user_prompt=f'请将以下文章切割成情节片段：\n\n{chunk}',
            max_tokens=4000,
        )
        data = _parse_json(result.text)
        segments = data.get('segments', [])
        all_segments.extend([s.strip() for s in segments if s.strip()])

    return all_segments


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