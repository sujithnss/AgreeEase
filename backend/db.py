"""
Database setup. Uses SQLite by default (zero setup, single file).
Swap SQLALCHEMY_DATABASE_URL to a Postgres URL (e.g. from Supabase/Neon)
when you need multi-user/production scale — no other code needs to change since SQLAlchemy
abstracts the difference.
"""

import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

# Absolute path so the DB file is always backend/app.db, regardless of
# whether the app or a helper script (e.g. scripts/create_admin.py) is
# the one running — avoids accidentally creating two separate DB files.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(BACKEND_DIR, 'app.db')}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class AgreementRequest(Base):
    """One row per customer request, tracked end to end — from first
    WhatsApp message through to printing and, eventually, renewal."""
    __tablename__ = "agreement_requests"

    id = Column(Integer, primary_key=True, index=True)

    # Customer identity — kept explicit so requests are easy to find later
    # for renewal outreach, not just buried in extracted_fields JSON.
    customer_phone = Column(String, index=True)
    customer_name = Column(String, nullable=True, index=True)

    original_message = Column(Text)
    language = Column(String, default="english")  # language the customer wrote in
    # Which template language the generated document uses — staff chooses
    # this on approval; defaults to Malayalam since that's how most
    # agreements are actually drafted, independent of what language the
    # customer's WhatsApp message happened to be in.
    doc_language = Column(String, default="malayalam")
    agreement_type = Column(String, nullable=True)
    extracted_fields = Column(JSON, default=dict)
    missing_fields = Column(JSON, default=list)

    # status flow: awaiting_customer_info -> ready_for_staff_review ->
    # approved -> draft_sent -> confirmed -> ready_to_print
    status = Column(String, default="awaiting_customer_info")

    # Soft delete — staff can remove a wrongly-classified/junk request from
    # the dashboard, but the row (and its audit trail) stays in the DB
    # rather than being hard-deleted, since this is a legal-document
    # business and the audit trail matters even for mistakes.
    is_deleted = Column(Boolean, default=False)

    stamp_duty_amount = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)

    # Payment tracking
    payment_status = Column(String, default="unpaid")  # unpaid | paid | partial
    amount_paid = Column(String, nullable=True)

    # File tracking — filenames are tagged with customer phone/name so a
    # document can be traced back to who it belongs to just by looking at
    # the generated/ folder, not only via the DB.
    draft_file_path = Column(String, nullable=True)
    # PDF conversion of the draft — this is what's actually sent to the
    # customer over WhatsApp as a watermarked preview, since a raw .docx
    # isn't a great preview experience on a phone.
    draft_pdf_path = Column(String, nullable=True)
    final_file_path = Column(String, nullable=True)

    # Renewal tracking — computed from start_date + duration once the
    # agreement is confirmed, so upcoming renewals can be queried directly.
    agreement_end_date = Column(DateTime, nullable=True)
    renewal_reminder_sent = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class AuditLog(Base):
    """Every state change, for audit / dispute resolution."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, index=True)
    action = Column(String)
    actor = Column(String)  # "system" or staff username
    details = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class AdminUser(Base):
    """Staff login accounts for the dashboard."""
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns():
    """create_all() only creates tables that don't exist yet — it won't add
    new columns to a table that's already there. This project has no
    migration framework (kept deliberately light, see CLAUDE.md), so for
    the rare column added after the fact, just patch it in directly.
    SQLite-only (PRAGMA); a Postgres swap should use a real migration tool
    instead, so this is skipped on any other dialect."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(agreement_requests)")}
        if "doc_language" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE agreement_requests ADD COLUMN doc_language VARCHAR DEFAULT 'malayalam'"
            )
            conn.commit()
        if "is_deleted" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE agreement_requests ADD COLUMN is_deleted BOOLEAN DEFAULT 0"
            )
            conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
