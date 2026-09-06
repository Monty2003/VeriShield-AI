"""VeriShield AI -- FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, verify
from app.core.config import settings

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


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
