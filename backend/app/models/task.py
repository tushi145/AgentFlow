from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (JSON, Boolean, Float, ForeignKey, Index, Integer, String,
                        Text, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, new_uuid, UTCDateTime


class TaskStatus:
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"

    ACTIVE = frozenset({QUEUED, PLANNING, AWAITING_APPROVAL, RUNNING})
    TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, REJECTED, INTERRUPTED})


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # live | demo
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.QUEUED, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), default="parallel", nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(8), default="none", nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    agent_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(String(36))
    # Execution bookkeeping that must survive approval pauses (e.g. active seconds used).
    # server_default matches migration 0002, so new and migrated databases have the same schema.
    runtime_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False,
                                                          server_default=text("'{}'"))

    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    iterations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    subtasks: Mapped[list[Subtask]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Subtask.id")


class Subtask(Base):
    __tablename__ = "subtasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    plan_key: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_key: Mapped[str] = mapped_column(String(32), nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    queries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    task: Mapped[Task] = relationship(back_populates="subtasks")
