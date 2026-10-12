import pytest

from users.models import UserProviderKey


@pytest.mark.django_db
def test_provider_key_verification_state_is_nullable_for_existing_keys(
    make_user_with_key,
):
    user = make_user_with_key()

    key = UserProviderKey.objects.get(user=user, provider='gemini')

    assert key.verification_status is None
    assert key.verification_checked_at is None
    assert key.verification_model is None
    assert key.verification_resolved_model is None


def test_provider_key_verification_fields_allow_null_and_blank():
    for field_name in (
        'verification_status',
        'verification_checked_at',
        'verification_model',
        'verification_resolved_model',
    ):
        field = UserProviderKey._meta.get_field(field_name)
        assert field.null is True

    assert UserProviderKey._meta.get_field('verification_status').blank is True
    assert UserProviderKey._meta.get_field('verification_model').blank is True
    assert (
        UserProviderKey._meta.get_field('verification_resolved_model').blank
        is True
    )
