import hashlib
import hmac

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth import generate_service_token

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _settings():
    from src.main import settings
    return settings


def _verify_github_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


async def _gateway_request(method: str, path: str, *, json: dict | None = None):
    settings = _settings()
    headers = {"Authorization": f"Bearer {generate_service_token(settings.auth.jwt_secret)}"}
    async with httpx.AsyncClient(base_url=settings.gateway.base_url, timeout=30.0) as client:
        resp = await client.request(method, path, json=json, headers=headers)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _repo_url(payload: dict) -> str:
    repo = payload.get("repository") or {}
    return repo.get("clone_url") or repo.get("html_url") or ""


@router.post("/github")
async def github_webhook(request: Request):
    settings = _settings()
    body = await request.body()

    if settings.github.webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(body, signature, settings.github.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    if event == "push":
        return await _handle_push_event(payload)

    return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"unsupported event {event}"})


async def _handle_push_event(payload: dict):
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not a branch push"})

    branch = ref.removeprefix("refs/heads/")
    repo_url = _repo_url(payload)
    head_commit = payload.get("head_commit") or {}
    commit_sha = head_commit.get("id")
    commit_message = head_commit.get("message")

    if not repo_url or not commit_sha:
        raise HTTPException(status_code=400, detail="Missing repo URL or commit SHA")

    try:
        env = await _gateway_request(
            "POST",
            "/internal/projects/env-by-git",
            json={"repo_url": repo_url, "target_branch": branch},
        )
    except HTTPException as e:
        if e.status_code == 404:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "no matching environment"})
        raise

    run = await _gateway_request(
        "POST",
        "/internal/deployments",
        json={
            "project_id": env["project_id"],
            "env_id": env["id"],
            "trigger_type": "webhook",
            "commit_sha": commit_sha,
            "commit_message": commit_message,
        },
    )
    return {"status": "accepted", "deployment_run_id": run["id"]}
