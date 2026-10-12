import logging
from typing import Any

from fastapi import FastAPI, Header

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger("uvicorn.error")


@app.post("/webhook", status_code=204)
async def receive_webhook(payload: dict[str, Any], idempotency_key: str = Header()) -> None:
    logger.info("Webhook received idempotency_key=%s payload=%s", idempotency_key, payload)
