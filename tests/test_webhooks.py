import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api import webhooks
from src.main import app, settings


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def webhook_secret():
    original = settings.github.webhook_secret
    settings.github.webhook_secret = "secret"
    yield
    settings.github.webhook_secret = original


def test_rejects_invalid_signature():
    client = TestClient(app)
    resp = client.post(
        "/api/v1/webhooks/github",
        content=b"{}",
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert resp.status_code == 401


def test_ignores_unknown_event():
    client = TestClient(app)
    body = b"{}"
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _signature(body, "secret")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_push_creates_deployment(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_gateway(method: str, path: str, *, json: dict | None = None):
        calls.append((method, path, json))
        if path == "/internal/projects/env-by-git":
            return {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "project_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        if path == "/internal/deployments":
            return {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
        raise AssertionError(path)

    monkeypatch.setattr(webhooks, "_gateway_request", fake_gateway)

    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/test/repo.git"},
        "head_commit": {"id": "abc123", "message": "update"},
    }
    import json
    body = json.dumps(payload).encode()

    client = TestClient(app)
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _signature(body, "secret"),
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert calls[-1] == (
        "POST",
        "/internal/deployments",
        {
            "project_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "env_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "trigger_type": "webhook",
            "commit_sha": "abc123",
            "commit_message": "update",
        },
    )


@pytest.mark.asyncio
async def test_push_ignores_unknown_project(monkeypatch):
    async def fake_gateway(method: str, path: str, *, json: dict | None = None):
        raise webhooks.HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(webhooks, "_gateway_request", fake_gateway)

    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/test/missing.git"},
        "head_commit": {"id": "abc123", "message": "update"},
    }
    import json
    body = json.dumps(payload).encode()

    client = TestClient(app)
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _signature(body, "secret"),
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "reason": "no matching environment"}
