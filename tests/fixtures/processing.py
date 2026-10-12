from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.db.models import Payment
from payments.domain.enums import PaymentStatus
from payments.integrations.gateway import PaymentGateway
from payments.integrations.webhooks import WebhookSender
from payments.schemas.payments import PaymentCreate
from payments.services.outbox import EventPublisher, OutboxDispatcher
from payments.services.payments import PaymentService
from payments.services.processing import PaymentProcessor


@pytest.fixture
def gateway() -> AsyncMock:
    gateway = AsyncMock(spec=PaymentGateway)
    gateway.charge.return_value = PaymentStatus.SUCCEEDED
    return gateway


@pytest.fixture
def webhooks() -> AsyncMock:
    return AsyncMock(spec=WebhookSender)


@pytest.fixture
def publisher() -> AsyncMock:
    return AsyncMock(spec=EventPublisher)


@pytest.fixture
def processor(sessions, gateway, webhooks, settings) -> PaymentProcessor:
    return PaymentProcessor(sessions, gateway, webhooks, settings)


@pytest.fixture
def dispatcher(sessions, publisher, settings) -> OutboxDispatcher:
    return OutboxDispatcher(sessions, publisher, settings)


@pytest.fixture
async def payment(
    sessions: async_sessionmaker[AsyncSession], payment_data: PaymentCreate
) -> Payment:
    async with sessions() as session:
        return await PaymentService(session).create(payment_data, "order-42")


@pytest.fixture
def webhook_payload(payment: Payment):
    from payments.db.models import utcnow

    payment.status = PaymentStatus.SUCCEEDED
    payment.processed_at = utcnow()
    return PaymentProcessor._webhook_payload(payment)
