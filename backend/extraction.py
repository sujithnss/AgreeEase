"""
AI extraction engine — classifies the agreement type, extracts structured
fields, detects language, and drafts a follow-up question for missing
fields. Uses Claude Haiku for cost efficiency.
"""

import json
import os
from anthropic import Anthropic
from templates_config import TEMPLATES, required_fields_for

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5-20251001"

EXTRACTION_SYSTEM_PROMPT = """You are an assistant for a stamp paper / legal
document vendor in Kerala, India. Customers send informal WhatsApp messages
in English, Malayalam, or a mix of both, requesting an agreement.

Your job:
1. Identify which agreement type they want. Choose ONE key from this list: {template_keys}
   If unclear, use "unknown".
2. Extract any details already present in the message into a flat JSON object
   using field names relevant to that agreement type (e.g. landlord_name,
   tenant_name, monthly_rent, property_address, start_date, etc.)
   Use null for anything not mentioned. Do not guess or invent values.
3. Detect the language the customer wrote in: "english", "malayalam", or "mixed".

Respond with ONLY valid JSON in this exact shape, nothing else:
{{
  "agreement_type": "...",
  "language": "...",
  "extracted_fields": {{ ... }}
}}
"""

FOLLOWUP_SYSTEM_PROMPT = """You write short, polite WhatsApp follow-up
messages for a stamp paper vendor's customers in Kerala. Given a list of
missing fields for their agreement, write ONE short message asking for all
of them, in the SAME language the customer originally used (English or
Malayalam — use Malayalam script if they wrote in Malayalam). Keep it
friendly and brief, like a real staff member would text. Do not add
greetings or sign-offs, just the question(s)."""


def extract_from_message(message: str) -> dict:
    prompt = EXTRACTION_SYSTEM_PROMPT.format(template_keys=list(TEMPLATES.keys()))
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=prompt,
        messages=[{"role": "user", "content": message}],
    )
    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def find_missing_fields(agreement_type: str, extracted_fields: dict) -> list:
    required = required_fields_for(agreement_type)
    return [f for f in required if not extracted_fields.get(f)]


def generate_followup_question(missing_fields: list, language: str) -> str:
    if not missing_fields:
        return ""
    prompt = (
        f"Customer's language: {language}\n"
        f"Missing fields needed: {', '.join(missing_fields)}\n"
        f"Write the follow-up WhatsApp message now."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=FOLLOWUP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def process_message(message: str) -> dict:
    """Main entry point for a fresh customer message."""
    result = extract_from_message(message)
    agreement_type = result.get("agreement_type", "unknown")
    extracted_fields = result.get("extracted_fields", {})
    language = result.get("language", "english")

    missing = find_missing_fields(agreement_type, extracted_fields)
    next_question = generate_followup_question(missing, language)

    return {
        "agreement_type": agreement_type,
        "language": language,
        "extracted_fields": extracted_fields,
        "missing_fields": missing,
        "next_question": next_question,
        "status": "ready_for_staff_review" if not missing else "awaiting_customer_info",
    }


def merge_followup_reply(previous_fields: dict, agreement_type: str, reply_message: str, language: str) -> dict:
    """Called when a customer replies with the missing info. Merges the new
    reply into what we already extracted, then re-checks what's still missing."""
    prompt = (
        f"Agreement type: {agreement_type}\n"
        f"Already known fields (JSON): {json.dumps(previous_fields)}\n"
        f"Customer's new reply: \"{reply_message}\"\n\n"
        f"Extract any NEW field values from the reply and merge them with "
        f"the already known fields. Respond with ONLY the merged flat JSON "
        f"object of all fields, nothing else."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system="You extract and merge structured agreement data. Respond with ONLY valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    merged_fields = json.loads(raw)

    missing = find_missing_fields(agreement_type, merged_fields)
    next_question = generate_followup_question(missing, language)

    return {
        "extracted_fields": merged_fields,
        "missing_fields": missing,
        "next_question": next_question,
        "status": "ready_for_staff_review" if not missing else "awaiting_customer_info",
    }
