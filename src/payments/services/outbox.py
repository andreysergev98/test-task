import asyncio
import logging
from contextlib import suppress
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.config.settings import Settings
from payments.db.models import utcnow
from payments.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(
        self, payload: dict[str, JsonValue], routing_key: str, event_id: UUID
    ) -> None: ...


class OutboxDispatcher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        settings: Settings,
    ) -> None:
        self.sessions = sessions
        self.publisher = publisher
        self.settings = settings

    async def publish_batch(self) -> int:
        published = 0
        for _ in range(self.settings.outbox_batch_size):
            async with self.sessions() as session, session.begin():
                event = await OutboxRepository(session).next_pending()
                if event is None:
                    break
                await self.publisher.publish(event.payload, event.routing_key, event.id)
                event.published_at = utcnow()
            published += 1
        return published

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                published = await self.publish_batch()
            except Exception as exc:
                logger.warning("Outbox publish failed error=%s", type(exc).__name__)
                published = 0
            if published == self.settings.outbox_batch_size:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.outbox_poll_interval)
