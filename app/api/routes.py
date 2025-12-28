import logging
from typing import Optional
from fastapi import APIRouter, Request, Response
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService
from app.services.suggest_service import get_suggest_service
from app.core.config import settings

router = APIRouter()
service = TransliterationService()


def _no_cache_headers(resp: Response):
    if resp is None:
        return
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"


@router.get("/health")
async def health():
    cache_stats = service.cache.stats() if service.cache else {"size": 0, "hits": 0, "misses": 0}
    base_present = bool(settings.TRANSLITERATOR_BASE_URL)
    return {
        "ok": True,
        "transliterator_enabled": settings.TRANSLITERATOR_ENABLED,
        "transliterator_base_url_present": base_present,
        "cache_size": cache_stats["size"],
        "cache_hits": cache_stats["hits"],
        "cache_misses": cache_stats["misses"],
    }


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(req: TransliterateRequest, request: Request, response: Response):
    rid = getattr(request.state, "request_id", "n/a")
    suggestions, used_runner, cache_status = await service.transliterate(req.text, req.mode, req.limit, rid)
    response.headers["X-Transliterator-Used"] = "true" if used_runner else "false"
    response.headers["X-Transliterator-Cache"] = cache_status
    _no_cache_headers(response)
    return TransliterateResponse(success=True, suggestions=suggestions)


@router.get("/transliterate/suggest", response_model=TransliterateResponse)
async def transliterate_suggest(
    q: str,
    limit: int = 5,
    mode: str = "spoken",
    prev: Optional[str] = None,
    request: Optional[Request] = None,
    response: Optional[Response] = None,
):
    """
    Production-grade suggest API implementing 12-step pipeline.
    
    Returns Tamil transliteration suggestions with strict linguistic validation.
    """
    rid = getattr(getattr(request, "state", None), "request_id", "n/a") if request else "n/a"
    
    # Use new suggest service with full pipeline (lazy initialization)
    suggest_service = get_suggest_service()
    suggestions, metadata = await suggest_service.suggest(q, limit, mode, prev, rid)
    
    if response is not None:
        _no_cache_headers(response)
        if "cache" in metadata:
            response.headers["X-Cache"] = metadata["cache"]
        if "latency_ms" in metadata:
            response.headers["X-Latency-Ms"] = str(int(metadata["latency_ms"]))
    
    return TransliterateResponse(success=True, suggestions=suggestions)
