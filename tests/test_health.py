from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError
from django.test import Client
from logs.middleware import EXCLUDE_PREFIXES


def test_liveness_does_not_touch_database():
    with patch('core.health.connection.cursor') as cursor:
        response = Client().get('/health/live/')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    cursor.assert_not_called()


def test_readiness_checks_database():
    with patch('core.health.connection.cursor') as cursor:
        response = Client().get('/health/')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'database': 'ok'}
    cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')


def test_readiness_returns_503_without_leaking_database_error():
    with patch('core.health.connection.cursor', side_effect=DatabaseError('secret connection detail')):
        response = Client().get('/health/')

    assert response.status_code == 503
    assert response.json() == {
        'status': 'unavailable',
        'database': 'unavailable',
    }
    assert b'secret connection detail' not in response.content


def test_health_requests_do_not_depend_on_database_logging():
    for path in ('/health/', '/health/live/'):
        assert any(path.startswith(prefix) for prefix in EXCLUDE_PREFIXES)


def test_railway_healthcheck_host_is_allowed():
    response = Client().get('/health/live/', HTTP_HOST='healthcheck.railway.app')

    assert 'healthcheck.railway.app' in settings.ALLOWED_HOSTS
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
