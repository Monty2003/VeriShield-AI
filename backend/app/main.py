"""VeriShield AI -- FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, health, liveness, verify
from app.core.config import settings

logger = logging.getLogger(__name__)


async def _warm_models() -> None:
    """
    Load the face models once, in the background, at startup.

    Measured cold: the first request that touches face analysis spends 86
    seconds loading five ONNX models, and every subsequent one takes 250 ms.
    On a liveness session that first request eats the whole 120-second
    challenge window, so the check appears broken rather than slow.

    Loading here moves that cost somewhere an operator can see it. It runs in a
    worker thread so the server still binds immediately and /health answers
    while the models are still coming up -- a slow start should not look like a
    dead service either.
    """
    try:
        from app.pipeline.stages.face import get_default_provider

        provider = get_default_provider()
        loader = getattr(provider, "_get_app", None)
        if loader is not None:
            await asyncio.to_thread(loader)
            logger.info("face models warmed")
    except Exception as exc:  # noqa: BLE001 -- warming is an optimisation, not a requirement
        logger.warning("face model warm-up failed: %s", exc)


async def _warm_datastores() -> None:
    """
    Open the database connections once, at startup, in the background.

    Same reasoning as the model warm-up above. Connecting to Atlas costs
    several seconds -- DNS SRV, TLS, replica-set handshake -- and whichever
    request pays that cost first is the one that looks broken. Paying it here
    means the first login is fast instead of being the slowest request the
    server will ever serve.

    Failure is deliberately not fatal: a database that is unreachable at boot
    may be reachable a minute later, and the stores re-probe on their own.
    """
    try:
        from app.core.users import user_store
        from app.storage.audit import audit_store

        reachable = await asyncio.to_thread(
            lambda: (user_store.available, audit_store.available)
        )
        logger.info(
            "datastores warmed: users=%s audit=%s", reachable[0], reachable[1]
        )
    except Exception as exc:  # noqa: BLE001 -- warming is an optimisation
        logger.warning("datastore warm-up failed: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [
        asyncio.create_task(_warm_models()),
        asyncio.create_task(_warm_datastores()),
    ]
    yield
    for task in tasks:
        task.cancel()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Multimodal identity and credential risk assessment.\n\n"
        "This service does not declare documents genuine or fake. It gathers "
        "evidence from independent checks -- machine-readable zone arithmetic, "
        "content rules, image forensics, and cross-document comparison -- and "
        "returns a risk score together with the reasoning behind it, so a human "
        "reviewer can make and justify the decision."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# The React dev server runs on 5173 (Vite). Tightened before any deployment --
# a verification API should not be callable from arbitrary origins.
_origins = settings.cors_origin_list

# "*" with credentials enabled makes every site a user visits a client of this
# API. Browsers reject that combination anyway; failing here states why rather
# than leaving someone to debug a CORS error that is actually a config error.
if "*" in _origins:
    raise RuntimeError(
        "VERISHIELD_CORS_ORIGINS contains '*'. Credentialed requests cannot use "
        "a wildcard origin -- list the front end's real origins instead."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    """
    Headers that cost nothing and close whole classes of attack.

    This API returns JSON describing people's identity documents. The headers
    below stop a browser from guessing a response is HTML and running it,
    embedding the API in a frame, or leaking the URL to a third party through
    the referrer.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    # This API serves no HTML of its own, so nothing legitimate needs to load.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(verify.router, prefix="/api/v1", tags=["verification"])
app.include_router(liveness.router, prefix="/api/v1", tags=["liveness"])


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
