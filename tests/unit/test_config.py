import pytest
from pydantic import ValidationError

from payments.config.settings import Settings


def test_settings_hide_secrets(settings):
    representation = repr(settings)
    assert settings.api_key.get_secret_value() not in representation
    assert settings.database_url.get_secret_value() not in representation
    assert settings.rabbitmq_url.get_secret_value() not in representation


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_attempts", 0),
        ("retry_base_delay", 0),
        ("webhook_timeout", 0),
        ("gateway_success_rate", 1.1),
        ("gateway_min_delay", -1),
        ("api_key", "short"),
    ],
)
def test_invalid_settings_rejected(settings, field, value):
    data = settings.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_gateway_delay_range_is_validated(settings):
    data = settings.model_dump()
    data.update(gateway_min_delay=5, gateway_max_delay=2)
    with pytest.raises(ValidationError, match="GATEWAY_MAX_DELAY"):
        Settings(_env_file=None, **data)


def test_webhook_allowed_hosts_are_normalized(settings):
    settings.webhook_allowed_hosts = "merchant.example, API.MERCHANT.EXAMPLE."

    assert settings.allowed_webhook_hosts == frozenset(
        {"merchant.example", "api.merchant.example"}
    )


@pytest.mark.parametrize(
    "hosts", ["", "*.example.com", "https://merchant.example", "merchant example"]
)
def test_invalid_webhook_allowlist_is_rejected(settings, hosts):
    data = settings.model_dump()
    data["webhook_allowed_hosts"] = hosts

    with pytest.raises(ValidationError, match="WEBHOOK_ALLOWED_HOSTS"):
        Settings(_env_file=None, **data)
