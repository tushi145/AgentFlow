from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utcnow, UTCDateTime


class AgentConfig(Base):
    """User-editable configuration of one specialised agent."""

    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # NULL provider/model => use DEFAULT_LLM_PROVIDER / <PROVIDER>_DEFAULT_MODEL from env.
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    effort: Mapped[str | None] = mapped_column(String(16))
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=8000, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow,
                                                 onupdate=utcnow, nullable=False)
