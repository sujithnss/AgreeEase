"""
WhatsApp Flow (Meta's native multi-screen interactive form) support --
the structured replacement for extraction.py's free-text AI extraction on
brand-new agreement requests. The actual screens live in
backend/flows_json/agreement_request.json (hand-authored, not generated --
there are only two agreement types, and the Flow UI copy benefits from
hand-tuned labels more than it would from a code-generation layer). This
module only loads that file (for the one-off creation script) and turns a
completed Flow submission into the same shape process_message()/
merge_followup_reply() return, so main.py can treat a Flow completion
exactly like a fully-extracted free-text request.
"""

import json
import os

from templates_config import extractable_fields_for, required_fields_for
from extraction import resolve_doc_language

FLOW_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flows_json", "agreement_request.json")


def load_flow_json() -> dict:
    """Used only by scripts/create_whatsapp_flow.py to upload the Flow
    definition to Meta -- not read at request-serving time."""
    with open(FLOW_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_flow_response(agreement_type: str, response_json: dict) -> dict:
    """Turns a completed Flow's response_json (already json.loads()'d by
    the caller) into the same {agreement_type, language, doc_language,
    extracted_fields, missing_fields, next_question, status} shape
    extraction.process_message() returns, so main.py's webhook handler
    treats a Flow completion the same way as a free-text request.

    missing_fields should always come back empty -- every field is marked
    "required" in the Flow JSON, so Meta won't let the customer submit
    without them. This check is a defensive fallback, not decoration: if a
    field name ever drifts out of sync with templates_config.py, it still
    catches the gap and degrades into the existing chat-based follow-up
    path instead of silently losing data.
    """
    extracted_fields = {
        k: v for k, v in response_json.items()
        if k in extractable_fields_for(agreement_type)
    }
    missing_fields = [
        f for f in required_fields_for(agreement_type)
        if not str(extracted_fields.get(f) or "").strip()
    ]
    doc_language = resolve_doc_language("english", extracted_fields)
    next_question = None
    if missing_fields:
        next_question = (
            "We still need a few details to complete your request: "
            + ", ".join(missing_fields)
            + ". Please reply with this information."
        )
    return {
        "agreement_type": agreement_type,
        "language": doc_language,
        "doc_language": doc_language,
        "extracted_fields": extracted_fields,
        "missing_fields": missing_fields,
        "next_question": next_question,
        "status": "awaiting_customer_info" if missing_fields else "ready_for_staff_review",
    }
