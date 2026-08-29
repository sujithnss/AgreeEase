"""
Template registry — single source of truth for:
  - which agreement types exist
  - which fields are required for each
  - which .docx file to fill

The extraction engine, the missing-field checker, and the document
generator all read from this same dict, so there is only one place
to update when a template changes.
"""

# Universal field asked for on any agreement type where more than one
# document language is available. Not part of the document body itself
# (docxtpl ignores unused context keys) — it only drives which template
# file gets generated, and is shown to staff for transparency into what
# the customer actually said. Applied automatically in required_fields_for()
# rather than listed by hand in each TEMPLATES entry, since it's a
# workflow concern (which file to use), not document content.
PREFERRED_LANGUAGE_FIELD = "preferred_document_language"

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
        # This template's wording is the vendor's real 11-month house
        # license specimen verbatim (see templates_docx/rental_agreement_ml.docx
        # docstring) — the duration is fixed prose, not a field, so
        # agreement_duration_months is deliberately absent here (unlike
        # shop_agreement below). See FIXED_DURATION_MONTHS.
        #
        # Age/address are asked over WhatsApp like everything else here
        # (customers do state them, and it saves staff from retyping what
        # the customer already gave) — but the WhatsApp side won't always
        # get a usable answer, so these stay editable on the review
        # dashboard as a correction/completion path, same as the rest of
        # required_fields.
        #
        # Aadhaar numbers are deliberately NOT collected here, even though
        # the real vendor specimen's document body has a placeholder for
        # them (templates_docx/rental_agreement_ml.docx: {{ landlord_aadhar }}
        # / {{ tenant_aadhar }}). Collecting and storing a government ID
        # number over WhatsApp carries real obligations under India's
        # Aadhaar Act and the DPDP Act 2023 (consent language, storage/
        # retention limits, breach handling) that this project hasn't
        # worked through yet. Until that's resolved, staff should collect
        # Aadhaar details offline (in person, at signing) rather than
        # through this system — the placeholder just renders blank
        # (docxtpl renders a missing context key as empty, not an error).
        "required_fields": [
            "landlord_name",
            "landlord_age",
            "landlord_address",
            "tenant_name",
            "tenant_age",
            "tenant_address",
            "property_address",
            "monthly_rent",
            "security_deposit",
            "start_date",
        ],
        # Land-record-level detail a casual WhatsApp message won't include —
        # staff fill these in on the review dashboard before approving.
        # property_description is the precise legal description of the
        # property (taluk/village/door no./etc.) written as one flowing
        # sentence, matching how the real specimen writes its "മാർജിൻ"
        # clause — not broken into separate district/taluk/village fields.
        "staff_fields": [
            "fee_due_day",
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


def required_fields_for(agreement_type: str) -> list:
    t = TEMPLATES.get(agreement_type)
    if not t:
        return []
    fields = list(t["required_fields"])
    if len(t["files"]) > 1:
        fields.append(PREFERRED_LANGUAGE_FIELD)
    return fields


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


# Templates whose wording fixes the agreement duration in prose (so it's
# not a customer/staff-editable field — see rental_agreement above).
FIXED_DURATION_MONTHS = {
    "rental_agreement": 11,
}


def duration_months_for(agreement_type: str, fields: dict):
    """Resolves the agreement duration in months for end-date calculations
    (see dateutils.calculate_agreement_end_date): whatever value is present
    in the fields dict, falling back to a template's fixed duration for
    templates like rental_agreement that don't collect the field at all."""
    value = fields.get("agreement_duration_months")
    if value:
        return value
    return FIXED_DURATION_MONTHS.get(agreement_type)
