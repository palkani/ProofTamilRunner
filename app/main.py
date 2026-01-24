import os
import logging
import asyncio
from fastapi import FastAPI
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.auth import AuthMiddleware
from app.services.suggest_service import SuggestService
from app.services.corpus_db import get_corpus


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

    # Mount API under /api/v1 to match caller expectations (primary),
    # and also at root for backward compatibility and local testing.
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(api_router)
    return app


app = create_app()

# 🔍 Startup log (very important for Cloud Run debugging)
@app.on_event("startup")
async def startup_event():
    port = os.environ.get("PORT", "8080")
    print(f"🚀 ProofTamilRunner starting on port {port}")
    
    # Warmup (optional): reduces first-request latency on Cloud Run cold starts.
    # NOTE: Real cold-start reduction still requires Cloud Run min instances, but this helps
    # by loading Aksharamukha + priming caches.
    warmup_enabled = os.environ.get("WARMUP_ON_STARTUP", "true").strip().lower() in ("1", "true", "yes", "on")
    if not warmup_enabled:
        logging.info("[Warmup] Disabled (WARMUP_ON_STARTUP=false)")
        return

    async def _warm():
        try:
            svc = SuggestService()
            seeds = ["tamil", "vanakkam", "enna", "enathu", "enpathu", "nanban"]
            for q in seeds:
                try:
                    await svc.suggest(q, limit=10, mode="spoken", request_id="warmup")
                except Exception:
                    pass
            # Warm corpus index (loads Postgres snapshot if configured)
            try:
                get_corpus().ensure_loaded()
            except Exception:
                pass
            logging.info("[Warmup] Completed: primed suggest cache")
        except Exception as e:
            logging.warning("[Warmup] Failed: %s", str(e))

    try:
        asyncio.create_task(_warm())
        logging.info("[Warmup] Scheduled")
    except Exception:
        # Fallback for environments without a running loop at startup
        try:
            asyncio.run(_warm())
        except Exception:
            pass