import logging
from fastapi import APIRouter, Request, Response
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService
from app.core.config import settings

router = APIRouter()
service = TransliterationService()


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
    return TransliterateResponse(success=True, suggestions=suggestions)


@router.get("/transliterate/suggest", response_model=TransliterateResponse)
async def transliterate_suggest(q: str, limit: int = 8, mode: str = "spoken", request: Request = None, response: Response = None):
    rid = getattr(getattr(request, "state", None), "request_id", "n/a") if request else "n/a"
    suggestions, used_runner, cache_status = await service.transliterate(q, mode, limit, rid)
    if response is not None:
        response.headers["X-Transliterator-Used"] = "true" if used_runner else "false"
        response.headers["X-Transliterator-Cache"] = cache_status
    return TransliterateResponse(success=True, suggestions=suggestions)
