import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from payments.db.models import OutboxEvent, Payment
from payments.domain.events import PaymentEvent
from payments.integrations.webhooks import HttpWebhookSender
from payments.messaging import topology
from payments.messaging.consumer import register_consumer
from payments.messaging.publisher import RabbitEventPublisher
from payments.services.outbox import OutboxDispatcher
from payments.services.payments import PaymentService
from payments.services.processing import PaymentProcessor

pytestmark = pytest.mark.integration


async def test_published_message_is_persistent_and_survives_redelivery(rabbit_broker, settings):
    event = PaymentEvent(payment_id=uuid4())
    publisher = RabbitEventPublisher(rabbit_broker, settings)
    await publisher.publish(event.model_dump(mode="json"), "payments.new", event.event_id)
    queue = await rabbit_broker.declare_queue(topology.PAYMENTS_QUEUE)

    message = await queue.get(timeout=5)
    assert message is not None
    assert message.message_id == str(event.event_id)
    assert message.delivery_mode == 2
    await message.nack(requeue=True)
    redelivered = await queue.get(timeout=5)
    assert redelivered is not None
    assert redelivered.redelivered
    assert redelivered.body == message.body
    await redelivered.ack()


async def test_unroutable_publish_fails(rabbit_broker, settings):
    from aio_pika.exceptions import DeliveryError

    event = PaymentEvent(payment_id=uuid4())
    with pytest.raises(DeliveryError):
        await RabbitEventPublisher(rabbit_broker, settings).publish(
            event.model_dump(mode="json"), "missing-route", event.event_id
        )


async def test_invalid_message_is_dead_lettered(rabbit_broker, settings):
    from unittest.mock import AsyncMock

    register_consumer(rabbit_broker, AsyncMock())
    await rabbit_broker.start()
    await rabbit_broker.publish(
        {}, topology.PAYMENTS_QUEUE, topology.PAYMENTS_EXCHANGE, persist=True
    )
    queue = await rabbit_broker.declare_queue(topology.DEAD_LETTER_QUEUE)

    async with queue.iterator() as messages, asyncio.timeout(10):
        message = await anext(messages)
        assert json.loads(message.body) == {}
        await message.ack()


@pytest.mark.parametrize("webhook_status", [204, 503])
async def test_full_processing_pipeline(
    postgres_sessions, rabbit_broker, settings, payment_data, gateway, webhook_status
):
    settings.retry_base_delay = 0.01
    settings.outbox_poll_interval = 0.01
    calls = []

    def receive(request):
        calls.append(request)
        return httpx.Response(webhook_status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        processor = PaymentProcessor(
            postgres_sessions,
            gateway,
            HttpWebhookSender(client, settings.allowed_webhook_hosts),
            settings,
        )
        register_consumer(rabbit_broker, processor)
        await rabbit_broker.start()
        dispatcher = OutboxDispatcher(
            postgres_sessions, RabbitEventPublisher(rabbit_broker, settings), settings
        )
        async with postgres_sessions() as session:
            payment = await PaymentService(session).create(payment_data, "pipeline-test")
        stop = asyncio.Event()
        task = asyncio.create_task(dispatcher.run(stop))
        try:
            async with asyncio.timeout(15):
                while True:
                    async with postgres_sessions() as session:
                        saved = await session.get(Payment, payment.id)
                        if webhook_status == 204 and saved.webhook_delivered_at is not None:
                            break
                        dead = await session.scalar(
                            select(OutboxEvent).where(
                                OutboxEvent.routing_key == "payments.dlq",
                                OutboxEvent.published_at.is_not(None),
                            )
                        )
                        if webhook_status == 503 and dead is not None:
                            break
                    await asyncio.sleep(0.02)
        finally:
            stop.set()
            await task

    assert saved.status == "succeeded"
    gateway.charge.assert_awaited_once()
    assert len(calls) == (1 if webhook_status == 204 else 3)
    assert len({request.headers["Idempotency-Key"] for request in calls}) == 1
    if webhook_status == 503:
        queue = await rabbit_broker.declare_queue(topology.DEAD_LETTER_QUEUE)
        message = await queue.get(timeout=5)
        assert message is not None
        assert json.loads(message.body)["attempt"] == 3
        await message.ack()
