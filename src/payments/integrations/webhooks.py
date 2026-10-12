from collections.abc import Collection
from typing import Protocol

import httpx

from payments.domain.errors import WebhookError
from payments.schemas.payments import WebhookPayload


class WebhookSender(Protocol):
    async def send(self, url: str, payload: WebhookPayload) -> None: ...


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient, allowed_hosts: Collection[str]) -> None:
        self.client = client
        self.allowed_hosts = frozenset(host.rstrip(".").lower() for host in allowed_hosts)

    async def send(self, url: str, payload: WebhookPayload) -> None:
        host = httpx.URL(url).host.rstrip(".").lower()
        if host not in self.allowed_hosts:
            raise WebhookError("Webhook host is not allowed")
        try:
            async with self.client.stream(
                "POST",
                url,
                json=payload.model_dump(mode="json"),
                headers={"Idempotency-Key": str(payload.event_id)},
                follow_redirects=False,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise WebhookError(f"Webhook returned HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            raise WebhookError(type(exc).__name__) from exc
