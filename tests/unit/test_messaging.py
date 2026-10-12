from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.message import RabbitMessage

from payments.domain.events import PaymentEvent
from payments.messaging.consumer import register_consumer
from payments.messaging.publisher import RabbitEventPublisher
from payments.messaging.topology import PAYMENTS_EXCHANGE, PAYMENTS_QUEUE, declare_topology


async def test_consumer_validates_and_processes_event():
    broker = RabbitBroker()
    processor = AsyncMock()
    register_consumer(broker, processor)
    event = PaymentEvent(payment_id=uuid4())

    async with TestRabbitBroker(broker):
        await broker.publish(event.model_dump(mode="json"), PAYMENTS_QUEUE, PAYMENTS_EXCHANGE)

    processor.process.assert_awaited_once_with(event)


@pytest.mark.parametrize("payload", [{}, {"payment_id": "invalid"}, [1, 2], "invalid"])
async def test_consumer_rejects_invalid_messages(payload):
    broker = RabbitBroker()
    processor = AsyncMock()
    register_consumer(broker, processor)

    async with TestRabbitBroker(broker):
        with patch.object(RabbitMessage, "reject", new_callable=AsyncMock) as reject:
            await broker.publish(payload, PAYMENTS_QUEUE, PAYMENTS_EXCHANGE)
            reject.assert_awaited_once()

    processor.process.assert_not_awaited()


async def test_consumer_rejects_unknown_payment():
    broker = RabbitBroker()
    processor = AsyncMock()
    processor.process.side_effect = LookupError()
    register_consumer(broker, processor)

    async with TestRabbitBroker(broker):
        with patch.object(RabbitMessage, "reject", new_callable=AsyncMock) as reject:
            await broker.publish(
                PaymentEvent(payment_id=uuid4()).model_dump(mode="json"),
                PAYMENTS_QUEUE,
                PAYMENTS_EXCHANGE,
            )
            reject.assert_awaited_once()


async def test_database_failure_is_not_acknowledged():
    broker = RabbitBroker()
    processor = AsyncMock()
    processor.process.side_effect = ConnectionError()
    register_consumer(broker, processor)

    async with TestRabbitBroker(broker):
        with (
            patch("payments.messaging.consumer.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError),
        ):
            await broker.publish(
                PaymentEvent(payment_id=uuid4()).model_dump(mode="json"),
                PAYMENTS_QUEUE,
                PAYMENTS_EXCHANGE,
            )


async def test_publisher_uses_persistent_mandatory_messages_and_stable_id(settings):
    broker = AsyncMock()
    publisher = RabbitEventPublisher(broker, settings)
    event = PaymentEvent(payment_id=uuid4())

    await publisher.publish(event.model_dump(mode="json"), "payments.new", event.event_id)

    kwargs = broker.publish.call_args.kwargs
    assert kwargs["persist"] is True
    assert kwargs["mandatory"] is True
    assert kwargs["message_id"] == str(event.event_id)
    assert kwargs["routing_key"] == "payments.new"
    assert kwargs["timeout"] == settings.broker_publish_timeout


async def test_topology_declares_both_routes_to_dlq():
    broker = AsyncMock()
    await declare_topology(broker)

    assert "x-delivery-limit" not in PAYMENTS_QUEUE.arguments
    assert broker.declare_exchange.await_count == 2
    assert broker.declare_queue.await_count == 2
    assert broker.declare_queue.return_value.bind.await_count == 3
