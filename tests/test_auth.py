import os
import importlib
from fastapi.testclient import TestClient


def setup_app():
    os.environ["API_KEY"] = "test-key"
    os.environ["API_KEY_SECRET"] = "test-secret"
    os.environ["CLIENT_ID"] = "test-client"
    os.environ["RATE_LIMIT_PER_MIN"] = "10"
    # reload settings
    import app.core.config as config
    importlib.reload(config)
    import app.main as main
    importlib.reload(main)
    return main.app


def test_auth_success():
    app = setup_app()
    client = TestClient(app)
    headers = {
        "X-API-Key": "test-key",
        "X-Client-Id": "test-client",
    }
    resp = client.post("/transliterate", json={"text": "enathu", "mode": "spoken", "limit": 3}, headers=headers)
    assert resp.status_code == 200


def test_auth_missing_headers():
    app = setup_app()
    client = TestClient(app)
    resp = client.post("/transliterate", json={"text": "enathu", "mode": "spoken", "limit": 3})
    assert resp.status_code == 401

