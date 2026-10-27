"""Print a Supabase-style access token for local end-to-end runs without a Supabase project.

    uv run scripts/dev-token.py --sub dev-seller [--seller <uuid>] [--hours 12]

The token is signed HS256 with SUPABASE_JWT_SECRET from .env (any local secret of 32 bytes
or more; the API must read the same one) and carries the claims core.auth checks: aud, exp
and sub, plus iss when SUPABASE_URL is set (a local Supabase, e.g. http://localhost:54321).
Without --seller, sign up with it first: POST /v1/sellers links the token's subject to the
new seller (T022). It refuses a hosted project (*.supabase.co), where it would mint tokens
for real accounts; there, sign in through Supabase instead.
"""

import argparse
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID

import jwt

from vitrina_core.auth import SUPABASE_AUDIENCE
from vitrina_core.settings import Settings, get_settings

HOSTED_SUFFIXES = (".supabase.co", ".supabase.com")


def mint(
    settings: Settings,
    subject: str,
    *,
    seller_id: UUID | None = None,
    hours: float = 12,
    now: int | None = None,
) -> str:
    if settings.supabase_url:
        host = (urlsplit(settings.supabase_url).hostname or "").lower()
        if host.endswith(HOSTED_SUFFIXES):
            raise SystemExit(
                f"refusing: SUPABASE_URL is a hosted Supabase project ({host}); sign in there"
            )
    if settings.supabase_jwt_secret is None:
        raise SystemExit("set SUPABASE_JWT_SECRET in .env to a local secret of 32+ bytes")
    issued = int(time.time()) if now is None else now
    claims: dict[str, object] = {
        "sub": subject,
        "aud": SUPABASE_AUDIENCE,
        "role": "authenticated",
        "iat": issued,
        "exp": issued + round(hours * 3600),
    }
    if settings.supabase_url:
        claims["iss"] = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    if seller_id is not None:
        claims["seller_id"] = str(seller_id)
    return jwt.encode(claims, settings.supabase_jwt_secret.get_secret_value(), algorithm="HS256")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print a local Supabase-style access token.")
    parser.add_argument("--sub", required=True, help="the account's subject, any string")
    parser.add_argument("--seller", type=UUID, help="put this seller id in the token")
    parser.add_argument("--hours", type=float, default=12, help="lifetime (default 12)")
    args = parser.parse_args(argv)
    token = mint(get_settings(), args.sub, seller_id=args.seller, hours=args.hours)
    sys.stdout.write(token + "\n")


if __name__ == "__main__":
    main()
