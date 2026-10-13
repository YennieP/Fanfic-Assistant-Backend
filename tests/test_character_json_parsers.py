import pytest

from characters.suggest import _parse_json as parse_suggest_json
from characters.translate import _parse_json as parse_translate_json


PARSERS = [
    pytest.param(parse_suggest_json, id='suggest'),
    pytest.param(parse_translate_json, id='translate'),
]


@pytest.mark.parametrize('parser', PARSERS)
@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('{"result": {"items": ["a", "b"]}}', {'result': {'items': ['a', 'b']}}),
        ('```json\n{"result": "ok"}\n```', {'result': 'ok'}),
        ('Model output:\n{"result": "ok"}\nEnd.', {'result': 'ok'}),
        (
            'Schema example: {"example": true}\nFinal: {"suggestions": []}',
            {'suggestions': []},
        ),
        ('', {}),
        ('not-json', {}),
    ],
)
def test_character_json_parsers_cover_common_provider_shapes(parser, text, expected):
    assert parser(text) == expected


@pytest.mark.parametrize('parser', PARSERS)
def test_character_json_parsers_skip_non_json_braces_before_payload(parser):
    text = (
        'The requested shape resembles {field: value}, but the final payload is:\n'
        '{"suggestions": []}\n'
        'End.'
    )

    assert parser(text) == {'suggestions': []}
