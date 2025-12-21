import os
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

    app.include_router(api_router)
    return app


app = create_app()


def run():
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()

