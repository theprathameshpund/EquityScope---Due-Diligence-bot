"""API request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateReportRequest(BaseModel):
    company: str = Field(min_length=1, max_length=120)
    focus: str = Field(default="", max_length=300)


class CreateReportResponse(BaseModel):
    run_id: str
    status: str = "queued"


class ReportStatusResponse(BaseModel):
    run_id: str
    status: str
    company: str = ""
    report: dict[str, Any] | None = None
    markdown: str | None = None
    evidence: list[dict[str, Any]] | None = None
    error: str | None = None


class RunSummary(BaseModel):
    run_id: str
    status: str
    company: str = ""
    updated_at: str = ""
    cost_usd: float | None = None
    tokens_used: int | None = None


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class HealthResponse(BaseModel):
    status: str
    qdrant: bool
    postgres: bool
    redis: bool
