from uuid import UUID

from faststream.rabbit import RabbitBroker
from pydantic import JsonValue

from payments.config.settings import Settings
from payments.messaging.topology import PAYMENTS_EXCHANGE


class RabbitEventPublisher:
    def __init__(self, broker: RabbitBroker, settings: Settings) -> None:
        self.broker = broker
        self.settings = settings

    async def publish(
        self, payload: dict[str, JsonValue], routing_key: str, event_id: UUID
    ) -> None:
        await self.broker.publish(
            payload,
            exchange=PAYMENTS_EXCHANGE,
            routing_key=routing_key,
            message_id=str(event_id),
            correlation_id=str(payload.get("payment_id", event_id)),
            persist=True,
            mandatory=True,
            timeout=self.settings.broker_publish_timeout,
        )
