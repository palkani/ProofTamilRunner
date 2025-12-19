import logging
from fastapi import APIRouter, Request
from app.api.schemas import TransliterateRequest, TransliterateResponse
from app.services.transliteration import TransliterationService

router = APIRouter()
service = TransliterationService()


@router.get("/health")
async def health():
    return {"ok": True}


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(req: TransliterateRequest, request: Request):
    rid = getattr(request.state, "request_id", "n/a")
    suggestions = await service.transliterate(req.text, req.mode, req.limit, rid)
    return TransliterateResponse(success=True, suggestions=suggestions)

