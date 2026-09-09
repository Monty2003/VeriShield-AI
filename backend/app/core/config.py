"""Application settings, read from environment or .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="VERISHIELD_", extra="ignore"
    )

    app_name: str = "VeriShield AI"
    debug: bool = True

    # --- storage ---
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    max_upload_mb: int = 20

    # --- datastores ---
    # Every one of these is optional at runtime. The pipeline is pure
    # computation over an image; persistence is for audit trail and the mock
    # authority registry. A missing database must degrade the service, never
    # stop it -- a verification tool that refuses to verify because Redis is
    # down is worse than one that verifies and cannot cache.
    # 27018, not 27017 -- see the port comment in docker-compose.yml.
    mongo_url: str = "mongodb://localhost:27018"
    mongo_db: str = "verishield"

    # How long to give MongoDB before declaring it unreachable.
    #
    # This was 1500 ms hard-coded in both stores, which was right for the
    # local container this project started with -- that connects in about
    # 50 ms. Atlas does not: mongodb+srv means a DNS SRV lookup, then TLS,
    # then a handshake with a remote replica set, measured here at 4.4
    # seconds cold. Under the old budget the first request after every
    # restart failed and reported the database as down when it was merely
    # far away, which is a much more alarming thing to tell an operator.
    mongo_timeout_ms: int = 8000
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "verishield"
    minio_secret_key: str = "verishield-dev-secret"
    minio_bucket: str = "documents"
    minio_secure: bool = False

    # --- models ---
    # Only one heavy model is held in VRAM at a time. Target hardware is a
    # 6 GB RTX 3050, which cannot hold the detector, OCR, layout model,
    # forensic backbone and face recogniser simultaneously.
    use_gpu: bool = True
    max_resident_models: int = 1
    ocr_lang: str = "en"

    # --- security ---
    # Origins allowed to call this API from a browser. The Vite dev server by
    # default; a deployment must set VERISHIELD_CORS_ORIGINS to its real front
    # end. Wildcards are refused at startup rather than accepted quietly --
    # "*" with credentials enabled is the configuration that turns any site a
    # user visits into a client of this API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
    
    jwt_secret: str = ""

    # False in a real deployment. When true, /docs is served and errors carry
    # detail; when false, both are withheld.
    debug_api: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # --- risk thresholds (mirrored in app/risk/engine.py defaults) ---
    risk_medium_threshold: float = 30.0
    risk_high_threshold: float = 65.0

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
