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

    text_payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }
    text_response = requests.post(f"{GRAPH_API_BASE}/messages", headers=headers, json=text_payload)

    if not attachment_path:
        return text_response.json()

    media_id = _upload_media(attachment_path)
    doc_payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "document",
        "document": {"id": media_id, "filename": os.path.basename(attachment_path)},
    }
    doc_response = requests.post(f"{GRAPH_API_BASE}/messages", headers=headers, json=doc_payload)
    return {"text": text_response.json(), "document": doc_response.json()}
