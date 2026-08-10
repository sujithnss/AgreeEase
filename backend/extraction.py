"""
AI extraction engine — classifies the agreement type, extracts structured
fields, detects language, and drafts a follow-up question for missing
fields. Uses Groq's OpenAI-compatible chat completions API for cost
efficiency and speed.
"""

import json
import os
import requests
from templates_config import TEMPLATES, required_fields_for

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def _chat(system: str, user: str, max_tokens: int) -> str:
    response = requests.post(
        GROQ_CHAT_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _schema_block() -> str:
    lines = []
    for key, info in TEMPLATES.items():
        fields = ", ".join(info["required_fields"])
        lines.append(f'- "{key}": {fields}')
    return "\n".join(lines)


EXTRACTION_SYSTEM_PROMPT = """You are an assistant for a stamp paper / legal
document vendor in Kerala, India. Customers send informal WhatsApp messages
in English, Malayalam, or a mix of both, requesting an agreement.

Your job:
1. Identify which agreement type they want. Choose ONE key from this list of
   agreement types and their exact field names:
{schema_block}
   If unclear, use "unknown".
2. Extract any details already present in the message into a flat JSON object.
   You MUST use EXACTLY the field names listed above for the chosen agreement
   type — do not invent your own field names or synonyms (e.g. use
   "monthly_rent", never "rent"; use "agreement_duration_months", never
   "lease_duration"). Use null for anything not mentioned. Do not guess or
   invent values. Any date field (e.g. "start_date") MUST be normalized to
   DD-MM-YYYY format, regardless of how the customer wrote it (e.g. "1 August
   2026" or "1/8/26" both become "01-08-2026").
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
    prompt = EXTRACTION_SYSTEM_PROMPT.format(schema_block=_schema_block())
    raw = _chat(prompt, message, max_tokens=1000)
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
    return _chat(FOLLOWUP_SYSTEM_PROMPT, prompt, max_tokens=300)


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


_COMPLETION_MESSAGE_EN = "Thank you! Your details are with our team for review. We'll send you a draft shortly."
_COMPLETION_MESSAGE_ML = "നന്ദി! നിങ്ങളുടെ വിവരങ്ങൾ ഞങ്ങളുടെ ടീം പരിശോധിക്കുന്നുണ്ട്. ഉടൻ തന്നെ ഡ്രാഫ്റ്റ് അയച്ചുതരുന്നതാണ്."


def completion_message(language: str) -> str:
    """Fixed (non-AI-generated) confirmation text sent once all required
    fields are in, in the customer's own language. Kept as static text
    rather than another LLM call — it's the same message every time, so
    there's no reason to pay the latency/cost of generating it."""
    if language == "malayalam":
        return _COMPLETION_MESSAGE_ML
    if language == "mixed":
        return f"{_COMPLETION_MESSAGE_EN}\n{_COMPLETION_MESSAGE_ML}"
    return _COMPLETION_MESSAGE_EN


_DRAFT_READY_MESSAGE_EN = "Here is your draft agreement for review. Please confirm if all details are correct."
_DRAFT_READY_MESSAGE_ML = "ഇതാ നിങ്ങളുടെ കരാർ ഡ്രാഫ്റ്റ്. എല്ലാ വിവരങ്ങളും ശരിയാണെന്ന് ഉറപ്പുവരുത്തി ദയവായി സ്ഥിരീകരിക്കുക."


def draft_ready_message(doc_language: str) -> str:
    """Sent alongside the draft attachment — matched to the DOCUMENT's
    language (staff's choice on approval), not the customer's original
    message language, since it should read naturally next to what they're
    actually looking at."""
    return _DRAFT_READY_MESSAGE_ML if doc_language == "malayalam" else _DRAFT_READY_MESSAGE_EN


def renewal_reminder_message(language: str, customer_name: str) -> str:
    name = customer_name or ""
    if language == "malayalam":
        return (
            f"നമസ്കാരം {name}, നിങ്ങളുടെ കരാറിന്റെ കാലാവധി അടുത്തുവരുന്നു. "
            f"പുതുക്കുന്നതിനായി ഞങ്ങളെ ബന്ധപ്പെടുക."
        )
    return f"Hi {name}, your agreement is approaching its renewal date. Please contact us to renew."


def merge_followup_reply(previous_fields: dict, agreement_type: str, reply_message: str, language: str) -> dict:
    """Called when a customer replies with the missing info. Extracts values
    from ONLY the new reply, then merges into what we already have in
    Python — the model never sees (or gets a chance to rewrite) the
    already-known fields, so it can't hallucinate a replacement value for
    something the customer didn't actually mention in this message."""
    prompt = (
        f"Agreement type: {agreement_type}\n"
        f"Required field names for this agreement type: {', '.join(required_fields_for(agreement_type))}\n"
        f"Customer's message: \"{reply_message}\"\n\n"
        f"Extract ONLY the field values mentioned in THIS message. Use "
        f"EXACTLY the required field names listed above — do not invent "
        f"your own field names or synonyms. Use null for any field not "
        f"mentioned in this message — do not guess, infer, or reuse a "
        f"value from anywhere else. Any date field MUST be normalized to "
        f"DD-MM-YYYY format, regardless of how the customer wrote it. "
        f"Respond with ONLY valid JSON, nothing else."
    )
    raw = _chat(
        "You extract structured agreement data from a single message. Respond with ONLY valid JSON.",
        prompt,
        max_tokens=500,
    )
    raw = raw.replace("```json", "").replace("```", "").strip()
    new_fields = json.loads(raw)

    merged_fields = dict(previous_fields)
    for field in required_fields_for(agreement_type):
        value = new_fields.get(field)
        if value not in (None, "", "null"):
            merged_fields[field] = value

    missing = find_missing_fields(agreement_type, merged_fields)
    next_question = generate_followup_question(missing, language)

    return {
        "extracted_fields": merged_fields,
        "missing_fields": missing,
        "next_question": next_question,
        "status": "ready_for_staff_review" if not missing else "awaiting_customer_info",
    }
