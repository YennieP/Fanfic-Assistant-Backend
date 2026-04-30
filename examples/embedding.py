"""
向量化工具模块。
MVP A1 方案：将 TAXONOMY 标签组合拼成描述文字后向量化。
Embedding 模型：Gemini text-embedding-004（768维）。

Phase 3 升级接口：
- 当前 tags_to_text() 负责标签向量（A1）
- 升级时新增 content_to_text() 负责内容向量（A2）
- 检索层加权合并两者，此文件无需其他改动
"""
from google import genai as google_genai

EMBEDDING_MODEL = 'models/text-embedding-004'
EMBEDDING_DIMENSIONS = 768


def get_embedding(text: str, api_key: str) -> list[float]:
    """使用 Gemini text-embedding-004 计算文本向量。"""
    client = google_genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def tags_to_text(tags: dict) -> str:
    """
    将 TAXONOMY 标签选择转成描述文字，用于向量化存储。
    对应 A1 方案。
    """
    parts = []
    if tags.get('scene_type'):
        parts.append(tags['scene_type'])
    if tags.get('initiative'):
        parts.append(tags['initiative'])
    if tags.get('emotion'):
        emotion = tags['emotion']
        if emotion.get('shared'):
            parts.append(emotion['shared'])
        if emotion.get('intensity'):
            parts.append(emotion['intensity'])
    if tags.get('target_type'):
        parts.append(tags['target_type'])
    if tags.get('speech_intent'):
        parts.append(tags['speech_intent'])
    if tags.get('relationship_state'):
        parts.append(tags['relationship_state'])
    return '，'.join(filter(None, parts))


def scene_to_text(scene_input: dict) -> str:
    """
    将场景输入转成查询文字，用于向量化检索。
    和 tags_to_text 保持结构对齐，最大化 cosine similarity 匹配效果。
    """
    parts = []
    if scene_input.get('tone'):
        parts.append(scene_input['tone'])
    if scene_input.get('intent'):
        parts.append(scene_input['intent'])
    if scene_input.get('location'):
        parts.append(scene_input['location'])
    return ' '.join(filter(None, parts))