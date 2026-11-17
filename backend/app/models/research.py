from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, utcnow


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (UniqueConstraint("task_id", "url", name="uq_source_task_url"),
                      UniqueConstraint("task_id", "ref", name="uq_source_task_ref"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(16), nullable=False)  # "S1", "S2", ...
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    search_query: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    injection_suspected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    injection_rules: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerificationResult(TimestampMixin, Base):
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supporting_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    conflicting_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
