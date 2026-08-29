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
| `backend/db.py` | SQLAlchemy models — requests, audit log, admin users. SQLite by default; reads `DATABASE_URL` for a hosted Postgres instead (needed on Render — see "Database persistence" below) |
| `backend/auth.py` | Staff login (session cookies, PBKDF2 password hashing) |
| `backend/dateutils.py` | Agreement end-date calculation (dependency-free) |
| `backend/whatsapp.py` | WhatsApp Cloud API sender — uploads the PDF/docx and sends it as a document message (console "stub" mode until real credentials are added) |
| `backend/templates_html/` | Dashboard pages: login, list (auto-refreshing), review, renewals, documents, staff, reports |
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
- **Auto-transliteration into Malayalam script** — if a customer types in
  English but asks for a Malayalam document, names/addresses typed in
  Latin script are auto-transliterated (not translated — "Rahul Menon"
  becomes "രാഹുൽ മേനോൻ" by sound, not by meaning) at approval time, via
  one Groq call in `extraction.py`. Only fields that actually contain
  Latin letters are sent (dates/amounts are skipped), the result is
  persisted on the request so the draft and final copy always match, and
  it falls back to the original English text — never blocking approval —
  if the call fails.
- **Live dashboard** — `/dashboard` polls a lightweight endpoint every
  10s and reloads automatically when a new WhatsApp message arrives or
  a request's status changes, so staff don't have to keep hitting refresh.
- **Staff login** — dashboard is behind session-based auth. A default
  admin account is auto-created on first run (credentials printed to the
  console once) — replace it with your own via `scripts/create_admin.py`,
  or manage accounts (add / reset password / remove) from
  `/dashboard/staff` once you're logged in. The approval form's "Staff
  Name" is a dropdown of real accounts, not free text.
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
- **Reports** (`/dashboard/reports`) — KPI tiles (total / completed /
  pending / new-in-range), a daily request-volume trend chart, a
  pipeline-status breakdown, and a by-agreement-type breakdown, filterable
  to the last 7/30/90 days. Plain inline SVG + vanilla JS, no charting
  library. Deliberately no pie charts: the pipeline breakdown is a
  stacked bar with a darkening blue ramp (it's a real stage progression,
  not arbitrary categories), and the type breakdown is a single-hue bar
  chart, since a bar reads more accurately than a pie for comparing a
  handful of values.

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
#   - DATABASE_URL        (optional locally — leave blank for SQLite; required
#                          in production, see "Database persistence" below)

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
1. Log in and open `/dashboard` — the request appears with fields the AI extracted (the page auto-refreshes, so a new message shows up without a manual reload).
2. Open it, correct anything needed, pick your name from the **Staff Name** dropdown, **Approve** — this generates the watermarked PDF draft (transliterating any English-typed name/address fields into Malayalam first, if that's the document language) and, in stub mode, prints the WhatsApp message to the console instead of sending it.
3. Click **Customer Confirmed** to generate the final in-house copy and calculate the renewal date.
4. Update **Payment Status** as payment comes in.
5. Visit `/dashboard/renewals` to see agreements approaching their end date, or `/dashboard/reports` for volume/pipeline charts.

## Wiring up real WhatsApp

1. Create a Meta Developer account and a WhatsApp Business app.
2. Get a test number (free) and a temporary access token.
3. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, and `WHATSAPP_VERIFY_TOKEN` (make up any string) in `.env`.
4. Also set `WHATSAPP_APP_SECRET` to the app's "App Secret" (App Dashboard
   → Settings → Basic — a different value from the token/verify-token
   above). This is required, not optional, once the webhook URL is public:
   without it, `main.py` can't verify Meta's `X-Hub-Signature-256` header,
   which means anyone who finds the URL could POST fabricated "customer
   messages" straight into the pipeline — each one triggers a paid Groq
   API call. Left unset, signature checking is skipped entirely (only
   fine for local testing where nothing calls the webhook but you).
5. Expose your local server with a tunnel (e.g. `ngrok http 8000`) for testing, or point at a deployed URL.
6. In the Meta App Dashboard's webhook setup, enter that URL + `/webhook/whatsapp` as the callback URL and the same string as `WHATSAPP_VERIFY_TOKEN` as the verify token, then subscribe to the `messages` field.

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
2. Fill in the `sync: false` env vars in the Render dashboard (`DATABASE_URL`,
   `GROQ_API_KEY`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`,
   `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `DEFAULT_ADMIN_PASSWORD`) — these aren't stored
   in git. See "Database persistence" below for `DATABASE_URL` specifically
   — without it, all data is lost on the next deploy.
3. Note that switching an *existing* service from the native Python
   runtime to Docker isn't an in-place change on Render's side — you'll
   likely need to recreate the service (or create a new one) from the
   updated blueprint, and re-enter those secrets.

Expect noticeably longer build times than the old native-runtime setup
(LibreOffice is a large install), and keep an eye on memory — the free
plan's 512MB can be tight for headless LibreOffice conversions under
load.

## Database persistence

Render's web service filesystem is **ephemeral** — every deploy builds a
fresh container, and any file written at runtime (including the SQLite
file at `backend/app.db`) is gone the moment the old container is
replaced. This is true regardless of the native Python runtime vs.
Docker; SQLite on a Render web service simply doesn't survive a
redeploy or restart.

Fix: set `DATABASE_URL` to a hosted Postgres instance — [Neon](https://neon.tech)
or [Supabase](https://supabase.com) both have a free tier. `db.py` reads
it automatically and falls back to the local SQLite file only when it's
unset, so this is a config change, not a code change:

1. Create a project on Neon/Supabase, then find the connection string
   (Neon: click **Connect** on the project dashboard — it's in the modal
   that opens, along with a pooled/direct toggle; leave pooling on).
2. Paste it as `DATABASE_URL` in Render's dashboard (Environment tab) and
   redeploy. `postgres://`-style URLs are rewritten to `postgresql://`
   automatically (psycopg2 requires the latter).
3. The first boot against a fresh Postgres database creates every table
   from scratch via `create_all()` — there's nothing to migrate forward
   from SQLite, since that data was already being wiped on every deploy.
4. Re-run `scripts/create_admin.py` (or let the auto-created default
   admin print to the logs again) — the new database starts empty.

## Before this goes near a real customer

- [ ] Replace the stamp duty placeholder formula in `docgen.py` with your cousin's actual Kerala rates
- [ ] Replace the sample template wording in `templates_docx/` with the real templates
- [ ] Change `SESSION_SECRET` to a real random value (see `.env.example` for how to generate one)
- [ ] Remove/replace the default admin account
- [ ] Connect a real (non-test) WhatsApp Business number
- [ ] Set up regular backups for the Postgres database (Neon/Supabase both offer point-in-time recovery on paid tiers — check what the free tier actually covers)
- [ ] Add HTTPS + a real domain when deploying (Render/Railway provide this by default)
- [ ] Review data retention — generated documents contain personal details; decide how long they're kept and who can access them
- [ ] Confirm the deployed watermark/PDF pipeline end-to-end on Render (approve a real request, check the customer actually gets a watermarked PDF, not a `.docx` fallback)
- [ ] Get legal sign-off that the diagonal watermark text/placement reads clearly as "not valid for stamping" on an actual printed/viewed page, not just in the source XML
- [x] Verify the WhatsApp webhook's `X-Hub-Signature-256` header (set `WHATSAPP_APP_SECRET`) so the endpoint isn't open to anyone who finds the URL
- [x] Rate-limit the webhook (`WEBHOOK_RATE_LIMIT_PER_PHONE` / `WEBHOOK_RATE_LIMIT_GLOBAL`, see `ratelimit.py`) so a bug or abuse can't run up the Groq bill
- [ ] Resolve Aadhaar handling before collecting it again — `rental_agreement`'s `required_fields` deliberately excludes `landlord_aadhar`/`tenant_aadhar` for now (the document template still has the placeholder; it just renders blank). Get a real answer — ideally legal advice — on consent/storage/retention under India's Aadhaar Act and the DPDP Act 2023 before turning collection back on. In the meantime, Aadhaar details need to be collected offline (in person, at signing).

## Cost estimate (early-stage volume)

Roughly **₹1,900–2,400/month** — hosting on a free/low tier, WhatsApp
service-window replies (mostly free), and Groq's Llama models at low
volume (Groq's free tier covers meaningful usage before any billing
kicks in). Both WhatsApp and Groq are pay-per-use past the free tier, so
costs scale with real traffic rather than jumping in flat-fee tiers.
Docker builds on Render's free plan don't add direct cost, but do use
more build minutes than the old native-runtime setup. Neon/Supabase's
free Postgres tier covers this comfortably too, though both auto-suspend
an idle database after a period of inactivity (briefly slower on the
next request while it wakes back up) — not a real cost, just worth
knowing about.
