"""FastAPI portal for report upload, confirmation, and reviewed-card matching."""

from __future__ import annotations

import hmac
import os
import time
import uuid
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..core.contracts import EvidenceMatchRequest
from ..core.metrics import METRIC_LABELS
from ..core.store import (
    ConfirmationInput,
    Database,
    ObjectStore,
    ReportAccessDenied,
    ReportStore,
)
from ..reports.extraction import (
    ReportExtractionError,
    ReportExtractionUnavailable,
    ReportFile,
)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[ConfirmationInput] = Field(max_length=600)


def create_app(
    *,
    database_path: Path | str,
    object_path: Path | str,
    max_file_bytes: int = 20 * 1024 * 1024,
    max_files: int = 20,
    max_total_bytes: int = 50 * 1024 * 1024,
    upload_limit: int = 10,
    upload_window_seconds: int = 3600,
    evidence_api_key: str = "",
) -> FastAPI:
    database = Database(database_path)
    database.initialize()
    store = ReportStore(database, ObjectStore(object_path))
    portal = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    app = FastAPI(title="Genesis Evidence Portal", docs_url=None, redoc_url=None)
    upload_times: deque[float] = deque()
    upload_lock = Lock()

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

    @app.exception_handler(ReportAccessDenied)
    async def access_denied(_, __: ReportAccessDenied) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "report not found"})

    @app.exception_handler(ReportExtractionUnavailable)
    async def extraction_unavailable(_, exc: ReportExtractionUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ReportExtractionError)
    @app.exception_handler(ValueError)
    async def invalid_report_value(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return portal

    @app.get("/api/metrics")
    def metrics() -> list[dict[str, str]]:
        return [{"code": code, "label": label} for code, label in METRIC_LABELS.items()]

    @app.post("/api/evidence/matches")
    def match_evidence(
        request: EvidenceMatchRequest,
        x_genesis_evidence_key: str = Header(default=""),
        x_correlation_id: str = Header(default=""),
    ) -> dict[str, object]:
        configured_key = evidence_api_key.strip()
        if configured_key and not hmac.compare_digest(configured_key, x_genesis_evidence_key):
            raise HTTPException(status_code=401, detail="invalid evidence API key")
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
        return store.match_published_cards(
            request.observations,
            correlation_id=correlation_id,
        )

    @app.post("/api/reports", status_code=202)
    async def upload(files: Annotated[list[UploadFile], File()]) -> dict[str, object]:
        now = time.monotonic()
        with upload_lock:
            while upload_times and upload_times[0] <= now - max(1, upload_window_seconds):
                upload_times.popleft()
            if len(upload_times) >= max(1, upload_limit):
                raise HTTPException(status_code=429, detail="报告处理请求较多，请稍后再试。")
            upload_times.append(now)
        if not files or len(files) > max_files:
            raise ValueError(f"一次最多上传 {max_files} 个报告文件。")
        report_files = []
        total = 0
        for index, upload_file in enumerate(files, start=1):
            content = await upload_file.read(max_file_bytes + 1)
            if len(content) > max_file_bytes:
                raise ValueError(f"第 {index} 个报告文件超过大小限制。")
            total += len(content)
            if total > max_total_bytes:
                raise ValueError("报告文件总大小超过限制。")
            report_files.append(
                ReportFile(
                    content=content,
                    filename=upload_file.filename or f"report-{index}",
                    media_type=upload_file.content_type or "",
                )
            )
        handle = await run_in_threadpool(store.create, tuple(report_files))
        report = await run_in_threadpool(store.get, handle.report_id, handle.access_token)
        return {**report, "report_id": handle.report_id, "access_token": handle.access_token}

    @app.get("/api/reports/{report_id}")
    def report(
        report_id: str,
        x_report_token: str = Header(default=""),
    ) -> dict[str, object]:
        return store.get(report_id, x_report_token)

    @app.post("/api/reports/{report_id}/confirm")
    def confirm(
        report_id: str,
        request: ConfirmationRequest,
        x_report_token: str = Header(default=""),
    ) -> dict[str, object]:
        store.confirm(report_id, x_report_token, request.observations)
        return store.get(report_id, x_report_token)

    @app.post("/api/reports/{report_id}/assess")
    def assess(
        report_id: str,
        x_report_token: str = Header(default=""),
    ) -> dict[str, object]:
        return store.assess(report_id, x_report_token)

    @app.get("/api/reports/{report_id}/assessment")
    def assessment(
        report_id: str,
        x_report_token: str = Header(default=""),
    ) -> dict[str, object]:
        return store.get_assessment(report_id, x_report_token)

    return app


def main() -> None:
    max_file_bytes = int(os.getenv("GENESIS_EVIDENCE_REPORT_MAX_BYTES", 20 * 1024 * 1024))
    app = create_app(
        database_path=Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")),
        object_path=Path(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
        max_file_bytes=max_file_bytes,
        upload_limit=int(os.getenv("GENESIS_EVIDENCE_UPLOAD_LIMIT", "10")),
        upload_window_seconds=int(os.getenv("GENESIS_EVIDENCE_UPLOAD_WINDOW_SECONDS", "3600")),
        evidence_api_key=os.getenv("GENESIS_EVIDENCE_API_KEY", ""),
    )
    uvicorn.run(
        app,
        host=os.getenv("GENESIS_EVIDENCE_PORTAL_HOST", "127.0.0.1"),
        port=int(os.getenv("GENESIS_EVIDENCE_PORTAL_PORT", "8091")),
    )


if __name__ == "__main__":
    main()
