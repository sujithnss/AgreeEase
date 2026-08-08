"""
Main FastAPI app for AgreeEase.

Endpoints:
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
import datetime

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_

from db import init_db, get_db, SessionLocal, AgreementRequest, AuditLog, AdminUser
from extraction import process_message, merge_followup_reply
from docgen import generate_document
from templates_config import TEMPLATES
from whatsapp import send_whatsapp_message
from dateutils import calculate_agreement_end_date
from auth import hash_password, verify_password, require_login, get_current_user, ensure_default_admin

app = FastAPI(title="AgreeEase")
templates = Jinja2Templates(directory="templates_html")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-change-this-secret")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

init_db()
_seed_db = SessionLocal()
ensure_default_admin(_seed_db)
_seed_db.close()


def log_action(db: Session, request_id: int, action: str, actor: str, details: dict = None):
    db.add(AuditLog(request_id=request_id, action=action, actor=actor, details=details or {}))
    db.commit()


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
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    # NOTE: parse according to Meta's actual webhook payload shape.
    # This is simplified for now — adapt to the real structure when
    # you connect a live WhatsApp Business number.
    customer_phone = payload.get("from")
    message_text = payload.get("text", "")

    # Check if this customer has an open request awaiting more info
    open_request = (
        db.query(AgreementRequest)
        .filter(
            AgreementRequest.customer_phone == customer_phone,
            AgreementRequest.status == "awaiting_customer_info",
        )
        .order_by(AgreementRequest.id.desc())
        .first()
    )

    if open_request:
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
        db.commit()
        log_action(db, open_request.id, "customer_reply_merged", "system", result)

        if result["missing_fields"]:
            send_whatsapp_message(customer_phone, result["next_question"])
        else:
            send_whatsapp_message(
                customer_phone,
                "Thank you! Your details are with our team for review. We'll send you a draft shortly.",
            )
        return {"status": "ok", "request_id": open_request.id}

    # Fresh request
    result = process_message(message_text)
    new_request = AgreementRequest(
        customer_phone=customer_phone,
        customer_name=result["extracted_fields"].get("tenant_name"),
        original_message=message_text,
        language=result["language"],
        agreement_type=result["agreement_type"],
        extracted_fields=result["extracted_fields"],
        missing_fields=result["missing_fields"],
        status=result["status"],
    )
    db.add(new_request)
    db.commit()
    db.refresh(new_request)
    log_action(db, new_request.id, "message_received_extracted", "system", result)

    if result["missing_fields"]:
        send_whatsapp_message(customer_phone, result["next_question"])
    else:
        send_whatsapp_message(
            customer_phone,
            "Thank you! Your request is with our team for review. We'll send you a draft shortly.",
        )

    return {"status": "ok", "request_id": new_request.id}


# ---------------------------------------------------------------------------
# Staff dashboard (all routes below require login)
# ---------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect
    all_requests = db.query(AgreementRequest).order_by(AgreementRequest.id.desc()).all()
    return templates.TemplateResponse(
        request, "list.html", {"requests": all_requests, "user": get_current_user(request)}
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
        )
        .order_by(AgreementRequest.agreement_end_date.asc())
        .all()
    )
    today = datetime.datetime.utcnow()
    return templates.TemplateResponse(
        request, "renewals.html", {"requests": upcoming, "today": today, "user": get_current_user(request)}
    )


@app.get("/dashboard/{request_id}", response_class=HTMLResponse)
def review_request(request_id: int, request: Request, db: Session = Depends(get_db)):
    redirect = require_login(request)
    if redirect:
        return redirect
    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    template_info = TEMPLATES.get(req.agreement_type, {})
    return templates.TemplateResponse(
        request,
        "review.html",
        {"req": req, "template_info": template_info, "user": get_current_user(request)},
    )


@app.post("/dashboard/{request_id}/approve")
def approve_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    staff_name: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    req = db.query(AgreementRequest).filter(AgreementRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    req.status = "approved"
    req.reviewed_by = staff_name
    db.commit()
    log_action(db, request_id, "approved", staff_name)

    # Generate watermarked draft and send to customer
    draft_path = generate_document(
        req.id, req.agreement_type, req.extracted_fields, draft=True,
        customer_phone=req.customer_phone, customer_name=req.customer_name,
    )
    req.draft_file_path = draft_path
    req.status = "draft_sent"
    db.commit()
    log_action(db, request_id, "draft_generated_sent", staff_name, {"path": draft_path})

    send_whatsapp_message(
        req.customer_phone,
        "Here is your draft agreement for review. Please confirm if all details are correct.",
        attachment_path=draft_path,
    )

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
    )
    req.final_file_path = final_path
    req.status = "ready_to_print"

    end_date = calculate_agreement_end_date(req.extracted_fields)
    if end_date:
        req.agreement_end_date = datetime.datetime.combine(end_date, datetime.time.min)

    db.commit()
    log_action(db, request_id, "final_copy_generated", "system", {"path": final_path, "end_date": str(end_date)})

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
        f"Hi {req.customer_name or ''}, your agreement is approaching its renewal date. "
        f"Please contact us to renew.",
    )
    log_action(db, request_id, "renewal_reminder_sent", get_current_user(request))
    return RedirectResponse(url="/dashboard/renewals", status_code=303)


@app.get("/download/{filename}")
def download_file(request: Request, filename: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return FileResponse(os.path.join("generated", filename))
