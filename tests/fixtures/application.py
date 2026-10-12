from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.api.app import create_app
from payments.config.settings import Settings
from payments.schemas.payments import PaymentCreate


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        api_key="test-api-key-at-least-sixteen",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost/",
        gateway_min_delay=0,
        gateway_max_delay=0,
        outbox_poll_interval=0.01,
        retry_base_delay=2,
        max_attempts=3,
        webhook_allowed_hosts="merchant.example",
    )


@pytest.fixture
def payment_data() -> PaymentCreate:
    return PaymentCreate(
        amount="123.45",
        currency="RUB",
        description="Order 42",
        metadata={"order_id": 42, "nested": {"items": ["book"]}},
        webhook_url="https://merchant.example/webhook",
    )


@pytest.fixture
def app(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> FastAPI:
    return create_app(settings, sessions)


@pytest.fixture
async def client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": settings.api_key.get_secret_value()},
        ) as client,
    ):
        yield client
