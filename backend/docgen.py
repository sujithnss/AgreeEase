"""
Fills the appropriate .docx template with verified data using docxtpl.
Same template is reused for both the customer-facing watermarked draft
and the final in-house print copy — only `watermark_text` differs.

Stamp duty calculation here is a placeholder formula — replace with the
actual Kerala stamp duty rules your cousin's business uses.
"""

import os
import re
import shutil
import subprocess
import datetime
from docxtpl import DocxTemplate
from templates_config import get_template
from dateutils import calculate_agreement_end_date

# Resolve paths relative to the project root (one level up from backend/),
# so this works regardless of the directory the app is launched from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _slugify(value: str, max_len: int = 30) -> str:
    """Turns a name/phone into a filesystem- and URL-safe slug, e.g.
    'Rahul Menon' -> 'rahul-menon'. Used to tag filenames so a document
    can be traced back to its customer just by its filename."""
    if not value:
        return "unknown"
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:max_len] or "unknown"


def calculate_stamp_duty(agreement_type: str, fields: dict) -> str:
    """
    PLACEHOLDER LOGIC — replace with real Kerala stamp duty rules.
    Rental/lease stamp duty is often a percentage of (annual rent + deposit),
    but exact rates depend on agreement type, duration, and local rules.
    Keep this as a single function so it's easy for your cousin's team
    to correct without touching the rest of the code.
    """
    try:
        monthly_rent = float(str(fields.get("monthly_rent", 0)).replace(",", ""))
        deposit = float(str(fields.get("security_deposit", 0)).replace(",", ""))
        months = int(fields.get("agreement_duration_months", 11))
    except (ValueError, TypeError):
        return "TO BE CALCULATED BY STAFF"

    annual_value = (monthly_rent * 12) + deposit
    # Placeholder: 1% of annual value, staff should verify against actual rules
    duty = round(annual_value * 0.01, 2)
    return f"{duty:,.2f}"


def generate_document(
    request_id: int,
    agreement_type: str,
    fields: dict,
    draft: bool = True,
    customer_phone: str = "",
    customer_name: str = "",
) -> str:
    """
    Generates the filled document.
    draft=True  -> watermarked "DRAFT - NOT VALID" copy for customer review
    draft=False -> clean copy for in-house printing only (staff use)

    The output filename is tagged with the customer's phone number and
    name (e.g. request_12_919876543210_rahul-menon_final.docx) so a
    document can be identified and traced back to its customer directly
    from the generated/ folder, in addition to the DB record.
    """
    template_info = get_template(agreement_type)
    if not template_info:
        raise ValueError(f"Unknown agreement type: {agreement_type}")

    template_path = os.path.join(PROJECT_ROOT, template_info["file"])
    doc = DocxTemplate(template_path)

    context = dict(fields)
    context["agreement_number"] = f"AGR-{request_id:06d}"
    context["today_date"] = datetime.date.today().strftime("%d-%m-%Y")
    context["stamp_duty_amount"] = calculate_stamp_duty(agreement_type, fields)
    context["watermark_text"] = "*** DRAFT - NOT VALID FOR STAMPING ***" if draft else ""
    end_date = calculate_agreement_end_date(fields)
    context["end_date"] = end_date.strftime("%d-%m-%Y") if end_date else ""

    doc.render(context)

    suffix = "draft" if draft else "final"
    phone_tag = _slugify(customer_phone, max_len=15)
    name_tag = _slugify(customer_name or fields.get("tenant_name", ""))
    filename = f"request_{request_id}_{phone_tag}_{name_tag}_{suffix}.docx"
    out_path = os.path.join(OUTPUT_DIR, filename)
    doc.save(out_path)
    return out_path


def convert_to_pdf(docx_path: str) -> str:
    """
    Converts a .docx to .pdf, so the customer-facing watermarked draft can be
    sent as a WhatsApp-friendly PDF preview instead of a raw .docx.

    Tries headless LibreOffice first — the production-viable path, works the
    same on Mac (brew install --cask libreoffice) and Linux (apt-get install
    libreoffice). Falls back to docx2pdf (drives MS Word) for local Mac/
    Windows dev machines that have Word but not LibreOffice installed yet —
    NOT viable on a Linux production host, install LibreOffice there instead.
    """
    pdf_path = os.path.splitext(docx_path)[0] + ".pdf"

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        outdir = os.path.dirname(docx_path)
        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", outdir, docx_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("PDF conversion timed out after 60s")
        if result.returncode != 0:
            raise RuntimeError(f"PDF conversion failed: {result.stderr or result.stdout}")
        if not os.path.exists(pdf_path):
            raise RuntimeError(f"PDF conversion did not produce the expected file: {pdf_path}")
        return pdf_path

    try:
        from docx2pdf import convert
    except ImportError:
        raise RuntimeError(
            "No PDF converter available. Install LibreOffice ('brew install "
            "--cask libreoffice' locally, 'apt-get install libreoffice' on "
            "the production host) for the path that works everywhere."
        )
    try:
        convert(docx_path, pdf_path)
    except BaseException as e:
        # docx2pdf raises SystemExit (not a normal Exception) on failure,
        # e.g. AppleScript/Word automation errors — must be caught broadly
        # so callers relying on RuntimeError to gracefully fall back to the
        # .docx don't get a crash instead.
        raise RuntimeError(f"docx2pdf conversion via MS Word failed: {e}")
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"PDF conversion did not produce the expected file: {pdf_path}")
    return pdf_path
