from characters.suggest import build_suggest_prompt


def _behavioral_pattern():
    return {
        'trigger': {
            'immediate': '被当众质疑判断',
            'priorContext': '上周刚因类似决定受批评',
            'relationship': '质疑者是关系紧张的队友',
            'stakes': '威胁到对自我掌控的认知',
        },
        'response': {
            'immediate': '简短回应后拉开距离',
            'followUp': '独处后主动复盘',
            'internal': '表面平静，内心重新评估边界',
        },
    }


def test_build_suggest_prompt_preserves_weight_hierarchy_and_camel_case_targets():
    prompt = build_suggest_prompt({
        'name': '林岚',
        'fandom': '原创世界',
        'core_values': [{'id': 'v1', 'content': '守护弱者'}],
        'core_fears': ['失去控制'],
        'quick_labels': [{'content': '克制'}],
        'forbidden_behaviors': [{'content': '背弃承诺'}],
        'behavioral_patterns': [_behavioral_pattern()],
        'emotional_triggers': [
            {'trigger': '朋友受伤', 'reaction': '立即挡在对方身前'},
        ],
        'default_state': '平静而警觉',
        'mbti': 'INTJ',
        'mbti_notes': '只用于解释她为何先观察再行动',
    })

    assert '核心价值观（高权重）：守护弱者' in prompt
    assert '人设红线（高权重，硬约束）：背弃承诺' in prompt
    assert '【已观察到的行为模式（最高权重，所有推断必须与此一致，不得矛盾）】' in prompt
    assert '触发="被当众质疑判断"' in prompt
    assert '前因="上周刚因类似决定受批评"' in prompt
    assert '内心="表面平静，内心重新评估边界"' in prompt
    assert 'MBTI：INTJ（辅助参考层' in prompt
    assert prompt.index('【已观察到的行为模式') < prompt.index('MBTI：INTJ')

    # A single existing item is still sparse, and targets always use the API's
    # camelCase field names rather than the internal snake_case names.
    assert 'field: coreValues, type: list_append' in prompt
    assert 'field: behavioralPatterns，type: pattern' in prompt
    assert 'field: emotionalTriggers，type: trigger' in prompt
    assert 'field: core_values' not in prompt


def test_build_suggest_prompt_uses_english_structure_with_chinese_source_content():
    prompt = build_suggest_prompt(
        {
            'name': '林岚',
            'fandom': '原创世界',
            'core_values': ['守护弱者'],
            'behavioral_patterns': [_behavioral_pattern()],
            'mbti': 'INTJ',
        },
        output_language='en',
    )

    assert 'Character：林岚' in prompt
    assert 'Source：原创世界' in prompt
    assert 'Core Values (high weight)：守护弱者' in prompt
    assert '[Observed Behavioral Patterns (highest weight' in prompt
    assert 'Pattern1：Trigger="被当众质疑判断"' in prompt
    assert 'MBTI: INTJ (Interpretive layer only' in prompt
    assert 'Core Values (field: coreValues, type: list_append)' in prompt
    assert 'write label as the English display name' in prompt
