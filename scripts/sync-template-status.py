"""Tell the API what Meta says about the marketing template, for an approval it never heard of.

    uv run --env-file .env scripts/sync-template-status.py --api https://<api address>/v1

The API learns a template's status from Meta's ``message_template_status_update`` webhook, so a
template approved before the webhook was connected reads as not approved and campaigns answer
409. This reads the template's status from Meta and delivers it to the API's webhook route as the
event Meta would have sent, signed with the app secret, so only someone holding that secret can
run it and the status always comes from Meta, never from whoever runs it. The API applies it under
the same rules as Meta's own events (WHATSAPP_BUSINESS_ACCOUNT_ID, WHATSAPP_BUSINESS_NUMBER), and
it stays in the webhook log with ``"source": "sync-template-status"``.

It needs WHATSAPP_ACCESS_TOKEN, WHATSAPP_APP_SECRET and WHATSAPP_BUSINESS_ACCOUNT_ID. ``--dry-run``
only prints what Meta says. The route acknowledges whatever it receives, so check the result in
``marketing_template_status`` on ``GET /v1/sellers/me``. Nothing secret is printed.
"""

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib.parse import quote

import httpx2

from vitrina_compliance.api import TEMPLATE_LANGUAGE, TEMPLATE_NAME
from vitrina_whatsapp.config import DEFAULT_GRAPH_VERSION, GRAPH_BASE_URL
from vitrina_whatsapp.signature import SIGNATURE_HEADER, sign
from vitrina_whatsapp.webhook import TEMPLATE_STATUS_FIELD

PAGE = 100
SOURCE = "sync-template-status"


class SyncError(Exception):
    """Meta or the API refused; the message holds the status and no secret."""


def template_status(graph: httpx2.Client, account_id: str) -> str:
    """The status Meta gives this account's marketing template, in its language."""
    params: dict[str, str | int] = {"fields": "name,status,language", "limit": PAGE}
    while True:
        response = graph.get(f"/{quote(account_id, safe='')}/message_templates", params=params)
        if response.status_code != 200:
            raise SyncError(f"Meta answered {response.status_code} {_graph_code(response)}".strip())
        body = response.json()
        for item in body.get("data", []):
            if (item.get("name"), item.get("language")) == (TEMPLATE_NAME, TEMPLATE_LANGUAGE):
                return str(item["status"])
        paging = body.get("paging", {})
        after = paging.get("cursors", {}).get("after")
        if not paging.get("next") or not after:
            raise SyncError(f"this account has no template {TEMPLATE_NAME}/{TEMPLATE_LANGUAGE}")
        params = {**params, "after": after}


def event_body(account_id: str, status: str, *, at: int) -> bytes:
    """The webhook Meta documents for this event, with only the fields the API reads."""
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "source": SOURCE,
            "entry": [
                {
                    "id": account_id,
                    "time": at,
                    "changes": [
                        {
                            "field": TEMPLATE_STATUS_FIELD,
                            "value": {
                                "event": status,
                                "message_template_name": TEMPLATE_NAME,
                                "message_template_language": TEMPLATE_LANGUAGE,
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def deliver(api: httpx2.Client, body: bytes, *, app_secret: str) -> None:
    response = api.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: sign(body, app_secret)},
    )
    if response.status_code != 200:
        raise SyncError(f"the API answered {response.status_code}")


def _graph_code(response: httpx2.Response) -> str:
    try:
        error: Any = response.json().get("error", {})
    except ValueError:
        return ""
    return f"(code {error.get('code')})" if isinstance(error, dict) else ""


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SyncError(f"set {name}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver Meta's template status to the API.")
    parser.add_argument("--api", required=True, help="the API base, ending in /v1")
    parser.add_argument("--dry-run", action="store_true", help="only print what Meta says")
    args = parser.parse_args(argv)
    try:
        account_id = _required("WHATSAPP_BUSINESS_ACCOUNT_ID")
        token = _required("WHATSAPP_ACCESS_TOKEN")
        app_secret = _required("WHATSAPP_APP_SECRET")
        with httpx2.Client(
            base_url=f"{GRAPH_BASE_URL}/{DEFAULT_GRAPH_VERSION}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as graph:
            status = template_status(graph, account_id)
        print(f"Meta says {TEMPLATE_NAME}/{TEMPLATE_LANGUAGE} is {status}")
        if not args.dry_run:
            with httpx2.Client(base_url=args.api.rstrip("/"), timeout=30) as api:
                deliver(
                    api, event_body(account_id, status, at=int(time.time())), app_secret=app_secret
                )
            print("Delivered. Check marketing_template_status on GET /v1/sellers/me.")
    except (SyncError, httpx2.RequestError) as error:
        message = str(error) if isinstance(error, SyncError) else type(error).__name__
        print(f"sync-template-status: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
