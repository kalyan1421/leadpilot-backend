-- Manual schema catch-up for the REAL LeadPilot Supabase project (ref
-- otbdjrexiscbxqlcnnvx). Not reachable via this machine's Supabase MCP
-- connector (different account) — see the leadpilot-supabase-flutter-plan
-- memory note. Paste this whole file into that project's SQL Editor and run
-- it once.
--
-- Render's Dockerfile boots with `uvicorn` only (not `alembic upgrade`) — see
-- commit f509bcc — so Base.metadata.create_all() auto-creates brand-new
-- tables on deploy, but NEVER adds columns to tables that already exist.
-- Every statement below is written to be safely re-runnable (IF NOT EXISTS
-- everywhere), so it doesn't matter which of this is already applied on
-- Supabase — anything already there is a no-op, and nothing here can error
-- out mid-script and abort the rest.
--
-- Covers alembic revisions a1b2c3d4e5f6 through l5e6f7g8h9i0 (the full
-- multi-tenant/team/attendance/revenue/password-reset feature set plus the
-- later org-logo/address, discount, follow-ups, relevance-reason and
-- content-hash additions). Kept in sync with app/models.py, which is what
-- Base.metadata.create_all() actually builds. Do NOT run this against
-- agntruuowrkfqqsdzjvd ("leadpilot-backend") — that's a mistakenly-provisioned
-- duplicate project on the wrong account, intentionally left paused.

-- ── organizations ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_organizations_id ON organizations (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_organizations_slug ON organizations (slug);

ALTER TABLE organizations ADD COLUMN IF NOT EXISTS industry VARCHAR(100);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS website_url VARCHAR(500);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS services JSON;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS pricing_min INTEGER;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS pricing_max INTEGER;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS target_audience TEXT;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS competitors JSON;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS brand_voice VARCHAR(50);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS languages JSON;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS usps JSON;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS monthly_revenue_target INTEGER;
-- org logo + address (rev c9d0e1f2a3b4) — shown on the telecaller Profile screen.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS address TEXT;

-- ── users ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,
    org_id VARCHAR(255) NOT NULL REFERENCES organizations(id),
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(30) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_id ON users (id);
CREATE INDEX IF NOT EXISTS ix_users_org_id ON users (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);

ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone);
-- must_reset_password: the newest column (this session's password-reset feature).
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_reset_password BOOLEAN NOT NULL DEFAULT false;

-- ── leads (pre-existing table — org/team/pipeline/revenue columns added) ──
ALTER TABLE leads ADD COLUMN IF NOT EXISTS org_id VARCHAR(255) REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS ix_leads_org_id ON leads (org_id);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(255) REFERENCES users(id);
CREATE INDEX IF NOT EXISTS ix_leads_assigned_to ON leads (assigned_to);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS pipeline_stage VARCHAR(30) NOT NULL DEFAULT 'New';
CREATE INDEX IF NOT EXISTS ix_leads_pipeline_stage ON leads (pipeline_stage);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS deal_value INTEGER;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_leads_closed_at ON leads (closed_at);
-- discount/margin tracking (rev g7a8b9c0d1e2)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS discount_pct DOUBLE PRECISION;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS list_price INTEGER;
-- org-scoped uniqueness on contact_key (rev a7b8c9d0e1f2). ADD CONSTRAINT has no
-- IF NOT EXISTS, so guard on pg_constraint to stay re-runnable.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_leads_org_contact_key') THEN
        ALTER TABLE leads ADD CONSTRAINT uq_leads_org_contact_key UNIQUE (org_id, contact_key);
    END IF;
END $$;

-- ── audio_calls (pre-existing table) ───────────────────────────────────────
ALTER TABLE audio_calls ADD COLUMN IF NOT EXISTS org_id VARCHAR(255) REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS ix_audio_calls_org_id ON audio_calls (org_id);
ALTER TABLE audio_calls ADD COLUMN IF NOT EXISTS telecaller_id VARCHAR(255) REFERENCES users(id);
CREATE INDEX IF NOT EXISTS ix_audio_calls_telecaller_id ON audio_calls (telecaller_id);
-- dedupe-on-retry hash (rev k4d5e6f7g8h9)
ALTER TABLE audio_calls ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_audio_calls_content_hash ON audio_calls (content_hash);

-- ── processing_jobs (pre-existing table) ──────────────────────────────────
ALTER TABLE processing_jobs ADD COLUMN IF NOT EXISTS org_id VARCHAR(255) REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS ix_processing_jobs_org_id ON processing_jobs (org_id);

-- ── lead_analysis (pre-existing table) ────────────────────────────────────
ALTER TABLE lead_analysis ADD COLUMN IF NOT EXISTS org_id VARCHAR(255) REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS ix_lead_analysis_org_id ON lead_analysis (org_id);
-- why a call was judged not_relevant (rev j3c4d5e6f7g8)
ALTER TABLE lead_analysis ADD COLUMN IF NOT EXISTS relevance_reason TEXT;

-- ── memory_bubbles (pre-existing table) ───────────────────────────────────
ALTER TABLE memory_bubbles ADD COLUMN IF NOT EXISTS org_id VARCHAR(255) REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS ix_memory_bubbles_org_id ON memory_bubbles (org_id);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_memory_bubbles_org_contact_key') THEN
        ALTER TABLE memory_bubbles ADD CONSTRAINT uq_memory_bubbles_org_contact_key UNIQUE (org_id, contact_key);
    END IF;
END $$;

-- ── attendance (brand-new table — create_all() would also self-create this
--    on next boot, included here so it's available immediately) ───────────
CREATE TABLE IF NOT EXISTS attendance (
    id VARCHAR(255) PRIMARY KEY,
    org_id VARCHAR(255) NOT NULL REFERENCES organizations(id),
    user_id VARCHAR(255) NOT NULL REFERENCES users(id),
    date DATE NOT NULL,
    check_in_at TIMESTAMPTZ,
    check_out_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_attendance_user_date UNIQUE (user_id, date)
);
CREATE INDEX IF NOT EXISTS ix_attendance_id ON attendance (id);
CREATE INDEX IF NOT EXISTS ix_attendance_org_id ON attendance (org_id);
CREATE INDEX IF NOT EXISTS ix_attendance_user_id ON attendance (user_id);
CREATE INDEX IF NOT EXISTS ix_attendance_date ON attendance (date);

-- ── follow_ups (brand-new table, rev h1a2b3c4d5e6 — feeds the missed-follow-up
--    leakage metric; create_all() would also self-create this on next boot) ──
CREATE TABLE IF NOT EXISTS follow_ups (
    id VARCHAR(255) PRIMARY KEY,
    org_id VARCHAR(255) NOT NULL REFERENCES organizations(id),
    lead_id VARCHAR(255) REFERENCES leads(id),
    telecaller_id VARCHAR(255) NOT NULL REFERENCES users(id),
    note TEXT,
    due_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_follow_ups_id ON follow_ups (id);
CREATE INDEX IF NOT EXISTS ix_follow_ups_org_id ON follow_ups (org_id);
CREATE INDEX IF NOT EXISTS ix_follow_ups_lead_id ON follow_ups (lead_id);
CREATE INDEX IF NOT EXISTS ix_follow_ups_telecaller_id ON follow_ups (telecaller_id);
CREATE INDEX IF NOT EXISTS ix_follow_ups_due_at ON follow_ups (due_at);
