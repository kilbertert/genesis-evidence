"""FastAPI portal for report upload, confirmation, and reviewed-card matching."""

from __future__ import annotations

import hmac
import os
import uuid

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..core.contracts import EvidenceMatchRequest, EvidenceMatchResponse
from ..core.metrics import METRIC_LABELS
from ..core.store import Database, ObjectStore, ReportStore


def create_app(
    *,
    database_path: os.PathLike[str] | str,
    object_path: os.PathLike[str] | str,
    evidence_api_key: str,
) -> FastAPI:
    configured_key = evidence_api_key.strip()
    if not configured_key:
        raise ValueError("GENESIS_EVIDENCE_API_KEY is required")
    if len(configured_key) < 24:
        raise ValueError("GENESIS_EVIDENCE_API_KEY must be at least 24 characters")
    database = Database(database_path)
    database.initialize()
    store = ReportStore(database, ObjectStore(object_path))
    app = FastAPI(title="Genesis Evidence API", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def require_api_key(x_genesis_evidence_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(configured_key, x_genesis_evidence_key):
            raise HTTPException(status_code=401, detail="invalid evidence API key")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def index() -> dict[str, str]:
        return {"service": "genesis-evidence-api", "scope": "published-evidence-read-only"}

    @app.get("/api/metrics")
    def metrics(x_genesis_evidence_key: str = Header(default="")) -> list[dict[str, str]]:
        require_api_key(x_genesis_evidence_key)
        return [{"code": code, "label": label} for code, label in METRIC_LABELS.items()]

    @app.post(
        "/api/evidence/matches",
        response_model=EvidenceMatchResponse,
        response_model_exclude_none=True,
    )
    def match_evidence(
        request: EvidenceMatchRequest,
        x_genesis_evidence_key: str = Header(default=""),
        x_correlation_id: str = Header(default=""),
    ) -> EvidenceMatchResponse:
        require_api_key(x_genesis_evidence_key)
        correlation_id = x_correlation_id.strip()
        if correlation_id:
            try:
                correlation_id = str(uuid.UUID(correlation_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="correlation ID must be a UUID",
                ) from exc
        else:
            correlation_id = str(uuid.uuid4())
        return EvidenceMatchResponse.model_validate(
            store.match_published_cards(
                request.observations,
                correlation_id=correlation_id,
            )
        )

    return app


def main() -> None:
    app = create_app(
        database_path=os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"),
        object_path=os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects"),
        evidence_api_key=os.getenv("GENESIS_EVIDENCE_API_KEY", ""),
    )
    uvicorn.run(
        app,
        host=os.getenv("GENESIS_EVIDENCE_PORTAL_HOST", "127.0.0.1"),
        port=int(os.getenv("GENESIS_EVIDENCE_PORTAL_PORT", "8091")),
    )


if __name__ == "__main__":
    main()
