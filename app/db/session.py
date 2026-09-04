from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite

from app.core.config import settings

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id                    INTEGER   PRIMARY KEY AUTOINCREMENT,
    telegram_message_id   INTEGER   NOT NULL,
    telegram_chat_id      INTEGER   NOT NULL,
    user_id               INTEGER,
    username              TEXT,

    -- Content
    text                  TEXT,
    source_url            TEXT,
    voice_file_id         TEXT,
    transcription         TEXT,
    reply_to_telegram_id  INTEGER,

    -- Orthogonal flags — preserved even when message_type collapses them by priority
    has_url               BOOLEAN   NOT NULL DEFAULT 0,
    is_reply              BOOLEAN   NOT NULL DEFAULT 0,

    -- Classification
    message_type          TEXT      NOT NULL DEFAULT 'text',
    channel               TEXT,

    -- Lifecycle
    status                TEXT      NOT NULL DEFAULT 'received',
    raw_payload           TEXT      NOT NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at          TIMESTAMP,

    UNIQUE (telegram_chat_id, telegram_message_id)
)
"""

# WHEN clause prevents recursion: trigger fires only when the UPDATE statement
# itself did not touch updated_at. If a caller sets updated_at explicitly,
# OLD.updated_at ≠ NEW.updated_at and the trigger is skipped.
_CREATE_UPDATED_AT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_messages_updated_at
AFTER UPDATE ON messages
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE messages SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER   PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT      NOT NULL,
    source_id       TEXT,
    title           TEXT,
    url             TEXT,
    summary         TEXT,
    raw_content     TEXT,
    relevance_score REAL,
    relevance_note  TEXT,
    decision        TEXT,
    message_id      INTEGER   REFERENCES messages(id),
    published_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    evaluated_at    TIMESTAMP
)
"""

_CREATE_EDITORIAL_PLANS = """
CREATE TABLE IF NOT EXISTS editorial_plans (
    id                 INTEGER   PRIMARY KEY AUTOINCREMENT,
    signal_ids         TEXT      NOT NULL,
    recommended_action TEXT      NOT NULL,
    confidence         REAL      NOT NULL,
    proposal_json      TEXT      NOT NULL,
    status             TEXT      NOT NULL DEFAULT 'draft'
                                   CHECK (
                                       status IN (
                                           'draft', 'approved', 'saved', 'discarded'
                                       )
                                   ),
    llm_used           BOOLEAN   NOT NULL DEFAULT 0,
    fallback_used      BOOLEAN   NOT NULL DEFAULT 0,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at        TIMESTAMP
)
"""

_CREATE_EDITORIAL_DRAFTS = """
CREATE TABLE IF NOT EXISTS editorial_drafts (
    id            INTEGER   PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER   NOT NULL UNIQUE REFERENCES editorial_plans(id),
    draft_json    TEXT      NOT NULL,
    status        TEXT      NOT NULL DEFAULT 'draft'
                               CHECK (status IN ('draft', 'discarded')),
    llm_used      BOOLEAN   NOT NULL DEFAULT 0,
    fallback_used BOOLEAN   NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_TELEGRAM_SESSIONS = """
CREATE TABLE IF NOT EXISTS telegram_sessions (
    chat_id            INTEGER   PRIMARY KEY,
    last_signal_ids    TEXT      NOT NULL DEFAULT '[]',
    last_plan_id       INTEGER,
    last_draft_id      INTEGER,
    pending_command    TEXT,
    pending_target_id  INTEGER,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Sub-phase B: a single active goal at a time. Older goals are kept
# (archived_at set) so we can show a simple history later.
_CREATE_ACTIVE_GOALS = """
CREATE TABLE IF NOT EXISTS active_goals (
    id           INTEGER   PRIMARY KEY AUTOINCREMENT,
    label        TEXT      NOT NULL,
    description  TEXT,
    deadline_at  TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_at  TIMESTAMP
)
"""

# Sub-phase B: scheduled follow-ups for the "después" path of the proactive
# MVP handoff. A daily cron processes rows where due_at <= now.
_CREATE_HANDOFF_FOLLOWUPS = """
CREATE TABLE IF NOT EXISTS pending_handoff_followups (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER   NOT NULL REFERENCES editorial_plans(id),
    chat_id     INTEGER   NOT NULL,
    due_at      TIMESTAMP NOT NULL,
    status      TEXT      NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'notified', 'dismissed')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notified_at TIMESTAMP
)
"""

# Phase 1 (career manager): every LinkedIn post the operator generates is
# recorded, and Carlos marks the ones he actually publishes. This is the
# only honest basis for cadence: "did a post go live this week", not
# "how many drafts exist".
_CREATE_LINKEDIN_POSTS = """
CREATE TABLE IF NOT EXISTS linkedin_posts (
    id            INTEGER   PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER   REFERENCES editorial_plans(id),
    goal_id       INTEGER   REFERENCES active_goals(id),
    chat_id       INTEGER,
    body          TEXT      NOT NULL,
    hook          TEXT,
    language      TEXT      NOT NULL DEFAULT 'es',
    llm_used      BOOLEAN   NOT NULL DEFAULT 0,
    model         TEXT,
    opinion_used  BOOLEAN   NOT NULL DEFAULT 0,
    status        TEXT      NOT NULL DEFAULT 'generated'
                            CHECK (status IN ('generated', 'published', 'discarded')),
    published_url TEXT,
    published_at  TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Phase 2 (career manager): vacancies found by the job radar plus the
# application pipeline Carlos moves by hand. Not part of the editorial
# reset: this is his career state, not the week's editorial work.
_CREATE_JOB_LEADS = """
CREATE TABLE IF NOT EXISTS job_leads (
    id           INTEGER   PRIMARY KEY AUTOINCREMENT,
    source       TEXT      NOT NULL,
    source_id    TEXT,
    title        TEXT      NOT NULL,
    company      TEXT,
    url          TEXT      NOT NULL UNIQUE,
    location     TEXT,
    remote       INTEGER,
    summary      TEXT,
    published_at TIMESTAMP,
    fit_score    REAL      NOT NULL DEFAULT 0,
    fit_note     TEXT,
    dream        INTEGER   NOT NULL DEFAULT 0,
    status       TEXT      NOT NULL DEFAULT 'new'
                           CHECK (status IN ('new', 'saved', 'applied', 'interview',
                                             'offer', 'rejected', 'dismissed')),
    notes        TEXT,
    found_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at   TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Phase 3: one ambitious lead turned into a four-week plan. A single active
# campaign at a time; starting a new one abandons the previous.
_CREATE_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER   PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER   NOT NULL REFERENCES job_leads(id),
    goal_id         INTEGER   REFERENCES active_goals(id),
    status          TEXT      NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'applied', 'abandoned')),
    started_at      TIMESTAMP NOT NULL,
    target_apply_at TIMESTAMP NOT NULL,
    plan_json       TEXT      NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Small key/value store for operator bookkeeping (last cadence reminder, etc.).
_CREATE_OPERATOR_STATE = """
CREATE TABLE IF NOT EXISTS operator_state (
    key        TEXT      PRIMARY KEY,
    value      TEXT      NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Idempotent migrations — ALTER TABLE ADD COLUMN is a no-op if the column
# already exists (OperationalError is caught and silenced in _migrate).
_MIGRATIONS: list[str] = [
    # Phase 4 pre-rule: persist voice note duration
    "ALTER TABLE messages ADD COLUMN voice_duration INTEGER",
    # Phase 4: richer signals schema (columns missing from original CREATE)
    "ALTER TABLE signals ADD COLUMN source_id TEXT",
    "ALTER TABLE signals ADD COLUMN relevance_score REAL",
    "ALTER TABLE signals ADD COLUMN published_at TIMESTAMP",
    # Sub-phase B: link plans/drafts to the goal active when they were created.
    "ALTER TABLE editorial_plans ADD COLUMN goal_id INTEGER"
    " REFERENCES active_goals(id)",
    "ALTER TABLE editorial_drafts ADD COLUMN goal_id INTEGER"
    " REFERENCES active_goals(id)",
    # Phase 1: remember the last generated post per chat so "publicado" works.
    "ALTER TABLE telegram_sessions ADD COLUMN last_post_id INTEGER",
    # Phase 2.5: posting text and extracted facts (salary, country, requirements).
    "ALTER TABLE job_leads ADD COLUMN posting_text TEXT",
    "ALTER TABLE job_leads ADD COLUMN details_json TEXT",
    "ALTER TABLE job_leads ADD COLUMN salary_text TEXT",
    "ALTER TABLE job_leads ADD COLUMN salary_min_usd_year REAL",
    "ALTER TABLE job_leads ADD COLUMN salary_max_usd_year REAL",
    "ALTER TABLE job_leads ADD COLUMN country TEXT",
    "ALTER TABLE job_leads ADD COLUMN remote_policy TEXT",
    "ALTER TABLE job_leads ADD COLUMN enriched_at TIMESTAMP",
    # Phase 2.6: gap analysis and tailored CV per lead.
    "ALTER TABLE job_leads ADD COLUMN gap_json TEXT",
    "ALTER TABLE job_leads ADD COLUMN cv_markdown TEXT",
    "ALTER TABLE job_leads ADD COLUMN cv_generated_at TIMESTAMP",
]

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_messages_telegram_message_id"
    " ON messages(telegram_message_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status)",
    "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)",
    "CREATE INDEX IF NOT EXISTS idx_signals_source_type ON signals(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_signals_source_identity"
    " ON signals(source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision)",
    "CREATE INDEX IF NOT EXISTS idx_signals_message_id ON signals(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_editorial_plans_status ON editorial_plans(status)",
    "CREATE INDEX IF NOT EXISTS idx_editorial_plans_created_at"
    " ON editorial_plans(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_editorial_drafts_status"
    " ON editorial_drafts(status)",
    "CREATE INDEX IF NOT EXISTS idx_telegram_sessions_updated_at"
    " ON telegram_sessions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_active_goals_archived_at"
    " ON active_goals(archived_at)",
    "CREATE INDEX IF NOT EXISTS idx_pending_handoff_followups_due"
    " ON pending_handoff_followups(status, due_at)",
    "CREATE INDEX IF NOT EXISTS idx_linkedin_posts_status_published"
    " ON linkedin_posts(status, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_linkedin_posts_chat_created"
    " ON linkedin_posts(chat_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_job_leads_status_fit"
    " ON job_leads(status, fit_score)",
]


async def _migrate(db: aiosqlite.Connection) -> None:
    """Apply idempotent ALTER TABLE migrations. Silences 'duplicate column' errors."""
    for stmt in _MIGRATIONS:
        try:
            await db.execute(stmt)
        except Exception:
            # OperationalError: table X already has column Y — safe to ignore.
            pass
    await db.commit()


async def init_db() -> None:
    db_file = Path(settings.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute(_CREATE_MESSAGES)
        await db.execute(_CREATE_UPDATED_AT_TRIGGER)
        await db.execute(_CREATE_SIGNALS)
        await db.execute(_CREATE_EDITORIAL_PLANS)
        await db.execute(_CREATE_EDITORIAL_DRAFTS)
        await db.execute(_CREATE_TELEGRAM_SESSIONS)
        await db.execute(_CREATE_ACTIVE_GOALS)
        await db.execute(_CREATE_HANDOFF_FOLLOWUPS)
        await db.execute(_CREATE_LINKEDIN_POSTS)
        await db.execute(_CREATE_JOB_LEADS)
        await db.execute(_CREATE_CAMPAIGNS)
        await db.execute(_CREATE_OPERATOR_STATE)
        for stmt in _CREATE_INDEXES:
            await db.execute(stmt)
        await _migrate(db)
        await db.commit()


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
