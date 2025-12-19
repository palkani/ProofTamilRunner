from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.core.security import verify_api_key
from app.core.rate_limit import RateLimiter
from app.core.config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, client_registry):
        super().__init__(app)
        self.client_registry = client_registry
        self.rate_limiter = RateLimiter(max_per_minute=settings.RATE_LIMIT_PER_MIN)

    async def dispatch(self, request, call_next):
        # allow health without auth to simplify health checks
        if request.url.path == "/health":
            return await call_next(request)

        client_id = request.headers.get("X-Client-Id")
        api_key = request.headers.get("X-API-Key")
        rid = getattr(request.state, "request_id", "n/a")

        if not client_id or not api_key:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        if client_id not in self.client_registry:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        if not verify_api_key(client_id, api_key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        if not self.rate_limiter.allow(client_id):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

        request.state.client_id = client_id
        response = await call_next(request)
        return response

