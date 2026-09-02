"""
AI extraction engine — classifies the agreement type, extracts structured
fields, detects language, and drafts a follow-up question for missing
fields. Uses Groq's OpenAI-compatible chat completions API for cost
efficiency and speed.
"""

import json
import os
import re
import requests
from templates_config import TEMPLATES, required_fields_for, extractable_fields_for, PREFERRED_LANGUAGE_FIELD

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

_LATIN_LETTERS_RE = re.compile(r"[A-Za-z]")


def _chat(system: str, user: str, max_tokens: int) -> str:
    # gpt-oss models spend part of max_tokens on internal reasoning before
    # writing the actual answer -- Groq's own docs note that below ~1000
    # tokens the reasoning can eat the whole budget and leave content
    # empty, even though the call "succeeds". reasoning_effort=low keeps
    # that reasoning short (this task never needs deep reasoning anyway);
    # only send it for gpt-oss models, since it's not a universal Groq
    # parameter and other models may reject an unknown field.
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if "gpt-oss" in MODEL:
        payload["reasoning_effort"] = "low"

    response = requests.post(
        GROQ_CHAT_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _schema_block() -> str:
    lines = []
    for key in TEMPLATES:
        fields = ", ".join(extractable_fields_for(key))
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
   If the field list includes "preferred_document_language", this is NOT
   about what language the customer is writing to you in — it's whether
   they explicitly stated which language they want the FINAL AGREEMENT
   DOCUMENT drafted in (e.g. "please make it in Malayalam", "venam
   Englishil", "മലയാളത്തിൽ വേണം", "I need it in English"). Only fill this
   in if they explicitly said so; normalize to exactly "malayalam" or
   "english". Leave it null if they didn't say — do not infer it from
   what language their message itself is written in.
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
greetings or sign-offs, just the question(s).

If "preferred_document_language" is one of the missing fields, phrase
that part as asking whether they'd like the final agreement DOCUMENT
drafted in English or Malayalam — make clear this is about the document
itself, not the language they're chatting in.

If "property_address" is one of the missing fields, phrase that part as
asking for the full property address including the door/building
number and the Municipality/Panchayat/Corporation name, not just the
locality — this is used to generate the agreement's formal property
description, so a complete address matters more than usual.

If "agreement_duration_months" is one of the missing fields, phrase that
part as asking how many months the agreement should run for (most are
11 months, but 24 or 36 month agreements are also common) — make clear
they should reply with a number of months."""


def extract_from_message(message: str) -> dict:
    prompt = EXTRACTION_SYSTEM_PROMPT.format(schema_block=_schema_block())
    raw = _chat(prompt, message, max_tokens=1500)
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
    return _chat(FOLLOWUP_SYSTEM_PROMPT, prompt, max_tokens=1200)


def process_message(message: str) -> dict:
    """Main entry point for a fresh customer message."""
    try:
        result = extract_from_message(message)
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        # Same shape as the "unrecognized agreement_type" case below --
        # ask the customer to resend and stay in awaiting_customer_info,
        # so main.py's webhook handler re-runs full classification on
        # their next message instead of the request being silently lost.
        print(f"[Extraction ERROR] Falling back to retry-request for {message!r}: {e}")
        return {
            "agreement_type": "unknown",
            "language": "mixed",
            "extracted_fields": {},
            "missing_fields": [],
            "next_question": extraction_error_message(),
            "status": "awaiting_customer_info",
        }
    agreement_type = result.get("agreement_type", "unknown")
    extracted_fields = result.get("extracted_fields", {})
    language = result.get("language", "english")

    if agreement_type not in TEMPLATES:
        # required_fields_for() would silently return [] for an
        # unrecognized type, which used to make find_missing_fields()
        # report "nothing missing" and the request would go straight to
        # ready_for_staff_review with no fields at all. Instead, ask the
        # customer to clarify/resend — this also sets status back to
        # awaiting_customer_info so their next reply gets re-classified
        # from scratch (see the webhook handler in main.py) rather than
        # merged against a template that was never actually determined.
        return {
            "agreement_type": "unknown",
            "language": language,
            "extracted_fields": extracted_fields,
            "missing_fields": [],
            "next_question": clarification_message(language),
            "status": "awaiting_customer_info",
        }

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


def resolve_doc_language(message_language: str, extracted_fields: dict) -> str:
    """The document language to generate: the customer's explicitly stated
    preference (preferred_document_language) if we have it, otherwise a
    best-guess fallback from the language they happened to type in. Staff
    can still override this on the review dashboard regardless."""
    stated = extracted_fields.get(PREFERRED_LANGUAGE_FIELD)
    if stated in ("malayalam", "english"):
        return stated
    return "english" if message_language == "english" else "malayalam"


_CLARIFICATION_MESSAGE_EN = (
    "Sorry, we couldn't tell what type of agreement you need. Could you "
    "resend your request with a bit more detail — e.g. \"house rental "
    "agreement\" or \"shop rental agreement\" — along with the basic "
    "details (names, address, rent, dates)?"
)
_CLARIFICATION_MESSAGE_ML = (
    "ക്ഷമിക്കണം, നിങ്ങൾക്ക് ഏത് തരം കരാറാണ് വേണ്ടതെന്ന് ഞങ്ങൾക്ക് "
    "മനസ്സിലായില്ല. ദയവായി കുറച്ചുകൂടി വിശദമായി വീണ്ടും അയയ്ക്കാമോ "
    "— ഉദാ: \"വീട് വാടക കരാർ\" അല്ലെങ്കിൽ \"കട വാടക കരാർ\" — "
    "പേരുകൾ, വിലാസം, വാടക, തീയതികൾ എന്നിവയും ചേർത്ത്."
)


def clarification_message(language: str) -> str:
    """Sent when the AI couldn't classify which agreement type the
    customer wants at all (as opposed to knowing the type but missing a
    few fields, which generate_followup_question() already handles)."""
    if language == "malayalam":
        return _CLARIFICATION_MESSAGE_ML
    if language == "mixed":
        return f"{_CLARIFICATION_MESSAGE_EN}\n{_CLARIFICATION_MESSAGE_ML}"
    return _CLARIFICATION_MESSAGE_EN


_EXTRACTION_ERROR_MESSAGE_EN = (
    "Sorry, we're having trouble processing your message right now. "
    "Please try again in a few minutes."
)
_EXTRACTION_ERROR_MESSAGE_ML = (
    "ക്ഷമിക്കണം, നിങ്ങളുടെ സന്ദേശം പ്രോസസ്സ് ചെയ്യുന്നതിൽ ഇപ്പോൾ ഒരു "
    "തകരാർ ഉണ്ട്. ദയവായി കുറച്ച് സമയത്തിന് ശേഷം വീണ്ടും ശ്രമിക്കുക."
)


def extraction_error_message(language: str = "mixed") -> str:
    """Sent when the Claude call/JSON parse in extract_from_message() or
    merge_followup_reply() raises (API timeout, rate limit, malformed
    response) -- keeps the customer informed instead of silently dropping
    their message, and (paired with returning status
    "awaiting_customer_info") means their next message retries extraction
    from scratch rather than the request being lost. Defaults to "mixed"
    since a fresh-message extraction failure means we never even learned
    what language the customer was writing in."""
    if language == "malayalam":
        return _EXTRACTION_ERROR_MESSAGE_ML
    if language == "mixed":
        return f"{_EXTRACTION_ERROR_MESSAGE_EN}\n{_EXTRACTION_ERROR_MESSAGE_ML}"
    return _EXTRACTION_ERROR_MESSAGE_EN


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


_DUPLICATE_REQUEST_MESSAGE_EN = (
    "We already have a request from you in progress and our team is working on it "
    "-- no need to resend. If this message is actually for a DIFFERENT agreement "
    "(e.g. a different property or a different name), please say so clearly and "
    "we'll open a new request for it."
)
_DUPLICATE_REQUEST_MESSAGE_ML = (
    "നിങ്ങളുടെ ഒരു അഭ്യർത്ഥന ഇതിനകം ഞങ്ങളുടെ ടീം പരിശോധിച്ചു വരികയാണ് -- "
    "വീണ്ടും അയക്കേണ്ടതില്ല. ഇത് വേറൊരു കരാറിനു വേണ്ടിയാണെങ്കിൽ (വേറൊരു "
    "സ്ഥലം അല്ലെങ്കിൽ വേറൊരു പേര്), ദയവായി അത് വ്യക്തമായി പറയുക -- ഞങ്ങൾ "
    "അതിനായി പുതിയൊരു അഭ്യർത്ഥന തുടങ്ങാം."
)


def duplicate_request_message(language: str) -> str:
    """Sent instead of silently opening a second request when the same
    phone number + customer name already has one actively moving through
    the pipeline -- avoids staff seeing two rows for what's actually one
    customer re-sending the same ask. Deliberately does NOT trigger just
    off phone number alone, since the same number legitimately can have
    multiple, different requests (e.g. a landlord with two properties)."""
    if language == "malayalam":
        return _DUPLICATE_REQUEST_MESSAGE_ML
    if language == "mixed":
        return f"{_DUPLICATE_REQUEST_MESSAGE_EN}\n{_DUPLICATE_REQUEST_MESSAGE_ML}"
    return _DUPLICATE_REQUEST_MESSAGE_EN


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
        f"Field names for this agreement type: {', '.join(extractable_fields_for(agreement_type))}\n"
        f"Customer's message: \"{reply_message}\"\n\n"
        f"Extract ONLY the field values mentioned in THIS message. Use "
        f"EXACTLY the field names listed above — do not invent "
        f"your own field names or synonyms. Use null for any field not "
        f"mentioned in this message — do not guess, infer, or reuse a "
        f"value from anywhere else. Any date field MUST be normalized to "
        f"DD-MM-YYYY format, regardless of how the customer wrote it. If "
        f"\"{PREFERRED_LANGUAGE_FIELD}\" is a required field, it means "
        f"which language they want the agreement DOCUMENT drafted in "
        f"(not what language this message is written in) — normalize to "
        f"exactly \"malayalam\" or \"english\". "
        f"Respond with ONLY valid JSON, nothing else."
    )
    try:
        raw = _chat(
            "You extract structured agreement data from a single message. Respond with ONLY valid JSON.",
            prompt,
            max_tokens=1200,
        )
        raw = raw.replace("```json", "").replace("```", "").strip()
        new_fields = json.loads(raw)
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        # Leave previous_fields untouched rather than losing this reply --
        # re-ask via the same missing-fields/status the customer was
        # already in, so their next message retries this same merge.
        print(f"[Follow-up extraction ERROR] Falling back to unchanged fields for {reply_message!r}: {e}")
        missing = find_missing_fields(agreement_type, previous_fields)
        return {
            "extracted_fields": previous_fields,
            "missing_fields": missing,
            "next_question": extraction_error_message(language),
            "status": "awaiting_customer_info",
        }

    merged_fields = dict(previous_fields)
    for field in extractable_fields_for(agreement_type):
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


TRANSLITERATION_SYSTEM_PROMPT = """You transliterate English-typed values
from a Kerala legal document (names, addresses, property/business
descriptions) into Malayalam script, based on how they sound in English —
you do NOT translate them by meaning, and you do NOT alter spellings
beyond what's needed to represent the same sounds in Malayalam script.
Keep any digits, punctuation, or text already in Malayalam exactly as-is.

You will receive a JSON object of field_name: value pairs. Respond with
ONLY a JSON object using the EXACT SAME keys, each mapped to its
Malayalam-script version, nothing else."""


def transliterate_fields_to_malayalam(fields: dict) -> dict:
    """Returns a copy of `fields` with English-typed (Latin-script) string
    values transliterated into Malayalam script — e.g. "Rahul Menon" ->
    "രാഹുൽ മേനോൻ" — so a Malayalam-language document doesn't end up mixing
    Malayalam boilerplate with English proper nouns. Fields with no Latin
    letters (already Malayalam, or purely numeric/date fields) are left
    untouched and never sent to the API. Falls back to the original
    fields unchanged if the API call or parsing fails, so a
    transliteration hiccup never blocks document generation.

    Called after build_property_description() has already written a
    Malayalam-script property_description onto the fields dict (see
    approve_request() in main.py) -- that field is naturally skipped
    here too, since it has no Latin letters left to transliterate."""
    candidates = {
        key: value
        for key, value in fields.items()
        if key != PREFERRED_LANGUAGE_FIELD
        and isinstance(value, str)
        and _LATIN_LETTERS_RE.search(value)
    }
    if not candidates:
        return dict(fields)

    prompt = (
        "Transliterate these into Malayalam script:\n"
        + json.dumps(candidates, ensure_ascii=False)
    )
    try:
        raw = _chat(TRANSLITERATION_SYSTEM_PROMPT, prompt, max_tokens=1200)
        raw = raw.replace("```json", "").replace("```", "").strip()
        transliterated = json.loads(raw)
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"[Transliteration ERROR] Falling back to original text for {list(candidates)}: {e}")
        return dict(fields)

    merged = dict(fields)
    for key in candidates:
        value = transliterated.get(key)
        if value:
            merged[key] = value
    return merged


PROPERTY_DESCRIPTION_SYSTEM_PROMPT = """You write the formal property
description clause for a Kerala rental/license agreement -- the
"മാർജിൻ" schedule clause in the vendor's Malayalam specimen -- from a
customer's plain-language property address.

From the address, identify whatever you can of: door/building number,
the local body name and type (Municipality, Corporation, or
Panchayat), and the District. Do NOT invent or guess anything not
present or clearly implied in the address -- just omit a piece you
can't identify rather than making it up.

Write ONE sentence in the requested target language, following this
pattern (adapt it to whatever pieces are actually available):

Malayalam example (for door no. 6/83, Ramanattukara Municipality,
Kozhikode District):
"കോഴിക്കോട് ജില്ലയിൽ രാമനാട്ടുകര മുനിസിപ്പാലിറ്റിയിൽ സ്ഥിതി ചെയ്യുന്നതും
6/83 എന്ന നമ്പറിട്ടതുമായ വീട്."

English example (same address):
"House bearing No. 6/83, situated in Ramanattukara Municipality,
Kozhikode District."

Place/local-body names should be transliterated into Malayalam script
the way they are commonly written in Malayalam when the target
language is Malayalam. Keep door/survey numbers and any other digits
exactly as given.

Respond with ONLY the finished sentence in the requested target
language and script -- nothing else, no explanation, no quotes."""


def build_property_description(property_address: str, language: str) -> str:
    """Builds the formal property-description clause (the vendor
    specimen's "മാർജിൻ" schedule text) from the customer's plain-language
    property_address, directly in the document's target language --
    so customers only need to give one normal address over WhatsApp
    instead of a separate land-record-style description they're
    unlikely to have on hand (door no., local body, district). Falls
    back to the raw address unchanged if the API call fails, so a
    generation hiccup never blocks document generation."""
    if not property_address:
        return property_address
    target_language = "Malayalam" if language == "malayalam" else "English"
    prompt = f'Target language: {target_language}\nProperty address: "{property_address}"'
    try:
        raw = _chat(PROPERTY_DESCRIPTION_SYSTEM_PROMPT, prompt, max_tokens=300)
    except requests.exceptions.RequestException as e:
        print(f"[Property description generation ERROR] Falling back to raw address: {e}")
        return property_address
    return raw.strip().strip('"')
