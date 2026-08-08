"""
WhatsApp Cloud API sender. Without real credentials, this just logs to console.
Once you have a Meta WhatsApp Business number, fill in the two
environment variables below and uncomment the real send logic.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import os
import requests

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID")
GRAPH_API_URL = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_ID}/messages"


def send_whatsapp_message(to_phone: str, message: str, attachment_path: str = None):
    if not WHATSAPP_TOKEN:
        # No real WhatsApp number connected yet — stub mode
        print(f"[WhatsApp STUB] To: {to_phone}\nMessage: {message}")
        if attachment_path:
            print(f"[WhatsApp STUB] Would attach file: {attachment_path}")
        return {"status": "stubbed"}

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }
    response = requests.post(GRAPH_API_URL, headers=headers, json=payload)
    # For attachments, upload the media first via the /media endpoint,
    # then send a "document" type message referencing the returned media_id.
    # See Meta's docs for the two-step upload+send flow.
    return response.json()
