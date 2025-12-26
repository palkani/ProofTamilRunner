import os
import logging
from fastapi import FastAPI
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.auth import AuthMiddleware


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="ProofTamilRunner IME", version="1.0.0")

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(AuthMiddleware, client_registry=settings.CLIENT_REGISTRY)

    logging.info(
        "transliterator_enabled enabled=%s base_url_present=%s",
        settings.TRANSLITERATOR_ENABLED,
        bool(settings.TRANSLITERATOR_BASE_URL),
    )

    # Mount API under /api/v1 to match caller expectations
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

# 🔍 Startup log (very important for Cloud Run debugging)
@app.on_event("startup")
async def startup_event():
    port = os.environ.get("PORT", "8080")
    print(f"🚀 ProofTamilRunner starting on port {port}")
