"""Local API guard. Every route needs the token; Host/Origin checks block DNS rebinding. Routes live in daemon.py."""
import collections
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import PORT

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
RATE_LIMIT = 30  # requests per minute


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ActionId(BaseModel):
    id: str = Field(pattern=r"^[Aa]\d{1,6}$")


def create_app(token: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hits: collections.deque[float] = collections.deque()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in ALLOWED_HOSTS:
            return JSONResponse({"error": "bad host"}, status_code=421)
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"error": "bad origin"}, status_code=403)
        if not secrets.compare_digest(request.headers.get("x-jarvis-token", ""), token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        now = time.monotonic()
        while hits and now - hits[0] > 60:
            hits.popleft()
        # the kill switch must always get through
        if len(hits) >= RATE_LIMIT and request.url.path != "/api/stop":
            return JSONResponse({"error": "slow down"}, status_code=429)
        hits.append(now)
        return await call_next(request)

    return app
