"""
Template registry — single source of truth for:
  - which agreement types exist
  - which fields are required for each
  - which .docx file to fill

The extraction engine, the missing-field checker, and the document
generator all read from this same dict, so there is only one place
to update when a template changes.
"""

TEMPLATES = {
    "rental_agreement": {
        "label": "Residential Rental / Licence Agreement",
        # Kerala stamp-paper agreements are drafted mostly in Malayalam in
        # practice, so that's the default file — English stays available
        # for the occasional customer (e.g. NRIs) who needs it instead.
        # Staff pick which one to generate on the review dashboard.
        "files": {
            "malayalam": "templates_docx/rental_agreement_ml.docx",
            "english": "templates_docx/rental_agreement_en.docx",
        },
        "required_fields": [
            "landlord_name",
            "tenant_name",
            "property_address",
            "monthly_rent",
            "security_deposit",
            "agreement_duration_months",
            "start_date",
        ],
        # Not gathered from the customer's WhatsApp message — staff fill
        # these in on the review dashboard before approving, since a
        # casual WhatsApp text won't include land-record-level detail
        # like age, parentage, Aadhar number, or survey schedule.
        "staff_fields": [
            "landlord_age",
            "landlord_parentage",
            "landlord_address",
            "landlord_aadhar",
            "tenant_age",
            "tenant_parentage",
            "tenant_address",
            "tenant_aadhar",
            "district",
            "sub_district",
            "taluk",
            "village",
            "desom",
            "local_authority",
            "house_no",
            "fee_due_day",
            "interest_rate_percent",
            "property_description",
        ],
    },
    "shop_agreement": {
        "label": "Shop / Commercial Lease Agreement",
        "files": {
            "english": "templates_docx/shop_agreement.docx",
        },
        "required_fields": [
            "landlord_name",
            "tenant_name",
            "shop_address",
            "monthly_rent",
            "security_deposit",
            "agreement_duration_months",
            "start_date",
            "business_type",
        ],
    },
    # Add more agreement types here as your cousin's team standardizes them.
}


def get_template(agreement_type: str):
    return TEMPLATES.get(agreement_type)


def required_fields_for(agreement_type: str):
    t = TEMPLATES.get(agreement_type)
    return t["required_fields"] if t else []


def get_template_file(agreement_type: str, language: str = "malayalam") -> str:
    """Resolves the .docx file for a given agreement type + document
    language. Falls back to English if the requested language has no file
    yet (e.g. shop_agreement is English-only for now), then to whatever's
    available, so a missing translation never blocks document generation."""
    t = TEMPLATES.get(agreement_type)
    if not t:
        raise ValueError(f"Unknown agreement type: {agreement_type}")
    files = t["files"]
    return files.get(language) or files.get("english") or next(iter(files.values()))


def available_languages_for(agreement_type: str) -> list:
    t = TEMPLATES.get(agreement_type)
    return list(t["files"].keys()) if t else []
