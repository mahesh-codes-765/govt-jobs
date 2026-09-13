from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base

class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    listing_url: Mapped[str] = mapped_column(Text)
    adapter: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Recruitment(Base):
    __tablename__ = "recruitments"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    recruitment_key: Mapped[str] = mapped_column(String(180), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    notification_number: Mapped[str | None] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="discovered")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    source: Mapped[Source] = relationship()
    documents: Mapped[list["DocumentVersion"]] = relationship(back_populates="recruitment", cascade="all, delete-orphan")

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    recruitment_id: Mapped[int | None] = mapped_column(ForeignKey("recruitments.id"), index=True)
    notification_number: Mapped[str | None] = mapped_column(String(120), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(50), default="notification")
    official_url: Mapped[str] = mapped_column(Text, unique=True)
    application_start: Mapped[datetime | None] = mapped_column(DateTime)
    application_end: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(40), default="discovered")
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    source: Mapped[Source] = relationship()
    recruitment: Mapped[Recruitment | None] = relationship()
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="notification", cascade="all, delete-orphan")

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("notification_id", "sha256", name="uq_notification_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id"), index=True)
    recruitment_id: Mapped[int | None] = mapped_column(ForeignKey("recruitments.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    local_pdf_path: Mapped[str] = mapped_column(Text)
    text_path: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_json: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(String(40), default="downloaded")
    classification_label: Mapped[str | None] = mapped_column(String(40))
    classification_reasons: Mapped[str | None] = mapped_column(Text)
    # pending_review | approved | rejected | needs_review | skipped_non_recruitment
    review_status: Mapped[str] = mapped_column(String(40), default="skipped_non_recruitment", index=True)
    review_verdict_json: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notification: Mapped[Notification] = relationship(back_populates="versions")
    recruitment: Mapped[Recruitment | None] = relationship()
    review_decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="document_version", cascade="all, delete-orphan")

class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(160), index=True)
    value_json: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(40))

class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    method: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ReviewDecision(Base):
    """Ledger of every approve/reject action taken on a DocumentVersion,
    whether by a human (via Telegram) or the automated verdict. Never
    overwritten — a document can be re-reviewed, but past decisions stay."""
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    decision: Mapped[str] = mapped_column(String(40))  # approved | rejected | auto_flagged
    decided_by: Mapped[str] = mapped_column(String(80))  # telegram user id, or "auto"
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    document_version: Mapped["DocumentVersion"] = relationship(back_populates="review_decisions")

class Lead(Base):
    """A consented candidate lead captured from the public eligibility
    checker — the actual revenue mechanism. Sold to coaching centres,
    filtered by district/category/qualification/exam."""
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_number: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(80), index=True)
    category: Mapped[str | None] = mapped_column(String(20), index=True)
    qualification_level: Mapped[str | None] = mapped_column(String(40), index=True)
    qualification_discipline: Mapped[str | None] = mapped_column(String(120))
    dob: Mapped[str | None] = mapped_column(String(20))
    gender: Mapped[str | None] = mapped_column(String(20))
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id"), index=True)
    notification_number: Mapped[str | None] = mapped_column(String(120))
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40), default="eligibility_checker")
    sold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class EventLog(Base):
    """Unified admin timeline: what happened, and exactly when. Every
    crawl start/finish, per-document outcome, LLM call, review decision
    and Telegram push lands here so the admin page can answer 'when did
    this data come in, and what came in' without grepping log files."""
    __tablename__ = "event_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")  # info | warn | error
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str | None] = mapped_column(String(40))
    notification_number: Mapped[str | None] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str | None] = mapped_column(Text)
