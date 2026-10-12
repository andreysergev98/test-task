import asyncio
from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from payments.db.models import OutboxEvent, Payment, utcnow
from payments.domain.enums import PaymentStatus
from payments.domain.errors import GatewayError, WebhookError
from payments.domain.events import PaymentEvent


@pytest.mark.parametrize("status", [PaymentStatus.SUCCEEDED, PaymentStatus.FAILED])
async def test_payment_result_is_saved_and_webhook_sent(
    processor, payment, gateway, webhooks, sessions, status
):
    gateway.charge.return_value = status

    await processor.process(PaymentEvent(payment_id=payment.id))

    gateway.charge.assert_awaited_once_with(payment.id, payment.amount, payment.currency)
    webhooks.send.assert_awaited_once()
    payload = webhooks.send.call_args.args[1]
    assert payload.status == status
    assert payload.metadata == payment.details
    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
    assert saved.status == status
    assert saved.processed_at is not None
    assert saved.webhook_delivered_at is not None
    assert saved.processing_attempts == 1


async def test_duplicate_event_does_not_repeat_side_effects(processor, payment, gateway, webhooks):
    event = PaymentEvent(payment_id=payment.id)

    await processor.process(event)
    await processor.process(event)
    await processor.process(PaymentEvent(payment_id=payment.id))

    gateway.charge.assert_awaited_once()
    webhooks.send.assert_awaited_once()


async def test_webhook_retry_preserves_payment_result_and_notification_id(
    processor, payment, gateway, webhooks, sessions
):
    webhooks.send.side_effect = [WebhookError("HTTP 503"), None]

    await processor.process(PaymentEvent(payment_id=payment.id))
    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
        processed_at = saved.processed_at
        assert saved.status == "succeeded"
        assert saved.webhook_delivered_at is None
    await processor.process(PaymentEvent(payment_id=payment.id, attempt=2))

    gateway.charge.assert_awaited_once()
    assert webhooks.send.await_count == 2
    assert webhooks.send.call_args_list[0].args[1] == webhooks.send.call_args_list[1].args[1]
    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
    assert saved.processed_at == processed_at
    assert saved.webhook_delivered_at is not None
    assert saved.last_error is None


async def test_three_failures_schedule_exponential_retry_then_dlq(
    processor, payment, gateway, webhooks, sessions
):
    webhooks.send.side_effect = WebhookError("HTTP 503")

    for attempt, expected_delay in [(1, 2), (2, 4)]:
        before = utcnow()
        await processor.process(PaymentEvent(payment_id=payment.id, attempt=attempt))
        after = utcnow()
        async with sessions() as session:
            events = list((await session.scalars(select(OutboxEvent))).all())
        retry = next(event for event in events if event.payload["attempt"] == attempt + 1)
        scheduled = retry.available_at.replace(tzinfo=UTC)
        assert before + timedelta(seconds=expected_delay) <= scheduled
        assert scheduled <= after + timedelta(seconds=expected_delay)
        assert retry.routing_key == "payments.new"

    await processor.process(PaymentEvent(payment_id=payment.id, attempt=3))
    await processor.process(PaymentEvent(payment_id=payment.id, attempt=3))

    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
        dead_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.routing_key == "payments.dlq")
                )
            ).all()
        )
    assert len(dead_events) == 1
    assert dead_events[0].payload["attempt"] == 3
    assert dead_events[0].payload["error"] == "WebhookError"
    assert saved.processing_attempts == 3
    assert saved.status == "succeeded"
    assert saved.webhook_delivered_at is None
    assert webhooks.send.await_count == 3
    gateway.charge.assert_awaited_once()


async def test_stale_failed_event_does_not_create_extra_retries(
    processor, payment, webhooks, sessions
):
    webhooks.send.side_effect = WebhookError()
    event = PaymentEvent(payment_id=payment.id)

    await processor.process(event)
    await processor.process(event)

    async with sessions() as session:
        events = list((await session.scalars(select(OutboxEvent))).all())
    assert len(events) == 2
    webhooks.send.assert_awaited_once()


async def test_gateway_error_is_retried(processor, payment, gateway, webhooks, sessions):
    gateway.charge.side_effect = [GatewayError(), PaymentStatus.SUCCEEDED]

    await processor.process(PaymentEvent(payment_id=payment.id))
    webhooks.send.assert_not_awaited()
    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
        assert saved.status == "pending"
        assert saved.processed_at is None
    await processor.process(PaymentEvent(payment_id=payment.id, attempt=2))

    assert gateway.charge.await_count == 2
    webhooks.send.assert_awaited_once()


async def test_nonterminal_gateway_result_is_retried(processor, payment, gateway, sessions):
    gateway.charge.return_value = PaymentStatus.PENDING
    await processor.process(PaymentEvent(payment_id=payment.id))

    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
    assert saved.status == "pending"
    assert saved.last_error == "ValueError"
    assert saved.processing_attempts == 1


async def test_missing_payment_is_reported(processor):
    with pytest.raises(LookupError):
        await processor.process(PaymentEvent(payment_id=uuid4()))


async def test_cancellation_during_gateway_leaves_payment_retryable(
    processor, payment, gateway, sessions
):
    gateway.charge.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await processor.process(PaymentEvent(payment_id=payment.id))

    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
    assert saved.status == "pending"
    assert saved.processing_attempts == 0


async def test_cancellation_during_webhook_keeps_committed_payment_result(
    processor, payment, gateway, webhooks, sessions
):
    webhooks.send.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await processor.process(PaymentEvent(payment_id=payment.id))

    async with sessions() as session:
        saved = await session.get(Payment, payment.id)
        assert saved.status == "succeeded"
        assert saved.processing_attempts == 0
    webhooks.send.side_effect = None
    await processor.process(PaymentEvent(payment_id=payment.id))
    gateway.charge.assert_awaited_once()
