import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from payments.db.models import OutboxEvent, utcnow
from payments.domain.events import PaymentEvent
from payments.repositories.outbox import OutboxRepository


async def test_outbox_marks_event_only_after_publish(dispatcher, publisher, payment, sessions):
    async def check_unpublished(payload, routing_key, event_id):
        async with sessions() as session:
            event = await session.get(OutboxEvent, event_id)
        assert event.published_at is None

    publisher.publish.side_effect = check_unpublished

    assert await dispatcher.publish_batch() == 1
    assert await dispatcher.publish_batch() == 0
    publisher.publish.assert_awaited_once()
    async with sessions() as session:
        event = await session.scalar(select(OutboxEvent))
    assert event.published_at is not None


async def test_publish_failure_keeps_event_for_redelivery(dispatcher, publisher, payment, sessions):
    publisher.publish.side_effect = ConnectionError("broker offline")

    with pytest.raises(ConnectionError):
        await dispatcher.publish_batch()

    async with sessions() as session:
        event = await session.scalar(select(OutboxEvent))
    assert event.published_at is None
    publisher.publish.side_effect = None
    assert await dispatcher.publish_batch() == 1
    assert publisher.publish.call_args_list[0] == publisher.publish.call_args_list[1]


async def test_future_retries_are_not_published(dispatcher, payment, sessions, publisher):
    await dispatcher.publish_batch()
    publisher.reset_mock()
    async with sessions() as session, session.begin():
        OutboxRepository(session).add(
            PaymentEvent(payment_id=payment.id, attempt=2),
            "payments.new",
            available_at=utcnow() + timedelta(hours=1),
        )

    assert await dispatcher.publish_batch() == 0
    publisher.publish.assert_not_awaited()


async def test_batch_limit_is_respected(dispatcher, payment, sessions, settings):
    settings.outbox_batch_size = 1
    async with sessions() as session, session.begin():
        OutboxRepository(session).add(PaymentEvent(payment_id=payment.id), "payments.new")

    assert await dispatcher.publish_batch() == 1
    assert await dispatcher.publish_batch() == 1
    assert await dispatcher.publish_batch() == 0


async def test_dispatcher_recovers_after_transient_failure(dispatcher, monkeypatch):
    stop = asyncio.Event()
    calls = 0

    async def publish_batch():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError
        stop.set()
        return 0

    monkeypatch.setattr(dispatcher, "publish_batch", publish_batch)
    await asyncio.wait_for(dispatcher.run(stop), timeout=1)
    assert calls == 2


async def test_stopped_dispatcher_does_not_publish(dispatcher, monkeypatch):
    stop = asyncio.Event()
    stop.set()
    publish = AsyncMock()
    monkeypatch.setattr(dispatcher, "publish_batch", publish)

    await dispatcher.run(stop)

    publish.assert_not_awaited()
