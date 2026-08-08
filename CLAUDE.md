# Project notes for Claude (or any AI assistant working on this repo)

This is **AgreeEase** — a tool for stamp paper vendors automating
agreement drafting: customer sends a WhatsApp message (English/Malayalam)
-> AI extracts structured fields -> staff reviews on a dashboard -> a
watermarked draft goes to the customer for confirmation -> a clean final
copy is generated for in-house printing only. Full narrative context is
in `README.md`.

## Architecture at a glance

- `backend/main.py` — FastAPI app, all routes (webhook + dashboard). This
  is the orchestration layer; keep business logic in the other modules
  and let this file stay mostly routing + wiring.
- `backend/extraction.py` — Claude API calls for classification/extraction.
  Uses `claude-haiku-4-5-20251001` for cost efficiency. Swappable — see
  "Swapping the AI provider" below.
- `backend/templates_config.py` — the single source of truth for agreement
  types and their required fields. The extraction engine, the missing-field
  checker, and `docgen.py` all read from this same dict.
- `backend/docgen.py` — fills `.docx` templates via docxtpl, computes
  (placeholder) stamp duty, toggles the watermark. Same template file is
  reused for both the customer-facing draft and the final in-house copy.
- `backend/db.py` — SQLAlchemy models. SQLite by default; swap
  `SQLALCHEMY_DATABASE_URL` for Postgres later, nothing else changes.
- `backend/auth.py` — session-based staff login (PBKDF2 password hashing,
  no bcrypt dependency by design, to keep the install light).
- `backend/dateutils.py` — dependency-free month-math for computing
  agreement end dates (no python-dateutil).
- `backend/whatsapp.py` — WhatsApp Cloud API sender. Runs in console-log
  "stub" mode when `WHATSAPP_TOKEN` isn't set — this is intentional and
  lets the whole pipeline be tested without a live WhatsApp number.
- `templates_docx/` — the actual `.docx` agreement templates with
  `{{jinja-style}}` placeholders (docxtpl syntax).

## Conventions to follow when extending this

- **Required fields live in one place**: if you add a new agreement type,
  add it to `TEMPLATES` in `templates_config.py` — don't hardcode field
  lists anywhere else.
- **Never send the customer a non-watermarked file.** The watermark logic
  in `docgen.py` (`draft=True/False`) is a deliberate safety boundary from
  the business requirements, not incidental — printing must stay in-house.
- **Every state-changing action should call `log_action()`** in `main.py`
  so the audit trail stays complete (this matters for a legal-document
  business).
- **Dashboard routes must call `require_login(request)` first** and return
  the redirect if one comes back. This project intentionally avoids
  FastAPI dependency-based redirects in favor of this simple explicit
  check — keep new routes consistent with that pattern.
- Keep new Python dependencies minimal — this is meant to stay easy to
  self-host cheaply (see README's cost section). Prefer stdlib solutions
  (as `auth.py` and `dateutils.py` do) over adding a library for something
  small.

## Swapping the AI provider

`extraction.py` is intentionally isolated — it's the only file that talks
to Claude. To try Gemini or GPT instead, only this file's API calls need
to change; the JSON contract it returns to `main.py`
(`agreement_type`, `language`, `extracted_fields`, `missing_fields`,
`next_question`, `status`) should stay the same shape so nothing else
in the app needs to change.

## Known placeholders that need real data before production

- `docgen.py: calculate_stamp_duty()` — placeholder formula (1% of annual
  value). Replace with actual Kerala stamp duty rules.
- `templates_docx/*.docx` — illustrative wording. Replace with the
  vendor's real template text.
- `main.py: whatsapp_webhook()` — payload parsing is simplified for the
  now. Adjust to Meta's actual webhook JSON shape once a live number is
  connected.
- Default admin account created on first run (see `auth.py:
  ensure_default_admin`) — fine for local dev, but create a real account
  via `scripts/create_admin.py` and don't rely on the default in
  production.
