import os
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()


def hash_key(raw: str) -> str:
    secret = os.environ.get("API_KEY_SECRET", "change-me")
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


class Settings:
    PORT: int = int(os.environ.get("PORT", 8080))
    MAX_TEXT_LEN: int = int(os.environ.get("MAX_TEXT_LEN", 64))
    RATE_LIMIT_PER_MIN: int = int(os.environ.get("RATE_LIMIT_PER_MIN", 60))
    TRANSLITERATOR_BASE_URL: str = os.environ.get("TRANSLITERATOR_BASE_URL", "")
    TRANSLITERATOR_TIMEOUT_SECONDS: int = int(os.environ.get("TRANSLITERATOR_TIMEOUT_SECONDS", 5))
    CACHE_TTL_SECONDS: int = int(os.environ.get("CACHE_TTL_SECONDS", 600))
    CACHE_MAX_SIZE: int = int(os.environ.get("CACHE_MAX_SIZE", 5000))
    TRANSLITERATOR_ENABLED: bool = bool(TRANSLITERATOR_BASE_URL.strip())
    # Simple in-memory client registry: client_id -> hashed_key
    CLIENT_REGISTRY = {
        os.environ.get("CLIENT_ID", "demo-client"): hash_key(os.environ.get("API_KEY", "demo-key"))
    }


settings = Settings()
