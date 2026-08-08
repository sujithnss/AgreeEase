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
        "label": "Residential Rental Agreement",
        "file": "templates_docx/rental_agreement.docx",
        "required_fields": [
            "landlord_name",
            "tenant_name",
            "property_address",
            "monthly_rent",
            "security_deposit",
            "agreement_duration_months",
            "start_date",
        ],
    },
    "shop_agreement": {
        "label": "Shop / Commercial Lease Agreement",
        "file": "templates_docx/shop_agreement.docx",
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
