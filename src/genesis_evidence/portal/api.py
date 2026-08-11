"""FastAPI portal for report upload, confirmation, and reviewed-card matching."""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..core.store import (
    ConfirmationInput,
    Database,
    ObjectStore,
    ReportAccessDenied,
    ReportStore,
)
from ..reports.extraction import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_REPORT_MODEL,
    HealthReportExtractor,
    ReportExtractionError,
    ReportExtractionUnavailable,
    ReportFile,
)

METRIC_LABELS = {
    "systolic_blood_pressure": "收缩压",
    "diastolic_blood_pressure": "舒张压",
    "fasting_glucose": "空腹血糖",
    "hba1c": "糖化血红蛋白",
    "triglycerides": "甘油三酯",
    "hdl_c": "高密度脂蛋白胆固醇",
    "ldl_c": "低密度脂蛋白胆固醇",
    "total_cholesterol": "总胆固醇",
    "alt": "丙氨酸氨基转移酶",
    "ast": "天门冬氨酸氨基转移酶",
    "ggt": "γ-谷氨酰转移酶",
    "uric_acid": "尿酸",
    "egfr": "估算肾小球滤过率",
    "creatinine": "肌酐",
    "uacr": "尿白蛋白肌酐比",
    "hemoglobin": "血红蛋白",
    "mcv": "平均红细胞体积",
    "ferritin": "铁蛋白",
    "tsat": "转铁蛋白饱和度",
    "25_oh_vitamin_d": "25-羟维生素 D",
    "bone_density_t_score": "骨密度 T 值",
    "calcium": "钙",
    "alp": "碱性磷酸酶",
    "grip_strength": "握力",
    "walking_speed": "步速",
    "muscle_mass": "肌肉量",
    "albumin": "白蛋白",
    "bmi": "体重指数",
    "prealbumin": "前白蛋白",
}


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[ConfirmationInput] = Field(min_length=1, max_length=600)


def create_app(
    *,
    database_path: Path | str,
    object_path: Path | str,
    extractor: HealthReportExtractor,
    max_file_bytes: int = 20 * 1024 * 1024,
    max_files: int = 20,
    max_total_bytes: int = 50 * 1024 * 1024,
    upload_limit: int = 10,
    upload_window_seconds: int = 3600,
    max_concurrent_extractions: int = 2,
) -> FastAPI:
    database = Database(database_path)
    database.initialize()
    store = ReportStore(database, ObjectStore(object_path))
    portal = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    app = FastAPI(title="Genesis Evidence Portal", docs_url=None, redoc_url=None)
    upload_times: deque[float] = deque()
    upload_lock = Lock()
    extraction_slots = asyncio.Semaphore(max(1, max_concurrent_extractions))

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

    @app.post("/api/reports")
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
        async with extraction_slots:
            extracted = await extractor.extract_files(tuple(report_files))
        handle = await run_in_threadpool(store.create, tuple(report_files))
        await run_in_threadpool(store.save_extraction, handle.report_id, extracted)
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
        max_concurrent_extractions=int(
            os.getenv("GENESIS_EVIDENCE_MAX_CONCURRENT_EXTRACTIONS", "2")
        ),
        extractor=HealthReportExtractor(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            responses_url=os.getenv("OPENAI_RESPONSES_URL", ""),
            model=os.getenv("OPENAI_REPORT_MODEL", DEFAULT_REPORT_MODEL),
            max_bytes=max_file_bytes,
        ),
    )
    uvicorn.run(
        app,
        host=os.getenv("GENESIS_EVIDENCE_PORTAL_HOST", "127.0.0.1"),
        port=int(os.getenv("GENESIS_EVIDENCE_PORTAL_PORT", "8091")),
    )


if __name__ == "__main__":
    main()
