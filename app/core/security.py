import hmac
import hashlib
import os
from app.core.config import settings


def hash_with_secret(raw: str) -> str:
    secret = os.environ.get("API_KEY_SECRET", "change-me")
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_api_key(client_id: str, presented_key: str) -> bool:
    stored = settings.CLIENT_REGISTRY.get(client_id)
    if not stored:
        return False
    candidate = hash_with_secret(presented_key)
    return hmac.compare_digest(stored, candidate)
import hmac
import hashlib
from app.core.config import settings


def verify_api_key(client_id: str, presented_key: str) -> bool:
    stored = settings.CLIENT_REGISTRY.get(client_id)
    if not stored:
        return False
    secret = presented_key.encode("utf-8")
    expected = stored
    candidate = hmac.new(
        settings.CLIENT_REGISTRY.get(client_id, "").encode("utf-8"), b"", hashlib.sha256
    ).hexdigest()
    # To avoid logging keys, we only compare using secure compare
    return hmac.compare_digest(stored, hash_key_with_secret(presented_key))


def hash_key_with_secret(raw: str) -> str:
    secret = settings.CLIENT_REGISTRY.get("secret_salt", "default-salt")
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()

