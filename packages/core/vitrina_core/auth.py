"""Who is calling (T006; spec.md §2 actors; openapi.yaml ``securitySchemes``).

- Sellers send a Supabase access token (``sellerAuth``). The seller is the token's
  ``seller_id`` claim when the Supabase access-token hook adds one, else the seller whose
  ``auth_subject`` is the token's ``sub``. Tokens are HS256 with the project secret or
  ES256/RS256 with the project's signing keys (JWKS).
- Workers send ``X-Service-Token`` (``serviceAuth``), compared in constant time.
- WhatsApp signs webhooks (``whatsappSignature``); this module only extracts the header,
  the conversation module checks the HMAC.

No credentials or bad ones: 401 UNAUTHORIZED. A valid token without a seller: 403 FORBIDDEN.
"""

import asyncio
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import jwt
import sqlalchemy as sa
from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import sellers
from vitrina_core.http import ProblemCode, ProblemError
from vitrina_core.ids import NIL

seller_bearer = HTTPBearer(
    scheme_name="sellerAuth",
    bearerFormat="JWT",
    description="Supabase JWT; `seller_id` claim scopes every query.",
    auto_error=False,
)
service_header = APIKeyHeader(
    name="X-Service-Token",
    scheme_name="serviceAuth",
    description="Internal workers.",
    auto_error=False,
)
whatsapp_signature_header = APIKeyHeader(
    name="X-Hub-Signature-256",
    scheme_name="whatsappSignature",
    description="`sha256=<hex HMAC of raw body with app secret>`",
    auto_error=False,
)

SUPABASE_AUDIENCE = "authenticated"
ASYMMETRIC = ("ES256", "RS256")


def _unauthorized(detail: str) -> ProblemError:
    return ProblemError(
        ProblemCode.UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


@dataclass(frozen=True)
class Principal:
    kind: Literal["seller", "service"]
    seller_id: UUID | None = None
    subject: str | None = None


class SellerDirectory(Protocol):
    async def seller_for_subject(self, subject: str) -> UUID | None: ...


@dataclass
class InMemorySellerDirectory:
    subjects: dict[str, UUID] = field(default_factory=dict)

    async def seller_for_subject(self, subject: str) -> UUID | None:
        return self.subjects.get(subject)


class PostgresSellerDirectory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def seller_for_subject(self, subject: str) -> UUID | None:
        async with self.sessions() as session:
            found = await session.scalar(
                sa.select(sellers.c.id).where(sellers.c.auth_subject == subject)
            )
        return found


class TokenVerifier:
    def __init__(
        self,
        *,
        secret: SecretStr | None,
        issuer: str | None = None,
        jwks: Mapping[str, Any] | None = None,
        jwks_url: str | None = None,
        leeway_s: int = 30,
    ) -> None:
        self.secret = secret
        self.issuer = issuer
        self.leeway_s = leeway_s
        self._jwks = jwt.PyJWKSet.from_dict(dict(jwks)) if jwks else None
        self._jwks_client = jwt.PyJWKClient(jwks_url) if jwks_url and not jwks else None

    async def _key(self, token: str, alg: str) -> Any:
        if alg == "HS256":
            if self.secret is None:
                raise _unauthorized("HS256 tokens need SUPABASE_JWT_SECRET")
            return self.secret.get_secret_value()
        if alg not in ASYMMETRIC:
            raise _unauthorized(f"unsupported token algorithm {alg}")
        kid = jwt.get_unverified_header(token).get("kid")
        if self._jwks is not None:
            matches = [key for key in self._jwks.keys if key.key_id == kid]
            if not matches:
                raise _unauthorized("unknown signing key")
            return matches[0].key
        if self._jwks_client is not None:
            client = self._jwks_client
            signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)
            return signing_key.key
        raise _unauthorized("asymmetric tokens need SUPABASE_URL for the project keys")

    async def claims(self, token: str) -> dict[str, Any]:
        try:
            alg = jwt.get_unverified_header(token).get("alg", "")
            key = await self._key(token, alg)
            decoded: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=SUPABASE_AUDIENCE,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "sub", "aud"], "verify_iss": self.issuer is not None},
            )
        except ProblemError:
            raise
        except (jwt.PyJWTError, ValueError) as error:
            raise _unauthorized(f"invalid token: {error}") from error
        return decoded


class Authenticator:
    def __init__(
        self, verifier: TokenVerifier, directory: SellerDirectory, service_token: SecretStr | None
    ) -> None:
        self.verifier = verifier
        self.directory = directory
        self.service_token = service_token

    async def seller(self, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _unauthorized("a Supabase bearer token is required")
        claims = await self.verifier.claims(credentials.credentials)
        subject = str(claims["sub"])
        claimed = claims.get("seller_id")
        seller_id = (
            UUID(str(claimed)) if claimed else await self.directory.seller_for_subject(subject)
        )
        if seller_id is None:
            raise ProblemError(
                ProblemCode.FORBIDDEN,
                detail="this account has no seller yet; create one with POST /sellers",
            )
        return Principal(kind="seller", seller_id=seller_id, subject=subject)

    def service(self, token: str | None) -> Principal:
        expected = self.service_token.get_secret_value() if self.service_token else ""
        if (
            not expected
            or token is None
            or not hmac.compare_digest(token.encode(), expected.encode())
        ):
            raise _unauthorized("a valid X-Service-Token is required")
        return Principal(kind="service")

    async def idempotency_scope(self, request: Request) -> UUID:
        """The seller that owns an Idempotency-Key; NIL for service and anonymous calls."""
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return NIL
        try:
            principal = await self.seller(
                HTTPAuthorizationCredentials(scheme=scheme, credentials=value)
            )
        except ProblemError:
            return NIL  # the route itself answers 401 or 403
        return principal.seller_id or NIL


def authenticator(request: Request) -> Authenticator:
    auth = getattr(request.app.state, "auth", None)
    if not isinstance(auth, Authenticator):
        raise RuntimeError("app.state.auth is not configured; create_app sets it")
    return auth


async def require_seller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(seller_bearer)],
) -> UUID:
    principal = await authenticator(request).seller(credentials)
    assert principal.seller_id is not None
    return principal.seller_id


async def require_service(
    request: Request, token: Annotated[str | None, Security(service_header)]
) -> Principal:
    return authenticator(request).service(token)


async def require_seller_or_service(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(seller_bearer)],
    token: Annotated[str | None, Security(service_header)],
) -> Principal:
    auth = authenticator(request)
    if token is not None:
        return auth.service(token)
    return await auth.seller(credentials)


async def whatsapp_signature(
    signature: Annotated[str | None, Security(whatsapp_signature_header)],
) -> str | None:
    return signature


SellerId = Annotated[UUID, Depends(require_seller)]
ServiceCaller = Annotated[Principal, Depends(require_service)]
SellerOrService = Annotated[Principal, Depends(require_seller_or_service)]
WhatsAppSignature = Annotated[str | None, Depends(whatsapp_signature)]
