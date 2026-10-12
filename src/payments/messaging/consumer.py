import asyncio
import logging
from typing import Any

from faststream import AckPolicy
from faststream.exceptions import RejectMessage
from faststream.rabbit import RabbitBroker
from pydantic import ValidationError

from payments.domain.events import PaymentEvent
from payments.messaging.topology import PAYMENTS_EXCHANGE, PAYMENTS_QUEUE
from payments.services.processing import PaymentProcessor

logger = logging.getLogger(__name__)


def register_consumer(broker: RabbitBroker, processor: PaymentProcessor) -> None:
    @broker.subscriber(
        PAYMENTS_QUEUE,
        PAYMENTS_EXCHANGE,
        ack_policy=AckPolicy.NACK_ON_ERROR,
        no_reply=True,
    )
    async def handle_payment(body: Any) -> None:
        try:
            event = PaymentEvent.model_validate(body)
        except ValidationError as exc:
            logger.warning("Invalid payment event rejected")
            raise RejectMessage from exc
        try:
            await processor.process(event)
        except LookupError as exc:
            logger.warning("Unknown payment rejected payment_id=%s", event.payment_id)
            raise RejectMessage from exc
        except Exception as exc:
            logger.warning(
                "Payment infrastructure failure payment_id=%s error=%s",
                event.payment_id,
                type(exc).__name__,
            )
            await asyncio.sleep(1)
            raise
