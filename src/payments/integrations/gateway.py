import asyncio
import random
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from payments.config.settings import Settings
from payments.domain.enums import PaymentStatus


class PaymentGateway(Protocol):
    async def charge(self, payment_id: UUID, amount: Decimal, currency: str) -> PaymentStatus: ...


class SimulatedGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def charge(self, payment_id: UUID, amount: Decimal, currency: str) -> PaymentStatus:
        await asyncio.sleep(
            random.uniform(self.settings.gateway_min_delay, self.settings.gateway_max_delay)
        )
        if random.random() < self.settings.gateway_success_rate:
            return PaymentStatus.SUCCEEDED
        return PaymentStatus.FAILED
