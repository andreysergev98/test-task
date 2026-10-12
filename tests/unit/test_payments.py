from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from payments.db.models import OutboxEvent, Payment
from payments.repositories.outbox import OutboxRepository
from payments.schemas.payments import PaymentCreate
from payments.services.payments import PaymentService


async def test_outbox_failure_rolls_back_payment(sessions, payment_data):
    with patch.object(OutboxRepository, "add", side_effect=RuntimeError("outbox unavailable")):
        async with sessions() as session:
            with pytest.raises(RuntimeError, match="outbox unavailable"):
                await PaymentService(session).create(payment_data, "order-42")

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.parametrize("amount", ["123.45", "123.450", 123.45])
def test_equivalent_amounts_and_metadata_have_same_fingerprint(payment_data, amount):
    payload = payment_data.model_dump(mode="json")
    payload["amount"] = amount
    payload["metadata"] = {"nested": {"items": ["book"]}, "order_id": 42}

    assert PaymentCreate.model_validate(payload).fingerprint() == payment_data.fingerprint()


@pytest.mark.parametrize("amount", ["1", "1.0", "1.00"])
def test_amount_is_normalized(amount):
    payment = PaymentCreate(amount=amount, currency="USD", webhook_url="https://example.com")
    assert payment.model_dump(mode="json")["amount"] == "1.00"
