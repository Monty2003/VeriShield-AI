"""
Audit trail and document storage.

A verification decision that cannot be reproduced later is not much of a
decision. When someone is refused, they are entitled to know why, and the
reviewer who refused them is entitled to show their working. That means
keeping the signals, the score and the derivation -- not just the verdict.

Everything here degrades. If MongoDB is down the pipeline still verifies
documents; it simply cannot record what it found, and says so. A verification
service that refuses to verify because its database is unavailable has turned
a logging problem into an outage.

Retention is a real decision, not an afterthought
------------------------------------------------
This stores what the system read from people's identity documents. The default
therefore keeps the EVIDENCE (which check ran, what it found, the reasoning)
and the MASKED identifiers, but not the raw document numbers. A reviewer
auditing a decision needs to know that the Aadhaar checksum failed and why;
they do not need the number itself, and neither does anything else in the
system once the assessment is complete.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.schemas.document import DocumentAnalysis, VerificationResult

# How long an availability probe is trusted before being repeated.
#
# Health checks are polled, and each probe opens a socket to a service that may
# be down. Without caching, /health re-probed on every request and a stopped
# MinIO took it from milliseconds to over a minute -- MinIO's client retries a
# refused connection with backoff. A health endpoint that hangs is worse than
# one that reports slightly stale information: monitoring reads the timeout as
# the whole service being dead.
_PROBE_CACHE_SECONDS = 10.0

# Field names whose values must never be written verbatim to the audit store.
# Masking happens here rather than at the call site so that a new caller cannot
# forget to do it.
_SENSITIVE_FIELDS = {"document_number", "aadhaar", "pan", "raw_text", "mrz_line1", "mrz_line2"}


def _mask_value(value: Any) -> str:
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * (len(text) - 4)}{text[-4:]}"


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively mask sensitive values in a nested structure."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_FIELDS and value not in (None, "", {}):
            out[key] = _mask_value(value)
        elif isinstance(value, dict):
            out[key] = _redact(value)
        elif isinstance(value, list):
            out[key] = [_redact(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def document_fingerprint(image_bytes: bytes) -> str:
    """
    Content hash of a document image.

    Lets the same document be recognised across cases without storing it: two
    submissions of an identical file share a fingerprint, which is enough to
    spot resubmission after a refusal.
    """
    return hashlib.sha256(image_bytes).hexdigest()


class AuditStore:
    """
    Records verification outcomes, when a database is reachable.

    The connection is made lazily and every failure is swallowed into an
    `available` flag: callers get a truthful answer about whether their record
    was kept, and never an exception in the middle of a verification.
    """

    def __init__(self, url: str | None = None, database: str | None = None) -> None:
        self.url = url or settings.mongo_url
        self.database = database or settings.mongo_db
        self._client = None
        self._error = ""
        self._probed_at = 0.0

    @property
    def available(self) -> bool:
        return self._connect() is not None

    @property
    def error(self) -> str:
        self._connect()
        return self._error

    def _connect(self):
        if self._client is not None:
            return self._client

        # Do not re-probe a service known to be down until the cache expires.
        import time

        if self._error and (time.monotonic() - self._probed_at) < _PROBE_CACHE_SECONDS:
            return None

        self._probed_at = time.monotonic()
        try:
            from pymongo import MongoClient

            timeout = settings.mongo_timeout_ms
            client = MongoClient(
                self.url,
                serverSelectionTimeoutMS=timeout,
                connectTimeoutMS=timeout,
                socketTimeoutMS=timeout,
            )
            client.admin.command("ping")
            self._client = client
            self._error = ""
        except Exception as exc:  # noqa: BLE001 -- unavailability is a state, not a crash
            self._error = f"{type(exc).__name__}: {exc}"
            return None
        return self._client

    # ---- writes ---------------------------------------------------------

    def record_document(
        self, analysis: DocumentAnalysis, fingerprint: str | None = None
    ) -> bool:
        """Store one document assessment. Returns whether it was persisted."""
        client = self._connect()
        if client is None:
            return False

        payload = _redact(analysis.model_dump(mode="json"))
        payload["fingerprint"] = fingerprint
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()

        try:
            client[self.database]["documents"].insert_one(payload)
            return True
        except Exception:  # noqa: BLE001
            return False

    def record_case(self, result: VerificationResult) -> bool:
        client = self._connect()
        if client is None:
            return False

        payload = _redact(result.model_dump(mode="json"))
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()

        try:
            client[self.database]["cases"].insert_one(payload)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- reads ----------------------------------------------------------

    def find_by_fingerprint(self, fingerprint: str) -> list[dict]:
        """
        Previous assessments of a byte-identical document.

        Resubmission of a refused document is a signal in its own right, and
        it is only visible from history.
        """
        client = self._connect()
        if client is None:
            return []
        try:
            return list(
                client[self.database]["documents"]
                .find({"fingerprint": fingerprint}, {"_id": 0})
                .limit(10)
            )
        except Exception:  # noqa: BLE001
            return []

    def recent_cases(self, limit: int = 20) -> list[dict]:
        client = self._connect()
        if client is None:
            return []
        try:
            return list(
                client[self.database]["cases"]
                .find({}, {"_id": 0})
                .sort("recorded_at", -1)
                .limit(limit)
            )
        except Exception:  # noqa: BLE001
            return []


class ObjectStore:
    """
    Stores the document images themselves, when MinIO is reachable.

    Kept separate from the audit record on purpose: the evidence of a decision
    and the personal data it was made from have different retention needs, and
    separating them means the images can be expired without destroying the
    reasoning that justified the outcome.
    """

    def __init__(self) -> None:
        self._client = None
        self._error = ""
        self._probed_at = 0.0

    @property
    def available(self) -> bool:
        return self._connect() is not None

    @property
    def error(self) -> str:
        self._connect()
        return self._error

    def _connect(self):
        if self._client is not None:
            return self._client

        import time

        if self._error and (time.monotonic() - self._probed_at) < _PROBE_CACHE_SECONDS:
            return None

        self._probed_at = time.monotonic()
        try:
            import urllib3
            from minio import Minio

            # MinIO's default client retries a refused connection with backoff,
            # which turned a stopped container into a 60-second hang on
            # /health. One short attempt, no retries: either it is there or it
            # is not, and the caller needs that answer immediately.
            http_client = urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=1.0, read=2.0),
                retries=urllib3.Retry(total=0, connect=0, read=0),
            )
            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                http_client=http_client,
            )
            if not client.bucket_exists(settings.minio_bucket):
                client.make_bucket(settings.minio_bucket)
            self._client = client
            self._error = ""
        except Exception as exc:  # noqa: BLE001
            self._error = f"{type(exc).__name__}: {exc}"
            return None
        return self._client

    def put(self, name: str, data: bytes, content_type: str = "application/octet-stream") -> str | None:
        """Store an object and return its key, or None if storage is unavailable."""
        client = self._connect()
        if client is None:
            return None
        import io

        try:
            client.put_object(
                settings.minio_bucket,
                name,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            return name
        except Exception:  # noqa: BLE001
            return None

    def get(self, name: str) -> bytes | None:
        client = self._connect()
        if client is None:
            return None
        try:
            response = client.get_object(settings.minio_bucket, name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception:  # noqa: BLE001
            return None


# Module-level instances. Constructing these does not connect -- the first
# actual operation does -- so importing this module is free even with every
# datastore down.
audit_store = AuditStore()
object_store = ObjectStore()
