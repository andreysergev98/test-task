import os
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitExchange, RabbitQueue
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from payments.db.base import Base
from payments.db.session import create_session_factory
from payments.messaging import consumer, publisher, topology


@pytest.fixture
async def postgres_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = f"test_{uuid4().hex}"
    engine = create_async_engine(url, execution_options={"schema_translate_map": {None: schema}})
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


@pytest.fixture
def postgres_sessions(postgres_engine):
    return create_session_factory(postgres_engine)


@pytest.fixture
async def rabbit_broker(settings, monkeypatch):
    url = os.getenv("TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("TEST_RABBITMQ_URL is not configured")
    settings.rabbitmq_url = SecretStr(url)
    suffix = uuid4().hex
    exchange = RabbitExchange(f"payments.test.{suffix}", durable=True)
    dead_exchange = RabbitExchange(f"payments.dead.test.{suffix}", durable=True)
    arguments = dict(topology.PAYMENTS_QUEUE.arguments)
    arguments["x-dead-letter-exchange"] = dead_exchange.name
    queue = RabbitQueue(
        f"payments.new.test.{suffix}",
        routing_key="payments.new",
        durable=True,
        arguments=arguments,
    )
    dead_queue = RabbitQueue(
        f"payments.dlq.test.{suffix}",
        routing_key="payments.dlq",
        durable=True,
        arguments={"x-queue-type": "quorum"},
    )
    for module in (topology, consumer, publisher):
        monkeypatch.setattr(module, "PAYMENTS_EXCHANGE", exchange)
    for module in (topology, consumer):
        monkeypatch.setattr(module, "PAYMENTS_QUEUE", queue)
    monkeypatch.setattr(topology, "DEAD_LETTER_EXCHANGE", dead_exchange)
    monkeypatch.setattr(topology, "DEAD_LETTER_QUEUE", dead_queue)
    broker = topology.create_broker(settings)
    await broker.connect()
    await topology.declare_topology(broker)
    try:
        yield broker
    finally:
        for definition in (queue, dead_queue):
            declared = await broker.declare_queue(definition)
            await declared.delete(if_unused=False, if_empty=False)
        for definition in (exchange, dead_exchange):
            declared = await broker.declare_exchange(definition)
            await declared.delete(if_unused=False)
        await broker.stop()
