"""
TAXONOMY — 系统级全局标签表
所有预设标签选项的唯一来源。
新增预设标签只需修改此文件，无需数据库迁移。
角色卡只存自定义增量标签（speech_style_custom_tags 等字段）。
Phase 2 的 Example Library A 片段标注将直接使用此 schema。

双语支持：
- TAXONOMY     中文标签值（默认，存量数据均为中文）
- TAXONOMY_EN  英文标签值（与中文一一对应，顺序相同）
- get_taxonomy(lang)  按语言返回对应 dict
"""

TAXONOMY = {
    "scene_type": [
        "日常闲聊",
        "争执发生",
        "积极情感高潮发生（告白/和解）",
        "负面情感高潮发生（决裂/崩溃）",
        "道别/分离发生",
        "重逢发生",
        "相遇发生",
        "道歉发生",
        "质疑/批评发生",
        "请求/拜托发生",
        "安慰发生",
        "调和发生",
    ],
    "scene_privacy": [
        "只有两人",
        "小群体（3-5人）",
        "大群体（6人以上）",
        "公开场合/众目睽睽",
        "他独自一人",
    ],
    "initiative": [
        "他主动发起对话",
        "他被动回应",
        "他被迫主动说话",
    ],
    "emotion": {
        "shared": [
            "平静",
            "冷漠/疏离",
            "亲密/温柔",
            "自信",
            "专注/认真",
            "喜悦",
            "兴奋/亢奋",
            "害羞",
            "焦虑/不安",
            "愤怒",
            "羞耻/难堪",
            "受伤",
            "委屈",
            "难过",
            "嫉妒",
            "羡慕",
            "恐惧/害怕/胆怯",
            "期待",
        ],
        "intensity": ["低", "中", "高"],
    },
    "target_type": [
        "挚友",
        "队友",
        "恋人",
        "友情以上恋人未满",
        "普通朋友",
        "平辈",
        "竞争者",
        "陌生人",
        "对立者/敌意方",
        "长辈/上级",
        "晚辈/下级",
    ],
    "target_count": [
        "一对一",
        "多人场合",
    ],
    "relationship_state": [
        "关系稳定",
        "关系紧张中",
        "关系破裂期",
        "关系修复期",
    ],
    "speech_intent": [
        "表达情绪",
        "传递信息",
        "回避/转移话题",
        "施压/控制",
        "安慰对方",
        "请求/商量",
        "表明立场",
        "缓和气氛/出于体贴",
    ],
    "relationship_category": [
        "职务关系",
        "家庭关系",
        "情感关系",
        "社交关系",
        "对立关系",
    ],
    "relationship_direction": [
        "上级",
        "下级",
        "平级",
    ],
}

# 英文版，与 TAXONOMY 顺序完全对应
TAXONOMY_EN = {
    "scene_type": [
        "Casual chat",
        "Argument",
        "Positive emotional peak (confession/reconciliation)",
        "Negative emotional peak (fallout/breakdown)",
        "Farewell/separation",
        "Reunion",
        "First meeting",
        "Apology",
        "Questioning/criticism",
        "Request/favor",
        "Comfort/consolation",
        "Mediation",
    ],
    "scene_privacy": [
        "Just the two of them",
        "Small group (3-5 people)",
        "Large group (6+ people)",
        "Public setting",
        "Alone",
    ],
    "initiative": [
        "Character initiates",
        "Character responds passively",
        "Character forced to speak",
    ],
    "emotion": {
        "shared": [
            "Calm",
            "Aloof/distant",
            "Intimate/gentle",
            "Confident",
            "Focused/serious",
            "Joyful",
            "Excited/agitated",
            "Shy",
            "Anxious/uneasy",
            "Angry",
            "Ashamed/embarrassed",
            "Hurt",
            "Wronged/aggrieved",
            "Sad",
            "Jealous",
            "Envious",
            "Fearful/afraid",
            "Anticipation",
        ],
        "intensity": ["Low", "Medium", "High"],
    },
    "target_type": [
        "Close friend",
        "Teammate",
        "Romantic partner",
        "More than friends",
        "Casual friend",
        "Peer",
        "Rival",
        "Stranger",
        "Opponent/hostile party",
        "Senior/superior",
        "Junior/subordinate",
    ],
    "target_count": [
        "One-on-one",
        "Group setting",
    ],
    "relationship_state": [
        "Stable relationship",
        "Relationship under tension",
        "Relationship broken",
        "Relationship repairing",
    ],
    "speech_intent": [
        "Express emotion",
        "Convey information",
        "Avoid/deflect",
        "Pressure/control",
        "Comfort the other",
        "Request/negotiate",
        "Assert position",
        "Ease tension/show care",
    ],
    "relationship_category": [
        "Professional relationship",
        "Family relationship",
        "Romantic relationship",
        "Social relationship",
        "Adversarial relationship",
    ],
    "relationship_direction": [
        "Superior",
        "Subordinate",
        "Equal",
    ],
}


def get_taxonomy(lang: str = 'zh') -> dict:
    """按语言返回 TAXONOMY。lang='en' 返回英文版，其余返回中文版。"""
    return TAXONOMY_EN if lang == 'en' else TAXONOMY


# 中→英对照表（扁平化），供前端 extractTagChips 翻译已存储的中文标签
# 通过对比 TAXONOMY 和 TAXONOMY_EN 的对应位置自动生成
def build_zh_to_en_map() -> dict[str, str]:
    result: dict[str, str] = {}

    def _zip_field(zh_vals, en_vals):
        for zh, en in zip(zh_vals, en_vals):
            result[zh] = en

    for field in TAXONOMY:
        zh = TAXONOMY[field]
        en = TAXONOMY_EN[field]
        if field == 'emotion':
            _zip_field(zh['shared'], en['shared'])
            _zip_field(zh['intensity'], en['intensity'])
        elif isinstance(zh, list):
            _zip_field(zh, en)

    return result


ZH_TO_EN: dict[str, str] = build_zh_to_en_map()