"""
Create and publish the agreement-intake WhatsApp Flow (see
backend/flows.py and backend/flows_json/agreement_request.json) on Meta's
Graph API, using the already-configured System User access token.

This is a ONE-OFF setup step, not something the running app calls. Run it
once, then copy the printed flow_id into WHATSAPP_FLOW_ID (in your .env
locally, or Render's environment variables in production).

A published Flow's JSON is immutable on Meta's side -- to change the
screens/fields later, re-run this script (it creates a brand-new Flow with
a new flow_id) and update WHATSAPP_FLOW_ID to the new id. It does not
touch or delete the old Flow.

Usage (from the project root, with the backend venv activated and
WHATSAPP_TOKEN set in the environment or backend/.env):
    python3 scripts/create_whatsapp_flow.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

from flows import load_flow_json  # noqa: E402

# This deployment's fixed WhatsApp Business Account id (see the
# whatsapp_business_tools MCP session that set up the real number) --
# hardcoded rather than read from an env var since it's a fixed identifier
# for this one business, not a per-environment secret.
WABA_ID = "1813115817055548"
GRAPH_API_VERSION = "v20.0"


def main():
    token = os.environ.get("WHATSAPP_TOKEN")
    if not token:
        print("WHATSAPP_TOKEN is not set -- can't call the Graph API. Aborting.")
        return

    flow_json = load_flow_json()
    payload = {
        "name": "AgreeEase Agreement Request",
        "categories": ["OTHER"],
        "flow_json": json.dumps(flow_json),
        "publish": True,
    }

    response = requests.post(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WABA_ID}/flows",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    data = response.json()

    if not response.ok:
        print("Flow creation failed. Full response:")
        print(json.dumps(data, indent=2))
        return

    flow_id = data.get("id")
    print(f"Created and published Flow. flow_id = {flow_id}")
    print("Copy this into WHATSAPP_FLOW_ID (backend/.env locally, Render env vars in production).")

    validation_errors = data.get("validation_errors")
    if validation_errors:
        print("\nValidation errors reported (the Flow may not work correctly):")
        print(json.dumps(validation_errors, indent=2))


if __name__ == "__main__":
    main()
