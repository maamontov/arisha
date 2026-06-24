from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.payments import router as payments_router
from app.config import get_settings
from app.db import dispose_engine, get_engine
from app.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()
    logger.info(
        "service.starting",
        service=settings.service_name,
        environment=settings.environment,
        log_level=settings.log_level,
    )
    _ = get_engine()
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("service.stopped", service=settings.service_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Arisha Payments API",
        version="0.1.0",
        description="Asynchronous payment processing microservice with guaranteed event delivery",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.include_router(payments_router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    return app


app = create_app()
