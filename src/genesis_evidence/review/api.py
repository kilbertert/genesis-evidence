"""FastAPI workbench for autonomous and exception-based evidence review."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..core.store import Database, ObjectStore, PaperStore, ReviewStore
from ..products.catalog import ProductCatalogStore
from .service import (
    AUTONOMOUS_REVIEW_POLICY_VERSION,
    ClaimReviewInput,
    EvidenceProfileInput,
    EvidenceReviewService,
    PublicationRole,
    StudyDesign,
)


class AdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_codes: list[str] = Field(min_length=1, max_length=12)
    consistency_resolution: str | None = Field(default=None, max_length=5000)
    differences_confirmed: bool = False
    study_design: StudyDesign
    publication_role: PublicationRole
    identity_confirmed: bool


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    condition_code: str
    review_question: str = Field(min_length=1, max_length=2000)
    picots: dict[str, str]
    eligible_study_designs: list[str] = Field(min_length=1)
    inclusion_criteria: list[str] = Field(min_length=1)
    exclusion_reasons: list[str] = Field(min_length=1)
    required_search_streams: list[str] = Field(min_length=1)
    evidence_cutoff_date: str = Field(pattern=r"\d{4}-\d{2}-\d{2}")


class ScreeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    decision: str
    primary_exclusion_reason: str | None = None


class FullTextRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern=r"^(pending|retrieved|not_retrieved)$")
    reason: str | None = Field(default=None, max_length=2000)


class CardDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    condition_code: str
    version: str
    claim_ids: list[str] = Field(min_length=1)
    patient_body: str = Field(min_length=1, max_length=20_000)
    profile: EvidenceProfileInput


class CardTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str


class ProductRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_codes: list[str] = Field(min_length=1, max_length=12)
    nutrient: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    safety_message: str = Field(min_length=1, max_length=2000)
    disclaimer: str = Field(min_length=1, max_length=2000)
    evidence_links: list[str] = Field(min_length=1, max_length=20)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    priority: int = Field(default=0, ge=0)
    high_risk_marketing_claim: bool = False
    note: str = Field(min_length=1, max_length=2000)
    decision_ref: str = Field(min_length=1, max_length=500)


class ProductTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["blocked", "in_review", "published", "withdrawn"]
    note: str = Field(min_length=1, max_length=2000)
    decision_ref: str = Field(min_length=1, max_length=500)


class ProductMappingTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["published", "rejected", "needs_more_info"]
    note: str = Field(min_length=1, max_length=2000)
    decision_ref: str = Field(min_length=1, max_length=500)


def create_app(*, database_path: Path | str, api_key: str, reviewer_id: str) -> FastAPI:
    normalized_key = api_key.strip()
    normalized_reviewer = reviewer_id.strip()
    if len(normalized_key) < 24:
        raise ValueError("GENESIS_EVIDENCE_REVIEW_API_KEY must contain at least 24 characters")
    if not normalized_reviewer or len(normalized_reviewer) > 200:
        raise ValueError("GENESIS_EVIDENCE_REVIEWER_ID must identify the authenticated reviewer")
    database = Database(database_path)
    database.initialize()
    store = ReviewStore(database)
    product_store = ProductCatalogStore(database)
    papers_store = PaperStore(database)
    service = EvidenceReviewService(
        store,
        papers_store,
        ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
    )
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

    def principal(authorization: str = Header(default="")) -> str:
        scheme, _, token = authorization.partition(" ")
        if (
            scheme.casefold() != "bearer"
            or not token
            or not secrets.compare_digest(token, normalized_key)
        ):
            raise HTTPException(
                status_code=401,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return normalized_reviewer

    @app.exception_handler(ValueError)
    async def invalid_workflow_value(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return workbench

    @app.get("/api/review/me")
    def me(reviewer: str = Depends(principal)) -> dict[str, str]:
        return {"reviewer_id": reviewer}

    @app.get("/api/review/conditions", dependencies=[Depends(principal)])
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

    @app.get("/api/review/topics", dependencies=[Depends(principal)])
    def topics() -> list[dict[str, object]]:
        return papers_store.list_topics()

    @app.post("/api/review/topics")
    def create_topic(request: TopicRequest, reviewer: str = Depends(principal)) -> dict[str, str]:
        values = request.model_dump()
        for field in (
            "eligible_study_designs",
            "inclusion_criteria",
            "exclusion_reasons",
            "required_search_streams",
        ):
            values[field] = tuple(dict.fromkeys(values[field]))
        topic_id = papers_store.create_topic(reviewer=reviewer, **values)
        return {"id": topic_id, "status": "draft"}

    @app.post("/api/review/topics/{topic_id}/lock")
    def lock_topic(topic_id: str, reviewer: str = Depends(principal)) -> dict[str, str]:
        papers_store.lock_topic(topic_id, reviewer=reviewer)
        return {"id": topic_id, "status": "locked"}

    @app.get("/api/review/topics/{topic_id}/ledger", dependencies=[Depends(principal)])
    def topic_ledger(topic_id: str) -> list[dict[str, object]]:
        return papers_store.list_topic_ledger(topic_id)

    @app.post("/api/review/topics/{topic_id}/ledger/reconcile")
    def reconcile_topic_ledger(
        topic_id: str, reviewer: str = Depends(principal)
    ) -> dict[str, object]:
        return papers_store.reconcile_topic_ledger(topic_id, reviewer=reviewer)

    @app.get("/api/review/extraction-jobs", dependencies=[Depends(principal)])
    def extraction_jobs() -> list[dict[str, object]]:
        return papers_store.list_extraction_jobs()

    @app.post("/api/review/extraction-jobs/{job_id}/retry")
    def retry_extraction(job_id: str, reviewer: str = Depends(principal)) -> dict[str, str]:
        papers_store.retry_extraction(job_id, reviewer=reviewer)
        return {"id": job_id, "status": "queued"}

    @app.post("/api/review/topics/{topic_id}/runs/{run_id}/papers/{paper_id}/screen")
    def screen_topic_paper(
        topic_id: str,
        run_id: str,
        paper_id: str,
        request: ScreeningRequest,
        reviewer: str = Depends(principal),
    ) -> dict[str, str]:
        if run_id not in {row["run_id"] for row in papers_store.list_topic_ledger(topic_id)}:
            raise HTTPException(status_code=404, detail="topic collection record not found")
        papers_store.screen_collection_paper(
            run_id,
            paper_id,
            stage=request.stage,
            decision=request.decision,
            exclusion_reason=request.primary_exclusion_reason,
            reviewer=reviewer,
        )
        return {"status": request.decision}

    @app.post("/api/review/topics/{topic_id}/runs/{run_id}/papers/{paper_id}/full-text-retrieval")
    def record_full_text_retrieval(
        topic_id: str,
        run_id: str,
        paper_id: str,
        request: FullTextRetrievalRequest,
        reviewer: str = Depends(principal),
    ) -> dict[str, str]:
        if run_id not in {row["run_id"] for row in papers_store.list_topic_ledger(topic_id)}:
            raise HTTPException(status_code=404, detail="topic collection record not found")
        papers_store.record_full_text_retrieval(
            run_id,
            paper_id,
            status=request.status,
            reason=request.reason,
            reviewer=reviewer,
        )
        return {"status": request.status}

    @app.get("/api/review/papers", dependencies=[Depends(principal)])
    def papers() -> list[dict[str, object]]:
        return store.list_review_queue()

    @app.post("/api/review/papers/auto-review")
    def auto_review_all(reviewer: str = Depends(principal)) -> dict[str, object]:
        results = []
        for item in store.list_review_queue():
            paper_id = str(item["id"])
            try:
                result = service.auto_review_paper(paper_id, requested_by=reviewer)
            except (ValueError, RuntimeError) as exc:
                result = {
                    "status": "attention_required",
                    "stage": "workflow_error",
                    "reason": str(exc),
                }
                store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_review_attention_required",
                    actor="ai:orchestrator",
                    detail={
                        "policy_version": AUTONOMOUS_REVIEW_POLICY_VERSION,
                        "requested_by": reviewer,
                        **result,
                    },
                )
            results.append({"paper_id": paper_id, **result})
        return {
            "status": "completed",
            "processed": len(results),
            "attention_required": sum(
                result["status"] == "attention_required" for result in results
            ),
            "pending_extraction": sum(
                result["status"] in {"queued", "running"} for result in results
            ),
            "results": results,
        }

    @app.get("/api/review/papers/{paper_id}", dependencies=[Depends(principal)])
    def paper(paper_id: str) -> dict[str, object]:
        item = store.get_review_item(paper_id)
        if item is None:
            raise HTTPException(status_code=404, detail="paper not found")
        return item

    @app.post("/api/review/papers/{paper_id}/auto-review")
    def auto_review_paper(paper_id: str, reviewer: str = Depends(principal)) -> dict[str, object]:
        return service.auto_review_paper(paper_id, requested_by=reviewer)

    @app.post("/api/review/papers/{paper_id}/admit")
    def admit(
        paper_id: str, request: AdmissionRequest, reviewer: str = Depends(principal)
    ) -> dict[str, str]:
        service.admit_paper(
            paper_id,
            reviewer=reviewer,
            condition_codes=request.condition_codes,
            consistency_resolution=request.consistency_resolution,
            differences_confirmed=request.differences_confirmed,
            study_design=request.study_design,
            publication_role=request.publication_role,
            identity_confirmed=request.identity_confirmed,
        )
        return {"status": "internally_admitted"}

    @app.post("/api/review/papers/{paper_id}/reject")
    def reject(paper_id: str, reviewer: str = Depends(principal)) -> dict[str, str]:
        service.reject_paper(paper_id, reviewer=reviewer)
        return {"status": "rejected"}

    @app.post("/api/review/claims/{claim_id}")
    def review_claim(
        claim_id: str, request: ClaimReviewInput, reviewer: str = Depends(principal)
    ) -> dict[str, str]:
        service.review_claim(
            claim_id,
            reviewer=reviewer,
            review=request,
        )
        return {"status": request.decision}

    @app.get("/api/review/cards", dependencies=[Depends(principal)])
    def cards() -> list[dict[str, object]]:
        return store.list_cards()

    @app.get("/api/review/coverage-matrix", dependencies=[Depends(principal)])
    def coverage_matrix() -> list[dict[str, object]]:
        return store.list_coverage_matrix()

    @app.get("/api/review/disease-papers", dependencies=[Depends(principal)])
    def disease_papers() -> list[dict[str, object]]:
        return store.list_disease_papers()

    @app.get("/api/review/products", dependencies=[Depends(principal)])
    def products() -> list[dict[str, object]]:
        return product_store.list_review_products()

    @app.put("/api/review/products/{product_id}/recommendation")
    def submit_product_recommendation(
        product_id: str,
        request: ProductRecommendationRequest,
        reviewer: str = Depends(principal),
    ) -> dict[str, object]:
        values = request.model_dump()
        note = values.pop("note")
        decision_ref = values.pop("decision_ref")
        condition_codes = values.pop("condition_codes")
        return product_store.submit_recommendation(
            product_id,
            condition_codes=condition_codes,
            recommendation=values,
            actor=reviewer,
            note=note,
            decision_ref=decision_ref,
        )

    @app.post("/api/review/products/{product_id}/transition")
    def transition_product_recommendation(
        product_id: str,
        request: ProductTransitionRequest,
        reviewer: str = Depends(principal),
    ) -> dict[str, object]:
        return product_store.transition_recommendation(
            product_id,
            target=request.target,
            actor=reviewer,
            note=request.note,
            decision_ref=request.decision_ref,
        )

    @app.get("/api/review/product-mappings", dependencies=[Depends(principal)])
    def product_mappings() -> list[dict[str, object]]:
        return product_store.list_mapping_drafts()

    @app.post("/api/review/product-mappings/{draft_id}/transition")
    def transition_product_mapping(
        draft_id: str,
        request: ProductMappingTransitionRequest,
        reviewer: str = Depends(principal),
    ) -> dict[str, object]:
        return product_store.transition_mapping_draft(
            draft_id,
            target=request.target,
            actor=reviewer,
            note=request.note,
            decision_ref=request.decision_ref,
        )

    @app.post("/api/review/cards")
    def create_card(
        request: CardDraftRequest, reviewer: str = Depends(principal)
    ) -> dict[str, str]:
        card_id = service.create_card_draft(reviewer=reviewer, **request.model_dump())
        return {"id": card_id, "status": "draft"}

    @app.post("/api/review/cards/{card_id}/transition")
    def transition_card(
        card_id: str, request: CardTransitionRequest, reviewer: str = Depends(principal)
    ) -> dict[str, str]:
        service.transition_card(
            card_id,
            reviewer=reviewer,
            target=request.target,
        )
        return {"id": card_id, "status": request.target}

    return app


def main() -> None:
    app = create_app(
        database_path=Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")),
        api_key=_review_api_key(),
        reviewer_id=os.getenv("GENESIS_EVIDENCE_REVIEWER_ID", ""),
    )
    uvicorn.run(
        app,
        host=os.getenv("GENESIS_EVIDENCE_REVIEW_HOST", "127.0.0.1"),
        port=int(os.getenv("GENESIS_EVIDENCE_REVIEW_PORT", "8090")),
    )


def _review_api_key() -> str:
    return os.getenv("GENESIS_EVIDENCE_REVIEW_API_KEY") or os.getenv("GENESIS_REVIEW_API_KEY", "")


if __name__ == "__main__":
    main()
