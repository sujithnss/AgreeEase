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
        # This template's wording is the vendor's real house license
        # specimen (see templates_docx/rental_agreement_ml.docx docstring),
        # which was originally an 11-month-only document with "11" fixed
        # in the prose. Both .docx templates now reference it via
        # {{ agreement_duration_months }} rather than a literal "11", but
        # it's deliberately NOT asked of the customer over WhatsApp (see
        # staff_fields below) -- most agreements really are 11 months, and
        # asking adds a WhatsApp round-trip for the common case. It's a
        # staff_field instead: defaults to FIXED_DURATION_MONTHS (11) on
        # the review dashboard, editable there for the 24/36-month cases.
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
            # property_description (the formal "മാർജിൻ" schedule clause —
            # door no., local body, district) is NOT collected separately
            # here — customers on WhatsApp rarely have that land-record-
            # style detail on hand. Instead it's generated automatically
            # from this address at approval time — see
            # extraction.build_property_description() and its call site
            # in main.py's approve_request(). Make sure a door/building
            # number and municipality/panchayat are asked for as part of
            # this field's follow-up question (see FOLLOWUP_SYSTEM_PROMPT
            # in extraction.py) so there's enough to work with.
            "property_address",
            "monthly_rent",
            "security_deposit",
            "start_date",
        ],
        # Not collected from the customer over WhatsApp — staff fill these
        # in (or confirm the pre-filled default) on the review dashboard
        # before approving. agreement_duration_months is pre-filled with
        # FIXED_DURATION_MONTHS's default (11) on the review page (see
        # main.py: review_request()'s field_defaults), rather than left
        # blank like fee_due_day, since most agreements really are 11
        # months and defaulting saves staff from typing it every time.
        "staff_fields": [
            "fee_due_day",
            "agreement_duration_months",
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
    # Add more agreement types here as the business standardizes them.
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


# Default duration per agreement type, used when a request has no
# agreement_duration_months value -- either because the customer never
# stated one (11 months is the traditional default for this business) or
# because the request predates agreement_duration_months becoming a
# collected field for rental_agreement.
FIXED_DURATION_MONTHS = {
    "rental_agreement": 11,
}


def duration_months_for(agreement_type: str, fields: dict):
    """Resolves the agreement duration in months, for both end-date
    calculations (see dateutils.calculate_agreement_end_date) and the
    document body itself: whatever value is present in the fields dict,
    falling back to the template's default duration otherwise."""
    value = fields.get("agreement_duration_months")
    if value:
        return value
    return FIXED_DURATION_MONTHS.get(agreement_type)
