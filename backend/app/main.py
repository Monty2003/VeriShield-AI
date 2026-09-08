"""VeriShield AI -- FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, liveness, verify
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_warm_models())
    yield
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(verify.router, prefix="/api/v1", tags=["verification"])
app.include_router(liveness.router, prefix="/api/v1", tags=["liveness"])


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
