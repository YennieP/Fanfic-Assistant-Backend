import pytest
from django.db import connection

from logs.models import LlmCallLog, VectorSearchLog


@pytest.mark.django_db
@pytest.mark.parametrize('model', [LlmCallLog, VectorSearchLog])
def test_generation_id_has_exactly_one_index(model):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor,
            model._meta.db_table,
        )

    generation_indexes = [
        name
        for name, details in constraints.items()
        if details['index'] and details['columns'] == ['generation_id']
    ]

    assert len(generation_indexes) == 1
