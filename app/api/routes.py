import logging
from fastapi import APIRouter, Request
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService

router = APIRouter()
service = TransliterationService()


@router.get("/health")
async def health():
    from app.services.transliteration import TransliterationService
    svc = TransliterationService()
    ok = {
        "ok": True,
        "cache_init": svc.cache is not None,
        "runner_configured": bool(svc.client) or (svc.adapter is not None),
    }
    return ok


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(req: TransliterateRequest, request: Request):
    rid = getattr(request.state, "request_id", "n/a")
    suggestions = await service.transliterate(req.text, req.mode, req.limit, rid)
    return TransliterateResponse(success=True, suggestions=suggestions)

