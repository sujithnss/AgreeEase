"""
WhatsApp Cloud API sender. Without real credentials, this just logs to console.
Once you have a Meta WhatsApp Business number, fill in the two
environment variables below and uncomment the real send logic.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import os
import mimetypes
import requests

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID")
# Set once by scripts/create_whatsapp_flow.py's printed output -- see
# backend/flows.py for the Flow definition this id refers to.
WHATSAPP_FLOW_ID = os.environ.get("WHATSAPP_FLOW_ID")
GRAPH_API_BASE = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_ID}"


def _upload_media(attachment_path: str) -> str:
    """Uploads a file to Meta's /media endpoint and returns the media_id
    to reference in a subsequent 'document' message. Required first step —
    Meta doesn't accept raw file bytes directly in the /messages call."""
    mime_type = mimetypes.guess_type(attachment_path)[0] or "application/octet-stream"
    with open(attachment_path, "rb") as f:
        response = requests.post(
            f"{GRAPH_API_BASE}/media",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (os.path.basename(attachment_path), f, mime_type)},
        )
    response.raise_for_status()
    return response.json()["id"]


def send_whatsapp_message(to_phone: str, message: str, attachment_path: str = None):
    if not WHATSAPP_TOKEN:
        # No real WhatsApp number connected yet — stub mode
        print(f"[WhatsApp STUB] To: {to_phone}\nMessage: {message}")
        if attachment_path:
            print(f"[WhatsApp STUB] Would attach file: {attachment_path}")
        return {"status": "stubbed"}

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    # Meta API calls (bad/expired token, rate limits, network issues) must
    # never blow up the caller — sending is best-effort so a WhatsApp outage
    # can't break dashboard actions or webhook processing (which Meta retries
    # aggressively on a 5xx response).
    try:
        text_payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": message},
        }
        text_response = requests.post(f"{GRAPH_API_BASE}/messages", headers=headers, json=text_payload)
        text_response.raise_for_status()

        if not attachment_path:
            return {"status": "sent", "text": text_response.json()}

        media_id = _upload_media(attachment_path)
        doc_payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "document",
            "document": {"id": media_id, "filename": os.path.basename(attachment_path)},
        }
        doc_response = requests.post(f"{GRAPH_API_BASE}/messages", headers=headers, json=doc_payload)
        doc_response.raise_for_status()
        return {"status": "sent", "text": text_response.json(), "document": doc_response.json()}
    except requests.exceptions.RequestException as e:
        # Meta's actual error reason (e.g. "recipient not in allowed list",
        # invalid parameter, etc.) is in the response body -- the bare
        # exception string from `requests` only has the status code/URL.
        body = e.response.text if e.response is not None else "<no response>"
        print(f"[WhatsApp ERROR] Failed to send to {to_phone}: {e}\n  Response body: {body}")
        return {"status": "error", "error": str(e), "response_body": body}


def send_whatsapp_flow(to_phone: str, flow_token: str, screen_id: str = "WELCOME", flow_cta: str = "Start"):
    """Sends the agreement-intake WhatsApp Flow (backend/flows.py /
    flows_json/agreement_request.json) as an interactive message, instead
    of the plain text send above. flow_token is minted by the caller
    (main.py) and stored on the AgreementRequest row so the Flow's
    eventual completion webhook (an "nfm_reply" message) can be matched
    back to the right row."""
    if not WHATSAPP_TOKEN or not WHATSAPP_FLOW_ID:
        print(f"[WhatsApp STUB] To: {to_phone}\nWould send Flow {WHATSAPP_FLOW_ID} (token={flow_token})")
        return {"status": "stubbed"}

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "body": {"text": "Let's set up your agreement -- tap below to get started."},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_id": WHATSAPP_FLOW_ID,
                    "flow_token": flow_token,
                    "flow_cta": flow_cta,
                    "flow_action": "navigate",
                    "flow_action_payload": {"screen": screen_id},
                },
            },
        },
    }
    try:
        response = requests.post(f"{GRAPH_API_BASE}/messages", headers=headers, json=payload)
        response.raise_for_status()
        return {"status": "sent", "response": response.json()}
    except requests.exceptions.RequestException as e:
        body = e.response.text if e.response is not None else "<no response>"
        print(f"[WhatsApp ERROR] Failed to send Flow to {to_phone}: {e}\n  Response body: {body}")
        return {"status": "error", "error": str(e), "response_body": body}
