"""The consent ledger, opt-out and the contacts they belong to (FR-060, FR-063; ADR-0009).

The ledger only grows: a grant, a revocation, another grant. A contact's current state is
the latest record per purpose, ``none`` before any. ``record_consent`` is the agent's path
and applies R8 outside the model: a consent prompt must be pending and the reply must be a
plain yes. ``opt_out`` is immediate and permanent: it stamps the contact once and appends a
revocation each time it is asked. Each record also emits ``consent.recorded`` or
``consent.revoked``. Campaign sends pending for an opted-out contact are dropped at send time
by ``evaluate`` (OPTED_OUT comes first), so nothing else needs cancelling here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.contacts import Contact, ContactIdentity, MarketingConsent, mask_phone
from vitrina_compliance.keywords import is_affirmative
from vitrina_compliance.tables import consent_records, contacts, erasure_receipts
from vitrina_core.events import EventType, emit
from vitrina_core.http import decode_cursor, encode_cursor, keyset_page, next_cursor
from vitrina_core.ids import new_id

Purpose = Literal["marketing", "data_processing"]
RecordedBy = Literal["agent", "seller", "system"]
OptOutVia = Literal["keyword", "tool", "api"]
# What a revocation records as shown: no prompt, the way the customer asked.
OPT_OUT_SHOWN: dict[OptOutVia, str] = {
    "keyword": "Palabra de baja (BAJA, no más, stop, cancelar)",
    "tool": "Pidió no recibir más mensajes en la conversación",
    "api": "Baja registrada por el negocio",
}


class ConsentRefusedError(Exception):
    """R8 failed: nothing was recorded."""


class ConsentRecordCreate(BaseModel):
    purpose: Purpose
    status: Literal["granted", "revoked"]
    text_shown: Annotated[str, Field(max_length=500)]
    response_text: Annotated[str, Field(max_length=200)]
    channel: Literal["whatsapp", "in_person", "web", "other"]
    evidence_message_id: UUID | None = None


class ConsentRecord(ConsentRecordCreate):
    id: UUID
    contact_id: UUID
    recorded_at: datetime
    recorded_by: RecordedBy | MISSING = MISSING


@dataclass(frozen=True)
class ErasureReceipt:
    """Proof an erasure ran: the number's hash and the times, nothing that identifies anyone."""

    id: UUID
    seller_id: UUID
    wa_id_hash: bytes = field(repr=False)
    requested_at: datetime
    completed_at: datetime
    scope: str


class ComplianceStore(Protocol):
    async def resolve_contact(
        self, seller_id: UUID, wa_id: str, *, display_name: str | None = None
    ) -> Contact: ...

    async def contact(self, seller_id: UUID, contact_id: UUID) -> Contact | None: ...

    async def list_contacts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        marketing_consent: MarketingConsent | None = None,
    ) -> tuple[list[Contact], str | None]: ...

    async def phone(self, seller_id: UUID, contact_id: UUID) -> str | None: ...

    async def append_consent(
        self,
        seller_id: UUID,
        contact_id: UUID,
        data: ConsentRecordCreate,
        *,
        recorded_by: RecordedBy,
        via: OptOutVia | None = None,
    ) -> ConsentRecord: ...

    async def consents(self, seller_id: UUID, contact_id: UUID) -> list[ConsentRecord]: ...

    async def mark_opted_out(self, seller_id: UUID, contact_id: UUID) -> Contact: ...

    async def is_opted_out(self, seller_id: UUID, contact_id: UUID) -> bool: ...

    async def erase(
        self, seller_id: UUID, contact_id: UUID, *, requested_at: datetime, scope: tuple[str, ...]
    ) -> ErasureReceipt: ...


async def record_consent(
    store: ComplianceStore,
    seller_id: UUID,
    contact_id: UUID,
    *,
    prompt_text: str | None,
    response_text: str,
    evidence_message_id: UUID | None = None,
    purpose: Purpose = "marketing",
) -> ConsentRecord:
    """AS-6.1, R8: ``prompt_text`` is the pending prompt the customer answered, or None."""
    if not prompt_text:
        raise ConsentRefusedError("no consent prompt is pending for this contact (R8)")
    if not is_affirmative(response_text):
        raise ConsentRefusedError("the reply is not an affirmative answer (R8)")
    data = ConsentRecordCreate(
        purpose=purpose,
        status="granted",
        text_shown=prompt_text[:500],
        response_text=response_text[:200],
        channel="whatsapp",
        evidence_message_id=evidence_message_id,
    )
    return await store.append_consent(seller_id, contact_id, data, recorded_by="agent")


async def opt_out(
    store: ComplianceStore,
    seller_id: UUID,
    contact_id: UUID,
    *,
    via: OptOutVia,
    response_text: str = "",
    evidence_message_id: UUID | None = None,
) -> Contact:
    """AS-6.5, R7: run it before any other processing of the message (FR-063)."""
    await store.mark_opted_out(seller_id, contact_id)
    data = ConsentRecordCreate(
        purpose="marketing",
        status="revoked",
        text_shown=OPT_OUT_SHOWN[via],
        response_text=response_text[:200],
        channel="other" if via == "api" else "whatsapp",
        evidence_message_id=evidence_message_id,
    )
    recorded_by: RecordedBy = "seller" if via == "api" else "system"
    await store.append_consent(seller_id, contact_id, data, recorded_by=recorded_by, via=via)
    contact = await store.contact(seller_id, contact_id)
    assert contact is not None
    return contact


def _identity(identity: ContactIdentity | None) -> ContactIdentity:
    """Resolving or reading a number needs the keys; the ledger alone does not."""
    if identity is None:
        raise LookupError(
            "contacts need CONTACT_HASH_PEPPER and CONTACT_ENCRYPTION_KEY (.env.example)"
        )
    return identity


def _event(record: ConsentRecord, via: OptOutVia | None) -> tuple[EventType, dict[str, str]]:
    base = {"contact_id": str(record.contact_id), "purpose": record.purpose}
    if record.status == "granted":
        return "consent.recorded", {**base, "consent_id": str(record.id)}
    return "consent.revoked", {**base, "via": via or "api"}


# ── in memory ──


@dataclass
class _ContactRow:
    id: UUID
    seller_id: UUID
    wa_id_hash: bytes
    wa_id_enc: bytes | None
    display_name: str | None
    created_at: datetime
    opted_out_at: datetime | None = None
    erased_at: datetime | None = None
    last_marketing_sent_at: datetime | None = None


@dataclass
class InMemoryCompliance:
    """For tests and machines without a database; same rules as Postgres."""

    identity: ContactIdentity | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    rows: dict[UUID, _ContactRow] = field(default_factory=dict)
    records: list[tuple[UUID, ConsentRecord]] = field(default_factory=list)
    events: list[tuple[EventType, dict[str, str]]] = field(default_factory=list)
    receipts: dict[UUID, ErasureReceipt] = field(default_factory=dict)

    def _row(self, seller_id: UUID, contact_id: UUID) -> _ContactRow:
        row = self.rows.get(contact_id)
        if row is None or row.seller_id != seller_id:
            raise LookupError(f"no contact {contact_id} for this seller")
        return row

    def _state(self, contact_id: UUID) -> MarketingConsent:
        latest = [r for c, r in self.records if c == contact_id and r.purpose == "marketing"]
        return latest[-1].status if latest else "none"

    def _view(self, row: _ContactRow) -> Contact:
        optional: dict[str, Any] = {}
        if row.wa_id_enc is not None and self.identity is not None:
            optional["phone_masked"] = mask_phone(self.identity.open(row.wa_id_enc))
        return Contact(
            id=row.id,
            seller_id=row.seller_id,
            display_name=row.display_name,
            marketing_consent=self._state(row.id),
            opted_out_at=row.opted_out_at,
            erased_at=row.erased_at,
            last_marketing_sent_at=row.last_marketing_sent_at,
            created_at=row.created_at,
            **optional,
        )

    async def resolve_contact(
        self, seller_id: UUID, wa_id: str, *, display_name: str | None = None
    ) -> Contact:
        sealed = _identity(self.identity).seal(wa_id)
        for row in self.rows.values():
            if row.seller_id == seller_id and row.wa_id_hash == sealed.hash:
                row.display_name = row.display_name or display_name
                # Back after an erasure: the number answers this message; the opt-out stays.
                row.wa_id_enc = row.wa_id_enc or sealed.encrypted
                return self._view(row)
        row = _ContactRow(
            new_id(), seller_id, sealed.hash, sealed.encrypted, display_name, self.clock()
        )
        self.rows[row.id] = row
        return self._view(row)

    async def contact(self, seller_id: UUID, contact_id: UUID) -> Contact | None:
        row = self.rows.get(contact_id)
        return self._view(row) if row is not None and row.seller_id == seller_id else None

    async def list_contacts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        marketing_consent: MarketingConsent | None = None,
    ) -> tuple[list[Contact], str | None]:
        rows = sorted(
            (
                row
                for row in self.rows.values()
                if row.seller_id == seller_id
                and (marketing_consent is None or self._state(row.id) == marketing_consent)
            ),
            key=lambda row: (row.created_at, row.id),
            reverse=True,
        )
        if cursor is not None:
            position = decode_cursor(cursor)
            rows = [row for row in rows if (row.created_at, row.id) < position]
        page = rows[:limit]
        token = (
            encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit and page else None
        )
        return [self._view(row) for row in page], token

    async def phone(self, seller_id: UUID, contact_id: UUID) -> str | None:
        row = self._row(seller_id, contact_id)
        return _identity(self.identity).open(row.wa_id_enc) if row.wa_id_enc is not None else None

    async def append_consent(
        self,
        seller_id: UUID,
        contact_id: UUID,
        data: ConsentRecordCreate,
        *,
        recorded_by: RecordedBy,
        via: OptOutVia | None = None,
    ) -> ConsentRecord:
        self._row(seller_id, contact_id)
        record = ConsentRecord(
            **data.model_dump(),
            id=new_id(),
            contact_id=contact_id,
            recorded_at=self.clock(),
            recorded_by=recorded_by,
        )
        self.records.append((contact_id, record))
        self.events.append(_event(record, via))
        return record

    async def consents(self, seller_id: UUID, contact_id: UUID) -> list[ConsentRecord]:
        self._row(seller_id, contact_id)
        return [record for c, record in reversed(self.records) if c == contact_id]

    async def mark_opted_out(self, seller_id: UUID, contact_id: UUID) -> Contact:
        row = self._row(seller_id, contact_id)
        row.opted_out_at = row.opted_out_at or self.clock()
        return self._view(row)

    async def is_opted_out(self, seller_id: UUID, contact_id: UUID) -> bool:
        return self._row(seller_id, contact_id).opted_out_at is not None

    async def erase(
        self, seller_id: UUID, contact_id: UUID, *, requested_at: datetime, scope: tuple[str, ...]
    ) -> ErasureReceipt:
        row = self._row(seller_id, contact_id)
        # Still erased: the first receipt stands. Back since then: erase again.
        if row.erased_at is not None and row.wa_id_enc is None and contact_id in self.receipts:
            return self.receipts[contact_id]
        now = self.clock()
        row.wa_id_enc, row.display_name, row.erased_at = None, None, now
        row.opted_out_at = row.opted_out_at or now
        receipt = ErasureReceipt(
            new_id(), seller_id, row.wa_id_hash, requested_at, now, ",".join(scope)
        )
        self.receipts[contact_id] = receipt
        return receipt


# ── Postgres ──

_LATEST_MARKETING = (
    sa.select(consent_records.c.status)
    .where(consent_records.c.contact_id == contacts.c.id, consent_records.c.purpose == "marketing")
    .order_by(consent_records.c.recorded_at.desc(), consent_records.c.id.desc())
    .limit(1)
    .correlate(contacts)
    .scalar_subquery()
)
_STATE = sa.func.coalesce(_LATEST_MARKETING, "none")


class PostgresCompliance:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        identity: ContactIdentity | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.identity = identity
        self.clock = clock

    def _view(self, row: Any) -> Contact:
        optional: dict[str, Any] = {}
        if row["wa_id_enc"] is not None and self.identity is not None:
            optional["phone_masked"] = mask_phone(self.identity.open(bytes(row["wa_id_enc"])))
        return Contact(
            id=row["id"],
            seller_id=row["seller_id"],
            display_name=row["display_name"],
            marketing_consent=row["marketing_consent"],
            opted_out_at=row["opted_out_at"],
            erased_at=row["erased_at"],
            last_marketing_sent_at=row["last_marketing_sent_at"],
            created_at=row["created_at"],
            **optional,
        )

    def _select(self, seller_id: UUID) -> Any:
        return sa.select(*contacts.c, _STATE.label("marketing_consent")).where(
            contacts.c.seller_id == seller_id
        )

    async def resolve_contact(
        self, seller_id: UUID, wa_id: str, *, display_name: str | None = None
    ) -> Contact:
        sealed = _identity(self.identity).seal(wa_id)
        insert = postgres_insert(contacts).values(
            id=new_id(),
            seller_id=seller_id,
            wa_id_hash=sealed.hash,
            wa_id_enc=sealed.encrypted,
            display_name=display_name,
        )
        upsert = insert.on_conflict_do_update(
            constraint="uq_contacts_seller_wa_id_hash",
            set_={
                "display_name": sa.func.coalesce(
                    contacts.c.display_name, insert.excluded.display_name
                ),
                # Back after an erasure: the number answers this message; the opt-out stays.
                "wa_id_enc": sa.func.coalesce(contacts.c.wa_id_enc, insert.excluded.wa_id_enc),
            },
        ).returning(contacts.c.id)
        async with self.sessions() as session, session.begin():
            contact_id = (await session.execute(upsert)).scalar_one()
        contact = await self.contact(seller_id, contact_id)
        assert contact is not None
        return contact

    async def contact(self, seller_id: UUID, contact_id: UUID) -> Contact | None:
        async with self.sessions() as session:
            row = (
                (await session.execute(self._select(seller_id).where(contacts.c.id == contact_id)))
                .mappings()
                .first()
            )
        return self._view(row) if row is not None else None

    async def list_contacts(
        self,
        seller_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        marketing_consent: MarketingConsent | None = None,
    ) -> tuple[list[Contact], str | None]:
        statement = self._select(seller_id)
        if marketing_consent is not None:
            statement = statement.where(marketing_consent == _STATE)
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        keyset_page(statement, contacts, limit=limit, cursor=cursor)
                    )
                )
                .mappings()
                .all()
            )
        page, token = next_cursor(rows, limit=limit)
        return [self._view(row) for row in page], token

    async def phone(self, seller_id: UUID, contact_id: UUID) -> str | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    sa.select(contacts.c.wa_id_enc).where(
                        contacts.c.id == contact_id, contacts.c.seller_id == seller_id
                    )
                )
            ).first()
        if row is None:
            raise LookupError(f"no contact {contact_id} for this seller")
        return _identity(self.identity).open(bytes(row[0])) if row[0] is not None else None

    async def append_consent(
        self,
        seller_id: UUID,
        contact_id: UUID,
        data: ConsentRecordCreate,
        *,
        recorded_by: RecordedBy,
        via: OptOutVia | None = None,
    ) -> ConsentRecord:
        record = ConsentRecord(
            **data.model_dump(),
            id=new_id(),
            contact_id=contact_id,
            recorded_at=self.clock(),
            recorded_by=recorded_by,
        )
        async with self.sessions() as session, session.begin():
            owned = await session.scalar(
                sa.select(contacts.c.id).where(
                    contacts.c.id == contact_id, contacts.c.seller_id == seller_id
                )
            )
            if owned is None:
                raise LookupError(f"no contact {contact_id} for this seller")
            await session.execute(
                sa.insert(consent_records).values(
                    seller_id=seller_id, **record.model_dump(exclude_defaults=False)
                )
            )
            event, payload = _event(record, via)
            await emit(session, event, payload, seller_id=seller_id)
        return record

    async def consents(self, seller_id: UUID, contact_id: UUID) -> list[ConsentRecord]:
        statement = (
            sa.select(consent_records)
            .where(
                consent_records.c.contact_id == contact_id,
                consent_records.c.seller_id == seller_id,
            )
            .order_by(consent_records.c.recorded_at.desc(), consent_records.c.id.desc())
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [ConsentRecord.model_validate(dict(row)) for row in rows]

    async def mark_opted_out(self, seller_id: UUID, contact_id: UUID) -> Contact:
        async with self.sessions() as session, session.begin():
            updated = await session.scalar(
                sa.update(contacts)
                .where(contacts.c.id == contact_id, contacts.c.seller_id == seller_id)
                .values(opted_out_at=sa.func.coalesce(contacts.c.opted_out_at, self.clock()))
                .returning(contacts.c.id)
            )
        if updated is None:
            raise LookupError(f"no contact {contact_id} for this seller")
        contact = await self.contact(seller_id, contact_id)
        assert contact is not None
        return contact

    async def is_opted_out(self, seller_id: UUID, contact_id: UUID) -> bool:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    sa.select(contacts.c.opted_out_at).where(
                        contacts.c.id == contact_id, contacts.c.seller_id == seller_id
                    )
                )
            ).first()
        if row is None:
            raise LookupError(f"no contact {contact_id} for this seller")
        return row[0] is not None

    async def erase(
        self, seller_id: UUID, contact_id: UUID, *, requested_at: datetime, scope: tuple[str, ...]
    ) -> ErasureReceipt:
        now = self.clock()
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    sa.select(contacts.c.wa_id_hash, contacts.c.erased_at, contacts.c.wa_id_enc)
                    .where(contacts.c.id == contact_id, contacts.c.seller_id == seller_id)
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise LookupError(f"no contact {contact_id} for this seller")
            wa_id_hash = bytes(row[0])
            # Still erased: the first receipt stands. Back since then: erase again.
            if row[1] is not None and row[2] is None:
                earlier = (
                    (
                        await session.execute(
                            sa.select(erasure_receipts)
                            .where(
                                erasure_receipts.c.seller_id == seller_id,
                                erasure_receipts.c.wa_id_hash == wa_id_hash,
                            )
                            .order_by(erasure_receipts.c.completed_at.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
                if earlier is not None:
                    return _receipt(earlier)
            await session.execute(
                sa.update(contacts)
                .where(contacts.c.id == contact_id)
                .values(
                    wa_id_enc=None,
                    display_name=None,
                    erased_at=now,
                    opted_out_at=sa.func.coalesce(contacts.c.opted_out_at, now),
                )
            )
            receipt = ErasureReceipt(
                new_id(), seller_id, wa_id_hash, requested_at, now, ",".join(scope)
            )
            await session.execute(
                sa.insert(erasure_receipts).values(
                    id=receipt.id,
                    seller_id=seller_id,
                    wa_id_hash=wa_id_hash,
                    requested_at=requested_at,
                    completed_at=now,
                    scope=receipt.scope,
                )
            )
        return receipt


def _receipt(row: Any) -> ErasureReceipt:
    return ErasureReceipt(
        id=row["id"],
        seller_id=row["seller_id"],
        wa_id_hash=bytes(row["wa_id_hash"]),
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
        scope=row["scope"],
    )
