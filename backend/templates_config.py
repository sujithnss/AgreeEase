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
        # Address is asked over WhatsApp like everything else here
        # (customers do state it, and it saves staff from retyping what
        # the customer already gave) — but the WhatsApp side won't always
        # get a usable answer, so it stays editable on the review
        # dashboard as a correction/completion path, same as the rest of
        # required_fields. Age is deliberately NOT required (see
        # optional_fields below) — many customers never volunteer it and
        # chasing it over WhatsApp just for a document detail isn't worth
        # the round-trip.
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
            "landlord_address",
            "tenant_name",
            "tenant_address",
            "monthly_rent",
            "security_deposit",
            "start_date",
            "fee_due_day",
            # district/local_body_type/local_body_name/property_type feed
            # the formal "മാർജിൻ" schedule clause (property_description) --
            # composed deterministically from these structured fields by
            # extraction.build_property_description(), called from
            # main.py's approve_request(). Collected as their own fields
            # (via the WhatsApp Flow -- see backend/flows_json/
            # agreement_request.json) rather than guessed out of free text,
            # since customers rarely type land-record-style detail
            # unprompted but will answer a direct structured question.
            "district",
            "local_body_type",
            "local_body_name",
            "property_type",
        ],
        # Fields the AI should still try to pick up if the customer
        # happens to volunteer them (see extraction.extractable_fields_for()
        # / merge_followup_reply()), but never asks a follow-up question
        # about and never blocks approval on. If a value is present it goes
        # into the document (both .docx templates wrap the age clause in
        # {% if landlord_age %}/{% if tenant_age %} so it renders cleanly
        # either way); if it's blank, the age phrase is omitted from the
        # document entirely rather than printing with a blank/zero age.
        # Editable on the review dashboard like required_fields, just
        # without the "still missing" red-flagging.
        #
        # flat_name/quarters_name/floor_number/building_number/house_portion/
        # house_number are the dynamic follow-up to property_type -- only
        # the subset matching whatever was picked (Flat/Apartment, House,
        # or Quarters) is ever filled in. All optional (never block
        # approval) since required_fields_for() has no concept of "required
        # only when property_type == X" -- the Flow JSON enforces that
        # narrower rule itself (house_portion is required there, but only
        # while visible, i.e. only when property_type == 'house'). None of
        # these block approval if blank; build_property_description()
        # already composes a usable clause from whichever subset is present.
        # property_address is a free-text door-number/extra-detail field --
        # optional because district/local_body_name alone are already
        # enough for a usable clause; this just lets it prepend a door
        # number when the customer happens to give one.
        "optional_fields": [
            "landlord_age",
            "tenant_age",
            "flat_name",
            "quarters_name",
            "floor_number",
            "building_number",
            "house_portion",
            "house_number",
            "property_address",
        ],
        # Not collected from the customer over WhatsApp — staff fill these
        # in (or confirm the pre-filled default) on the review dashboard
        # before approving. All are pre-filled with sensible defaults on
        # the review page (see main.py: review_request()'s field_defaults
        # — FIXED_DURATION_MONTHS for duration, DEFAULT_RENEWAL_FEE_INCREASE_TERMS
        # for renewal_fee_increase_terms) since most agreements use the
        # common case and defaulting saves staff from typing it every time.
        "staff_fields": [
            "agreement_duration_months",
            # Clause (13)'s renewal wording — "കാലാനുസൃതമായ [ലൈസൻസ് ഫീസ്
            # വർദ്ധനവ്]" ("a customary/prevailing-rate [license fee
            # increase]" on renewal). Defaults to that same wording so the
            # clause reads exactly as the vendor specimen originally did,
            # but staff can override it with a specific rate/term per
            # agreement (e.g. "10%").
            "renewal_fee_increase_terms",
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


def extractable_fields_for(agreement_type: str) -> list:
    """required_fields_for() plus optional_fields -- the full set of field
    names the AI is allowed to pull out of a customer's message. Used only
    for the extraction/merge schema (extraction.py), so an optional field
    still gets captured when a customer volunteers it unprompted. Missing-
    field checks and follow-up questions must keep using required_fields_for()
    alone, since optional fields are never asked about or blocked on."""
    t = TEMPLATES.get(agreement_type)
    if not t:
        return []
    return required_fields_for(agreement_type) + list(t.get("optional_fields", []))


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


# Fallback day-of-month rent is due, pre-filled on the review dashboard
# (see main.py: review_request()'s field_defaults) only for requests
# missing fee_due_day -- it's now a customer-provided required_field via
# the WhatsApp Flow, so this mainly covers pre-Flow legacy requests.
DEFAULT_FEE_DUE_DAY = 5

# Default wording for clause (13)'s renewal_fee_increase_terms staff_field
# — the vendor specimen's original phrase describing the license fee
# increase on renewal ("a customary/prevailing-rate [fee increase]").
# Pre-filled on the review dashboard so the clause reads exactly as it did
# before this became an editable field; staff override it with a specific
# rate/term (e.g. "10%") when an agreement needs one.
DEFAULT_RENEWAL_FEE_INCREASE_TERMS = "കാലാനുസൃതമായ"
