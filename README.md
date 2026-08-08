# AgreeEase

AI-assisted agreement drafting for stamp paper vendors. A customer's
WhatsApp message (English/Malayalam) is read by AI, structured into an
agreement draft, reviewed by staff, sent to the customer as a
**watermarked draft only**, and — once confirmed — a clean copy is
generated for **in-house printing only**. Every request is tracked
through payment status and, later, renewal.

```
Customer (WhatsApp) → AI extraction (Claude) → Staff review dashboard
   → Watermarked draft to customer → Customer confirms
   → Clean final copy generated (in-house print only)
   → Payment & renewal tracked in DB
```

## What's included

| File | Purpose |
|---|---|
| `backend/main.py` | FastAPI app — webhook + staff dashboard routes |
| `backend/extraction.py` | Claude-based classification, field extraction, follow-up questions |
| `backend/templates_config.py` | Registry of agreement types & required fields (single source of truth) |
| `backend/docgen.py` | Fills `.docx` templates, computes stamp duty, applies watermark, tags filenames with customer identity |
| `backend/db.py` | SQLite models — requests, audit log, admin users |
| `backend/auth.py` | Staff login (session cookies, PBKDF2 password hashing) |
| `backend/dateutils.py` | Agreement end-date calculation (dependency-free) |
| `backend/whatsapp.py` | WhatsApp Cloud API sender (console "stub" mode until real credentials are added) |
| `backend/templates_html/` | Dashboard pages: login, list, review, renewals |
| `templates_docx/` | Sample `.docx` templates with `{{placeholders}}` |
| `scripts/create_admin.py` | CLI to create/reset staff login accounts |
| `CLAUDE.md` | Notes for AI-assisted development on this repo (see below) |

## Features

- **Staff login** — dashboard is behind session-based auth. A default
  admin account is auto-created on first run (credentials printed to the
  console once) — replace it with your own via `scripts/create_admin.py`.
- **Documents tagged by customer** — every generated file is named
  `request_<id>_<phone>_<name>_<draft|final>.docx`, and the DB also
  stores `draft_file_path` / `final_file_path`, so any document can be
  traced back to its customer from either the filesystem or the database.
- **Renewal tracking** — once a customer confirms, the agreement's end
  date is calculated from `start_date` + `agreement_duration_months` and
  stored on the request. `/dashboard/renewals` lists everything ending
  within 30 days, with a one-click "Send Renewal Reminder" action.
- **Payment status** — each request tracks `payment_status`
  (unpaid/partial/paid) and `amount_paid`, editable from the review page
  and visible as a badge on the main dashboard list.

## Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env:
#   - ANTHROPIC_API_KEY   (required, from platform.claude.com)
#   - SESSION_SECRET      (change from the default before real use)
#   - WHATSAPP_TOKEN / WHATSAPP_PHONE_ID  (optional — stub mode without these)

export $(cat .env | xargs)      # or use python-dotenv / direnv
uvicorn main:app --reload
```

On first run, watch the console — it prints a default admin username and
password once:

```
No admin account found — created a default one:
  username: admin
  password: <random>
```

Log in at `http://localhost:8000/login` with those, then create your own
account and stop relying on the default one:

```bash
python3 scripts/create_admin.py
```

## Testing the flow without a live WhatsApp number

```bash
curl -X POST http://localhost:8000/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "entry": [{
      "changes": [{
        "value": {
          "messages": [{
            "from": "919876543210",
            "type": "text",
            "text": {"body": "Need a rental agreement for my 2bhk in kochi, rent is 15000, tenant name Rahul Menon, landlord name Suresh Kumar, deposit 30000, 11 months, starting 1 August 2026"}
          }]
        }
      }]
    }]
  }'
```

This mirrors the real shape Meta's Cloud API sends, so the same request
body works once you switch to a live webhook.

Then:
1. Log in and open `/dashboard` — the request appears with fields Claude extracted.
2. Open it, correct anything needed, enter your name, **Approve** — this generates the watermarked draft and (in stub mode) prints the WhatsApp message to the console instead of sending it.
3. Click **Customer Confirmed** to generate the final in-house copy and calculate the renewal date.
4. Update **Payment Status** as payment comes in.
5. Visit `/dashboard/renewals` to see agreements approaching their end date.

## Wiring up real WhatsApp

1. Create a Meta Developer account and a WhatsApp Business app.
2. Get a test number (free) and a temporary access token.
3. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, and `WHATSAPP_VERIFY_TOKEN` (make up any string) in `.env`.
4. Expose your local server with a tunnel (e.g. `ngrok http 8000`) for testing, or point at a deployed URL.
5. In the Meta App Dashboard's webhook setup, enter that URL + `/webhook/whatsapp` as the callback URL and the same string as `WHATSAPP_VERIFY_TOKEN` as the verify token, then subscribe to the `messages` field.

## Before this goes near a real customer

- [ ] Replace the stamp duty placeholder formula in `docgen.py` with your cousin's actual Kerala rates
- [ ] Replace the sample template wording in `templates_docx/` with the real templates
- [ ] Change `SESSION_SECRET` to a real random value (see `.env.example` for how to generate one)
- [ ] Remove/replace the default admin account
- [ ] Connect a real (non-test) WhatsApp Business number
- [ ] Move from SQLite to Postgres (Supabase/Neon — same SQLAlchemy code, just change the URL)
- [ ] Add HTTPS + a real domain when deploying (Render/Railway provide this by default)
- [ ] Review data retention — generated documents contain personal details; decide how long they're kept and who can access them

## Cost estimate (early-stage volume)

Roughly **₹1,900–2,400/month** — hosting on a free/low tier, WhatsApp
service-window replies (mostly free), and Claude Haiku usage at low
volume. Both WhatsApp and Claude are pay-per-use, so costs scale with
real traffic rather than jumping in flat-fee tiers.
