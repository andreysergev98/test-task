import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from faststream import FastStream

from payments.config.logging import configure_logging
from payments.config.settings import get_settings
from payments.db.session import create_engine, create_session_factory
from payments.integrations.gateway import SimulatedGateway
from payments.integrations.webhooks import HttpWebhookSender
from payments.messaging.consumer import register_consumer
from payments.messaging.publisher import RabbitEventPublisher
from payments.messaging.topology import create_broker, declare_topology
from payments.services.outbox import OutboxDispatcher
from payments.services.processing import PaymentProcessor


def create_worker() -> FastStream:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    broker = create_broker(settings)
    client = httpx.AsyncClient(timeout=settings.webhook_timeout, trust_env=False)
    processor = PaymentProcessor(
        sessions,
        SimulatedGateway(settings),
        HttpWebhookSender(client, settings.allowed_webhook_hosts),
        settings,
    )
    register_consumer(broker, processor)
    dispatcher = OutboxDispatcher(sessions, RabbitEventPublisher(broker, settings), settings)
    stop = asyncio.Event()
    publisher_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan() -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()
            await engine.dispose()

    app = FastStream(broker, lifespan=lifespan)

    @app.on_startup
    async def initialize() -> None:
        await broker.connect()
        await declare_topology(broker)

    @app.after_startup
    async def start_dispatcher() -> None:
        nonlocal publisher_task
        publisher_task = asyncio.create_task(dispatcher.run(stop), name="outbox-dispatcher")

    @app.on_shutdown
    async def stop_dispatcher() -> None:
        stop.set()
        if publisher_task is not None:
            await publisher_task

    return app


if __name__ == "__main__":
    asyncio.run(create_worker().run())
