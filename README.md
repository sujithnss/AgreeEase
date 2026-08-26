# AgreeEase

**Live deployment:** https://agreeease.onrender.com/login

AI-assisted agreement drafting for stamp paper vendors. A customer's
WhatsApp message (English/Malayalam) is read by AI, structured into an
agreement draft, reviewed by staff, sent to the customer as a
**watermarked PDF draft only**, and — once confirmed — a clean copy is
generated for **in-house printing only**. Every request is tracked
through payment status and, later, renewal.

```
Customer (WhatsApp) → AI extraction (Groq/Llama) → Staff review dashboard
   → Watermarked PDF draft to customer → Customer confirms
   → Clean final copy generated (in-house print only)
   → Payment & renewal tracked in DB
```

The customer-facing draft is a real PDF (converted from the filled
`.docx` via headless LibreOffice) with a proper diagonal, translucent
watermark baked into the page header — not a `.docx` attachment or a
bold banner line — so it previews cleanly on WhatsApp and reads like an
actual watermarked draft. See "Watermarked PDF drafts" under Features.

## What's included

| File | Purpose |
|---|---|
| `backend/main.py` | FastAPI app — webhook + staff dashboard routes |
| `backend/extraction.py` | Groq (Llama)-based classification, field extraction, follow-up questions |
| `backend/templates_config.py` | Registry of agreement types & required fields (single source of truth) |
| `backend/docgen.py` | Fills `.docx` templates, computes stamp duty, injects the diagonal watermark, converts drafts to PDF, tags filenames with customer identity |
| `backend/db.py` | SQLite models — requests, audit log, admin users |
| `backend/auth.py` | Staff login (session cookies, PBKDF2 password hashing) |
| `backend/dateutils.py` | Agreement end-date calculation (dependency-free) |
| `backend/whatsapp.py` | WhatsApp Cloud API sender — uploads the PDF/docx and sends it as a document message (console "stub" mode until real credentials are added) |
| `backend/templates_html/` | Dashboard pages: login, list, review, renewals |
| `templates_docx/` | Sample `.docx` templates with `{{placeholders}}` |
| `scripts/create_admin.py` | CLI to create/reset staff login accounts |
| `Dockerfile` | Render deploy image — installs headless LibreOffice + Malayalam fonts so `docgen.py` can convert drafts to PDF (Render's native Python runtime can't install system packages) |
| `render.yaml` | Render Blueprint — Docker-based web service config + env var list |
| `CLAUDE.md` | Notes for AI-assisted development on this repo (see below) |

## Features

- **Watermarked PDF drafts** — on approval, the filled `.docx` draft is
  converted to PDF (headless LibreOffice) and sent to the customer as a
  WhatsApp document message, so it previews properly on a phone instead
  of showing up as a generic file icon. The watermark itself is a real
  diagonal, translucent stamp injected into the page header — the same
  VML shape technique Word's own "Insert Watermark" feature generates —
  so it repeats behind the content on every page rather than being a
  bold line of text at the top. It's applied dynamically in `docgen.py`,
  not baked into the `.docx` template files, and only ever appears on
  the draft — the final in-house copy has no header/watermark at all.
  If LibreOffice isn't available, sending falls back to the raw
  watermarked `.docx` rather than blocking the approval.
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
#   - GROQ_API_KEY        (required, from console.groq.com)
#   - SESSION_SECRET      (change from the default before real use)
#   - WHATSAPP_TOKEN / WHATSAPP_PHONE_ID  (optional — stub mode without these)

export $(cat .env | xargs)      # or use python-dotenv / direnv
uvicorn main:app --reload
```

For draft PDFs to actually generate locally, install LibreOffice too
(`brew install --cask libreoffice` on Mac, `apt-get install libreoffice`
on Linux) — without it, `docgen.py` falls back to sending the raw
watermarked `.docx` instead of a PDF. Production (Render) gets this via
the `Dockerfile` instead — see "Deploying" below.

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
1. Log in and open `/dashboard` — the request appears with fields the AI extracted.
2. Open it, correct anything needed, enter your name, **Approve** — this generates the watermarked PDF draft and (in stub mode) prints the WhatsApp message to the console instead of sending it.
3. Click **Customer Confirmed** to generate the final in-house copy and calculate the renewal date.
4. Update **Payment Status** as payment comes in.
5. Visit `/dashboard/renewals` to see agreements approaching their end date.

## Wiring up real WhatsApp

1. Create a Meta Developer account and a WhatsApp Business app.
2. Get a test number (free) and a temporary access token.
3. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, and `WHATSAPP_VERIFY_TOKEN` (make up any string) in `.env`.
4. Expose your local server with a tunnel (e.g. `ngrok http 8000`) for testing, or point at a deployed URL.
5. In the Meta App Dashboard's webhook setup, enter that URL + `/webhook/whatsapp` as the callback URL and the same string as `WHATSAPP_VERIFY_TOKEN` as the verify token, then subscribe to the `messages` field.

## Deploying (Render)

`render.yaml` defines a **Docker**-based web service (`runtime: docker`,
building from the repo-root `Dockerfile`) rather than Render's native
Python runtime. That's a deliberate choice, not the default: Render's
native runtime build environment is sandboxed and read-only, so it can't
`apt-get install` anything — and LibreOffice (needed for the docx→PDF
conversion above) is a system package, not a pip one. The Dockerfile
installs LibreOffice plus `fonts-noto` (for the Malayalam template's
glyphs — without it LibreOffice renders Malayalam text as tofu boxes in
the converted PDF), then installs `backend/requirements.txt` as usual.

To deploy:
1. Connect the repo in Render as a Blueprint (uses `render.yaml` as-is).
2. Fill in the `sync: false` env vars in the Render dashboard (`GROQ_API_KEY`,
   `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`,
   `DEFAULT_ADMIN_PASSWORD`) — these aren't stored in git.
3. Note that switching an *existing* service from the native Python
   runtime to Docker isn't an in-place change on Render's side — you'll
   likely need to recreate the service (or create a new one) from the
   updated blueprint, and re-enter those secrets.

Expect noticeably longer build times than the old native-runtime setup
(LibreOffice is a large install), and keep an eye on memory — the free
plan's 512MB can be tight for headless LibreOffice conversions under
load.

## Before this goes near a real customer

- [ ] Replace the stamp duty placeholder formula in `docgen.py` with your cousin's actual Kerala rates
- [ ] Replace the sample template wording in `templates_docx/` with the real templates
- [ ] Change `SESSION_SECRET` to a real random value (see `.env.example` for how to generate one)
- [ ] Remove/replace the default admin account
- [ ] Connect a real (non-test) WhatsApp Business number
- [ ] Move from SQLite to Postgres (Supabase/Neon — same SQLAlchemy code, just change the URL)
- [ ] Add HTTPS + a real domain when deploying (Render/Railway provide this by default)
- [ ] Review data retention — generated documents contain personal details; decide how long they're kept and who can access them
- [ ] Confirm the deployed watermark/PDF pipeline end-to-end on Render (approve a real request, check the customer actually gets a watermarked PDF, not a `.docx` fallback)
- [ ] Get legal sign-off that the diagonal watermark text/placement reads clearly as "not valid for stamping" on an actual printed/viewed page, not just in the source XML

## Cost estimate (early-stage volume)

Roughly **₹1,900–2,400/month** — hosting on a free/low tier, WhatsApp
service-window replies (mostly free), and Groq's Llama models at low
volume (Groq's free tier covers meaningful usage before any billing
kicks in). Both WhatsApp and Groq are pay-per-use past the free tier, so
costs scale with real traffic rather than jumping in flat-fee tiers.
Docker builds on Render's free plan don't add direct cost, but do use
more build minutes than the old native-runtime setup.
