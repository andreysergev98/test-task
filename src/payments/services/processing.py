import logging
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.config.settings import Settings
from payments.db.models import Payment, utcnow
from payments.domain.enums import Currency, PaymentStatus
from payments.domain.events import PaymentEvent
from payments.integrations.gateway import PaymentGateway
from payments.integrations.webhooks import WebhookSender
from payments.repositories.outbox import OutboxRepository
from payments.repositories.payments import PaymentRepository
from payments.schemas.payments import WebhookPayload

logger = logging.getLogger(__name__)


class PaymentProcessor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: PaymentGateway,
        webhooks: WebhookSender,
        settings: Settings,
    ) -> None:
        self.sessions = sessions
        self.gateway = gateway
        self.webhooks = webhooks
        self.settings = settings

    async def process(self, event: PaymentEvent) -> None:
        async with self.sessions() as session, session.begin():
            payment = await PaymentRepository(session).get(event.payment_id, for_update=True)
            if not self._should_process(payment, event):
                return
            assert payment is not None
            if payment.status == PaymentStatus.PENDING:
                try:
                    status = await self.gateway.charge(payment.id, payment.amount, payment.currency)
                    if status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                        raise ValueError("Gateway returned a non-terminal status")
                except Exception as exc:
                    self._record_failure(session, payment, event, exc)
                    return
                payment.status = status
                payment.processed_at = utcnow()

        async with self.sessions() as session, session.begin():
            payment = await PaymentRepository(session).get(event.payment_id, for_update=True)
            if not self._should_process(payment, event):
                return
            assert payment is not None
            try:
                await self.webhooks.send(payment.webhook_url, self._webhook_payload(payment))
            except Exception as exc:
                self._record_failure(session, payment, event, exc)
            else:
                payment.webhook_delivered_at = utcnow()
                payment.processing_attempts = event.attempt
                payment.last_error = None
                logger.info("Payment completed payment_id=%s status=%s", payment.id, payment.status)

    def _should_process(self, payment: Payment | None, event: PaymentEvent) -> bool:
        if payment is None:
            raise LookupError(f"Payment {event.payment_id} does not exist")
        return (
            payment.webhook_delivered_at is None
            and payment.processing_attempts < self.settings.max_attempts
            and event.attempt == payment.processing_attempts + 1
        )

    def _record_failure(
        self, session: AsyncSession, payment: Payment, event: PaymentEvent, error: Exception
    ) -> None:
        payment.processing_attempts = event.attempt
        payment.last_error = type(error).__name__
        outbox = OutboxRepository(session)
        if event.attempt >= self.settings.max_attempts:
            outbox.add(
                PaymentEvent(payment_id=payment.id, attempt=event.attempt),
                "payments.dlq",
                error=payment.last_error,
            )
            logger.error(
                "Payment exhausted retries payment_id=%s attempt=%s", payment.id, event.attempt
            )
        else:
            delay = self.settings.retry_base_delay * 2 ** (event.attempt - 1)
            outbox.add(
                PaymentEvent(payment_id=payment.id, attempt=event.attempt + 1),
                "payments.new",
                available_at=utcnow() + timedelta(seconds=delay),
            )
            logger.warning(
                "Payment retry scheduled payment_id=%s attempt=%s error=%s",
                payment.id,
                event.attempt,
                payment.last_error,
            )

    @staticmethod
    def _webhook_payload(payment: Payment) -> WebhookPayload:
        assert payment.processed_at is not None
        return WebhookPayload(
            event_id=uuid5(NAMESPACE_URL, f"payments/{payment.id}/processed"),
            payment_id=payment.id,
            status=PaymentStatus(payment.status),
            amount=payment.amount,
            currency=Currency(payment.currency),
            description=payment.description,
            metadata=payment.details,
            created_at=payment.created_at,
            processed_at=payment.processed_at,
        )
