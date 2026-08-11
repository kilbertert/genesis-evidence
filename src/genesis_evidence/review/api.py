"""FastAPI workbench for the one-reviewer evidence workflow."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..core.store import Database, ReviewStore
from .service import ClaimReviewInput, EvidenceReviewService


class AdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=200)
    condition_codes: list[str] = Field(min_length=1, max_length=12)


class ReviewerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=200)


class ClaimReviewRequest(ClaimReviewInput):
    reviewer: str = Field(min_length=1, max_length=200)


class CardDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    version: str
    claim_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=200)
    patient_body: str = Field(min_length=1, max_length=20_000)


class CardTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=200)
    target: str


def create_app(*, database_path: Path | str, api_key: str) -> FastAPI:
    normalized_key = api_key.strip()
    if len(normalized_key) < 24:
        raise ValueError("GENESIS_EVIDENCE_REVIEW_API_KEY must contain at least 24 characters")
    database = Database(database_path)
    database.initialize()
    store = ReviewStore(database)
    service = EvidenceReviewService(store)
    workbench = Path(__file__).with_name("workbench.html").read_text(encoding="utf-8")
    app = FastAPI(title="Genesis Evidence Review", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path == "/" or request.url.path.startswith("/api/review/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_key(x_review_key: str = Header(default="")) -> None:
        if not secrets.compare_digest(x_review_key, normalized_key):
            raise HTTPException(status_code=401, detail="invalid review key")

    @app.exception_handler(ValueError)
    async def invalid_workflow_value(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return workbench

    @app.get("/api/review/conditions", dependencies=[Depends(require_key)])
    def conditions() -> list[dict[str, object]]:
        return [
            {
                "code": item.code,
                "name": item.name,
                "department": item.department,
                "recheck_direction": item.recheck_direction,
            }
            for item in database.list_conditions()
        ]

    @app.get("/api/review/papers", dependencies=[Depends(require_key)])
    def papers() -> list[dict[str, object]]:
        return store.list_review_queue()

    @app.get("/api/review/papers/{paper_id}", dependencies=[Depends(require_key)])
    def paper(paper_id: str) -> dict[str, object]:
        item = store.get_review_item(paper_id)
        if item is None:
            raise HTTPException(status_code=404, detail="paper not found")
        return item

    @app.post("/api/review/papers/{paper_id}/admit", dependencies=[Depends(require_key)])
    def admit(paper_id: str, request: AdmissionRequest) -> dict[str, str]:
        service.admit_paper(
            paper_id,
            reviewer=request.reviewer,
            condition_codes=request.condition_codes,
        )
        return {"status": "internally_admitted"}

    @app.post("/api/review/papers/{paper_id}/reject", dependencies=[Depends(require_key)])
    def reject(paper_id: str, request: ReviewerRequest) -> dict[str, str]:
        service.reject_paper(paper_id, reviewer=request.reviewer)
        return {"status": "rejected"}

    @app.post("/api/review/claims/{claim_id}", dependencies=[Depends(require_key)])
    def review_claim(claim_id: str, request: ClaimReviewRequest) -> dict[str, str]:
        values = request.model_dump(exclude={"reviewer"})
        service.review_claim(
            claim_id,
            reviewer=request.reviewer,
            review=ClaimReviewInput.model_validate(values),
        )
        return {"status": request.decision}

    @app.get("/api/review/cards", dependencies=[Depends(require_key)])
    def cards() -> list[dict[str, object]]:
        return store.list_cards()

    @app.post("/api/review/cards", dependencies=[Depends(require_key)])
    def create_card(request: CardDraftRequest) -> dict[str, str]:
        card_id = service.create_card_draft(**request.model_dump())
        return {"id": card_id, "status": "draft"}

    @app.post("/api/review/cards/{card_id}/transition", dependencies=[Depends(require_key)])
    def transition_card(card_id: str, request: CardTransitionRequest) -> dict[str, str]:
        service.transition_card(
            card_id,
            reviewer=request.reviewer,
            target=request.target,
        )
        return {"id": card_id, "status": request.target}

    return app


def main() -> None:
    app = create_app(
        database_path=Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")),
        api_key=os.getenv("GENESIS_EVIDENCE_REVIEW_API_KEY", ""),
    )
    uvicorn.run(
        app,
        host=os.getenv("GENESIS_EVIDENCE_REVIEW_HOST", "127.0.0.1"),
        port=int(os.getenv("GENESIS_EVIDENCE_REVIEW_PORT", "8090")),
    )


if __name__ == "__main__":
    main()
