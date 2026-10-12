from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments.db.models import OutboxEvent, utcnow
from payments.domain.events import PaymentEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(
        self,
        event: PaymentEvent,
        routing_key: str,
        *,
        available_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        payload = event.model_dump(mode="json")
        if error is not None:
            payload["error"] = error
        self.session.add(
            OutboxEvent(
                id=event.event_id,
                routing_key=routing_key,
                payload=payload,
                available_at=available_at or utcnow(),
            )
        )

    async def next_pending(self) -> OutboxEvent | None:
        result = await self.session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= utcnow())
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.one_or_none()
