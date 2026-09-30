import os

from api.main import app

ENDPOINT = "/v1/conversation/turn"
TOKEN = os.environ["AI_SERVICE_INTERNAL_TOKEN"]


def _boom(*args, **kwargs):
    raise RuntimeError("internal detail: secret-token-should-never-leak")


def test_unhandled_error_returns_controlled_500(client, auth_headers, make_payload, monkeypatch):
    monkeypatch.setattr("api.main.run_pipeline", _boom)
    r = client.post(ENDPOINT, json=make_payload(), headers=auth_headers)
    assert r.status_code == 500
    body = r.json()
    assert body == {"detail": "Internal server error."}
    assert "Traceback" not in r.text
    assert "secret-token" not in r.text
    assert TOKEN not in r.text
    assert os.sep + "ai_service" not in r.text
