from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.api.dependencies import require_api_key
from payments.api.routes import health_router, router
from payments.config.logging import configure_logging
from payments.config.settings import Settings, get_settings
from payments.db.session import create_engine, create_session_factory
from payments.domain.errors import IdempotencyConflict, PaymentNotFound, WebhookHostNotAllowed


def create_app(
    settings: Settings | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    configuration = settings or get_settings()
    configure_logging(configuration.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if sessions is not None:
            app.state.sessions = sessions
            yield
        else:
            engine = create_engine(configuration)
            app.state.sessions = create_session_factory(engine)
            try:
                yield
            finally:
                await engine.dispose()

    app = FastAPI(
        title="Payment Processing API",
        version="1.0.0",
        lifespan=lifespan,
        dependencies=[Depends(require_api_key)],
        docs_url="/docs" if configuration.api_docs_enabled else None,
        redoc_url="/redoc" if configuration.api_docs_enabled else None,
        openapi_url="/openapi.json" if configuration.api_docs_enabled else None,
    )
    app.state.settings = configuration
    app.include_router(router)
    app.include_router(health_router)

    if not configuration.api_docs_enabled:

        @app.get("/openapi.json", include_in_schema=False)
        async def openapi_schema() -> JSONResponse:
            return JSONResponse(app.openapi())

    @app.exception_handler(PaymentNotFound)
    async def payment_not_found(request: Request, exc: PaymentNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Payment not found"})

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Idempotency key is already used with a different request"},
        )

    @app.exception_handler(WebhookHostNotAllowed)
    async def webhook_host_not_allowed(
        request: Request, exc: WebhookHostNotAllowed
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Webhook host is not allowed"})

    return app
