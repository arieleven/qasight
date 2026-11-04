"""Lightweight FastAPI mock for orchestrator sandbox testing."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Body, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="QASight Sandbox Mock API")

# 32+ bytes, the minimum PyJWT accepts for HS256 without a warning.
JWT_SECRET = "testo-sandbox-secret-for-local-tests-only"
JWT_ALG = "HS256"


def _issue_token(username: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + 3600}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _require_bearer(auth: str | None) -> dict[str, Any]:
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token") from None


@dataclass
class Item:
    id: int
    name: str
    meta: dict[str, Any]


ITEMS: list[Item] = []
NEXT_ID = 1


def _find_item(item_id: int) -> Item | None:
    for it in ITEMS:
        if it.id == item_id:
            return it
    return None


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "message": "Hello World"}


@app.get("/slow")
async def slow() -> dict[str, str]:
    await asyncio.sleep(2)
    return {"status": "ok", "message": "slow"}


@app.get("/error")
async def error() -> JSONResponse:
    raise HTTPException(status_code=500, detail="forced error for tests")


@app.get("/flaky")
async def flaky() -> dict[str, str]:
    # ~30% failure rate
    if random.random() < 0.3:
        raise HTTPException(status_code=500, detail="flaky failure")
    return {"status": "ok", "message": "sometimes fails"}


@app.post("/submit")
async def submit(payload: dict[str, Any]) -> dict[str, Any]:
    return {"received": True, "payload": payload}


@app.post("/login")
async def login(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    username = str(payload.get("username") or "user")
    token = _issue_token(username)
    return {"token": token}


@app.get("/secure")
async def secure(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _require_bearer(authorization)
    return {"status": "ok", "secure": True}


@app.get("/admin")
async def admin(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    claims = _require_bearer(authorization)
    return {"status": "ok", "admin": True, "claims": claims}


@app.get("/items")
async def list_items() -> dict[str, Any]:
    return {"items": [{"id": i.id, "name": i.name, "meta": i.meta} for i in ITEMS]}


@app.post("/items")
async def create_item(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    global NEXT_ID
    name = str(payload.get("name") or f"item-{NEXT_ID}")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    item = Item(id=NEXT_ID, name=name, meta=meta)
    ITEMS.append(item)
    NEXT_ID += 1
    return {"created": True, "item": {"id": item.id, "name": item.name, "meta": item.meta}}


@app.get("/items/{item_id}")
async def get_item(item_id: int) -> dict[str, Any]:
    item = _find_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return {"item": {"id": item.id, "name": item.name, "meta": item.meta}}


@app.delete("/items/{item_id}")
async def delete_item(item_id: int) -> dict[str, Any]:
    item = _find_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    ITEMS.remove(item)
    return {"deleted": True, "id": item_id}


@app.post("/upload")
async def upload(
    file: UploadFile | None = None, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    # Randomized latency between 50ms and 2000ms
    delay = random.uniform(0.05, 2.0)
    await asyncio.sleep(delay)

    size = 0
    if file is not None:
        content = await file.read()
        size += len(content)
    if payload is not None:
        size += len(str(payload).encode("utf-8"))

    return {"ok": True, "delay_ms": int(delay * 1000), "size": size}


@app.get("/chaos")
async def chaos() -> dict[str, Any]:
    """
    Chaos profile:
      - 200 (success): 50%
      - 500 (server error): 20%
      - 401 (unauthorized): 15%
      - high latency (3-5 seconds delay): 15% (still 200)
    """
    p = random.random()
    if p < 0.15:
        delay = random.uniform(3.0, 5.0)
        await asyncio.sleep(delay)
        return {"status": "ok", "mode": "high_latency", "delay_ms": int(delay * 1000)}
    if p < 0.30:
        raise HTTPException(status_code=401, detail="chaos unauthorized")
    if p < 0.50:
        raise HTTPException(status_code=500, detail="chaos server error")
    return {"status": "ok", "mode": "normal", "delay_ms": 0}
