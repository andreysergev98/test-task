from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from payments.api.dependencies import get_payment_service, get_session
from payments.schemas.payments import PaymentAccepted, PaymentCreate, PaymentRead
from payments.services.payments import PaymentService

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])
health_router = APIRouter(tags=["health"])
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^[!-~]+$")
]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentAccepted)
async def create_payment(
    payload: PaymentCreate,
    idempotency_key: IdempotencyKey,
    service: PaymentServiceDep,
) -> PaymentAccepted:
    payment = await service.create(payload, idempotency_key)
    return PaymentAccepted.model_validate(payment)


@router.get("/{payment_id}", response_model=PaymentRead)
async def get_payment(payment_id: UUID, service: PaymentServiceDep) -> PaymentRead:
    return PaymentRead.model_validate(await service.get(payment_id))


@health_router.get("/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
