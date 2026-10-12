from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from payments.domain.enums import PaymentStatus
from payments.domain.errors import WebhookError
from payments.integrations.gateway import SimulatedGateway
from payments.integrations.webhooks import HttpWebhookSender


@pytest.mark.parametrize("status_code", [200, 201, 202, 204])
async def test_webhook_accepts_any_success_response(respx_mock, webhook_payload, status_code):
    route = respx_mock.post("https://merchant.example/webhook").respond(status_code)
    async with httpx.AsyncClient() as client:
        await HttpWebhookSender(client, {"merchant.example"}).send(
            "https://merchant.example/webhook", webhook_payload
        )

    request = route.calls[0].request
    assert request.headers["Idempotency-Key"] == str(webhook_payload.event_id)
    assert b'"amount":"123.45"' in request.content
    assert "X-API-Key" not in request.headers


@pytest.mark.parametrize("status_code", [301, 302, 400, 401, 404, 429, 500, 503])
async def test_webhook_errors_are_reported_without_following_redirects(
    respx_mock, webhook_payload, status_code
):
    route = respx_mock.post("https://merchant.example/webhook").respond(
        status_code, headers={"Location": "http://internal.example/secret"}
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        with pytest.raises(WebhookError, match=f"HTTP {status_code}"):
            await HttpWebhookSender(client, {"merchant.example"}).send(
                "https://merchant.example/webhook", webhook_payload
            )
    assert route.call_count == 1
    assert len(respx_mock.calls) == 1


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_webhook_network_failure_is_wrapped(respx_mock, webhook_payload, error):
    respx_mock.post("https://merchant.example/webhook").mock(side_effect=error("network error"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebhookError, match=error.__name__):
            await HttpWebhookSender(client, {"merchant.example"}).send(
                "https://merchant.example/webhook", webhook_payload
            )


async def test_webhook_sender_rejects_hosts_outside_allowlist(webhook_payload):
    async with httpx.AsyncClient() as client:
        with pytest.raises(WebhookError, match="host is not allowed"):
            await HttpWebhookSender(client, {"merchant.example"}).send(
                "http://169.254.169.254/latest/meta-data", webhook_payload
            )


@pytest.mark.parametrize(
    ("random_value", "expected"),
    [(0.0, PaymentStatus.SUCCEEDED), (0.899, PaymentStatus.SUCCEEDED), (0.9, PaymentStatus.FAILED)],
)
async def test_gateway_probability_and_delay(settings, random_value, expected):
    settings.gateway_min_delay = 2
    settings.gateway_max_delay = 5
    with (
        patch("payments.integrations.gateway.random.uniform", return_value=3.5) as delay,
        patch("payments.integrations.gateway.random.random", return_value=random_value),
        patch("payments.integrations.gateway.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await SimulatedGateway(settings).charge(uuid4(), Decimal("10.00"), "RUB")

    assert result == expected
    delay.assert_called_once_with(2, 5)
    sleep.assert_awaited_once_with(3.5)
