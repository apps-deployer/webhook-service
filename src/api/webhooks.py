import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth import generate_service_token

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


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
    logger.info(
        "gateway request completed method=%s path=%s status_code=%s",
        method,
        path,
        resp.status_code,
        extra={
            "method": method,
            "path": path,
            "status_code": resp.status_code,
        },
    )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        logger.warning(
            "gateway request failed method=%s path=%s status_code=%s detail=%s",
            method,
            path,
            resp.status_code,
            detail,
            extra={
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "detail": detail,
            },
        )
        raise HTTPException(status_code=resp.status_code, detail=detail)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _repo_urls(payload: dict) -> list[str]:
    repo = payload.get("repository") or {}
    raw_candidates = [
        repo.get("html_url"),
        repo.get("clone_url"),
        repo.get("ssh_url"),
    ]
    full_name = repo.get("full_name")
    if full_name:
        raw_candidates.append(f"github.com/{full_name}")

    urls: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_candidates:
        if not raw_url:
            continue
        base_url = raw_url.rstrip("/")
        candidates = [base_url]
        if base_url.endswith(".git"):
            candidates.append(base_url.removesuffix(".git"))
        else:
            candidates.append(f"{base_url}.git")
        for url in candidates:
            if url in seen:
                continue
            urls.append(url)
            seen.add(url)
    return urls


@router.post("/github")
async def github_webhook(request: Request):
    settings = _settings()
    body = await request.body()

    if settings.github.webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(body, signature, settings.github.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    payload = await request.json()
    repo = payload.get("repository") or {}

    logger.info(
        "github webhook received event=%s delivery_id=%s repo=%s ref=%s",
        event,
        delivery_id,
        repo.get("full_name"),
        payload.get("ref"),
        extra={
            "event": event,
            "delivery_id": delivery_id,
            "repo": repo.get("full_name"),
            "ref": payload.get("ref"),
        },
    )

    if event == "push":
        return await _handle_push_event(payload, delivery_id=delivery_id)

    logger.info(
        "github webhook ignored event=%s delivery_id=%s reason=unsupported_event",
        event,
        delivery_id,
        extra={"event": event, "delivery_id": delivery_id, "reason": "unsupported event"},
    )
    return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"unsupported event {event}"})


async def _handle_push_event(payload: dict, *, delivery_id: str = ""):
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        logger.info(
            "push webhook ignored delivery_id=%s ref=%s reason=not_branch_push",
            delivery_id,
            ref,
            extra={"delivery_id": delivery_id, "ref": ref, "reason": "not a branch push"},
        )
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not a branch push"})

    branch = ref.removeprefix("refs/heads/")
    repo_urls = _repo_urls(payload)
    head_commit = payload.get("head_commit") or {}
    commit_sha = head_commit.get("id")
    commit_message = head_commit.get("message")

    if not repo_urls or not commit_sha:
        logger.warning(
            "push webhook payload is incomplete delivery_id=%s repo_urls=%s branch=%s has_commit_sha=%s",
            delivery_id,
            repo_urls,
            branch,
            bool(commit_sha),
            extra={
                "delivery_id": delivery_id,
                "repo_urls": repo_urls,
                "branch": branch,
                "has_commit_sha": bool(commit_sha),
            },
        )
        raise HTTPException(status_code=400, detail="Missing repo URL or commit SHA")

    logger.info(
        "processing push webhook delivery_id=%s repo_urls=%s branch=%s commit_sha=%s",
        delivery_id,
        repo_urls,
        branch,
        commit_sha,
        extra={
            "delivery_id": delivery_id,
            "repo_urls": repo_urls,
            "branch": branch,
            "commit_sha": commit_sha,
        },
    )

    env = None
    for repo_url in repo_urls:
        try:
            env = await _gateway_request(
                "POST",
                "/internal/projects/env-by-git",
                json={"repo_url": repo_url, "target_branch": branch},
            )
            logger.info(
                "matching environment found delivery_id=%s repo_url=%s branch=%s env_id=%s project_id=%s",
                delivery_id,
                repo_url,
                branch,
                env.get("id"),
                env.get("project_id"),
                extra={
                    "delivery_id": delivery_id,
                    "repo_url": repo_url,
                    "branch": branch,
                    "env_id": env.get("id"),
                    "project_id": env.get("project_id"),
                },
            )
            break
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(
                    "environment lookup missed delivery_id=%s repo_url=%s branch=%s status_code=%s detail=%s",
                    delivery_id,
                    repo_url,
                    branch,
                    e.status_code,
                    e.detail,
                    extra={
                        "delivery_id": delivery_id,
                        "repo_url": repo_url,
                        "branch": branch,
                        "status_code": e.status_code,
                        "detail": e.detail,
                    },
                )
                continue
            logger.exception(
                "environment lookup failed delivery_id=%s repo_url=%s branch=%s status_code=%s detail=%s",
                delivery_id,
                repo_url,
                branch,
                e.status_code,
                e.detail,
                extra={
                    "delivery_id": delivery_id,
                    "repo_url": repo_url,
                    "branch": branch,
                    "status_code": e.status_code,
                    "detail": e.detail,
                },
            )
            raise

    if env is None:
        logger.info(
            "push webhook ignored delivery_id=%s repo_urls=%s branch=%s reason=no_matching_environment",
            delivery_id,
            repo_urls,
            branch,
            extra={
                "delivery_id": delivery_id,
                "repo_urls": repo_urls,
                "branch": branch,
                "reason": "no matching environment",
            },
        )
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "no matching environment"})

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
    logger.info(
        "deployment created from webhook delivery_id=%s deployment_run_id=%s env_id=%s project_id=%s commit_sha=%s",
        delivery_id,
        run.get("id"),
        env.get("id"),
        env.get("project_id"),
        commit_sha,
        extra={
            "delivery_id": delivery_id,
            "deployment_run_id": run.get("id"),
            "env_id": env.get("id"),
            "project_id": env.get("project_id"),
            "commit_sha": commit_sha,
        },
    )
    return {"status": "accepted", "deployment_run_id": run["id"]}
