"""Database models for the Voice Summary application."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Organization(Base):
    """A tenant. Every user and (eventually) every lead belongs to exactly one org."""

    __tablename__ = "organizations"

    id = Column(String(255), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)

    # Organisation Knowledge Base — grounds every AI decision (scoring relevance,
    # follow-up tone, script generation) in what this specific business actually
    # sells. Populated by the founder onboarding wizard; all nullable since no
    # existing org has these yet.
    industry = Column(String(100), nullable=True)
    website_url = Column(String(500), nullable=True)
    services = Column(JSON, nullable=True)          # [str, ...]
    pricing_min = Column(Integer, nullable=True)
    pricing_max = Column(Integer, nullable=True)
    target_audience = Column(Text, nullable=True)
    competitors = Column(JSON, nullable=True)        # [str, ...]
    brand_voice = Column(String(50), nullable=True)
    languages = Column(JSON, nullable=True)          # [str, ...]
    usps = Column(JSON, nullable=True)               # [str, ...]

    # Revenue goal tracking — drives the Monthly Goal gauge and the revenue
    # target line on the founder dashboard. Nullable: an org with no target set
    # simply gets no target-based breakdown (never a fabricated number).
    monthly_revenue_target = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Organization(id='{self.id}', name='{self.name}')>"


class User(Base):
    """A person who can log in. `role` gates which portal(s) they can reach."""

    __tablename__ = "users"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=False, index=True)

    email = Column(String(255), nullable=False, unique=True, index=True)
    phone = Column(String(20), nullable=True, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="founder")  # founder / ad_manager / telecaller
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="users")

    def __repr__(self):
        return f"<User(id='{self.id}', email='{self.email}', role='{self.role}')>"


class AudioCall(Base):
    """Model for storing audio call information."""

    __tablename__ = "audio_calls"

    call_id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=True, index=True)
    telecaller_id = Column(String(255), ForeignKey("users.id"), nullable=True, index=True)
    timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    transcript = Column(JSON, nullable=False)
    audio_file_url = Column(Text, nullable=False)
    processed_data = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    lead_analysis = relationship(
        "LeadAnalysis", back_populates="call", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<AudioCall(call_id='{self.call_id}', timestamp='{self.timestamp}')>"


class ProcessingJob(Base):
    """
    Durable record of the upload→transcribe→analyse→memory pipeline for one call.

    Persisted so a crash/restart/deploy never SILENTLY drops an in-flight call — the #1
    production risk the council flagged. Locally: stuck jobs are re-dispatched on startup.
    On AWS this table becomes the source of truth behind a real worker/queue (SQS/Celery),
    with no change to the rest of the code.
    """

    __tablename__ = "processing_jobs"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=True, index=True)
    call_id = Column(String(255), nullable=False, index=True)
    audio_path = Column(Text, nullable=True)
    stage = Column(String(20), default="queued")                 # queued/transcribe/analyse/memory/done
    status = Column(String(20), default="queued", index=True)    # queued/running/done/failed
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<ProcessingJob(call_id='{self.call_id}', stage='{self.stage}', status='{self.status}')>"




class LeadAnalysis(Base):
    """Full post-call AI analysis: BANT, sentiment arc, intent tags, lead verdict, next action."""

    __tablename__ = "lead_analysis"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=True, index=True)
    call_id = Column(String(255), ForeignKey("audio_calls.call_id"), nullable=False, unique=True, index=True)

    # Core analysis outputs
    bant_score = Column(Float, nullable=True)
    bant_breakdown = Column(JSON, nullable=True)       # {budget, authority, need, timeline} each with score+reason
    lead_verdict = Column(String(20), nullable=True)   # Hot / Warm / Cold / Junk
    lead_verdict_reason = Column(Text, nullable=True)

    # Per-turn data
    sentiment_arc = Column(JSON, nullable=True)        # [{turn, role, score, label}]
    intent_tags = Column(JSON, nullable=True)          # [{turn, role, intent}]

    # Extracted entities
    entities = Column(JSON, nullable=True)             # {budget, authority, need, timeline, objections, ...}

    # Summary and action
    call_summary = Column(JSON, nullable=True)         # {headline, key_moments, objections_raised, commitments_made, overall_tone}
    key_points = Column(JSON, nullable=True)           # ["bullet 1", "bullet 2", ...]  (Figma AI Summary)
    next_steps = Column(JSON, nullable=True)           # [{step, text, action_type, action_label}]
    next_action = Column(JSON, nullable=True)          # {recommended_action, follow_up_script, channel, urgency}
    agent_debrief = Column(JSON, nullable=True)        # {strengths, improvements, 5 scores, total_score}

    # Status
    status = Column(String(20), default="pending", index=True)   # pending / processing / completed / not_relevant / failed
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    call = relationship("AudioCall", back_populates="lead_analysis")

    def __repr__(self):
        return f"<LeadAnalysis(call_id='{self.call_id}', verdict='{self.lead_verdict}')>"


class Lead(Base):
    """
    Thin lead store so a saved lead appears in the inbox immediately (as 'New'),
    before any call is made. Enriched by AI once a call is analysed.

    BACKEND (Module 2) extends this with org_id, assigned_to, tags, notes, etc.
    `contact_key` is the join key to calls/memory (= phone in production; a name
    slug today to match the call_id convention).
    """

    __tablename__ = "leads"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=True, index=True)
    assigned_to = Column(String(255), ForeignKey("users.id"), nullable=True, index=True)
    contact_key = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=True)
    phone = Column(String(40), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)
    status = Column(String(30), default="new")  # new/contacted/qualified/converted/lost
    pipeline_stage = Column(String(30), nullable=False, default="New", index=True)
    # kanban board position: New/Assigned/Contacted/Interested/Proposal Sent/
    # Negotiation/Closed Won/Closed Lost/Junk — distinct from `status` above,
    # which is the coarser AI-derived verdict category used by dedupe/inbox.

    # Revenue — set when a founder/telecaller moves a lead to "Closed Won" (see
    # the kanban stage-update endpoint). closed_at is cleared if the lead is
    # ever moved out of Closed Won again, so revenue reports never double-count
    # a lead that was reopened and hasn't re-closed.
    deal_value = Column(Integer, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Lead(contact_key='{self.contact_key}', name='{self.name}', status='{self.status}')>"


class MemoryBubble(Base):
    """
    Per-contact cumulative memory — the LeadPilot moat.

    Keyed by `contact_key` (phone number in production; a name-slug today).
    Rebuilt every time a new call for this contact finishes lead analysis.
    """

    __tablename__ = "memory_bubbles"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=True, index=True)
    contact_key = Column(String(255), nullable=False, unique=True, index=True)  # phone in prod

    total_calls = Column(Integer, default=0)
    last_call_id = Column(String(255), nullable=True)
    last_call_at = Column(DateTime(timezone=True), nullable=True)

    facts = Column(JSON, nullable=True)               # [{category, text, call_index, confidence}]
    cumulative_bant = Column(JSON, nullable=True)     # {budget, authority, need, timeline}
    running_verdict = Column(String(20), nullable=True)   # Hot/Warm/Cold/Junk
    sentiment_trend = Column(String(20), nullable=True)   # improving/declining/flat/mixed
    open_objections = Column(JSON, nullable=True)
    pending_commitments = Column(JSON, nullable=True)
    next_call_strategy = Column(Text, nullable=True)
    headline = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<MemoryBubble(contact_key='{self.contact_key}', calls={self.total_calls})>"


class Attendance(Base):
    """Telecaller check-in/check-out attendance — timestamp only (no geolocation, no
    photo; that's an explicit product decision). One row per user per calendar day."""

    __tablename__ = "attendance"

    id = Column(String(255), primary_key=True, index=True)
    org_id = Column(String(255), ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(String(255), ForeignKey("users.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    check_in_at = Column(DateTime(timezone=True), nullable=True)
    check_out_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_attendance_user_date"),)

    def __repr__(self):
        return f"<Attendance(user_id='{self.user_id}', date='{self.date}')>"






