import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from payments.db.models import OutboxEvent, Payment
from payments.domain.errors import IdempotencyConflict
from payments.domain.events import PaymentEvent
from payments.services.outbox import OutboxDispatcher
from payments.services.payments import PaymentService
from payments.services.processing import PaymentProcessor

pytestmark = pytest.mark.integration


async def test_concurrent_requests_create_one_payment_and_event(postgres_sessions, payment_data):
    async def create():
        async with postgres_sessions() as session:
            return await PaymentService(session).create(payment_data, "same-key")

    payments = await asyncio.gather(*(create() for _ in range(12)))

    assert len({payment.id for payment in payments}) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_concurrent_different_requests_conflict(postgres_sessions, payment_data):
    async def create(description):
        data = payment_data.model_copy(update={"description": description})
        async with postgres_sessions() as session:
            return await PaymentService(session).create(data, "same-key")

    results = await asyncio.gather(create("First"), create("Second"), return_exceptions=True)

    assert sum(isinstance(result, Payment) for result in results) == 1
    assert sum(isinstance(result, IdempotencyConflict) for result in results) == 1


async def test_concurrent_consumers_do_not_repeat_payment_or_webhook(
    postgres_sessions, payment_data, gateway, webhooks, settings
):
    async with postgres_sessions() as session:
        payment = await PaymentService(session).create(payment_data, "order")
    processor = PaymentProcessor(postgres_sessions, gateway, webhooks, settings)
    event = PaymentEvent(payment_id=payment.id)

    await asyncio.gather(*(processor.process(event) for _ in range(8)))

    gateway.charge.assert_awaited_once()
    webhooks.send.assert_awaited_once()


async def test_skip_locked_allows_parallel_outbox_dispatch(
    postgres_sessions, payment_data, settings
):
    async with postgres_sessions() as session:
        await PaymentService(session).create(payment_data, "first")
    async with postgres_sessions() as session:
        await PaymentService(session).create(payment_data, "second")
    settings.outbox_batch_size = 1
    first_locked = asyncio.Event()
    release_first = asyncio.Event()

    async def blocked_publish(*args):
        first_locked.set()
        await release_first.wait()

    slow_publisher = AsyncMock()
    slow_publisher.publish.side_effect = blocked_publish
    fast_publisher = AsyncMock()
    first = OutboxDispatcher(postgres_sessions, slow_publisher, settings)
    second = OutboxDispatcher(postgres_sessions, fast_publisher, settings)
    task = asyncio.create_task(first.publish_batch())
    try:
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        assert await asyncio.wait_for(second.publish_batch(), timeout=5) == 1
    finally:
        release_first.set()
        await task
    assert slow_publisher.publish.call_args.args[2] != fast_publisher.publish.call_args.args[2]
