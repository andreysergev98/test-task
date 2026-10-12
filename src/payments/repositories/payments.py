from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments.db.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, payment_id: UUID, *, for_update: bool = False) -> Payment | None:
        query = select(Payment).where(Payment.id == payment_id)
        if for_update:
            query = query.with_for_update()
        return (await self.session.scalars(query)).one_or_none()

    async def get_by_key(self, key: str) -> Payment | None:
        result = await self.session.scalars(select(Payment).where(Payment.idempotency_key == key))
        return result.one_or_none()

    async def add(self, payment: Payment) -> None:
        self.session.add(payment)
        await self.session.flush()
