"""Contacts & Consent router.

Contract paths: /contacts, /contacts/{contact_id} and its /consents, /opt-out.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from vitrina_compliance.api import (
    ComplianceStore,
    ConsentRecord,
    ConsentRecordCreate,
    Contact,
    OptedOutError,
    opt_out,
    request_erasure,
)
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.jobs import Job, PostgresJobQueue

router = APIRouter(tags=["Contacts & Consent"])


def compliance_store(request: Request) -> ComplianceStore:
    store: ComplianceStore = request.app.state.compliance
    return store


Store = Annotated[ComplianceStore, Depends(compliance_store)]
OPTED_OUT_DETAIL = (
    "Este contacto se dio de baja: no se puede registrar un nuevo permiso de marketing."
)
Paging = Annotated[PageQuery, Depends()]


def _not_found(contact_id: UUID) -> ProblemError:
    return ProblemError(ProblemCode.NOT_FOUND, detail=f"no contact {contact_id} for this seller")


@router.get(
    "/contacts",
    operation_id="listContacts",
    summary="List contacts with consent status",
    response_description="Page of contacts",
)
async def list_contacts(
    seller_id: SellerId,
    store: Store,
    page: Paging,
    marketing_consent: Literal["granted", "revoked", "none"] | None = None,
) -> Page[Contact]:
    items, cursor = await store.list_contacts(
        seller_id, limit=page.limit, cursor=page.cursor, marketing_consent=marketing_consent
    )
    return Page[Contact](items=items, next_cursor=cursor)


@router.get(
    "/contacts/{contact_id}",
    operation_id="getContact",
    summary="Get a contact",
    response_description="Contact",
    responses=problem_responses(404),
)
async def get_contact(seller_id: SellerId, store: Store, contact_id: UUID) -> Contact:
    contact = await store.contact(seller_id, contact_id)
    if contact is None:
        raise _not_found(contact_id)
    return contact


@router.delete(
    "/contacts/{contact_id}",
    status_code=202,
    operation_id="eraseContact",
    summary="Erase a contact's personal data (Habeas Data request)",
    description=(
        "Schedules erasure within 24 h and keeps a non-reversible erasure receipt (FR-065)."
    ),
)
async def erase_contact(
    request: Request, seller_id: SellerId, store: Store, contact_id: UUID
) -> Job:
    if await store.contact(seller_id, contact_id) is None:
        raise _not_found(contact_id)
    jobs: PostgresJobQueue | None = request.app.state.jobs
    if jobs is None or request.app.state.sessions is None:
        raise ProblemError(
            ProblemCode.PROVIDER_UNAVAILABLE, detail="erasure needs the database (DATABASE_URL)"
        )
    async with request.app.state.sessions() as session, session.begin():
        job_id = await request_erasure(jobs, session, seller_id, contact_id)
    job = await jobs.get_for_seller(seller_id, job_id)
    assert job is not None
    return job


@router.get(
    "/contacts/{contact_id}/consents",
    operation_id="listConsents",
    summary="Consent ledger for a contact (append-only)",
    response_description="Ledger entries, newest first",
)
async def list_consents(seller_id: SellerId, store: Store, contact_id: UUID) -> list[ConsentRecord]:
    # The contract answers 200 only: an unknown contact simply has no entries.
    return await store.consents(seller_id, contact_id)


@router.post(
    "/contacts/{contact_id}/consents",
    status_code=201,
    operation_id="recordConsent",
    summary="Record consent or revocation manually (e.g. from a paper form)",
    response_description="Ledger entry appended",
)
async def record_consent_manually(
    seller_id: SellerId,
    store: Store,
    contact_id: UUID,
    record: ConsentRecordCreate,
    _key: IdempotencyKey = None,
) -> ConsentRecord:
    """The seller's own attestation; the agent's path applies R8 in conversation instead.

    After an opt-out the opt-out wins: a marketing grant is a conflict and records nothing.
    """
    try:
        return await store.append_consent(seller_id, contact_id, record, recorded_by="seller")
    except LookupError as error:
        raise _not_found(contact_id) from error
    except OptedOutError as error:
        # The dashboard shows a conflict's detail to the seller as written.
        raise ProblemError(ProblemCode.CONFLICT, detail=OPTED_OUT_DETAIL) from error


@router.post(
    "/contacts/{contact_id}/opt-out",
    operation_id="optOutContact",
    summary="Opt a contact out of marketing immediately",
    response_description="Contact opted out",
)
async def opt_out_contact(seller_id: SellerId, store: Store, contact_id: UUID) -> Contact:
    try:
        return await opt_out(store, seller_id, contact_id, via="api")
    except LookupError as error:
        raise _not_found(contact_id) from error
