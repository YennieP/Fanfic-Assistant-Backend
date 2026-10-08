"""Deterministic, synthetic inputs for Gemini segmentation capacity checks.

The text is intentionally fictional and contains no production or user data.
Both samples stay within one numbered 3000-character chunk so a real-provider
smoke test measures output capacity rather than multi-request orchestration.
"""


def near_limit_narrative_sample() -> str:
    lines: list[str] = []
    for scene in range(1, 13):
        lines.append(f'【场景{scene}】')
        for beat in range(1, 6):
            lines.append(
                f'角色甲在第{scene}个场景的第{beat}段走过长廊，听见窗外的雨声，'
                '又停下来回想刚才没有说完的话。'
            )
        lines.append('')
    return '\n'.join(lines)


def high_line_density_sample() -> str:
    lines: list[str] = []
    for scene in range(1, 25):
        lines.append(f'【场景{scene}】')
        for beat in range(1, 8):
            lines.append(f'甲：第{scene}-{beat}句。')
        lines.append('')
    return '\n'.join(lines)
