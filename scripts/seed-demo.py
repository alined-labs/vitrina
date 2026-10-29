"""Create the demo shop through the public API: the seller, its products, facts and photos.

    uv run --env-file apps/dashboard/.env.local scripts/seed-demo.py \
        --api https://<api address>/v1 --whatsapp-number <E.164> --sign-in <owner email> \
        [--photo mochila-azul=<path> ...]

The shop is ``evals/fixtures/sellers/mochilas_ana.json``, the business the eval conversations
and the replay fixtures talk to, so the demo meets the same catalog. ``--whatsapp-number`` is
the shop's WhatsApp business number: inbound messages go to the seller with that number
(deploy/README.md). Run it again at any time: a product or fact already there by name or text
is kept, and a product that has photos gets no more.

The calls carry the owner's Supabase access token. ``--sign-in`` gets one with the owner's
email and a password typed at the prompt, from NEXT_PUBLIC_SUPABASE_URL and the public
NEXT_PUBLIC_SUPABASE_ANON_KEY (never the service-role key); without it the token is read from
VITRINA_TOKEN (locally, from ``make dev-token``). Nothing secret is printed.
"""

import argparse
import getpass
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

import httpx2

FIXTURE = Path(__file__).resolve().parents[1] / "evals/fixtures/sellers/mochilas_ana.json"
SELLER_KEYS = ("business_name", "owner_name", "city")
PRODUCT_KEYS = ("name", "description", "price_cop", "stock", "attributes", "status")
PAGE = 100


class SeedError(Exception):
    """A call the seed needs was refused; the message holds the status and the problem detail."""


def seed(
    client: httpx2.Client, shop: dict[str, Any], *, whatsapp_number: str, photos: dict[str, Path]
) -> dict[str, Any]:
    """Make the signed-in account's shop hold ``shop``; report what was created and kept."""
    keys = {item["key"]: item["name"] for item in shop["products"]}
    unknown = sorted(set(photos) - set(keys))
    if unknown:
        raise SeedError(f"no product with the key {', '.join(unknown)} in the fixture")
    report: dict[str, Any] = {"seller": "kept", "products": {}, "facts": {}, "photos": {}}

    me = client.get("/sellers/me")
    if me.status_code == 403:  # the account has no seller yet (POST /sellers links it)
        seller = {key: shop["seller"][key] for key in SELLER_KEYS}
        _expect(client.post("/sellers", json={**seller, "whatsapp_number": whatsapp_number}), 201)
        report["seller"] = "created"
    else:
        _expect(me, 200)

    products = {item["name"]: item for item in _every(client, "/products")}
    for item in shop["products"]:
        if item["name"] in products:
            report["products"][item["key"]] = "kept"
            continue
        body = {key: item[key] for key in PRODUCT_KEYS if key in item}
        products[item["name"]] = _expect(client.post("/products", json=body), 201)
        report["products"][item["key"]] = "created"

    texts = {fact["text"] for fact in _every(client, "/facts")}
    created = 0
    for fact in shop["facts"]:
        if fact["text"] in texts:
            continue
        made = _expect(
            client.post("/facts", json={"kind": fact["kind"], "text": fact["text"]}), 201
        )
        if fact.get("status", "active") != made["status"]:
            _expect(client.patch(f"/facts/{made['id']}", json={"status": fact["status"]}), 200)
        created += 1
    report["facts"] = {"created": created, "kept": len(shop["facts"]) - created}

    for key, path in photos.items():
        product = products[keys[key]]
        if product.get("asset_ids"):
            report["photos"][key] = "kept"
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload = {"file": (path.name, path.read_bytes(), mime)}
        _expect(client.post("/assets", data={"product_id": product["id"]}, files=upload), 201)
        report["photos"][key] = "uploaded"
    return report


def sign_in(email: str) -> str:
    """The owner's access token from Supabase Auth's password grant, with the public key."""
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        raise SeedError(
            "--sign-in needs NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY"
        )
    response = httpx2.post(
        f"{url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": key},
        json={"email": email, "password": getpass.getpass(f"Password for {email}: ")},
        timeout=30,
    )
    if response.status_code != 200:
        raise SeedError(f"sign-in refused with {response.status_code}")
    return str(response.json()["access_token"])


def _every(client: httpx2.Client, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": PAGE, **({"cursor": cursor} if cursor else {})}
        page = _expect(client.get(path, params=params), 200)
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return items


def _expect(response: httpx2.Response, status: int) -> Any:
    if response.status_code != status:
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            detail = ""
        request = response.request
        raise SeedError(f"{request.method} {request.url.path}: {response.status_code} {detail}")
    return response.json()


def _photo(spec: str) -> tuple[str, Path]:
    key, _, path = spec.partition("=")
    if not key or not path or not Path(path).is_file():
        raise SeedError(f"--photo takes KEY=PATH to an existing file, got {spec!r}")
    return key, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the demo shop through the API.")
    parser.add_argument("--api", required=True, help="the API base, ending in /v1")
    parser.add_argument(
        "--whatsapp-number", required=True, help="the shop's WhatsApp business number (E.164)"
    )
    parser.add_argument(
        "--photo", action="append", default=[], metavar="KEY=PATH", help="a product photo"
    )
    parser.add_argument("--sign-in", metavar="EMAIL", help="sign in as the owner")
    parser.add_argument("--fixture", type=Path, default=FIXTURE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        photos = dict(_photo(spec) for spec in args.photo)
        token = sign_in(args.sign_in) if args.sign_in else os.environ.get("VITRINA_TOKEN", "")
        if not token:
            raise SeedError("set VITRINA_TOKEN or pass --sign-in")
        shop = json.loads(args.fixture.read_text(encoding="utf-8"))
        headers = {"Authorization": f"Bearer {token}"}
        with httpx2.Client(base_url=args.api.rstrip("/"), headers=headers, timeout=60) as client:
            report = seed(client, shop, whatsapp_number=args.whatsapp_number, photos=photos)
    except SeedError as error:
        print(f"seed-demo: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
