"""FastAPI app: carrier login → MFA → document list → streamed PDF.

The frontend and backend are cleanly split: the UI only ever calls these JSON/
PDF endpoints. Each user's live browser session is held open through the MFA
wait by the SessionStore. Credentials are used in-memory and never logged; PDFs
are streamed to the client and never written to disk.
"""
import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config
from .browser import launch, shutdown
from .carriers.base import Credentials
from .carriers.registry import carrier_names, get_adapter
from .session import SessionStore

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
store = SessionStore()


def _state_dir(carrier: str, username: str) -> str:
    """Per (carrier, username) persistent browser-profile dir for opt-in device
    trust. The whole profile persists here, so Okta's device trust survives.
    Username is hashed so it never lands in a path."""
    d = Path(config.STATE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(f"{carrier}:{username}".encode()).hexdigest()[:16]
    return str(d / h)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sweeper = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        sweeper.cancel()
        await store.close_all()
        await shutdown()


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(60)
        await store.sweep()


app = FastAPI(lifespan=lifespan)


class LoginReq(BaseModel):
    carrier: str
    username: str
    password: str | None = None
    session_id: str | None = None   # reuse a prewarmed session if supplied
    remember: bool = False          # opt-in: persist device trust to skip MFA next time


class MfaReq(BaseModel):
    session_id: str
    code: str


class PrewarmReq(BaseModel):
    carrier: str


@app.get("/api/carriers")
async def carriers() -> list[str]:
    return carrier_names()


@app.post("/api/prewarm")
async def prewarm(req: PrewarmReq):
    """Optional: launch the browser and navigate to the login page ahead of the
    user clicking Login, so the slow part (launch + login-page load through the
    proxy) overlaps with credential typing. Only carriers that implement
    `prewarm` are warmed. Best-effort — returns null if it can't, and login just
    launches fresh."""
    adapter = get_adapter(req.carrier)
    if adapter is None or not hasattr(adapter, "prewarm"):
        return {"session_id": None}
    try:
        br = await launch(adapter.launch)
        sess = store.create(req.carrier, adapter, br)
        await adapter.prewarm(br.page)
    except Exception:
        return {"session_id": None}
    return {"session_id": sess.id}


@app.post("/api/login")
async def login(req: LoginReq):
    adapter = get_adapter(req.carrier)
    if adapter is None:
        raise HTTPException(400, f"unknown carrier '{req.carrier}'")
    state_dir = _state_dir(req.carrier, req.username) if req.remember else None
    sess = None
    # Remember-flow: launch a persistent profile so device trust persists across
    # runs (first login writes it; later ones reuse it → Okta skips MFA). The
    # prewarmed context is ephemeral, so we can't reuse it here.
    if state_dir:
        if req.session_id:
            await store.close(req.session_id)   # discard the ephemeral prewarmed context
        br = await launch(adapter.launch, user_data_dir=state_dir)
        sess = store.create(req.carrier, adapter, br)
    else:
        # Reuse a prewarmed session if handed in and still valid; else launch fresh.
        if req.session_id:
            warm = store.get(req.session_id)
            if warm is not None and warm.carrier == req.carrier and not warm.authenticated:
                sess = warm
        if sess is None:
            br = await launch(adapter.launch)
            sess = store.create(req.carrier, adapter, br)
    sess.remember = req.remember
    sess.state_path = state_dir
    try:
        mfa = await adapter.start_login(
            sess.browser.page, Credentials(username=req.username, password=req.password))
    except Exception as e:
        print(f"  [login err] {req.carrier}: {type(e).__name__}: {e}", flush=True)
        await store.close(sess.id)
        raise HTTPException(502, f"login failed: {e}")
    if not mfa.required:                          # trusted device skipped MFA (or no-MFA carrier)
        sess.authenticated = True
    return {"session_id": sess.id,
            "mfa": {"required": mfa.required, "message": mfa.message}}


@app.post("/api/mfa")
async def mfa(req: MfaReq):
    sess = store.get(req.session_id)
    if sess is None:
        raise HTTPException(404, "session expired")
    try:
        t = time.perf_counter()
        await sess.adapter.submit_mfa(sess.browser.page, req.code)
        print(f"  [t] submit_mfa: {time.perf_counter() - t:.2f}s", flush=True)
    except Exception as e:
        raise HTTPException(502, f"mfa failed: {e}")
    sess.authenticated = True
    # (remember-flow uses a persistent profile — device trust auto-persists on
    # context close; no explicit save needed here.)
    # Fold discovery into the MFA response so the client skips a separate
    # /api/documents round-trip. Best-effort — on failure the client falls back.
    docs_out = []
    try:
        t = time.perf_counter()
        sess.docs = await sess.adapter.list_documents(sess.browser.context, sess.browser.page)
        print(f"  [t] list_documents: {time.perf_counter() - t:.2f}s", flush=True)
        docs_out = [{"doc_id": d.doc_id, "title": d.title, "category": d.category}
                    for d in sess.docs]
    except Exception as e:
        print(f"  [docs err] {sess.carrier}: {e}", flush=True)
    return {"ok": True, "docs": docs_out}


@app.get("/api/documents")
async def documents(session_id: str):
    sess = store.get(session_id)
    if sess is None:
        raise HTTPException(404, "session expired")
    try:
        t = time.perf_counter()
        sess.docs = await sess.adapter.list_documents(sess.browser.context, sess.browser.page)
        print(f"  [t] list_documents: {time.perf_counter() - t:.2f}s", flush=True)
    except Exception as e:
        print(f"  [docs err] {sess.carrier}: {e}", flush=True)
        raise HTTPException(502, f"document discovery failed: {e}")
    return [{"doc_id": d.doc_id, "title": d.title, "category": d.category} for d in sess.docs]


@app.get("/api/documents/{doc_id}/pdf")
async def doc_pdf(doc_id: str, session_id: str):
    sess = store.get(session_id)
    if sess is None:
        raise HTTPException(404, "session expired")
    doc = next((d for d in sess.docs if d.doc_id == doc_id), None)
    if doc is None:
        raise HTTPException(404, "unknown document")
    try:
        t = time.perf_counter()
        body = await sess.adapter.fetch_pdf(sess.browser.context, sess.browser.page, doc)
        print(f"  [t] fetch_pdf: {time.perf_counter() - t:.2f}s", flush=True)
    except Exception as e:
        print(f"  [pdf err] {doc.title}: {e}", flush=True)
        raise HTTPException(502, f"pdf fetch failed: {e}")
    return Response(content=body, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc_id}.pdf"'})


@app.post("/api/session/{session_id}/close")
async def close_session(session_id: str):
    """Best-effort teardown fired by the frontend on page unload (sendBeacon).
    Each page load is a fresh session, so a reload/close drops its browser right
    away instead of leaving it for the TTL sweep. Idempotent."""
    await store.close(session_id)
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")
