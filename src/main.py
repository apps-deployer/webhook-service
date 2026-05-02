from fastapi import FastAPI

from src.config import load_settings

settings = load_settings()
app = FastAPI(title="Webhook Service")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


from src.api.webhooks import router as webhooks_router

app.include_router(webhooks_router)
