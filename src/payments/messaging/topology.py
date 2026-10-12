from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue
from faststream.rabbit.schemas import Channel
from faststream.rabbit.schemas.queue import QueueType

from payments.config.settings import Settings

PAYMENTS_EXCHANGE = RabbitExchange("payments", durable=True)
DEAD_LETTER_EXCHANGE = RabbitExchange("payments.dead", durable=True)
PAYMENTS_QUEUE = RabbitQueue(
    "payments.new",
    queue_type=QueueType.QUORUM,
    durable=True,
    arguments={
        "x-dead-letter-exchange": "payments.dead",
        "x-dead-letter-routing-key": "payments.dlq",
        "x-dead-letter-strategy": "at-least-once",
        "x-overflow": "reject-publish",
    },
)
DEAD_LETTER_QUEUE = RabbitQueue("payments.dlq", queue_type=QueueType.QUORUM, durable=True)


def create_broker(settings: Settings) -> RabbitBroker:
    return RabbitBroker(
        settings.rabbitmq_url.get_secret_value(),
        default_channel=Channel(prefetch_count=1, publisher_confirms=True, on_return_raises=True),
        graceful_timeout=settings.webhook_timeout + settings.gateway_max_delay + 10,
        logger=None,
    )


async def declare_topology(broker: RabbitBroker) -> None:
    exchange = await broker.declare_exchange(PAYMENTS_EXCHANGE)
    dead_exchange = await broker.declare_exchange(DEAD_LETTER_EXCHANGE)
    dead_queue = await broker.declare_queue(DEAD_LETTER_QUEUE)
    await dead_queue.bind(dead_exchange, routing_key=DEAD_LETTER_QUEUE.routing())
    await dead_queue.bind(exchange, routing_key=DEAD_LETTER_QUEUE.routing())
    queue = await broker.declare_queue(PAYMENTS_QUEUE)
    await queue.bind(exchange, routing_key=PAYMENTS_QUEUE.routing())
