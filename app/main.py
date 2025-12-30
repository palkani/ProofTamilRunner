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
    
    # Warmup transliteration cache with common prefixes
    from app.services.google_transliteration import get_transliteration_suggestions
    prefixes = [
        "a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "ga", "gi", "gu",
        "sa", "si", "su", "se", "so", "cha", "chi", "chu", "ja", "ji", "ju",
        "ta", "ti", "tu", "te", "to", "tha", "thi", "thu", "the", "tho",
        "da", "di", "du", "na", "ni", "nu", "ne", "no", "pa", "pi", "pu", "pe", "po",
        "ba", "bi", "bu", "ma", "mi", "mu", "me", "mo", "ya", "yi", "yu",
        "ra", "ri", "ru", "re", "ro", "la", "li", "lu", "le", "lo",
        "va", "vi", "vu", "ve", "vo", "sha", "shi", "shu", "ha", "hi", "hu",
        "tam", "van", "nan", "mur", "vel", "kan", "man", "pan", "sel", "ara",
    ]
    logging.info(f"[Warmup] Pre-caching {len(prefixes)} common prefixes...")
    for prefix in prefixes:
        try:
            await get_transliteration_suggestions(text=prefix, limit=8, timeout=3.0)
            await asyncio.sleep(0.03)  # Small delay to avoid rate limiting
        except Exception as e:
            logging.debug(f"[Warmup] Error caching {prefix}: {e}")
    
    from app.services.google_transliteration import get_cache_stats
    stats = get_cache_stats()
    logging.info(f"[Warmup] Complete: {stats['size']} entries cached, hit_rate={stats.get('hit_rate_percent', 0)}%")