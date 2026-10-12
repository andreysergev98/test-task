from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from payments.db.models import Payment
from payments.domain.errors import IdempotencyConflict, PaymentNotFound, WebhookHostNotAllowed
from payments.domain.events import PaymentEvent
from payments.repositories.outbox import OutboxRepository
from payments.repositories.payments import PaymentRepository
from payments.schemas.payments import PaymentCreate


class PaymentService:
    def __init__(
        self, session: AsyncSession, allowed_webhook_hosts: frozenset[str] | None = None
    ) -> None:
        self.session = session
        self.allowed_webhook_hosts = allowed_webhook_hosts
        self.payments = PaymentRepository(session)
        self.outbox = OutboxRepository(session)

    async def create(self, data: PaymentCreate, idempotency_key: str) -> Payment:
        request_hash = data.fingerprint()
        async with self.session.begin():
            existing = await self.payments.get_by_key(idempotency_key)
            if existing is not None:
                return self._check_request(existing, request_hash)
            if (
                self.allowed_webhook_hosts is not None
                and data.webhook_url.host.rstrip(".").lower() not in self.allowed_webhook_hosts
            ):
                raise WebhookHostNotAllowed
            payment = Payment(
                amount=data.amount,
                currency=data.currency,
                description=data.description,
                details=data.metadata,
                webhook_url=str(data.webhook_url),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            try:
                async with self.session.begin_nested():
                    await self.payments.add(payment)
            except IntegrityError:
                existing = await self.payments.get_by_key(idempotency_key)
                if existing is None:
                    raise
                return self._check_request(existing, request_hash)
            self.outbox.add(PaymentEvent(payment_id=payment.id), "payments.new")
        return payment

    async def get(self, payment_id: UUID) -> Payment:
        payment = await self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFound
        return payment

    @staticmethod
    def _check_request(payment: Payment, request_hash: str) -> Payment:
        if payment.request_hash != request_hash:
            raise IdempotencyConflict
        return payment
