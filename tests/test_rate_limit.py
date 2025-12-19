import os
import importlib
from fastapi.testclient import TestClient


def setup_app():
    os.environ["API_KEY"] = "rl-key"
    os.environ["API_KEY_SECRET"] = "rl-secret"
    os.environ["CLIENT_ID"] = "rl-client"
    os.environ["RATE_LIMIT_PER_MIN"] = "1"
    import app.core.config as config
    importlib.reload(config)
    import app.main as main
    importlib.reload(main)
    return main.app


def test_rate_limit_exceeded():
    app = setup_app()
    client = TestClient(app)
    headers = {"X-API-Key": "rl-key", "X-Client-Id": "rl-client"}
    resp1 = client.post("/transliterate", json={"text": "enathu", "mode": "spoken", "limit": 3}, headers=headers)
    assert resp1.status_code == 200
    resp2 = client.post("/transliterate", json={"text": "enathu", "mode": "spoken", "limit": 3}, headers=headers)
    assert resp2.status_code == 429

