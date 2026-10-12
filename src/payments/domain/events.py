from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class PaymentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    payment_id: UUID
    attempt: int = Field(default=1, ge=1, le=10)
    version: Literal[1] = 1
