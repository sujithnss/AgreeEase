"""
Main FastAPI app for AgreeEase.

Endpoints:
  GET  /webhook/whatsapp         -> Meta's webhook verification handshake
  POST /webhook/whatsapp        -> receives customer messages
  GET  /login, POST /login       -> staff login
  GET  /logout                   -> staff logout
  GET  /dashboard                -> staff list view of all requests (login required)
  GET  /dashboard/{id}           -> staff review/edit view for one request
  POST /dashboard/{id}/approve   -> staff approves, triggers draft generation
  POST /dashboard/{id}/confirm   -> customer confirmed, triggers final print copy
  POST /dashboard/{id}/payment   -> update payment status
  GET  /dashboard/renewals       -> agreements approaching their end date

Run with:
    uvicorn main:app --reload
"""

import os
import csv
import hashlib
import hmac
import io
import json
import datetime

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from db import init_db, get_db, SessionLocal, AgreementRequest, AuditLog, AdminUser
from extraction import (
    process_message,
    merge_followup_reply,
    completion_message,
    draft_ready_message,
    duplicate_request_message,
    renewal_reminder_message,
    resolve_doc_language,
    transliterate_fields_to_malayalam,
    build_property_description,
)
from docgen import generate_document, convert_to_pdf
from templates_config import TEMPLATES, available_languages_for, required_fields_for, duration_months_for, DEFAULT_FEE_DUE_DAY, DEFAULT_RENEWAL_FEE_INCREASE_TERMS
from whatsapp import send_whatsapp_message
from ratelimit import allow_webhook_message
from dateutils import calculate_agreement_end_date, start_date_looks_stale
from auth import hash_password, verify_password, require_login, get_current_user, ensure_default_admin

app = FastAPI(title="AgreeEase")
templates = Jinja2Templates(directory="templates_html")
# list.html renders the Print button straight off the AgreementRequest row
# (unlike review.html, which pre-computes basenames in the route) -- needed
# to turn a stored full path like "generated/foo_final.pdf" into the bare
# filename /download/{filename} expects.
templates.env.filters["basename"] = os.path.basename

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-change-this-secret")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Arbitrary string you choose and enter into Meta's webhook setup form —
# Meta echoes it back on the GET verification handshake below to prove
# you control this endpoint.
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")

# The Meta App's "App Secret" (App Dashboard -> Settings -> Basic) -- NOT
# the same as WHATSAPP_TOKEN (the page access token used to send messages)
# or WHATSAPP_VERIFY_TOKEN (the one-time GET handshake token above). Meta
# signs every webhook POST body with this via the X-Hub-Signature-256
# header; verifying it is the only thing stopping anyone who finds this
# URL from posting fabricated "customer messages" straight into the
# pipeline (each of which triggers a paid Groq API call). Left blank, the
# check is skipped -- same "stub mode" idea as WHATSAPP_TOKEN, so local
# testing with a plain curl/TestClient POST doesn't need a real Meta app
# -- but this MUST be set before connecting a real webhook.
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")


def _verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


# Statuses where a request is actively moving through the pipeline but
# isn't the "still awaiting the customer's first reply" case handled
# separately above (open_request) -- used to detect a duplicate re-send
# from the same customer. Deliberately excludes "ready_to_print": once a
# request is done, a new message from the same customer is presumed to be
# a genuinely new ask, not a duplicate of the finished one.
ACTIVE_DUPLICATE_STATUSES = ("ready_for_staff_review", "approved", "draft_sent")

init_db()
_seed_db = SessionLocal()
ensure_default_admin(_seed_db)
_seed_db.close()


def log_action(db: Session, request_id: int, action: str, actor: str, details: dict = None):
    db.add(AuditLog(request_id=request_id, action=action, actor=actor, details=details or {}))
    db.commit()


def _effective_template_info(agreement_type: str) -> dict:
    """TEMPLATES[agreement_type] with "required_fields" replaced by
    required_fields_for()'s result — which includes the universal
    preferred_document_language field for multi-language types. Templates
    and approve_request read required_fields from this instead of the raw
    TEMPLATES dict so that field actually shows up on the review form and
    is accepted as an editable field on approve."""
    info = dict(TEMPLATES.get(agreement_type, {}))
    if info:
        info["required_fields"] = required_fields_for(agreement_type)
    return info


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}, status_code=401
        )
    request.session["user"] = username
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# WhatsApp webhook — receives incoming customer messages
# ---------------------------------------------------------------------------
@app.get("/webhook/whatsapp")
def whatsapp_webhook_verify(request: Request):
    # Meta calls this once, at the time you save the webhook URL in the
    # App Dashboard, to confirm you control the endpoint.
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    if WHATSAPP_APP_SECRET:
        if not _verify_webhook_signature(raw_body, request.headers.get("x-hub-signature-256", "")):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

    payload = json.loads(raw_body)
    # Meta's actual shape: entry[0].changes[0].value.messages[0]. The same
    # webhook also delivers delivery/read status callbacks and other event
    # types with no "messages" key — those are acknowledged and ignored.
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        return {"status": "ignored"}

    if message.get("type") != "text":
        return {"status": "ignored", "reason": "non-text message type"}

    customer_phone = message["from"]
    message_text = message.get("text", {}).get("body", "")

    # Rate limit BEFORE any DB/Groq work -- the whole point is to cap paid
    # API calls, so this has to be the first gate, not an afterthought.
    if not allow_webhook_message(customer_phone):
        log_action(db, 0, "webhook_rate_limited", "system", {"phone": customer_phone})
        return {"status": "rate_limited"}

    # Check if this customer has an open request awaiting more info
    open_request = (
        db.query(AgreementRequest)
        .filter(
            AgreementRequest.customer_phone == customer_phone,
            AgreementRequest.status == "awaiting_customer_info",
            AgreementRequest.is_deleted.isnot(True),
        )
        .order_by(AgreementRequest.id.desc())
        .first()
    )

    if open_request:
        if open_request.agreement_type not in TEMPLATES:
            # We never managed to classify this request in the first
            # place (see process_message() in extraction.py) — merging
            # against an unknown template's empty field list would be a
            # no-op, so re-run full classification on the combined text
            # instead of just extracting from the new reply.
            combined_message = f"{open_request.original_message}\n{message_text}"
            result = process_message(combined_message)
            open_request.original_message = combined_message
            open_request.language = result["language"]
            open_request.agreement_type = result["agreement_type"]
            open_request.extracted_fields = result["extracted_fields"]
            open_request.missing_fields = result["missing_fields"]
            open_request.status = result["status"]
            open_request.customer_name = open_request.customer_name or result["extracted_fields"].get("tenant_name")
            open_request.doc_language = resolve_doc_language(open_request.language, open_request.extracted_fields)
            db.commit()
            log_action(db, open_request.id, "customer_reply_reclassified", "system", result)
        else:
            result = merge_followup_reply(
                open_request.extracted_fields,
                open_request.agreement_type,
                message_text,
                open_request.language,
            )
            open_request.extracted_fields = result["extracted_fields"]
            open_request.missing_fields = result["missing_fields"]
            open_request.status = result["status"]
            open_request.customer_name = open_request.customer_name or result["extracted_fields"].get("tenant_name")
            open_request.doc_language = resolve_doc_language(open_request.language, open_request.extracted_fields)
            db.commit()
            log_action(db, open_request.id, "customer_reply_merged", "system", result)

        if result["next_question"]:
            send_whatsapp_message(customer_phone, result["next_question"])
        else:
            send_whatsapp_message(customer_phone, completion_message(open_request.language))
        return {"status": "ok", "request_id": open_request.id}

    # Fresh request
    result = process_message(message_text)

    # Guard against the same customer re-sending the same ask while an
    # earlier request from them is already in the pipeline -- without this,
    # staff would see two separate rows for one customer and could end up
    # working them as if unrelated. Only fires when BOTH the phone number
    # AND the (extracted) customer name match an in-progress request --
    # matching on phone alone would also flag legitimate cases, like the
    # same landlord sending in a second, different property's agreement.
    new_customer_name = (result["extracted_fields"].get("tenant_name") or "").strip().lower()
    if new_customer_name:
        active_request = (
            db.query(AgreementRequest)
            .filter(
                AgreementRequest.customer_phone == customer_phone,
                AgreementRequest.status.in_(ACTIVE_DUPLICATE_STATUSES),
                AgreementRequest.is_deleted.isnot(True),
                func.lower(func.trim(AgreementRequest.customer_name)) == new_customer_name,
            )
            .order_by(AgreementRequest.id.desc())
            .first()
        )
        if active_request:
            log_action(
                db, active_request.id, "duplicate_request_blocked", "system",
                {"incoming_message": message_text},
            )
            send_whatsapp_message(customer_phone, duplicate_request_message(result["language"]))
            return {"status": "ok", "duplicate_of_request_id": active_request.id}

    new_request = AgreementRequest(
        customer_phone=customer_phone,
        customer_name=result["extracted_fields"].get("tenant_name"),
        original_message=message_text,
        language=result["language"],
        # Prefer the customer's explicitly stated document-language
        # preference (preferred_document_language, asked for as a normal
        # required field); falls back to a guess from message language
        # until they answer. Staff can still override on the dashboard.
        doc_language=resolve_doc_language(result["language"], result["extracted_fields"]),
        agreement_type=result["agreement_type"],
        extracted_fields=result["extracted_fields"],
        missing_fields=result["missing_fields"],
        status=result["status"],
    )
    db.add(new_request)
    db.commit()
    db.refresh(new_request)
    log_action(db, new_request.id, "message_received_extracted", "system", result)

    if result["next_question"]:
        send_whatsapp_message(customer_phone, result["next_question"])
    else:
        send_whatsapp_message(customer_phone, completion_message(result["language"]))

    return {"status": "ok", "request_id": new_request.id}


# ---------------------------------------------------------------------------
# Staff dashboard (all routes below require login)
# ---------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), q: str = "", status: str = "", payment: str = ""):
    redirect = require_login(request)
    if redirect:
        return redirect

    q = q.strip()
    query = db.query(AgreementRequest).filter(AgreementRequest.is_deleted.isnot(True))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(AgreementRequest.customer_name.ilike(like), AgreementRequest.customer_phone.ilike(like))
        )
    if status:
        query = query.filter(AgreementRequest.status == status)
    if payment:
        query = query.filter(AgreementRequest.payment_status == payment)
    all_requests = query.order_by(AgreementRequest.id.desc()).all()

    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "requests": all_requests,
            "user": get_current_user(request),
            "q": q,
            "status_filter": status,
            "payment_filter": payment,
            "status_pipeline": STATUS_PIPELINE,
        },
    )


@app.get("/dashboard/poll")
def dashboard_poll(request: Request, db: Session = Depends(get_db)):
    """Lightweight JSON endpoint the dashboard list page polls every few
    seconds so new WhatsApp messages / status changes show up without
    staff manually refreshing. Returns just enough (latest updated_at +
    row count) for the client to tell something changed and reload the
    full page -- avoids re-sending the whole request list on every poll."""
    if require_login(request):
        return {"login_required": True}
    latest, count = (
        db.query(func.max(AgreementRequest.updated_at), func.count(AgreementRequest.id))
        .filter(AgreementRequest.is_deleted.isnot(True))
        .one()
    )
    return {"latest": latest.isoformat() if latest else None, "count": count}


STATUS_PIPELINE = [
    ("awaiting_customer_info", "Awaiting Customer Info"),
    ("ready_for_staff_review", "Ready for Staff Review"),
    ("approved", "Approved"),
    ("draft_sent", "Draft Sent"),
    ("confirmed", "Confirmed"),
    ("ready_to_print", "Ready to Print"),
]
COMPLETED_STATUSES = ("confirmed", "ready_to_print")
PENDING_STATUSES = ("awaiting_customer_info", "ready_for_staff_review")


@app.get("/dashboard/reports", response_class=HTMLResponse)
def reports(request: Request, db: Session = Depends(get_db), range_days: int = 30):
    """Staff-facing summary: how many requests came in and how they're
    moving through the pipeline. Status/type breakdowns are all-time
    (they describe current state, not activity in a window); the daily
    trend and "new in range" KPI are scoped to range_days."""
    redirect = require_login(request)
    if redirect:
        return redirect

    range_days = range_days if range_days in (7, 30, 90) else 30
    now = datetime.datetime.utcnow()
    since = now - datetime.timedelta(days=range_days)

    base_q = db.query(AgreementRequest).filter(AgreementRequest.is_deleted.isnot(True))
    total = base_q.count()
    completed = base_q.filter(AgreementRequest.status.in_(COMPLETED_STATUSES)).count()
    pending = base_q.filter(AgreementRequest.status.in_(PENDING_STATUSES)).count()
    new_in_range = base_q.filter(AgreementRequest.created_at >= since).count()

    created_dates = [
        row[0].date() for row in
        base_q.filter(AgreementRequest.created_at >= since)
        .with_entities(AgreementRequest.created_at).all()
        if row[0]
    ]
    daily_counts = {}
    for i in range(range_days):
        day = (now - datetime.timedelta(days=range_days - 1 - i)).date()
        daily_counts[day] = 0
    for d in created_dates:
        if d in daily_counts:
            daily_counts[d] += 1
    trend = [{"label": d.strftime("%d %b"), "count": c} for d, c in sorted(daily_counts.items())]

    status_counts = dict(
        db.query(AgreementRequest.status, func.count(AgreementRequest.id))
        .filter(AgreementRequest.is_deleted.isnot(True))
        .group_by(AgreementRequest.status)
        .all()
    )
    status_breakdown = [
        {"key": key, "label": label, "count": status_counts.get(key, 0)}
        for key, label in STATUS_PIPELINE
        if status_counts.get(key, 0) > 0
    ]

    type_counts = dict(
        db.query(AgreementRequest.agreement_type, func.count(AgreementRequest.id))
        .filter(AgreementRequest.is_deleted.isnot(True), AgreementRequest.agreement_type.isnot(None))
        .group_by(AgreementRequest.agreement_type)
        .all()
    )
    type_breakdown = [
        {"label": TEMPLATES.get(key, {}).get("label", key), "count": count}
        for key, count in sorted(type_counts.items(), key=lambda kv: -kv[1])
    ]

    # Staff performance: approvals per staff member and average turnaround
    # (time from request creation to approval) within the selected range.
    # Joined against AgreementRequest rather than read off reviewed_by so
    # this reflects who actually clicked Approve, per the audit log.
    approvals = (
        db.query(AuditLog.actor, AuditLog.timestamp, AgreementRequest.created_at)
        .join(AgreementRequest, AgreementRequest.id == AuditLog.request_id)
        .filter(
            AuditLog.action == "approved",
            AgreementRequest.is_deleted.isnot(True),
            AgreementRequest.created_at >= since,
        )
        .all()
    )
    staff_stats = {}
    for actor, approved_at, created_at in approvals:
        if not actor:
            continue
        stats = staff_stats.setdefault(actor, {"approvals": 0, "total_hours": 0.0})
        stats["approvals"] += 1
        if approved_at and created_at:
            stats["total_hours"] += (approved_at - created_at).total_seconds() / 3600
    staff_performance = [
        {
            "staff": actor,
            "approvals": s["approvals"],
            "avg_turnaround_hours": round(s["total_hours"] / s["approvals"], 1) if s["approvals"] else 0,
        }
        for actor, s in sorted(staff_stats.items(), key=lambda kv: -kv[1]["approvals"])
    ]

    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "user": get_current_user(request),
            "range_days": range_days,
            "total": total,
            "completed": completed,
            "pending": pending,
            "new_in_range": new_in_range,
            "trend": trend,
            "status_breakdown": status_breakdown,
            "type_breakdown": type_breakdown,
            "staff_performance": staff_performance,
        },
    )


@app.get("/dashboard/renewals", response_class=HTMLResponse)
def renewals(request: Request, db: Session = Depends(get_db)):
    """Agreements whose end date is within the next 30 days (or already
    passed and not yet flagged) — the list to work from for renewal
    outreach."""
    redirect = require_login(request)
    if redirect:
        return redirect

    cutoff = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    upcoming = (
        db.query(AgreementRequest)
        .filter(
            AgreementRequest.agreement_end_date.isnot(None),
            AgreementRequest.agreement_end_date <= cutoff,
            AgreementRequest.is_deleted.isnot(True),
        )
        .order_by(AgreementRequest.agreement_end_date.asc())
        .all()
    )
    today = datetime.datetime.utcnow()
    return templates.TemplateResponse(
        request, "renewals.html", {"requests": upcoming, "today": today, "user": get_current_user(request)}
    )


@app.get("/dashboard/export")
def export_requests(request: Request, db: Session = Depends(get_db)):
    """One-click CSV export of every non-deleted request -- a lightweight,
    in-app backup/reporting option, distinct from the real Postgres backup
    story covered in README's "Database persistence" section. Registered
    before /dashboard/{request_id} so "export" isn't swallowed as a request
    id."""
    redirect = require_login(request)
    if redirect:
        return redirect

    rows = (
        db.query(AgreementRequest)
        .filter(AgreementRequest.is_deleted.isnot(True))
        .order_by(AgreementRequest.id.asc())
        .all()
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "id", "customer_name", "customer_phone", "agreement_type", "language",
        "doc_language", "status", "payment_status", "amount_paid", "reviewed_by",
        "stamp_duty_amount", "created_at", "updated_at", "agreement_end_date",
        "extracted_fields",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.customer_name, r.customer_phone, r.agreement_type, r.language,
            r.doc_language, r.status, r.payment_status, r.amount_paid, r.reviewed_by,
            r.stamp_duty_amount,
            r.created_at.isoformat() if r.created_at else "",
            r.updated_at.isoformat() if r.updated_at else "",
            r.agreement_end_date.isoformat() if r.agreement_end_date else "",
            json.dumps(r.extracted_fields or {}, ensure_ascii=False),
        ])

    log_action(db, 0, "requests_exported_csv", get_current_user(request), {"row_count": len(rows)})

    filename = f"agreeease_requests_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/dashboard/documents", response_class=HTMLResponse)
def documents(request: Request, db: Session = Depends(get_db), q: str = ""):
    """Audit trail of every generated document across all requests — draft
    .docx/PDF and final .docx — with download and delete controls, so staff
    can review or clean up generated files (e.g. demo/test runs) from the UI
    instead of touching the filesystem directly. Registered before
    /dashboard/{request_id} so "documents" isn't swallowed as a request id."""
    redirect = require_login(request)
    if redirect:
        return redirect

    q = q.strip()
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(["draft_generated_sent", "final_copy_generated"]))
        .order_by(AuditLog.timestamp.desc())
        .all()
    )
    request_ids = {log.request_id for log in logs}
    requests_by_id = {
        r.id: r
        for r in db.query(AgreementRequest).filter(AgreementRequest.id.in_(request_ids)).all()
    }

    rows = []
    for log in logs:
        req = requests_by_id.get(log.request_id)
        file_specs = []
        if log.action == "draft_generated_sent":
            if log.details.get("path"):
                file_specs.append(("Draft (.docx)", log.details["path"]))
            if log.details.get("pdf_path"):
                file_specs.append(("Draft PDF (sent to customer)", log.details["pdf_path"]))
        elif log.action == "final_copy_generated":
            if log.details.get("path"):
                file_specs.append(("Final (.docx, in-house print)", log.details["path"]))
            if log.details.get("pdf_path"):
                file_specs.append(("Final PDF (in-house print)", log.details["pdf_path"]))

        files = [
            {
                "label": label,
                "filename": os.path.basename(path),
                "exists": os.path.exists(path),
            }
            for label, path in file_specs
        ]
        if not files:
            continue

        rows.append(
            {
                "timestamp": log.timestamp,
                "request_id": log.request_id,
                "customer_name": req.customer_name if req else None,
                "customer_phone": req.customer_phone if req else None,
                "agreement_type": req.agreement_type if req else None,
                "request_exists": req is not None,
                "files": files,
            }
        )

    if q:
        ql = q.lower()
        rows = [
            r for r in rows
            if ql in str(r["request_id"])
            or (r["customer_name"] and ql in r["customer_name"].lower())
            or (r["customer_phone"] and ql in r["customer_phone"].lower())
            or (r["agreement_type"] and ql in r["agreement_type"].lower())
        ]

    return templates.TemplateResponse(
        request, "documents.html", {"rows": rows, "user": get_current_user(request), "q": q}
    )


@app.post("/dashboard/documents/delete")
def delete_document(
    request: Request,
    db: Session = Depends(get_db),
    request_id: int = Form(...),
    filename: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    # Strip any directory components so this can't be used to delete files
    # outside generated/, regardless of what's submitted.
    safe_name = os.path.basename(filename)
    file_path = os.path.join("generated", safe_name)

    existed = os.path.exists(file_path)
    if existed:
        os.remove(file_path)

    # Clear the matching column so the review page doesn't keep showing a
    # dead link to a file that no longer exists.
    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if req:
        for field in ("draft_file_path", "draft_pdf_path", "final_file_path"):
            current = getattr(req, field)
            if current and os.path.basename(current) == safe_name:
                setattr(req, field, None)
        db.commit()

    log_action(
        db, request_id, "document_deleted", get_current_user(request),
        {"filename": safe_name, "existed": existed},
    )

    referer = request.headers.get("referer", "/dashboard/documents")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/dashboard/staff", response_class=HTMLResponse)
def staff_list(request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect
    staff = db.query(AdminUser).order_by(AdminUser.username).all()
    return templates.TemplateResponse(
        request, "staff.html", {"staff": staff, "user": get_current_user(request)}
    )


@app.post("/dashboard/staff/add")
def staff_add(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    username = username.strip()
    staff = db.query(AdminUser).order_by(AdminUser.username).all()
    error = None
    if not username or not password:
        error = "Username and password are required."
    elif password != confirm_password:
        error = "Passwords don't match."

    if error:
        return templates.TemplateResponse(
            request, "staff.html", {"staff": staff, "user": get_current_user(request), "error": error},
            status_code=400,
        )

    # Mirrors scripts/create_admin.py: an existing username gets its
    # password reset rather than erroring, so this form doubles as the
    # "reset a staff member's password" flow too.
    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        existing.password_hash = hash_password(password)
        db.commit()
    else:
        db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()

    return RedirectResponse(url="/dashboard/staff", status_code=303)


@app.post("/dashboard/staff/{staff_id}/delete")
def staff_delete(staff_id: int, request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect

    target = db.query(AdminUser).filter(AdminUser.id == staff_id).first()
    if not target:
        return RedirectResponse(url="/dashboard/staff", status_code=303)

    total_staff = db.query(AdminUser).count()
    current_username = get_current_user(request)
    staff = db.query(AdminUser).order_by(AdminUser.username).all()
    error = None
    if target.username == current_username:
        error = "You can't delete the account you're currently logged in as."
    elif total_staff <= 1:
        error = "Can't delete the last remaining staff account -- it would lock everyone out."

    if error:
        return templates.TemplateResponse(
            request, "staff.html", {"staff": staff, "user": get_current_user(request), "error": error},
            status_code=400,
        )

    db.delete(target)
    db.commit()
    return RedirectResponse(url="/dashboard/staff", status_code=303)


@app.get("/dashboard/{request_id}", response_class=HTMLResponse)
def review_request(request_id: int, request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect
    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    template_info = _effective_template_info(req.agreement_type)
    staff_list = [u.username for u in db.query(AdminUser).order_by(AdminUser.username).all()]
    incomplete_fields_param = request.query_params.get("incomplete_fields", "")
    # Pre-fills review-form inputs for fields with a sensible default (most
    # rental agreements run 11 months and charge rent on the 5th) so staff
    # aren't forced to retype them on every request — they can still edit
    # before approving. Not written onto req.extracted_fields itself so an
    # un-submitted review page never silently persists a value nobody
    # confirmed.
    field_defaults = {
        "agreement_duration_months": duration_months_for(req.agreement_type, req.extracted_fields or {}),
        "fee_due_day": DEFAULT_FEE_DUE_DAY,
        "renewal_fee_increase_terms": DEFAULT_RENEWAL_FEE_INCREASE_TERMS,
    }
    # Advisory-only check, not a blocker: if start_date + the agreement's
    # duration computes an end date more than a grace period in the past
    # (see dateutils.START_DATE_GRACE_MONTHS), the customer likely
    # mistyped start_date's year (e.g. "2024" instead of "2026") rather
    # than genuinely backdating a recent agreement -- a start_date a few
    # months back, still within (or just past) the duration, is normal
    # and intentionally NOT flagged. Staff can confirm it's correct and
    # approve anyway.
    fields_with_duration = {
        **(req.extracted_fields or {}),
        "agreement_duration_months": field_defaults["agreement_duration_months"],
    }
    computed_end_date = calculate_agreement_end_date(fields_with_duration)
    stale_start_date = start_date_looks_stale(fields_with_duration)
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "req": req,
            "template_info": template_info,
            "field_defaults": field_defaults,
            "user": get_current_user(request),
            "staff_list": staff_list,
            "draft_filename": os.path.basename(req.draft_file_path) if req.draft_file_path else None,
            "draft_pdf_filename": os.path.basename(req.draft_pdf_path) if req.draft_pdf_path else None,
            "final_filename": os.path.basename(req.final_file_path) if req.final_file_path else None,
            "final_pdf_filename": os.path.basename(req.final_pdf_path) if req.final_pdf_path else None,
            "incomplete_fields": [f for f in incomplete_fields_param.split(",") if f],
            "start_date_looks_stale": stale_start_date,
            "computed_end_date": computed_end_date.strftime("%d-%m-%Y") if computed_end_date else "",
        },
    )


@app.post("/dashboard/{request_id}/approve")
async def approve_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    form = await request.form()
    staff_name = form.get("staff_name", "").strip()
    if not staff_name:
        raise HTTPException(status_code=400, detail="Staff name is required")

    template_info = _effective_template_info(req.agreement_type)
    editable_fields = (
        template_info.get("required_fields", [])
        + template_info.get("staff_fields", [])
        + template_info.get("optional_fields", [])
    )
    updated_fields = dict(req.extracted_fields or {})
    for field in editable_fields:
        if field in form:
            updated_fields[field] = form.get(field, "")
    req.extracted_fields = updated_fields
    # Persist whatever staff typed even if the completeness check below
    # blocks approval, so a rejected attempt never loses their work.
    db.commit()

    # Block approving with any field still blank -- whether it's a
    # required_field the customer never answered over WhatsApp or a
    # staff_field nobody typed in here. A sent draft can't be un-sent, so
    # this has to be a hard stop, not just a warning. Uses the raw
    # TEMPLATES entry rather than the effective one so the universal
    # preferred_document_language field (which is legitimately optional --
    # it defaults to Malayalam when the customer never states a
    # preference) isn't treated as a missing field.
    raw_template = TEMPLATES.get(req.agreement_type, {})
    fields_to_check = raw_template.get("required_fields", []) + raw_template.get("staff_fields", [])
    incomplete_fields = [
        field for field in fields_to_check
        if not str(updated_fields.get(field) or "").strip()
    ]
    if incomplete_fields:
        return RedirectResponse(
            url=f"/dashboard/{request_id}?incomplete_fields={','.join(incomplete_fields)}",
            status_code=303,
        )

    doc_language = form.get("doc_language", "malayalam")
    if doc_language not in available_languages_for(req.agreement_type):
        doc_language = "malayalam"
    req.doc_language = doc_language

    # Build the formal "മാർജിൻ" schedule-clause property description
    # directly from the customer's plain-language property_address, in
    # the document's language -- customers don't collect this field
    # themselves (see templates_config.py). Done here (rather than
    # inside docgen.py) so the draft and final copy always use the exact
    # same wording -- it's persisted onto the request, not re-derived
    # per document.
    property_address = req.extracted_fields.get("property_address")
    if property_address:
        property_description = build_property_description(property_address, doc_language)
        if property_description != req.extracted_fields.get("property_description"):
            req.extracted_fields = {
                **req.extracted_fields,
                "property_description": property_description,
            }
            log_action(db, request_id, "property_description_generated", staff_name, {"language": doc_language})

    # Auto-transliterate any remaining English-typed name/address fields
    # into Malayalam script so the generated document doesn't mix
    # Malayalam boilerplate with English proper nouns. Fields already in
    # Malayalam (including the property_description just built above),
    # or with no Latin letters (dates, amounts), are left untouched.
    if doc_language == "malayalam":
        transliterated = transliterate_fields_to_malayalam(req.extracted_fields)
        changed_fields = [
            field for field in transliterated
            if transliterated[field] != req.extracted_fields.get(field)
        ]
        req.extracted_fields = transliterated
        if changed_fields:
            log_action(db, request_id, "fields_transliterated", staff_name, {"fields": changed_fields})

    req.status = "approved"
    req.reviewed_by = staff_name
    db.commit()
    log_action(db, request_id, "approved", staff_name, {"doc_language": doc_language})

    # Generate watermarked draft and send to customer
    draft_path = generate_document(
        req.id, req.agreement_type, req.extracted_fields, draft=True,
        customer_phone=req.customer_phone, customer_name=req.customer_name,
        language=doc_language,
    )
    req.draft_file_path = draft_path

    # Convert to PDF for the customer-facing WhatsApp preview — a raw .docx
    # isn't a great preview experience on a phone. Falls back to sending the
    # .docx if LibreOffice isn't installed, so approving isn't blocked on it.
    try:
        pdf_path = convert_to_pdf(draft_path)
        req.draft_pdf_path = pdf_path
    except RuntimeError as e:
        pdf_path = None
        log_action(db, request_id, "pdf_conversion_failed", staff_name, {"error": str(e)})

    req.status = "draft_sent"
    db.commit()
    log_action(db, request_id, "draft_generated_sent", staff_name, {"path": draft_path, "pdf_path": pdf_path})

    send_result = send_whatsapp_message(
        req.customer_phone,
        draft_ready_message(doc_language),
        attachment_path=pdf_path or draft_path,
    )
    if send_result.get("status") == "error":
        log_action(db, request_id, "draft_whatsapp_send_failed", staff_name, {"error": send_result.get("error")})

    return RedirectResponse(url=f"/dashboard/{request_id}", status_code=303)


@app.post("/dashboard/{request_id}/confirm")
def confirm_and_print(request_id: int, request: Request, db: Session = Depends(get_db)):
    """Called once customer confirms via WhatsApp (or staff marks it manually
    for local testing). Generates the clean, non-watermarked copy for
    in-house printing only — never sent to the customer. Also computes the
    agreement's end date so it shows up on the renewals list later."""
    redirect = require_login(request)
    if redirect:
        return redirect

    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.status = "confirmed"
    db.commit()

    final_path = generate_document(
        req.id, req.agreement_type, req.extracted_fields, draft=False,
        customer_phone=req.customer_phone, customer_name=req.customer_name,
        language=req.doc_language or "malayalam",
    )
    req.final_file_path = final_path

    # Convert to PDF too so staff can open/print it straight from the
    # dashboard with one click -- a raw .docx has no browser print path.
    # Falls back to leaving final_pdf_path unset if LibreOffice/Word isn't
    # available, same as the draft's PDF conversion; staff can still
    # download and print the .docx manually in that case.
    try:
        final_pdf_path = convert_to_pdf(final_path)
        req.final_pdf_path = final_pdf_path
    except RuntimeError as e:
        final_pdf_path = None
        log_action(db, request_id, "final_pdf_conversion_failed", "system", {"error": str(e)})

    req.status = "ready_to_print"

    fields_with_duration = {
        **req.extracted_fields,
        "agreement_duration_months": duration_months_for(req.agreement_type, req.extracted_fields),
    }
    end_date = calculate_agreement_end_date(fields_with_duration)
    if end_date:
        req.agreement_end_date = datetime.datetime.combine(end_date, datetime.time.min)

    db.commit()
    log_action(
        db, request_id, "final_copy_generated", "system",
        {"path": final_path, "pdf_path": final_pdf_path, "end_date": str(end_date)},
    )

    return RedirectResponse(url=f"/dashboard/{request_id}", status_code=303)


@app.post("/dashboard/{request_id}/generate_final_pdf")
def generate_final_pdf(request_id: int, request: Request, db: Session = Depends(get_db)):
    """Backfills final_pdf_path for a request that was already confirmed
    before the print-from-dashboard feature existed (or whose PDF
    conversion failed the first time, e.g. LibreOffice was briefly down) --
    the normal confirm/print flow only generates the PDF once, at confirm
    time, and confirm_and_print() can't just be re-run from the UI once a
    request is ready_to_print. Only converts the existing final .docx; does
    not regenerate the document itself or touch agreement_end_date."""
    redirect = require_login(request)
    if redirect:
        return redirect

    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if not req.final_file_path:
        raise HTTPException(status_code=400, detail="No final copy generated yet for this request")

    try:
        req.final_pdf_path = convert_to_pdf(req.final_file_path)
        db.commit()
        log_action(db, request_id, "final_pdf_generated", get_current_user(request), {"pdf_path": req.final_pdf_path})
    except RuntimeError as e:
        log_action(db, request_id, "final_pdf_conversion_failed", get_current_user(request), {"error": str(e)})

    return RedirectResponse(url=f"/dashboard/{request_id}", status_code=303)


@app.post("/dashboard/{request_id}/payment")
def update_payment(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    payment_status: str = Form(...),
    amount_paid: str = Form(""),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.payment_status = payment_status
    req.amount_paid = amount_paid
    db.commit()
    log_action(db, request_id, "payment_updated", get_current_user(request), {"payment_status": payment_status, "amount_paid": amount_paid})

    return RedirectResponse(url=f"/dashboard/{request_id}", status_code=303)


@app.post("/dashboard/{request_id}/delete")
def delete_request(request_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-deletes a request (e.g. wrong agreement type identified, junk/
    test entry) so it drops off the dashboard list. This does NOT remove
    the DB row or its audit trail — this is a legal-document business, so
    even a staff mistake should stay reconstructable from the audit log
    rather than being permanently erased."""
    redirect = require_login(request)
    if redirect:
        return redirect
    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.is_deleted = True
    db.commit()
    log_action(db, request_id, "request_deleted", get_current_user(request))
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/dashboard/{request_id}/mark_renewal_sent")
def mark_renewal_sent(request_id: int, request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect
    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.renewal_reminder_sent = True
    db.commit()
    send_whatsapp_message(
        req.customer_phone,
        renewal_reminder_message(req.language, req.customer_name),
    )
    log_action(db, request_id, "renewal_reminder_sent", get_current_user(request))
    return RedirectResponse(url="/dashboard/renewals", status_code=303)


@app.get("/download/{filename}")
def download_file(request: Request, filename: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return FileResponse(os.path.join("generated", filename))
