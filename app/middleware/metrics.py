import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.requests_total = {}
        self.errors_total = {}
        self.latency_ms = {}

    async def dispatch(self, request, call_next):
        client_id = request.headers.get("X-Client-Id", "unknown")
        start = time.time()
        rid = getattr(request.state, "request_id", "n/a")
        self.requests_total[client_id] = self.requests_total.get(client_id, 0) + 1
        try:
            response = await call_next(request)
            return response
        except Exception:
            self.errors_total[client_id] = self.errors_total.get(client_id, 0) + 1
            logging.exception("[METRICS] request_id=%s client_id=%s error", rid, client_id)
            raise
        finally:
            elapsed = (time.time() - start) * 1000
            self.latency_ms[client_id] = elapsed
            logging.info("[METRICS] request_id=%s client_id=%s latency_ms=%.2f", rid, client_id, elapsed)

