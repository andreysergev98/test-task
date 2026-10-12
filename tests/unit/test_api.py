from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from payments.api.app import create_app
from payments.db.models import OutboxEvent, Payment


async def test_create_payment_persists_payment_and_outbox(client, payment_data, sessions):
    response = await client.post(
        "/api/v1/payments",
        json=payment_data.model_dump(mode="json"),
        headers={"Idempotency-Key": "order-42"},
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"payment_id", "status", "created_at"}
    assert body["status"] == "pending"
    async with sessions() as session:
        payment = await session.get(Payment, UUID(body["payment_id"]))
        event = await session.scalar(select(OutboxEvent))
    assert payment.amount == payment_data.amount
    assert event.payload["payment_id"] == str(payment.id)
    assert event.payload["attempt"] == 1
    assert event.routing_key == "payments.new"
    assert event.published_at is None


async def test_get_payment_returns_details(client, payment):
    response = await client.get(f"/api/v1/payments/{payment.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == "123.45"
    assert body["currency"] == "RUB"
    assert body["metadata"] == payment.details
    assert body["idempotency_key"] == "order-42"
    assert body["processed_at"] is None
    assert "request_hash" not in body


async def test_duplicate_request_returns_original_payment(client, payment, payment_data, sessions):
    response = await client.post(
        "/api/v1/payments",
        json=payment_data.model_dump(mode="json"),
        headers={"Idempotency-Key": "order-42"},
    )

    assert response.status_code == 202
    assert response.json()["payment_id"] == str(payment.id)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", "999.00"),
        ("currency", "EUR"),
        ("description", "Different order"),
        ("metadata", {"order_id": 43}),
        ("webhook_url", "https://different.example/webhook"),
    ],
)
async def test_duplicate_key_with_different_request_conflicts(
    client, payment, payment_data, field, value
):
    payload = payment_data.model_dump(mode="json")
    payload[field] = value

    response = await client.post(
        "/api/v1/payments", json=payload, headers={"Idempotency-Key": "order-42"}
    )

    assert response.status_code == 409


@pytest.mark.parametrize("api_key", [None, "wrong"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/payments"),
        ("GET", "/api/v1/payments/00000000-0000-0000-0000-000000000000"),
        ("GET", "/health"),
        ("GET", "/openapi.json"),
    ],
)
async def test_all_endpoints_require_api_key(client, api_key, method, path):
    client.headers.pop("X-API-Key")
    headers = {} if api_key is None else {"X-API-Key": api_key}

    response = await client.request(method, path, headers=headers)

    assert response.status_code == 401


@pytest.mark.parametrize("key", [None, "", "with space", "a" * 256])
async def test_idempotency_key_validation(client, payment_data, key):
    headers = {} if key is None else {"Idempotency-Key": key}
    response = await client.post(
        "/api/v1/payments", json=payment_data.model_dump(mode="json"), headers=headers
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "amount", ["0", "-0.01", "1.001", "10000000000000000.00", "NaN", "Infinity"]
)
async def test_invalid_amount_rejected(client, payment_data, amount):
    payload = payment_data.model_dump(mode="json")
    payload["amount"] = amount
    response = await client.post(
        "/api/v1/payments", json=payload, headers={"Idempotency-Key": "order-42"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "GBP"),
        ("description", "a" * 1001),
        ("metadata", []),
        ("metadata", {"large": "a" * 16385}),
        ("webhook_url", "ftp://example.com/file"),
        ("webhook_url", "https://user:password@example.com/webhook"),
        ("webhook_url", "https://example.com/webhook#fragment"),
        ("unexpected", "value"),
    ],
)
async def test_invalid_payload_rejected(client, payment_data, field, value):
    payload = payment_data.model_dump(mode="json")
    payload[field] = value
    response = await client.post(
        "/api/v1/payments", json=payload, headers={"Idempotency-Key": "order-42"}
    )

    assert response.status_code == 422


async def test_webhook_host_outside_allowlist_is_rejected(client, payment_data):
    payload = payment_data.model_dump(mode="json")
    payload["webhook_url"] = "http://169.254.169.254/latest/meta-data"

    response = await client.post(
        "/api/v1/payments", json=payload, headers={"Idempotency-Key": "order-42"}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Webhook host is not allowed"


async def test_unknown_payment_returns_404(client):
    response = await client.get(f"/api/v1/payments/{uuid4()}")
    assert response.status_code == 404


async def test_invalid_payment_id_returns_422(client):
    response = await client.get("/api/v1/payments/not-a-uuid")
    assert response.status_code == 422


async def test_health_and_authenticated_schema(client):
    health = await client.get("/health")
    schema = await client.get("/openapi.json")

    assert health.status_code == 200
    assert schema.status_code == 200
    assert (
        schema.json()["components"]["securitySchemes"]["APIKeyHeader"]["name"]
        == "X-API-Key"
    )


async def test_docs_are_available_without_api_key_when_enabled(settings, sessions):
    settings.api_docs_enabled = True
    app = create_app(settings, sessions)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as docs_client,
    ):
        docs = await docs_client.get("/docs")
        redoc = await docs_client.get("/redoc")
        schema = await docs_client.get("/openapi.json")

    assert docs.status_code == 200
    assert redoc.status_code == 200
    assert schema.status_code == 200
    assert (
        schema.json()["components"]["securitySchemes"]["APIKeyHeader"]["name"]
        == "X-API-Key"
    )


async def test_replay_after_processing_returns_current_status(
    client, processor, payment, payment_data
):
    from payments.domain.events import PaymentEvent

    await processor.process(PaymentEvent(payment_id=payment.id))

    response = await client.post(
        "/api/v1/payments",
        json=payment_data.model_dump(mode="json"),
        headers={"Idempotency-Key": payment.idempotency_key},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"
